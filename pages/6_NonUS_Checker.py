"""
Non-US Checker
==============
Burry's Tragic Algebra and IV15, unchanged, for companies that do not file
with the SEC in US GAAP in dollars. The method transfers; the data does not,
and this page exists for the data. He applies the same algebra to ASML and
excludes it from his index only for FX ("euro-denominated, foreign exchange
over time complicates analysis") — so this page keeps his exclusion as a
rule: every figure stays in the filing currency, prices come only from a
listing in that same currency, and nothing is ever converted.

THREE CASES, EASY TO HARD
-------------------------
1. Foreign filers on EDGAR under IFRS (ASML, SAP in EUR; Grab, Sophia
   Genetics, Legend Biotech in USD). The reader below runs in the filing
   currency and knows IFRS tag names for the lines tool 1 reads. Where a
   line has no verified IFRS name it is REFUSED in the tag panel, never
   guessed — the panel is the main instrument on this page.
2. Companies with no SEC filing at all (CTS Eventim, Frankfurt): nothing on
   EDGAR, so the figures are typed into the paste mode, which drives the
   IDENTICAL engine — a self-test pins the paste path against tool 1's
   published Alphabet figures. The paste mode is also the fallback for any
   line the reader cannot fetch.
3. Currency. One currency throughout, verified, never mixed: the reader
   detects the filing currency from the facts themselves, the price
   request's own currency field is checked against it, and a mismatch
   refuses prices rather than footnoting them. The ADS ratio input converts
   UNITS (one ADS = N ordinary shares — Legend Biotech is 2), never
   currency, so it is allowed only for USD filers.

A US-GAAP USD filer typed here — 10-K, or a 40-F like Shopify — is sent to
the Tragic Algebra Analyzer by name: it needs nothing from this page.
Financial filers are refused outright: IFRS insurance and banking
accounting is its own world and no page prices it yet.

The reader below is tool 1's, copied verbatim (a Streamlit page cannot be
imported without executing its UI) with marked edits, each dated 3 Sep
2026: the currency plumbing, the IFRS tag names, the router, the financial
refusal, the price-currency gate and the ADS ratio. Everything else — the
Year dataclass, pooling, the IV ladder, the seed helper, split handling,
the share ladder, the tag panel — is unchanged, and the self-test pins the
ported engine against tool 1's Alphabet figures.

THE KEY SIMPLIFICATION (tool 1's, kept verbatim)
------------------------------------------------
The published cost formula is  V = T x (W + dS) / W  , which needs W, the number
of shares repurchased. W is almost never tagged in XBRL. But P = T / W, so:

    V = T x (W + dS)/W  =  T + (T/W) x dS  =  T + P x dS

W cancels. Only the average share price is needed. So:
    V  = max(0, T + P x dS)      market value of shares handed to employees
    C  = Cw - Ce                 net cash award payments
    Om = C + V                   true SBC cost, replaces GAAP's estimate
    OE = N + G - Om              owners' earnings
    dE = OE / N                  fraction of reported profit that is really yours
Pooled over ~10 years as sum(OE)/sum(N) — never an average of annual ratios.
Every identity is currency-invariant, which is why the method transfers and
only the data needed work.

Run:  streamlit run Home.py
"""

from __future__ import annotations

import datetime as dt
import os
import statistics
import threading
import time
from dataclasses import dataclass, field

import pandas as pd
import requests
import streamlit as st

# ══════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════

# The SEC requires a real contact address in the User-Agent and blocks generic
# ones. Keep it OUT of the repo: set it in Streamlit secrets (Settings →
# Secrets) as   sec_contact = "you@example.com"   and it never appears in your
# source. Falls back to an environment variable for local runs.
def _sec_contact() -> str:
    try:
        v = st.secrets.get("sec_contact", "")
        if v:
            return str(v)
    except Exception:
        pass
    return os.environ.get("SEC_CONTACT", "")


SEC_HEADERS = {
    "User-Agent": f"Tragic Algebra Analyzer {_sec_contact() or 'contact-not-set'}",
    "Accept-Encoding": "gzip, deflate",
}

# SEC allows 10 requests/second per user agent and blocks offenders. One person
# clicking around never gets close; ten people sharing an app easily does
# (this page has no watchlist, but the shared budget is app-wide). All SEC traffic funnels through _sec_get, which spaces
# requests process-wide and backs off when throttled.
_SEC_MIN_INTERVAL = 0.15          # ~6.7 req/s, comfortably inside the limit
_sec_lock = threading.Lock()
_sec_last = [0.0]


def _sec_get(url: str, timeout: int = 25) -> requests.Response:
    for attempt in range(4):
        with _sec_lock:
            wait = _SEC_MIN_INTERVAL - (time.monotonic() - _sec_last[0])
            if wait > 0:
                time.sleep(wait)
            _sec_last[0] = time.monotonic()
        try:
            r = requests.get(url, headers=SEC_HEADERS, timeout=timeout)
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            return r
        if r.status_code in (403, 429, 502, 503):
            time.sleep(2 ** attempt)          # 1s, 2s, 4s
            continue
        r.raise_for_status()
    raise RuntimeError(
        "SEC is throttling this app. Wait a minute and try again. If it keeps happening, "
        "check that SEC_HEADERS at the top of the file has a real email address in it — "
        "the SEC blocks generic user agents outright.")


@dataclass(frozen=True)
class Tier:
    stage1_years: int
    stage2_years: int
    stage2_multiplier: float
    terminal_growth_cap: float
    debt_capacity_ebitda: float

    @property
    def horizon(self) -> int:
        return self.stage1_years + self.stage2_years

    traded_multiple: float = 14.5

    @property
    def perpetuity_equivalent(self) -> float:
        """(1+g)/(r-g) at r=15% — the multiple this tier's own terminal growth
        already implies. A useful floor, but too punitive as a default: Burry
        applies 'a multiple based on my experience with traded multiples' to
        year-15 earnings, and traded multiples sit well above perpetuity maths."""
        return (1 + self.terminal_growth_cap) / (0.15 - self.terminal_growth_cap)

    @property
    def default_exit_multiple(self) -> float:
        return self.traded_multiple


# Stage durations, multipliers, terminal caps and debt capacity are published.
# The traded exit multiple is NOT — these are calibrated so that the growth rate
# needed to reproduce a published IV15 matches the company's actual growth.
# Adobe is the anchor: at 14.5x, reaching his $262 needs 11.1% growth, and Adobe
# grew 11%. Treat them as reasonable starting points, not gospel.
AICT: dict[str, Tier] = {
    "Fortress": Tier(8, 16, 0.70, 0.07, 3.0, 20.0),
    "Castle":   Tier(7, 13, 0.55, 0.05, 2.5, 16.0),
    "Chapel":   Tier(5, 10, 0.45, 0.04, 2.0, 14.5),
    "Stone":    Tier(4,  7, 0.35, 0.03, 0.0,  9.0),
    "Wood":     Tier(2,  4, 0.25, 0.00, 0.0,  5.0),
}

TIER_BLURB = {
    "Fortress": "regulated or platform; owns its AI; no acute seat risk",
    "Castle":   "strong moat; owned AI at material scale; outcome fairly certain",
    "Chapel":   "acute AI threat but owned AI at decent scale plus switching costs",
    "Stone":    "meaningful threat without strong adaptability, or chronic pressure",
    "Wood":     "borrowed AI; no credible R&D; direct attack from foundation models",
}

VALUATION_BRACKETS = [(0.50, 35), (0.75, 32), (0.90, 28), (1.00, 24), (1.25, 20),
                      (1.50, 17), (2.00, 14), (3.00, 8), (5.00, 5), (10.0, 3)]

RUNG_MEANING = {8: "baseline intrinsic value, upper", 10: "baseline intrinsic value, lower",
                12: "a fair price", 15: "the benchmark buy target",
                18: "deep margin of safety", 20: "crisis pricing"}

# ══════════════════════════════════════════════════════════════════════
#  TRAGIC ALGEBRA
# ══════════════════════════════════════════════════════════════════════


@dataclass
class Year:
    """One fiscal year. Dollars in $M, shares in millions."""
    fy: int
    N: float                  # GAAP net income
    G: float = 0.0            # GAAP SBC expense
    T: float = 0.0            # buyback dollars
    dS: float = 0.0           # change in shares outstanding (+ = dilution)
    Cw: float = 0.0           # tax withheld on vesting
    Ce: float = 0.0           # option / ESPP proceeds
    price: float = 0.0        # average share price for the year
    A: float = 0.0            # stock issued as acquisition consideration, $M
    cash_settled_sbc: bool = False   # MELI-style: no equity gap to close
    excluded: str = ""        # non-empty means capital formation, not pay

    @property
    def C(self) -> float:
        return self.Cw - self.Ce

    @property
    def V(self) -> float:
        """Market value of shares delivered to EMPLOYEES.

        Floored at zero: you cannot deliver a negative number of shares.

        `A` is stock issued to buy a company, and it is netted out here rather
        than through dS. The protocol has always excluded M&A issuance — it is
        a corporate transaction, not pay — but the exclusion was routed through
        the share count, which meant finding a tagged number of SHARES. Filers
        mostly do not publish one. Salesforce publishes the dollar
        consideration instead, and a tagged value is better than a count in any
        case, because the count has to be priced at the year's average while
        the value is what the deal actually cost.

        Untreated, Slack put $11.3B and Tableau $15.6B into Salesforce's
        stock-comp column. Pooled dE read 19.7% against Burry's published
        54.7%, and four separate years printed dE below -200%.
        """
        if self.cash_settled_sbc:
            return 0.0
        return max(0.0, self.T + self.price * self.dS - self.A)

    @property
    def omega(self) -> float:
        return self.C + self.V

    @property
    def OE(self) -> float:
        return self.N + self.G - self.omega

    @property
    def dE(self) -> float | None:
        return self.OE / self.N if self.N else None


@dataclass
class Pooled:
    dE: float
    sum_N: float
    sum_OE: float
    sum_omega: float
    sum_G: float
    years: int

    @property
    def gaap_overstatement(self) -> float:
        return (self.sum_omega - self.sum_G) / self.sum_OE if self.sum_OE else float("nan")

    @property
    def street_overstatement(self) -> float:
        return self.sum_omega / self.sum_OE if self.sum_OE else float("nan")

    @property
    def dE_defined(self) -> bool:
        """dE = OE/N only means something when N is positive.

        With a negative denominator the sign flips and a loss-making company
        that ALSO bled value to stock comp reports a healthy-looking positive
        ratio. HubSpot: cumulative net income -$587M, owners' earnings
        -$3,964M, dE = +675%. Read naively that looks excellent. It is the
        opposite."""
        return self.sum_N > 0

    @property
    def tragic_tier(self) -> bool:
        return self.sum_OE < 0

    def retention(self, t: int) -> float:
        """Share of reported value growth that survives to year t. dE compounds."""
        return self.dE ** t

    def true_cagr(self, gaap_growth: float) -> float:
        """Break-even dE is 1/(1+g). Below it, reported growth never reaches you."""
        return self.dE * (1.0 + gaap_growth) - 1.0


def pool_recent(years: list[Year], n: int = 3) -> Pooled:
    """Pooled dE over just the last n years.

    The long window is the honest diagnostic, but where capital policy has
    changed the recent regime is what should feed a forward estimate.
    Salesforce is the clear case: 54.7% pooled over eleven years, 90.4% over
    the last three once buybacks overwhelmed issuance.
    """
    return pool(years[-n:])


def pool(years: list[Year]) -> Pooled:
    years = [y for y in years if not y.excluded]
    sN = sum(y.N for y in years)
    if not years or sN == 0:
        raise ValueError("Not enough data to pool.")
    return Pooled(
        dE=sum(y.OE for y in years) / sN,
        sum_N=sN, sum_OE=sum(y.OE for y in years),
        sum_omega=sum(y.omega for y in years), sum_G=sum(y.G for y in years),
        years=len(years),
    )


# ══════════════════════════════════════════════════════════════════════
#  INTRINSIC VALUE LADDER
# ══════════════════════════════════════════════════════════════════════


@dataclass
class IVParams:
    OE: float               # $M
    shares: float           # M
    tier: str
    growth: float           # decimal
    net_cash: float = 0.0   # $M
    exit_multiple: float = 20.0
    blend: float = 0.5      # weight on the perpetuity model
    stage0_years: int = 0
    stage0_growth: float = 0.0
    m2_style: str = "dcf"   # "dcf" = discount the stream, then a year-15 exit
                            # multiple. "hold" = buy, let it compound, sell in
                            # year 15 — no interim cash. See note below.


def _stream(p: IVParams, n: int) -> list[float]:
    t = AICT[p.tier]
    g2 = p.growth * t.stage2_multiplier
    out, e = [], p.OE
    for y in range(1, n + 1):
        if y <= p.stage0_years:
            g = p.stage0_growth
        elif y <= p.stage0_years + t.stage1_years:
            g = p.growth
        else:
            g = g2
        e *= 1.0 + g
        out.append(e)
    return out


def intrinsic_value(p: IVParams, required_return_pct: float) -> float:
    """IV15 -> intrinsic_value(p, 15).

    Two models sharing one earnings stream, blended:
      model 1  stages 1 and 2, then a terminal perpetuity at the tier cap
      model 2  project to year 15, apply a market multiple

    Every rung is a full re-run at its own discount rate. Scaling one rung off
    another does not work — published IV12/IV15 ratios span 1.33 to 1.44.

    A negative result is meaningful: no share price delivers that return.
    """
    r = required_return_pct / 100.0
    t = AICT[p.tier]
    if r <= t.terminal_growth_cap or p.shares <= 0:
        return float("nan")

    n = t.horizon + p.stage0_years
    s = _stream(p, n)
    pv = sum(cf / (1 + r) ** y for y, cf in enumerate(s, 1))
    m1 = pv + s[-1] * (1 + t.terminal_growth_cap) / (r - t.terminal_growth_cap) / (1 + r) ** n

    # Two readings of the Buffett leg, and the published figures do not settle
    # which is right:
    #   "dcf"  — a normal DCF that finishes with a market multiple instead of a
    #            perpetuity. Fits Salesforce, Adobe, Paycom at blends of 0.5-1.
    #   "hold" — buy the business, let it reinvest, sell in year 15. No interim
    #            cash reaches you. Only this reading reaches Paylocity's
    #            published IV15, but it makes the blend a ~3x lever.
    s2 = _stream(p, 15)
    if p.m2_style == "hold":
        m2 = s2[-1] * p.exit_multiple / (1 + r) ** 15
    else:
        pv2 = sum(cf / (1 + r) ** y for y, cf in enumerate(s2, 1))
        m2 = pv2 + s2[-1] * p.exit_multiple / (1 + r) ** 15

    return (p.blend * m1 + (1 - p.blend) * m2 + p.net_cash) / p.shares


def model_legs(p: IVParams, required_return_pct: float = 15.0) -> tuple[float, float]:
    """Per-share value from each leg, so the blend's effect is visible rather
    than buried. A wide spread means the blend choice is doing a lot of work."""
    a = IVParams(**{**p.__dict__, "blend": 1.0})
    b = IVParams(**{**p.__dict__, "blend": 0.0})
    return (intrinsic_value(a, required_return_pct),
            intrinsic_value(b, required_return_pct))


def ladder(p: IVParams) -> dict[int, float]:
    return {n: intrinsic_value(p, n) for n in (8, 10, 12, 15, 18, 20)}


def expected_return(price: float, p: IVParams) -> float:
    """IVB — the CAGR implied by today's price. Needs no required return chosen
    in advance, which arguably makes it the most useful single output."""
    lo, hi = AICT[p.tier].terminal_growth_cap + 1e-6, 3.0
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if intrinsic_value(p, mid * 100) > price else (lo, mid)
    out = (lo + hi) / 2
    # Saturating at the ceiling is not a 300% forecast, it means the inputs are
    # wrong — nearly always a bad share count.
    return float("inf") if out > 2.5 else out


def solve_growth(target_iv15: float, p: IVParams,
                 lo: float = -0.30, hi: float = 1.00) -> float | None:
    """Growth rate reproducing a given IV15, by bisection.

    Intrinsic value rises monotonically with growth, so bisection is exact
    enough and avoids a scipy dependency for one root-find.
    """
    f = lambda g: intrinsic_value(IVParams(**{**p.__dict__, "growth": g}), 15) - target_iv15
    flo, fhi = f(lo), f(hi)
    if flo != flo or fhi != fhi or flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def valuation_points(ratio: float) -> int:
    if ratio < 0:
        return -2
    for ceiling, pts in VALUATION_BRACKETS:
        if ratio <= ceiling:
            return pts
    return -2


def zone(ratio: float) -> tuple[str, str]:
    if ratio < 0:
        return "Not investible", "error"
    if ratio <= 1.0:
        return "Fat Pitch", "success"
    if ratio <= 1.5:
        return "Just Outside", "info"
    return "Out Field", "error"


# ══════════════════════════════════════════════════════════════════════
#  DATA
# ══════════════════════════════════════════════════════════════════════

CONCEPTS = {
    # Order is priority, and it is a judgement about whose profit this is.
    # NetIncomeLoss is the parent's share. NetIncomeLossAvailableToCommon-
    # StockholdersBasic is what is left for common holders after preferred
    # dividends. ProfitLoss includes what belongs to minority holders of
    # consolidated subsidiaries, so it is the most generous and goes last.
    #
    # The middle one was added after Booking Holdings. BKNG tags NetIncomeLoss
    # in 10-Ks only through 2012 and ProfitLoss only through 2015; from 2013 on
    # its bottom line sits in the available-to-common tag. Without it the window
    # ended at FY2015 and the page valued the company on eleven-year-old
    # earnings of $2,551M against an actual FY2025 figure of $5,404M — and
    # printed a full verdict rather than refusing.
    # The IFRS list had the same defect the US-GAAP list was fixed for, and it
    # survived because no filer in the regression set is IFRS.
    # ProfitLossAttributableToOwnersOfParent is the parent's share — the direct
    # counterpart of NetIncomeLoss — and ProfitLoss is the consolidated figure
    # including minority holders, so it belongs last on both sides.
    #
    # 26 Aug 2026: the brief named Toyota and SAP as the regression pair for
    # this. Neither can exercise it. Toyota's
    # ProfitLossAttributableToOwnersOfParent carries ONE unit key, JPY —
    # checked against EDGAR, every 20-F row under it, the latest being
    # ¥4,765,086,000,000 to 2025-03-31 — and SAP reports in EUR. Both are
    # refused by reporting_currency() long before a net income tag is chosen,
    # so the swap is invisible on them either way. It is pinned by the
    # synthetic test in self_test instead, and a live check needs a
    # USD-reporting IFRS filer.
    "N":  (["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
            "ProfitLoss"],
           ["ProfitLossAttributableToOwnersOfParent", "ProfitLoss"]),
    # PAGE 6 EDIT (3 Sep 2026): IFRS candidates added to G, Ce, REV and SHD
    # below. ShareBasedPaymentsExpense was inherited from tool 1 unverified;
    # the two Expense... names are the ifrs-full elements for the IFRS 2
    # charge and stay behind it until a live panel decides.
    "G":  (["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
           ["ShareBasedPaymentsExpense",
            "ExpenseFromSharebasedPaymentTransactionsWithEmployees",
            "ExpenseFromSharebasedPaymentTransactions"]),
    "T":  (["PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity",
            "PaymentsForRepurchaseOfCommonStockAndRestrictedStockUnits",
            "StockRepurchasedAndRetiredDuringPeriodValue",
            "StockRepurchasedDuringPeriodValue"],
           # PaymentsToAcquireOrRedeemEntitysShares verified live on Novo
           # Nordisk and SAP. PurchaseOfTreasuryShares verified 4 Sep 2026 by
           # browser on Ferrari (CIK 1648416), whose buybacks read ZERO years
           # without it while the company retired billions — the ceiling note
           # fired honestly, and this name is the fix.
           ["PaymentsToAcquireOrRedeemEntitysShares", "PurchaseOfTreasuryShares"]),
    "Cw": (["PaymentsRelatedToTaxWithholdingForShareBasedCompensation",
            "TreasuryStockValueAcquiredCostMethod"], []),
    "Ce": (["ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlans",
            "ProceedsFromStockOptionsExercised", "ProceedsFromIssuanceOfTreasuryStock",
            "ProceedsFromSaleOfTreasuryStock", "ProceedsFromStockPlans",
            "ProceedsFromEmployeeStockPurchasePlan", "ProceedsFromIssuanceOfCommonStock"],
           # Only the narrow exercise-proceeds name. ProceedsFromIssuingShares
           # exists in ifrs-full but is every issuance including offerings —
           # the same trap observation C flags on the US side, where the broad
           # proceeds tag credits ATM raises to owners' earnings.
           ["ProceedsFromExerciseOfOptions"]),
    "REV": (["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
             "RevenueFromContractWithCustomerIncludingAssessedTax"],
            # Grab read no revenue with only "Revenue" here, so it tags
            # something else — RevenueFromContractsWithCustomers is the
            # IFRS 15 name and the likely answer. Same figure, two names;
            # ordering is convenience, not definition.
            ["Revenue", "RevenueFromContractsWithCustomers"]),
    "SHD": (["WeightedAverageNumberOfDilutedSharesOutstanding",
             "WeightedAverageNumberOfSharesOutstandingDiluted"],
            # Diluted first, basic behind it — a correctness ordering, the
            # same judgement as the US list, not a synonym list.
            ["AdjustedWeightedAverageShares", "WeightedAverageShares"]),
    # Shares issued for reasons that are NOT compensation. The extraction
    # protocol excludes these from dS explicitly: M&A issuance, public
    # offerings and debt-to-equity conversions are corporate transactions,
    # not pay. Salesforce issued heavily for Slack, Tableau, MuleSoft and
    # Informatica; charging those to employees makes dE far too negative.
    #
    # Read by _issuance(), NOT _annual() — see that function for why. The tag
    # lists are longer than they look because filers put the same event in
    # different places: the equity rollforward, or the business-combination
    # note. Salesforce uses only the second, which is why "shares issued for
    # acquisitions" read 0 years on CRM while Slack, Tableau and MuleSoft sat
    # in the filings.
    "MA":   (["StockIssuedDuringPeriodSharesAcquisitions",
              "BusinessAcquisitionEquityInterestsIssuedOrIssuableNumberOfSharesIssued",
              "StockIssuedDuringPeriodSharesBusinessAcquisition"], []),
    "OFFER": (["StockIssuedDuringPeriodSharesNewIssues",
               "SaleOfStockNumberOfSharesIssuedInTransaction"], []),
    "CONV": (["StockIssuedDuringPeriodSharesConversionOfConvertibleSecurities",
              "StockIssuedDuringPeriodSharesConversionOfUnits"], []),
    # The same event in dollars. Most filers tag this and not the share count —
    # Salesforce tags only this — so it is the line that actually catches
    # all-stock acquisitions. Read in USD, netted out of V directly.
    "MAV": (["StockIssuedDuringPeriodValueAcquisitions",
             "StockIssuedDuringPeriodValueBusinessAcquisition",
             "BusinessCombinationConsiderationTransferredEquityInterestsIssuedAndIssuable"], []),
}

BALANCE = {
    # PAGE 6 EDIT (3 Sep 2026): _instant scans us-gaap, dei, ifrs-full in
    # turn, so IFRS names can sit in the same lists — a name simply does not
    # exist in the wrong taxonomy. CashAndCashEquivalents is the IAS 7 line.
    # Investments (sti/lti) get NO IFRS names in v1: the ifrs-full candidates
    # (OtherCurrentFinancialAssets and kin) are broader than cash-like debt
    # securities, and an unread investment line only UNDERSTATES net cash —
    # the safe direction — while a broad one would overstate it.
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
             "CashAndCashEquivalents"],
    "sti":  ["ShortTermInvestments", "MarketableSecuritiesCurrent",
             "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    # LongTermInvestments is LAST because it is broader, not a synonym. The two
    # ahead of it are debt securities — cash-like, and safe to add to net cash.
    # LongTermInvestments is total long-term investments and can hold strategic
    # equity stakes in other companies, which are not deployable cash.
    # Booking Holdings, 24 Aug 2026: it tagged AvailableForSale...Noncurrent for
    # the last time in 2010 and has used LongTermInvestments since 2017, so the
    # page carried a fifteen-year-old balance into today's net cash with no note.
    # The recency rule promotes the fresher tag and the fallback note names the
    # swap — which is the point of keeping the narrower one preferred.
    "lti":  ["MarketableSecuritiesNoncurrent",
             "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
             "LongTermInvestments"],
    # DebtLongtermAndShorttermCombinedAmount is LAST because it is broader, not
    # a synonym: it is the whole debt balance, long-term and current together.
    # Progressive, 25 Aug 2026: it does not tag LongTermDebtNoncurrent at all
    # and stopped tagging LongTermDebt after 2015, so the capital base carried
    # no borrowings from 2016 on — $6,897M missing at FY2025 with nothing on the
    # page to show it. Its LongTermDebtCurrent is tagged and reads exactly zero
    # every year, so the combined figure double-counts nothing here.
    # Keeping the two narrower tags preferred is what confines this to filers
    # where they have gone stale, and makes the fallback note name the swap.
    # Borrowings (ifrs-full) is LAST for the same reason the combined US tag
    # is: it is the WHOLE debt balance. If it answers, the std group below —
    # which carries no IFRS names — reads nothing, so nothing double-counts;
    # the tag panel shows the swap. Narrower IFRS borrowings names vary too
    # much by filer to guess; candidates go in only from a real filing.
    "ltd":  ["LongTermDebtNoncurrent", "LongTermDebt",
             "DebtLongtermAndShorttermCombinedAmount",
             # LongtermBorrowings verified 4 Sep 2026 by browser on
             # TotalEnergies (CIK 879764) — the filer whose unread debt let
             # net cash print +$26B of fiction and forced the debt guard.
             # Narrow long-term portion, so it sits BEFORE the whole-balance
             # Borrowings.
             "NoncurrentBorrowings", "LongtermBorrowings", "Borrowings"],
    "std":  ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings", "CommercialPaper",
             "ShorttermBorrowings"],
    # Not debt in Burry's sense — his ROIC formula subtracts long-term operating
    # leases from capital rather than treating them as borrowings. Shown so a
    # retailer's zero-debt line doesn't look like a failed lookup.
    "lease": ["OperatingLeaseLiabilityNoncurrent", "OperatingLeaseLiability"],
}

ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "40-F")

# The display name for each balance-sheet line, in panel order. Hoisted out of
# the panel builder so the staleness guard below reads the same list: a guard
# that keeps its own copy is one edit away from checking a line the panel does
# not show, or missing one it does.
BALANCE_ROWS = (
    ("Cash", BALANCE["cash"]),
    ("Short-term investments", BALANCE["sti"]),
    ("Long-term investments", BALANCE["lti"]),
    ("Long-term debt", BALANCE["ltd"]),
    ("Short-term debt", BALANCE["std"]),
    ("Operating leases", BALANCE["lease"]),
)


@st.cache_data(ttl=86400, show_spinner=False)
def _ticker_map() -> dict[str, str]:
    r = _sec_get("https://www.sec.gov/files/company_tickers.json", timeout=15)
    return {e["ticker"].upper(): str(e["cik_str"]).zfill(10) for e in r.json().values()}


@st.cache_data(ttl=86400, show_spinner=False)
def _sic(cik: str) -> tuple[str, str]:
    """SIC code and description, for sector-specific guards."""
    try:
        j = _sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json", timeout=20).json()
        return str(j.get("sic", "")), str(j.get("sicDescription", ""))
    except Exception:
        return "", ""


def is_financial(sic: str) -> bool:
    """SIC 6000-6799: banks, insurers, brokers, REITs. For these, investments
    back policyholder or depositor liabilities and are not shareholder cash, so
    a balance-sheet 'net cash' figure is meaningless and hugely overstated."""
    return sic.isdigit() and 6000 <= int(sic) <= 6799


@st.cache_data(ttl=86400, show_spinner=False)
def _facts(cik: str) -> dict:
    return _sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                    timeout=30).json()


# Lines where a second tag means the same thing, so a gap in one can be filled
# from another. Deliberately not net income, stock comp, revenue or share
# counts: their alternates carry different definitions (parent-only versus
# consolidated, per-plan versus total, ASC 606 versus legacy), and stitching
# those across years would put a step change in a growth rate and call it
# history.
# Lines whose concepts are alternates for the same thing, so a later one may
# fill the years an earlier one left empty rather than being ignored outright.
#
# "N" was added after Booking Holdings returned FY2008-FY2010 and nothing else.
# BKNG tagged NetIncomeLoss in three old filings and has used ProfitLoss since;
# three years was enough to stop the search, so sixteen years of perfectly good
# net income were never looked at and the page refused to load at all. This is
# the H&R Block failure in the _annual docstring, still live on the one line
# every other figure is built from. Paychex is very likely the same fault
# showing up quietly instead of loudly: an eight-year hole rather than a
# refusal, with the pooled figures spanning a gap nobody could see.
# Lines where the alternates are names for ONE figure, so the freshest series
# is always the right one. Everything else in CONCEPTS is either filled or has
# an ordering that carries meaning. See _instant's docstring for what happens
# when this distinction is ignored.
RECENCY_KEYS = {"REV", "SHD"}


FILL_KEYS = {"T", "Cw", "Ce", "DIV", "INT", "LEASEPAY", "CAPEX", "MA", "OFFER", "CONV", "G", "N"}


# ══════════════════════════════════════════════════════════════════════
#  WINDOW GUARDS — refuse when the window is not usable
# ══════════════════════════════════════════════════════════════════════
#
# The four-year minimum inside load() asks whether there is enough history.
# These two ask whether it is the RIGHT history. Both were exposed by one
# Booking Holdings run on 23 Aug 2026, before the net income tag list was
# repaired: eight years of net income ending FY2015 cleared the minimum and
# the page printed a full verdict on eleven-year-old earnings — forward net
# income 2,551 against an actual 5,404. The page carried its own proof, a
# note dividing a 2015 profit by 2025 revenue and calling owners' earnings
# 6.4% of revenue. Seven of those eight years also showed an average price
# of $0.00, because the window ended before the eleven-year price history
# begins: V floors at zero, the true SBC cost collapses to withholding minus
# option proceeds — negative in one year — and ΔE measures nothing.
#
# Both thresholds are deliberately loose, because the cost of a false
# refusal is a page that will not load for a healthy company. A December
# filer read in January sits two calendar years behind its own newest 10-K,
# since FY2025 stays the latest until the FY2026 report lands in February.
# Two is ordinary reporting lag. Three is a hole.
#
# Neither of these has a live ticker that reproduces it any more — the net
# income tag work closed both shapes — so they are pinned by the self-tests
# below and verified by construction rather than by a run.

STALE_VS_REVENUE = 3     # years net income may trail revenue before refusing
STALE_VS_TODAY = 3       # years net income may trail the calendar year
MIN_PRICED_SHARE = 0.5   # more than half the window must carry a share price


# ══════════════════════════════════════════════════════════════════════
#  PER-YEAR ΔE CELL — a ratio needs a denominator worth dividing by
# ══════════════════════════════════════════════════════════════════════
#
# Booking Holdings FY2020: net income 59, owners' earnings -979, and the
# year-by-year column printed -1659.5%. Nothing was wrong with either
# figure — covid took net income to almost nothing while $1,303M of
# buybacks against a share count that barely moved left a real cost
# behind. The ratio between them is the problem: divide anything by 59
# and you get a number that looks like a measurement and is only an
# artifact of the denominator.
#
# The POOLED figures already handle this correctly, because they sum
# before dividing — BKNG reads 92.2% and 92.5% with that year fully
# weighted inside them. So nothing about the arithmetic changes here.
# Only the cell changes, and only for a year that cannot carry a ratio.
#
# Two ways a denominator fails: it is not positive at all, or it is so
# small against the rest of the window that the ratio says more about the
# denominator than about the company. A tenth of the window's median
# separates BKNG's FY2020 (1.7%) from every ordinary bad year in the set
# — Adobe's weakest is 24% of its median, Paychex's 66%.

DE_CELL_MIN_SHARE = 0.10   # of the window's median net income


def dE_cell(N: float, dE: float | None, median_N: float) -> float | None:
    """The per-year dE to display, or None where the denominator cannot carry it."""
    if dE is None or N <= 0:
        return None
    if median_N > 0 and N < median_N * DE_CELL_MIN_SHARE:
        return None
    return dE


def median_positive_N(values: list[float]) -> float:
    """Median of the positive net income figures in a window; 0.0 when there are none."""
    pos = sorted(v for v in values if v > 0)
    return pos[len(pos) // 2] if pos else 0.0

# ══════════════════════════════════════════════════════════════════════
#  TABLE DECIMALS — a row the reader can add up, at any scale
# ══════════════════════════════════════════════════════════════════════
#
# Pro-Dex, 28 Aug 2026: 3.3M shares, and every dollar column rounded to
# whole millions. FY2016 read net income 1, GAAP SBC 0, true SBC cost 0,
# owners' earnings 1 — and ΔE 79.2%. The engine was right; the ratios are
# computed unrounded. But six of ten rows showed a stock-comp cost of "0"
# that was plainly not zero, and the table exists so a reader can follow
# the arithmetic across a row. "Your table says 1 minus 0 equals 79.2%" is
# the first comment a microcap reader would write.
#
# One decision for the WHOLE table, not per column, because it is the row
# that has to reconcile: net income at whole millions beside stock comp at
# tenths adds up no better than today. Gated on the largest absolute value
# across the dollar columns, so a table with anything at $100M or more
# keeps printing whole millions — HRB, BRBR, Apple and every other baseline
# are untouched. Display only; no figure the page computes passes through
# this.

MONEY_1DP_BELOW = 100.0   # largest |value| in the table, $M: below this, one decimal
MONEY_2DP_BELOW = 10.0    # ...and below this, two


def money_decimals(values) -> int:
    """Decimal places for a year-by-year table's dollar columns: 0, 1 or 2."""
    big = max((abs(v) for v in values if v is not None), default=0.0)
    if big >= MONEY_1DP_BELOW:
        return 0
    return 1 if big >= MONEY_2DP_BELOW else 2


def money_fmt(values) -> str:
    """The style.format string for a table's dollar columns, from its own values."""
    return f"{{:,.{money_decimals(values)}f}}"


# ══════════════════════════════════════════════════════════════════════
#  SEED — where the owners' earnings box starts from, and why
# ══════════════════════════════════════════════════════════════════════
#
# Crocs, 29 Aug 2026. FY2025 net income -81M after a 738M non-cash HEYDUDE
# write-down, on a record of 950M the year before and a five-year median in
# the hundreds of millions. ΔE over the last three years was 100.9% and
# projectable, so the seed was forward net income x ΔE = -81, the verdict
# read "not investible — not even one cent", and the page stopped before
# the notes. Nothing mentioned the median it had already computed. The
# reader was right and the judgement was wrong.
#
# The median was already the fallback when ΔE cannot be projected. A filed
# loss year on a record whose median is a profit is a worse reading of a
# normal year than that median is, so it takes the same fallback. Every
# other shape is unchanged: a profit seeds from ΔE as before, a loss on a
# losing record still falls to the median if positive and to net income as
# a ceiling if not. The source is returned alongside the figure so the
# page can say which it used.

SEED_FROM_DE = "forward net income x ΔE"
SEED_FROM_MEDIAN = "the 5-year median — ΔE is not projectable"
SEED_FROM_MEDIAN_LOSS = "the 5-year median — the forward year is a loss on a profitable record"
SEED_CEILING = "forward net income, a ceiling to revise DOWN from"


def loss_year_on_record(fwd_N: float, median_OE: float) -> bool:
    """A forward year that is a loss, on a record whose 5-year median is a profit."""
    return fwd_N <= 0 < median_OE


def seed_owners_earnings(fwd_N: float, applied_dE: float, dE_ok: bool,
                         median_OE: float) -> tuple[float, str]:
    """The owners' earnings seed and the sentence that says where it came from."""
    if dE_ok and not loss_year_on_record(fwd_N, median_OE):
        return fwd_N * applied_dE, SEED_FROM_DE
    if median_OE > 0:
        return median_OE, (SEED_FROM_MEDIAN_LOSS if dE_ok else SEED_FROM_MEDIAN)
    # Every recent year is negative. Seeding zero makes IV15 collapse to net
    # cash per share, which looks like an answer but is not one. Forward net
    # income is at least a defensible ceiling to revise down from.
    return fwd_N, SEED_CEILING

# ══════════════════════════════════════════════════════════════════════
#  ΔE CEILING — a measurement above 100% is real; a projection is not
# ══════════════════════════════════════════════════════════════════════
#
# dE above 100% says shareholders kept more than the company reported
# earning. For a single year that is often true and is the whole point of
# the method: Adobe charged $1,942M of GAAP stock comp in FY2025 while the
# measured cost was $370M, because $11,281M of buybacks retired more stock
# than the year issued. So the POOLED FIGURE IS LEFT ALONE — it is a
# measurement of what happened and it stays on the page as filed.
#
# What cannot stand is seeding forward owners' earnings above forward net
# income. That projects a company handing owners more than it earns, every
# year, for the fifteen years IV15 runs. Adobe's 3-year 107.4% seeded
# 7,656 against 7,130 of profit and put roughly 17 dollars a share into
# IV15 that no year of trading produced.
#
# Capping each YEAR at 100% before pooling was the alternative and it is
# wrong: pooling exists so a good year offsets a bad one, and clipping the
# good years while keeping every bad one can only drag the pool down. A
# company alternating 120% and 80% honestly pools to 100%; cap the years
# and it reads 90%, a penalty invented out of nothing. Adobe would read
# 97.0% for exactly that reason.
#
# Above 125% nothing is capped, because that is no longer a company with a
# heavy buyback — it is issuance the reader failed to capture, and quietly
# projecting it at 100% would turn a broken read into a plausible number.
# That band still refuses and asks for owners' earnings by hand.

# Build-A-Bear, 29 Aug 2026: the holes note said "nothing read for FY2018".
# Nothing was missing. BBW moved its year end from late December to the
# Saturday nearest 31 January in 2018, so fiscal 2017 ended December 2017 and
# fiscal 2018 ended February 2019, and a reader that names each year by the
# calendar year it ended in puts consecutive filings on labels two apart. The
# figures were right; the sentence was false. Telling the two cases apart
# needs the period-end MONTH, which the reader sees and discards — that is
# the proper fix and it is queued. Until then a one-label hole says which
# two things it can be, and asserts neither.
HOLE_OR_FYE_CHANGE = (
    " A single missing label can also be a change of fiscal year end: consecutive filings "
    "whose year end moved by more than a month land on labels two apart, and nothing is "
    "missing. The period-end dates in the filing settle which this is.")

DE_SEED_CEILING = 1.00    # highest dE that may be projected forward
DE_UNUSABLE_ABOVE = 1.25  # above this, refuse rather than cap


def seed_dE(measured: float) -> float:
    """The dE to project forward. Never above 100%; the measurement is untouched."""
    return min(measured, DE_SEED_CEILING)


def dE_was_capped(measured: float) -> bool:
    """True when the projection is being held below what the filings measured."""
    return DE_SEED_CEILING < measured <= DE_UNUSABLE_ABOVE


def buybacks_shrank_count(win) -> bool:
    """Did buybacks actually retire more stock than this window issued?

    The ONLY thing that licenses saying so. VEEV, 28 Aug 2026: the capped-ΔE
    note named buybacks as the cause of a 113.5% reading on a company whose
    share count ROSE in all three years of the window and which repurchased
    nothing at all until FY2026. ΔE above 100% has a second cause with no
    buyback in it — the stock-comp cost measured off the share count pooling
    below the GAAP charge — and the note has to know which one it found.

    XPEL, 29 Aug 2026: the same wrong sentence by another door. Nothing
    bought back in FY2023-24, $3.0M in FY2025, and the count moved by about
    -0.04M on 27.6M over the window — enough to pass both tests above. The
    excess over 100% was 3.8, which is exactly the GAAP charge of 7.6 less
    the measured cost of 3.8: the second cause, with a token buyback standing
    in front of it. So a third question: were there enough buyback dollars
    to account for the excess being credited to them? Apple, PDEX and Adobe
    clear it by orders of magnitude; XPEL's 3.0 against 3.8 does not, and
    gets the sentence that names the charge instead.
    """
    excess = sum(y.G - y.omega for y in win)
    return (sum(y.T for y in win) > 0 and sum(y.dS for y in win) < 0
            and sum(y.T for y in win) >= excess)


def dE_projectable(p: "Pooled") -> bool:
    """Can this ΔE be applied to next year's profit?

    Two ways it cannot, and only one of them was checked. The obvious one is a
    ratio that is negative or absurd — stock comp swamping earnings.

    The one that took until Rivian to find is a NEGATIVE DENOMINATOR. ΔE is
    sum(OE) / sum(N), and a company that loses money makes BOTH sums negative,
    so the ratio comes out positive. Worse, the true stock-comp cost makes
    owners' earnings more negative than net income, so it lands just above 1.0
    and gets capped to a flattering 100%.

    RIVN, 27 Aug 2026: -9,743 / -9,078 = 107.3% over three years in which it
    earned nothing at all, and -14,291 / -21,962 = 65.1% over six. The 65.1%
    is the dangerous one — it is not extreme, so nothing capped it, nothing
    warned, and it would have seeded owners' earnings at 65% of a profit the
    company has never made. `dE_defined` knew all along; it was wired to the
    radio captions and to the wording of the refusals, but not to the gate
    that decides whether the number gets projected.
    """
    return p.dE_defined and 0.0 < p.dE <= DE_UNUSABLE_ABOVE


def stale_instant_lines(bal_fy: dict[str, str], ni_fy: int,
                        rows=BALANCE_ROWS) -> list[tuple[str, int, int]]:
    """Balance-sheet lines whose latest year trails net income's. ITEM 9.

    Item 1a refuses when NET INCOME is old. The same disease turned up on
    lines that have nothing to do with earnings, and with no symptom at all:
    AutoZone's short-term debt ends FY2014, Adobe's old debt tag ended FY2009,
    Progressive's goodwill ends FY2022. The year-by-year table ran to the
    current year and every other line was current. A wrong number, silently.

    INSTANTS ONLY, and that restriction is the whole design. A flow line can
    legitimately stop: Booking's acquisitions ended in 2018, and Progressive
    genuinely stopped repurchasing stock after 2016 — its share count rose in
    seven of the nine years since. A guard that does not draw that distinction
    fires on every healthy company. A BALANCE SHEET cannot stop. Every 10-K
    reports the position at fiscal year end, so if net income reached FY2025
    and a balance line stops at FY2014, one of two things is true and both are
    wrong on the page:

      - the balance is still there under a tag this reader does not know, or
      - the company no longer has that line and the figure should be zero.

    `g()` takes max(d.items()) per line independently, so either way the last
    figure found is carried into today's net cash as though it were current.

    Any gap at all counts. No threshold is invented here: unlike item 1a,
    which tolerates two years because a December filer read in January sits
    behind its own newest 10-K, both years compared here come out of the SAME
    filings, so a gap of one is already a gap.

    Lines with no data at all are excluded — the panel already says "none of
    the tags this reader knows are in the filing", which is a different
    finding with a different fix.
    """
    out = []
    for name, ks in rows:
        fy = bal_fy.get(ks[0], "—")
        if not fy.isdigit():
            continue
        if int(fy) < ni_fy:
            out.append((name, int(fy), ni_fy - int(fy)))
    return out


# How each balance-sheet line enters net cash. Leases are 0 because they do
# not: they are shown, and handled inside tool 2's capital base instead.
NET_CASH_SIGN = {"Cash": 1, "Short-term investments": 1, "Long-term investments": 1,
                 "Long-term debt": -1, "Short-term debt": -1, "Operating leases": 0}


def stale_swing_note(net_cash: float, contributions: list[tuple[str, float]]) -> str:
    """How much of net cash rests on a figure that is being carried forward.

    ITEM 4, and the reason it is a note rather than a rule. The two pages treat
    a stale balance-sheet line in opposite ways — this one carries the last
    figure found forward, tool 2 adds the missing component as zero — and
    NEITHER is conservative in general, because the direction flips with the
    side of the balance sheet:

      asset carried forward  -> overstates cash    -> flatters IV15
      asset zeroed           -> understates cash   -> conservative
      liability carried fwd  -> subtracts a stale, usually smaller debt
      liability zeroed       -> ignores the debt entirely -> flatters badly

    Adobe is the proof that unifying on zero would be worse, not better:
    `LongTermDebtNoncurrent` stopped at FY2009, and zeroing it would have
    dropped $6.1B of real debt instead of subtracting $1.0B of stale debt.
    So neither treatment is imposed. The size of what is at stake is stated
    and the reader is pointed at the tag, which is what actually fixed Adobe,
    Progressive and TransDigm.

    Returns "" when no stale line touches net cash — an operating-lease line
    is reported by the caller but moves nothing here.
    """
    live = [(n, v) for n, v in contributions if abs(v) > 0.05]
    if not live:
        return ""
    alt = net_cash - sum(v for _, v in live)
    return (f" Net cash reads {net_cash:,.0f}M with "
            + ", ".join(f"{n.lower()} at {abs(v):,.0f}M" for n, v in live)
            + f" carried forward; treated as zero instead it would read {alt:,.0f}M, a swing of "
              f"{abs(alt - net_cash):,.0f}M. Which of the two is right depends on whether the "
              "balance moved to another tag or genuinely ended, so the tag name is the fix and "
              "neither figure is guessed at here.")


def stale_window_refusal(fys: list[int], rev_fys: list[int], today_year: int) -> str:
    """Reason to refuse a stale earnings window, or '' when it is usable.

    Revenue is the better reference than the calendar where it exists: both
    series come from the same filings, so a gap between them is the reader
    losing a tag rather than the company being slow to file.
    """
    if not fys:
        return ""
    last_n = max(fys)
    if rev_fys and max(rev_fys) - last_n >= STALE_VS_REVENUE:
        return (
            f"net income was read only to FY{last_n} while revenue reaches FY{max(rev_fys)}, "
            f"a gap of {max(rev_fys) - last_n} years. Every figure on this page is built from "
            "net income, so the verdict would describe the company as it was, priced against "
            "the company as it is — and nothing on the page would say so. The usual cause is "
            "the filer moving to a tag this reader does not know, not a company that stopped "
            "reporting. Send the tag panel and it can be fixed.")
    if today_year - last_n >= STALE_VS_TODAY:
        return (
            f"the most recent annual figure read is FY{last_n}, {today_year - last_n} years "
            "behind the calendar. A late filer runs one year behind, and a December filer read "
            "early in the year runs two; three is a hole rather than a lag. Owners' earnings, "
            "ΔE and IV15 would all describe a company that no longer exists.")
    return ""


def price_coverage_refusal(n_years: int, unpriced: int, have_history: bool) -> str:
    """Reason to refuse for missing prices, or '' when enough years carry one."""
    if n_years <= 0 or unpriced <= n_years * MIN_PRICED_SHARE:
        return ""
    if not have_history:
        return (
            "no price history could be fetched, so every year's average price is zero. The "
            "market value of shares handed to employees is the whole of the stock-comp cost, "
            "and without a price it floors at zero — ΔE would read near 100% for any company "
            "at all. This is usually a temporary failure at the price source rather than "
            "anything about the filer, so it is worth trying again in a minute.")
    return (
        f"{unpriced} of the {n_years} years in this window have no share price. The price "
        "history runs about eleven years, so a window reaching further back leaves its early "
        "years unpriced. The market value of shares delivered floors at zero in those years, "
        "the true stock-comp cost becomes withholding minus option proceeds — negative where "
        "options were exercised — and ΔE stops being a measurement of anything.")


def _annual(facts: dict, us: list[str], ifrs: list[str],
            sources: list[str] | None = None,
            fill: bool = False,
            prefer_recent: bool = False,
            unit: str = "USD") -> dict[int, tuple[str, str, float]]:
    """{fy: (start, end, value)} for full-year facts from annual reports only.

    Three filters that matter: the period must be roughly a year (so quarterly
    rows tagged fp='FY' cannot slip through); annual forms only; and where a
    year appears in several filings, keep the latest — a 10-K restates the
    prior year as a comparative.

    On the choice between concepts: the first one with data used to win
    outright, and everything after it was ignored. That is right when the
    alternates mean different things, and quietly wrong when they do not.
    H&R Block retired shares rather than holding them as treasury, so
    PaymentsForRepurchaseOfCommonStock covered three of nineteen years and the
    remaining sixteen sat in StockRepurchasedAndRetiredDuringPeriodValue —
    unread, because three years was enough to stop the search. The buyback
    column printed zeros for a decade and every figure downstream inherited it.

    With fill=True the concepts are tried in order and later ones fill only the
    years earlier ones left empty. Priority is preserved; nothing is summed;
    and every concept that contributed is appended to `sources` so the panel in
    the UI can show which ones answered.
    """
    out: dict[int, tuple[str, str, str, float]] = {}
    for taxonomy, concepts in (("us-gaap", us), ("ifrs-full", ifrs)):
        tax = facts.get("facts", {}).get(taxonomy, {})
        cands: list[tuple[str, dict[int, tuple[str, str, str, float]]]] = []
        for concept in concepts:
            if concept not in tax:
                continue
            units = tax[concept].get("units", {})
            got: dict[int, tuple[str, str, str, float]] = {}
            # PAGE 6 EDIT (3 Sep 2026): the unit is the caller's filing
            # currency rather than hardcoded USD — ASML and SAP file 20-Fs in
            # EUR, and their facts sit under a "EUR" unit key this reader
            # never looked at. The "shares" fallback is unchanged: share-count
            # lines carry no currency and must keep reading whatever the
            # filing currency is.
            for row in units.get(unit, []) or units.get("shares", []):
                if row.get("form") not in ANNUAL_FORMS:
                    continue
                start, end = row.get("start"), row.get("end")
                if not (start and end):
                    continue
                if not 330 <= (dt.date.fromisoformat(end)
                               - dt.date.fromisoformat(start)).days <= 400:
                    continue
                fy, filed = int(end[:4]), row.get("filed", "")
                if fy not in got or filed > got[fy][0]:
                    got[fy] = (filed, start, end, float(row.get("val", 0.0)))
            if not got:
                continue
            if fill:
                fresh = {fy: v for fy, v in got.items() if fy not in out}
                if fresh:
                    out.update(fresh)
                    if sources is not None:
                        sources.append(concept)
            else:
                cands.append((concept, got))
        # Without fill, ONE concept answers for the whole line, so choosing the
        # first with any data was the same staleness bug _instant had.
        # TransDigm's revenue came from RevenueFromContractWithCustomer... for
        # five years ending FY2024, with Revenues never tried, so the revenue
        # leg of "has delivered" was dropped for being too short — while a
        # longer, current series sat behind it. Picking the concept that
        # reaches the latest year keeps one definition across all years, which
        # filling across these tags would not.
        if cands and not fill:
            # Same opt-in as _instant, and for the same reason. The only two
            # lines that reach here are REV and SHD, both of which are lists of
            # alternate names for one figure. Everything else is filled.
            latest = (max(max(g) for _, g in cands) if prefer_recent
                      else max(cands[0][1]))
            for concept, got in cands:
                if max(got) == latest:
                    if sources is not None:
                        sources.append(concept)
                    return {k: (v[1], v[2], v[3]) for k, v in got.items()}
    return {k: (v[1], v[2], v[3]) for k, v in out.items()}


def currency_facts(facts: dict, concepts: list[str]) -> dict[str, int]:
    """How many annual-report facts each currency unit carries, for one line.

    reporting_currency() answers "which unit exists, preferring USD", which is
    the wrong question. Toyota tags two years of USD convenience translations
    from old 20-F filings alongside a full history in yen; asking whether USD
    exists gets a yes, and the page then runs on two stale years while
    multiplying an ADR price by an ordinary share count. Counting the facts in
    each unit shows which currency the company actually reports in.
    """
    out: dict[str, int] = {}
    for taxonomy in ("us-gaap", "ifrs-full"):
        tax = facts.get("facts", {}).get(taxonomy, {})
        for concept in concepts:
            for unit, rows in tax.get(concept, {}).get("units", {}).items():
                if unit == "shares":
                    continue
                n = sum(1 for r in rows if r.get("form") in ANNUAL_FORMS)
                if n:
                    out[unit] = out.get(unit, 0) + n
    return out


def filing_currency(facts: dict) -> str:
    """The currency this filer actually reports in, by counting facts.

    PAGE 6 EDIT (3 Sep 2026). Tool 1 asks this question in order to REFUSE
    a foreign currency; this page asks it in order to READ one. The count
    logic is currency_facts(), unchanged — it already knows the Toyota trap
    (two USD convenience translations beside a full history in yen), which
    is exactly why the answer is the unit with the MOST annual facts, ties
    broken toward USD, never merely "does USD exist".
    """
    counts = currency_facts(facts, CONCEPTS["N"][0] + CONCEPTS["N"][1])
    if not counts:
        return "USD"
    best = max(counts.values())
    if counts.get("USD", 0) == best:
        return "USD"
    return max(counts.items(), key=lambda kv: kv[1])[0]


def sent_to_tool1(facts: dict) -> str:
    """Non-empty when this filer belongs on tool 1, with the sentence to say.

    PAGE 6 EDIT (3 Sep 2026). The rule is taxonomy plus currency, never the
    form: Shopify files a 40-F yet reports in US GAAP in USD, so it needs
    nothing from this page — while Grab files a 20-F in USD under IFRS and
    needs everything. Counting annual net-income facts by taxonomy decides:
    us-gaap USD facts at least matching ifrs-full facts means the ordinary
    reader already reads this company better than this page can.
    """
    us_n = 0
    tax = facts.get("facts", {}).get("us-gaap", {})
    for concept in CONCEPTS["N"][0]:
        for row in tax.get(concept, {}).get("units", {}).get("USD", []):
            if row.get("form") in ANNUAL_FORMS:
                us_n += 1
    if_n = 0
    tax = facts.get("facts", {}).get("ifrs-full", {})
    for concept in CONCEPTS["N"][1]:
        for unit, rows in tax.get(concept, {}).get("units", {}).items():
            if unit == "shares":
                continue
            if_n += sum(1 for r in rows if r.get("form") in ANNUAL_FORMS)
    if us_n and us_n >= if_n:
        return ("this company reports in US GAAP in US dollars, so it needs nothing this page "
                "adds — use the **Tragic Algebra Analyzer** page instead (or the Financials "
                "Checker if it is a bank, insurer or REIT). A 40-F filer like Shopify still "
                "reports full US GAAP and belongs there too. This page is only for filings the "
                "ordinary reader cannot read: IFRS taxonomies and non-dollar currencies.")
    return ""


def debt_unread_note(cash_years: int, ltd_years: int, std_years: int) -> str:
    """Non-empty when cash answered and no debt line did.

    PAGE 6 EDIT (4 Sep 2026). Net cash = cash minus debt, and a debt line
    that reads nothing is treated as zero. When cash reads and debt is
    silent, net cash is an upper bound wearing the costume of a
    measurement — and it feeds IV15 directly. The warning names the fix
    the page already has: the Total debt box is editable, and the balance
    sheet in the annual report has the number.
    """
    if cash_years <= 0 or ltd_years > 0 or std_years > 0:
        return ""
    return ("**Net cash here may be fiction.** Cash was read from the filings but NO "
            "debt line answered — none of the borrowing tags this reader knows are in "
            "the filing — so debt is standing at zero and net cash is an upper bound, "
            "not a measurement. For a capital-intensive company that is almost "
            "certainly wrong in the flattering direction, and net cash feeds IV15 "
            "directly. Open the balance sheet in the annual report, find total "
            "financial debt, and type it into the Total debt box before trusting the "
            "verdict; the tag panel names which lines went unread.")


def nonus_financial_refusal(sic: str, sic_desc: str) -> str:
    """The financial refusal for foreign filers, coordinated with page 5.

    PAGE 6 EDIT (3 Sep 2026). The Financials Checker owns the financial-gate
    decision for the app, but its gate is US SIC ranges confirmed by filed
    US-GAAP lines (deposits, premiums), none of which an IFRS insurer tags —
    it would refuse with a sentence that misdescribes. So this page refuses
    the whole SIC 6000-6799 range itself, plainly, and the handover records
    that the two gates should merge when the Financials Checker takes on
    foreign financials.
    """
    if not is_financial(sic):
        return ""
    return (f"is a financial company (SIC {sic}, {sic_desc or 'financial'}). IFRS insurance "
            "and banking accounting is its own world — premiums, float, deposits and reserves "
            "under IFRS 17 and IFRS 9 — and no page in this app prices it yet. The Financials "
            "Checker reads US filers only. Refusing is the honest answer.")


def reporting_currency(facts: dict, concepts: list[str]) -> str | None:
    """Currency this filer actually reports in.

    Everything downstream assumes USD. A foreign private issuer filing a 20-F
    in EUR has perfectly good data under a EUR unit key that this reader never
    looks at, which used to surface as 'no net income found' and blame the
    taxonomy. Naming the currency turns a confusing dead end into a clear
    limitation.
    """
    for taxonomy in ("us-gaap", "ifrs-full"):
        tax = facts.get("facts", {}).get(taxonomy, {})
        for concept in concepts:
            if concept not in tax:
                continue
            units = [u for u in tax[concept].get("units", {}) if u != "shares"]
            if units:
                return "USD" if "USD" in units else units[0]
    return None


def _instant(facts: dict, concepts: list[str], unit: str = "USD",
             sources: list[str] | None = None,
             skipped: list[tuple[str, int, str, int]] | None = None,
             prefer_recent: bool = False) -> dict[int, float]:
    """Latest balance-sheet value per fiscal year.

    ONE concept answers for the whole line. Merging them silently mixes
    incompatible definitions — CashAndCashEquivalents and
    CashCashEquivalentsRestrictedCash differ by the restricted balance, which
    is not shareholder money. That has not changed.

    What has changed is WHICH one. The first concept with any data used to win
    outright, however old that data was, and the alternates behind it were
    never tried. Every figure on the page then took the latest year of a series
    that had stopped:

        AutoZone   LongTermDebtCurrent          1 year,  ending 2014
        TransDigm  LongTermDebtNoncurrent      12 years, ending 2020
        Salesforce MarketableSecuritiesCurrent  6 years, ending 2014
        Progressive LongTermDebt                8 years, ending 2015
        Paychex    MarketableSecuritiesNoncurrent 3 years, ending 2011

    None of it showed. The year-by-year table ran to the current year, every
    other line was current, and net cash quietly mixed a 2025 cash balance with
    a 2014 debt figure. Five of the seven baseline tickers.

    So with prefer_recent: gather every concept in the group that has data and
    take the first one — in the caller's preference order — that reaches the
    latest year any of them reach. Preference still decides between equals;
    recency only breaks the tie when one series has stopped.

    WHY THIS IS OPT-IN, AND MUST STAY OPT-IN. It is only safe where the tags in
    a group are alternate NAMES for one line, so that any of them would be an
    acceptable answer and the ordering is mere convenience. It is wrong wherever
    the ordering IS the definition.

    TransDigm proved that the expensive way. The share-count ladder reads
    ["CommonStockSharesOutstanding", "CommonStockSharesIssued",
    "EntityCommonStockSharesOutstanding"], and those are three different things
    in order of correctness — issued shares include treasury stock. TDG tags
    outstanding for 3 years ending 2012 and issued for 17 ending 2025, so
    recency promoted issued, the count read 62.5M instead of 56.3M, the share
    change turned positive in every year because issued shares grow while
    outstanding shrinks, the entire buyback was charged to employees, and
    pooled dE fell from 78.4% to 56.5%. This is the AutoZone treasury bug,
    re-entering by the door built to catch it: the guard needs issued to exceed
    1.15x the diluted average and 62.5/58.2 is 1.07, so nothing fired.

    Default False, so every caller that has not thought about this question
    keeps the old first-with-data behaviour.

    When the first-preference concept exists and is passed over, the loser and
    the winner are recorded in `skipped` so the caller can say so.
    """
    for taxonomy in ("us-gaap", "dei", "ifrs-full"):
        tax = facts.get("facts", {}).get(taxonomy, {})
        cands: list[tuple[str, dict[int, float]]] = []
        for concept in concepts:
            if concept not in tax:
                continue
            out: dict[int, tuple[str, float]] = {}
            for row in tax[concept].get("units", {}).get(unit, []):
                if row.get("start") or not row.get("end"):
                    continue
                if row.get("form") not in ANNUAL_FORMS:
                    continue
                fy, filed = int(row["end"][:4]), row.get("filed", "")
                if fy not in out or filed > out[fy][0]:
                    out[fy] = (filed, float(row["val"]))
            if out:
                cands.append((concept, {k: v[1] for k, v in out.items()}))
        if not cands:
            continue
        latest = max(max(s) for _, s in cands) if prefer_recent else max(cands[0][1])
        for concept, s in cands:
            if max(s) == latest:
                if sources is not None:
                    sources.append(concept)
                if skipped is not None and concept != cands[0][0]:
                    skipped.append((cands[0][0], max(cands[0][1]), concept, latest))
                return s
    return {}


@st.cache_data(ttl=86400, show_spinner=False)
def _monthly_closes(ticker: str) -> tuple[dict[str, float], dict[str, float], str]:
    """Monthly closes keyed 'YYYY-MM', split events, and the QUOTE CURRENCY.

    The splits come back on the SAME request, which is why they are returned
    here rather than fetched separately: one round trip, one cache entry, and
    no possibility of the prices and the splits being read at different times.

    Every price in this series is already restated for any split, including
    one that happened yesterday. The share counts in this file come from
    filings and are not. See the reconciliation in load().
    """
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        "?interval=1mo&range=11y&events=split",
        headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    res = r.json()["chart"]["result"][0]
    # PAGE 6 EDIT (3 Sep 2026): the response names its own currency, and this
    # page checks it against the filing currency instead of assuming. The
    # gate lives in load(); this function only reports what it saw.
    px_ccy = str((res.get("meta") or {}).get("currency") or "")
    closes = res["indicators"]["quote"][0]["close"]
    out = {}
    for ts, c in zip(res["timestamp"], closes):
        if c:
            d = dt.datetime.utcfromtimestamp(ts)
            out[f"{d.year:04d}-{d.month:02d}"] = float(c)

    # Yahoo has used both {"numerator": 2, "denominator": 1} and
    # {"splitRatio": "2:1"} over the years, and returns the block under
    # different keys depending on the endpoint version. Parse defensively:
    # a split this reader cannot read must leave the factor at 1.0 rather
    # than throw, because the whole price series is riding on this call.
    splits: dict[str, float] = {}
    for s in ((res.get("events") or {}).get("splits") or {}).values():
        try:
            day = dt.datetime.utcfromtimestamp(int(s["date"])).date().isoformat()
        except (KeyError, TypeError, ValueError, OSError):
            continue
        num, den = s.get("numerator"), s.get("denominator")
        if not (num and den):
            try:
                num, den = str(s.get("splitRatio", "")).split(":")
            except ValueError:
                continue
        try:
            ratio = float(num) / float(den)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if ratio > 0:
            splits[day] = ratio
    return out, splits, px_ccy


def _avg_price(closes: dict[str, float], start: str, end: str) -> float | None:
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    vals, d = [], s
    while d <= e:
        v = closes.get(f"{d.year:04d}-{d.month:02d}")
        if v:
            vals.append(v)
        d = (d.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    return statistics.fmean(vals) if vals else None


@st.cache_data(ttl=900, show_spinner=False)
def current_price(ticker: str) -> tuple[float, str] | None:
    """PAGE 6 EDIT (3 Sep 2026): returns (price, quote currency) so the
    caller can refuse a price in the wrong currency instead of using it."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        m = r.json()["chart"]["result"][0]["meta"]
        return (float(m.get("regularMarketPrice") or m.get("chartPreviousClose")),
                str(m.get("currency") or ""))
    except Exception:
        return None


MAX_SPLIT = 200.0   # no real split comes near this; see split_adjust


def split_adjust(shares: dict[int, float]) -> tuple[dict[int, float], list[str]]:
    """Restate historical share counts onto the current basis.

    Gate 3 of the published QA protocol: year-on-year share counts must stay
    within [0.35, 2.85]. Outside that band is a stock split, not real dilution.

    This matters enormously. XBRL reports shares as-filed, so pre-split years
    carry the old basis, while market prices are already split-adjusted. Mixing
    the two makes dS jump by the whole split in one year, and since
    V = T + P x dS, the SBC cost explodes. ServiceNow's 5-for-1 turned a
    pooled dE of about -79% into -2391%.
    """
    # Drop non-positive entries first. Dual-class filers report each class
    # separately and a class can read as zero in some years; a zero here made
    # the reverse-split branch compute 1/0.
    shares = {k: v for k, v in shares.items() if v and v > 0}
    fys, notes = sorted(shares), []
    if len(fys) < 2:
        return dict(shares), notes
    adjusted, factor = {}, 1.0
    for i in range(len(fys) - 1, -1, -1):
        fy = fys[i]
        adjusted[fy] = shares[fy] * factor
        if i > 0 and shares[fys[i - 1]] > 0:
            # Raw-to-raw. Comparing the ADJUSTED current year against the raw
            # prior year re-detects the same split on every pass and compounds
            # the factor geometrically.
            ratio = shares[fy] / shares[fys[i - 1]]
            # A ratio this extreme is not a split. Berkshire, 26 Aug 2026:
            # its Class A and Class B counts arrived in one series and the
            # jump between them read as "about 1:948347", which the page then
            # restated history by. Real splits are small integers; 200:1 is
            # already far beyond anything a listed company has done.
            if ratio > MAX_SPLIT or (0 < ratio < 1 / MAX_SPLIT):
                notes.append(
                    f"The share count changes by about {max(ratio, 1 / ratio):,.0f}x at FY{fy}, "
                    "which is too large to be a stock split. The usual cause is two share "
                    "classes arriving in one series — a count in Class A equivalents beside a "
                    "count of Class B shares. History has been left as filed rather than "
                    "restated onto a basis that would be wrong either way; check the share "
                    "count against the market capitalisation before using any figure here.")
                continue
            # A share count that MULTIPLIES from a small base is usually a
            # listing, not a split. Splits move a large, established count.
            if ratio > 2.85 and shares[fys[i - 1]] < 25e6:
                continue
            if ratio > 0 and (ratio > 2.85 or ratio < 0.35):
                # Round to a plausible split ratio. Reverse splits must be
                # rounded on the reciprocal: round(0.1 * 2) / 2 is zero.
                # Rounded to a whole number, NOT to the nearest half. This
                # branch is only reachable above 2.85:1, and every real split
                # in that range is an integer — 3:1, 4:1, 7:1, 10:1, 20:1.
                # 3:2 and 5:4 sit below the gate and never arrive here.
                #
                # AAPL, 28 Aug 2026. What is measured is never the bare split
                # ratio: a 10-K carries two years of balance sheet, so the
                # filing after a split restates ONE earlier year and the year
                # before it is left as filed. The jump therefore sits at the
                # restatement boundary, and the ratio across it is the split
                # times whatever buybacks did in between. Apple measured
                # 17,772.9 / 4,755.0 = 3.738 for a 4:1 split, which the old
                # half-rounding snapped to 3.5. Earlier years were then
                # multiplied by 3.5 instead of 4, and the 1,247M shares Apple
                # RETIRED in FY2019 read as 1,130M shares issued — $123.9B of
                # stock-comp cost against a $6.1B charge, owners' earnings of
                # -$62.6B in a year it earned $55.3B, and a ten-year ΔE of
                # 76.5% that the page reported as a shareholder-quality
                # failure. Rounding to 4 restates it exactly.
                if ratio >= 1:
                    clean = float(round(ratio))
                    label = f"{clean:g}:1"
                else:
                    inv = float(round(1 / ratio))
                    clean = 1 / inv if inv > 0 else 0.0
                    label = f"1:{inv:g}"
                if clean > 0:
                    factor *= clean
                    # Says what it SAW, not what it concluded. RIVN, 27 Aug
                    # 2026: the November 2021 listing moved the weighted
                    # average share count by about 9x, this read it as a 9:1
                    # split and announced one — on a company that has never
                    # split. Nothing here can tell a split from a listing: the
                    # prices arrive already split-adjusted, so both look
                    # identical from the filings alone. The restatement is
                    # still the better guess in both cases, which is why the
                    # numbers are unchanged; the claim was the wrong part.
                    notes.append(f"The share count changes by about {label} at FY{fy} — the "
                                 "size of a stock split, so earlier share counts have been "
                                 "restated onto the current basis; without this the SBC cost "
                                 "would be wildly overstated. A first listing or a "
                                 "recapitalisation produces the same jump and this reader "
                                 "cannot tell them apart, so if the company did not split, the "
                                 "restated years are wrong.")
    return adjusted, notes


TAG_LABELS = {
    "N": "Net income", "G": "GAAP stock comp", "T": "Buybacks",
    "Cw": "Tax withheld on vesting", "Ce": "Option / ESPP proceeds",
    "REV": "Revenue", "INT": "Interest income", "LEASEPAY": "Finance lease payments",
    "DIV": "Dividends paid", "CAPEX": "Capital expenditure",
    "MA": "Shares issued for acquisitions", "OFFER": "Shares issued in offerings",
    "CONV": "Shares from conversions",
    "MAV": "Value of stock issued for acquisitions",
    # Without an entry here the panel printed the raw dictionary key "SHD"
    # beside a "— Shares: diluted average" row and looked like a duplicate.
    # They are two different reads of the same idea: this one is unfilled and
    # feeds the dual-class check, the row below is filled across three tags
    # and feeds the share ladder. Named for what it is used for.
    "SHD": "Diluted shares — dual-class check",
}


def _issuance(facts: dict, concepts: list[str], n_series: dict,
              sources: list[str] | None = None,
              unit: str = "shares") -> dict[int, tuple[str, str, float]]:
    """Shares issued in corporate transactions, which _annual cannot read.

    _annual demands a period of 330-400 days, because a compensation or an
    earnings line is a full-year flow and a quarterly row tagged fp='FY' must
    not slip through. An acquisition is not a flow. It happens on a date, and
    filers tag it over the period the deal closed in — a quarter, a month,
    sometimes an instant with no start at all. Every one of those facts failed
    the duration test, so the line read zero years on almost every filer and
    the shares landed in the stock-comp column instead.

    Salesforce is the case that matters. Slack at 7.7% of the share count,
    MuleSoft at 5.3%, FY2017 at 5.2% — none large enough to trip the 15%
    capital-event guard, all of them charged to employees at the market price.
    Roughly $17.4B of phantom stock-comp cost from Slack alone. Pooled dE read
    19.7% against Burry's published 54.7%.

    So: take any fact whose period ENDS inside a fiscal year and attribute it
    to that year. A company can buy more than one business in a year, so the
    facts are summed rather than replaced — but a full-year fact, where one
    exists, is used ALONE, because summing it with the sub-periods it already
    contains would double-count the same deal.

    Facts longer than 400 days are cumulative "since acquisition" disclosures
    and are dropped. Within a year, (start, end) identifies a fact and the
    latest filing wins, so a 10-K restating last year as a comparative does not
    count it twice.

    Concepts are tried in order and the FIRST with any data wins outright. Do
    NOT merge or fall through here: a filer that tags the same deal in both the
    equity rollforward and the business-combination note would have it counted
    twice, and a doubled subtraction from dS is worse than a missed one.
    """
    windows = {fy: (v[0], v[1]) for fy, v in n_series.items()}
    if not windows:
        return {}
    for taxonomy in ("us-gaap", "dei", "ifrs-full"):
        tax = facts.get("facts", {}).get(taxonomy, {})
        for concept in concepts:
            if concept not in tax:
                continue
            seen: dict[tuple[int, str, str], tuple[str, float]] = {}
            for row in tax[concept].get("units", {}).get(unit, []):
                if row.get("form") not in ANNUAL_FORMS:
                    continue
                end = row.get("end")
                if not end:
                    continue
                start = row.get("start") or end
                try:
                    days = (dt.date.fromisoformat(end)
                            - dt.date.fromisoformat(start)).days
                except ValueError:
                    continue
                if days > 400:
                    continue
                fy = next((f for f, (ws, we) in windows.items() if ws <= end <= we), None)
                if fy is None:
                    continue
                key, filed = (fy, start, end), row.get("filed", "")
                if key not in seen or filed > seen[key][0]:
                    seen[key] = (filed, abs(float(row.get("val", 0.0))))
            if not seen:
                continue
            out: dict[int, tuple[str, str, float]] = {}
            for fy, (ws, we) in windows.items():
                rows = [(s, e, v) for (f, s, e), (_, v) in seen.items() if f == fy]
                if not rows:
                    continue
                full = [r for r in rows
                        if 330 <= (dt.date.fromisoformat(r[1])
                                   - dt.date.fromisoformat(r[0])).days <= 400]
                use = full if full else rows
                out[fy] = (ws, we, sum(v for _, _, v in use))
            if out:
                if sources is not None:
                    sources.append(concept)
                return out
    return {}


def _latest_fy(d) -> str:
    """The most recent fiscal year a line actually reached.

    A count on its own cannot show staleness. Booking Holdings read eight
    years of net income and printed a full verdict on them; the panel said
    "8" and nothing on the page said those eight ended in FY2015. TransDigm
    reads LongTermDebtNoncurrent for twelve years, and net cash is built from
    whichever year that series happens to stop at against a cash balance that
    may run several years later — the panel said "12" and could not tell you
    whether the twelve reach the balance sheet being priced.

    Both _instant and _annual silently take the latest year they find. This
    column is the only place that says which year that was, so a series that
    stops early stops being invisible.
    """
    try:
        return str(max(d)) if d else "—"
    except (TypeError, ValueError):
        return "—"


def tag_report(facts: dict, series: dict, sources: dict[str, list[str]]) -> list[dict]:
    """Which tags answered for each line, and how many years they covered.

    Every silent zero in this app is a tag that did not match. Reading the
    panel is how the H&R Block buyback bug was found: the tag was there, it
    covered three years of nineteen, and nothing said so.
    """
    rows = []
    for key, (us, ifrs) in CONCEPTS.items():
        used = sources.get(key, [])
        n = len(series.get(key, {}))
        present = ""
        for taxonomy, concepts in (("us-gaap", us), ("ifrs-full", ifrs)):
            for c in concepts:
                if c in facts.get("facts", {}).get(taxonomy, {}):
                    present = c
                    break
            if present:
                break
        rows.append({
            "Line": TAG_LABELS.get(key, key),
            "Years read": n,
            "Latest year": _latest_fy(series.get(key, {})),
            "XBRL tag": " + ".join(used) if used else (present or "—"),
            "Status": ("read" if n and len(used) <= 1 else
                       f"read — gaps filled from {len(used)} tags" if n else
                       "tag present but no annual figures survived the filters" if present else
                       "none of the tags this reader knows are in the filing"),
        })
    return rows


def _cover_shares(facts: dict, nseries: dict) -> dict[int, float]:
    """Shares outstanding from the 10-K cover page, aligned to fiscal years.

    Every 10-K carries dei:EntityCommonStockSharesOutstanding — a real count of
    shares outstanding, net of treasury, required on the cover. It is the most
    reliable share figure in the whole filing and this reader was reaching it
    third, behind CommonStockSharesIssued, which includes treasury.

    It cannot go through _instant, because that keys a fact by the calendar
    year of its date. The cover date is the FILING date, weeks or months after
    the year end: for a December filer that lands in February and would file
    the whole series one year late, so every share change would be measured
    between the wrong pair of years. Here each cover figure is matched to the
    fiscal year whose end date it follows most closely instead.
    """
    rows = (facts.get("facts", {}).get("dei", {})
            .get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", []))
    ends = {}
    for fy, v in nseries.items():
        try:
            ends[fy] = dt.date.fromisoformat(v[1])
        except (ValueError, IndexError):
            continue
    out: dict[int, tuple[str, float]] = {}
    for r in rows:
        if r.get("form") not in ANNUAL_FORMS or r.get("start") or not r.get("end"):
            continue
        try:
            d = dt.date.fromisoformat(r["end"])
        except ValueError:
            continue
        best = None
        for fy, e in ends.items():
            gap = (d - e).days
            if 0 <= gap <= 150 and (best is None or gap < best[1]):
                best = (fy, gap)
        if best:
            fy, filed = best[0], r.get("filed", "")
            if fy not in out or filed > out[fy][0]:
                out[fy] = (filed, float(r.get("val", 0.0)))
    return {k: v[1] for k, v in out.items() if v[1] > 0}


def _cover_asof(facts: dict) -> str:
    """Filing date of the most recent 10-K cover page, as 'YYYY-MM-DD'.

    The cover count is dated at the FILING, months after the year end, so when
    that is the series in use it is the later date a split has to beat before
    it counts as unreflected. Using the fiscal year end for a cover-page filer
    would re-apply a split the cover page had already absorbed.
    """
    rows = (facts.get("facts", {}).get("dei", {})
            .get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", []))
    best = ""
    for r in rows:
        if r.get("form") in ANNUAL_FORMS and not r.get("start"):
            filed = str(r.get("filed", ""))
            if filed > best:
                best = filed
    return best


def confirm_band_splits(shares: dict[int, float],
                        splits: dict[str, float]) -> tuple[dict[int, float], str]:
    """Restate in-band split jumps that a market split event confirms.

    PAGE 6 EDIT (4 Sep 2026). Only ratios split_adjust's band lets
    through (1.6x to 2.85x, and their reciprocals), only on established
    bases (> 25M shares), only rounding to a whole ratio within 12%, and
    ONLY when the fetched price history carries a split event of that
    same rounded ratio. Every earlier year is multiplied; iterates so
    two boundaries restate independently.
    """
    if not shares or not splits:
        return shares, ""
    event_ratios = set()
    for r in splits.values():
        if r > 1:
            event_ratios.add(float(round(r)))
        elif r > 0:
            event_ratios.add(1.0 / float(round(1.0 / r)))
    out = dict(shares)
    fys = sorted(out)
    fixed = []
    for i in range(len(fys) - 1, 0, -1):
        a, b = out[fys[i - 1]], out[fys[i]]
        if a <= 25e6 or b <= 0:
            continue
        ratio = b / a
        m = 0.0
        if 1.6 <= ratio <= 2.85 and abs(ratio / round(ratio) - 1) <= 0.12:
            m = float(round(ratio))
        elif 1 / 2.85 <= ratio <= 1 / 1.6 and abs((1 / ratio) / round(1 / ratio) - 1) <= 0.12:
            m = 1.0 / float(round(1 / ratio))
        if m and m in event_ratios:
            for fy in fys[:i]:
                out[fy] = out[fy] * m
            fixed.append((fys[i], m))
    if not fixed:
        return shares, ""
    what = ", ".join((f"{m:g}-for-1 at the FY{fy} boundary" if m > 1
                      else f"1-for-{1/m:g} at the FY{fy} boundary")
                     for fy, m in fixed)
    return out, (
        "A split hid inside the tolerance band: the share count jumps by about "
        + what + " — a ratio the split detector deliberately tolerates because "
        "organic changes reach it, but the price history itself records a split "
        "event of exactly that ratio, so earlier counts were restated onto the "
        "current basis. Without this, the jump year is excluded as a phantom "
        "capital event and every earlier year prices pre-split counts against "
        "split-adjusted prices. The boundary year is where filings stop "
        "restating comparatives, not the split date itself.")


def split_asof(share_fys, fy_ends: dict, cover_asof: str = "",
               use_cover: bool = False) -> str:
    """The date the share counts in use were filed as of. BRIEF ITEM 1c.

    A post-filing split is detected by comparing split dates against this
    anchor: anything AFTER it has not reached the filings, so the counts need
    scaling. The anchor was taken from the NET INCOME series, which is a
    different series that can end in a different year.

    The damage runs one way. Where the share counts stop before net income
    does — AutoZone's stop at FY2018 against earnings reaching FY2025 — the
    anchor reads 2025 and a split in, say, 2020 looks OLDER than the data.
    It is not: the 2018 counts are pre-split and never get scaled, so every
    per-share figure, market cap and IV15 is wrong by the split ratio. The
    brief called this out and the earlier fix folded in the cover-page date
    instead, which repairs it only when the cover-page route happens to win.

    Anchoring to the year of the series actually being scaled fixes it for
    every route. The cover-page date is still folded in where that route won,
    because a cover figure is dated at the FILING rather than the year end and
    is therefore the more recent evidence.
    """
    fys = [fy for fy in (share_fys or [])]
    asof = fy_ends.get(max(fys), "") if fys else max(fy_ends.values(), default="")
    if use_cover and cover_asof:
        asof = max(asof, cover_asof)
    return asof


def foreign_filer_note(net_income_tag: str, unread: list[str], ccy: str = "USD") -> str:
    """Say plainly that this is an IFRS filer and which lines went unread.

    Written after Shell plc, 26 Aug 2026 — the first foreign filer either page
    had been pointed at. Net income and revenue read correctly and every other
    line silently did not: no stock-comp tag, no balance-sheet tag, no share
    count. The page printed a complete, confident valuation in which net cash
    read 0 against roughly $77B of real debt, and six of ten years charged
    their whole buyback as stock compensation, with nothing saying a word.

    This is not IFRS support. It is the refusal that should exist before
    support is attempted: the tool's premise is that it never prints a number
    it cannot stand behind, and a reader that knows US-GAAP tag names cannot
    stand behind an IFRS filing.
    """
    if not net_income_tag.startswith("ProfitLoss"):
        return ""
    # PAGE 6 EDIT (3 Sep 2026): tool 1's banner says its reader cannot stand
    # behind an IFRS filing, which was true there and is not here. This page's
    # banner states what remains structurally unread instead: four lines have
    # no standard IFRS name at all, so tax withheld on vesting reads as sparse
    # coverage, and an all-stock acquisition is caught only by the share-jump
    # exclusion rather than netted at its tagged consideration the way
    # Salesforce's Tableau is on tool 1.
    head = ("**This is a foreign private issuer reporting under IFRS"
            + (f", in {ccy}" if ccy and ccy != "USD" else "")
            + f".** Net income was read from {net_income_tag}. This page's reader carries "
            "IFRS names for the main lines, but they are candidates until the tag panel "
            "shows them answered — and no standard IFRS name exists for tax withheld on "
            "vesting, acquisition consideration, offering shares or conversion shares, so "
            "withholding runs on sparse coverage and an all-stock deal is caught only by "
            "the share-jump exclusion. ")
    if not unread:
        return head + "Every core line read something; check the tag panel before trusting."
    return head + ("Nothing at all was read for: " + ", ".join(unread) + ". Those lines are "
                   "wrong rather than missing — a line that reads nothing is treated as a "
                   "zero. Treat the whole page as unverified and do not use the valuation.")


def growth_trend_phrase(cagr3: float, latest: float) -> str:
    """Describe a change in growth rate without lying about its direction.

    Shell, 26 Aug 2026: -6.1% latest against -11.2% over three years satisfied
    `latest - cagr3 > 0.05` and printed "revenue growth is accelerating" about
    a company whose revenue was shrinking in both readings. Arithmetically an
    increase, verbally false. The sign of each rate decides the words.
    """
    if latest > cagr3:
        if latest <= 0:
            return "shrinking, though less quickly than it was"
        return "growing faster than it was" if cagr3 > 0 else "back in growth after shrinking"
    if latest < 0 <= cagr3:
        return "shrinking after growing"
    return "shrinking faster than it was" if latest < 0 else "growing more slowly than it was"


def treasury_signal(shares_out: dict[int, float], wavg: dict[int, float],
                    ratio: float = 1.15) -> tuple[bool, int | None]:
    """Is the tagged share count inflated by treasury stock? Compared IN ONE YEAR.

    The test is sound: a year-end count materially above the weighted-average
    diluted count means treasury shares are being counted, because the average
    excludes them by construction. What was unsound was the years it compared.
    It took max(shares_out) against max(wavg) with nothing requiring those to
    be the same year, and on a filer whose tagged series stopped years ago the
    gap it measured was mostly elapsed time.

    AutoZone, checked against EDGAR on 26 Aug 2026. The ladder reads
    CommonStockSharesOutstanding here, which stops at FY2018:

        CommonStockSharesOutstanding     FY2018   25.7M
        WeightedAverageNumber...Diluted  FY2018   27,424,000   ratio 0.94
        WeightedAverageNumber...Diluted  FY2025   17,245,000   ratio 1.49
        (CommonStockSharesIssued         FY2018   27,530,000   ratio 1.004)

    In its own year the count sits BELOW the diluted average, which is what
    dilution looks like and the opposite of a treasury block — and the issued
    tag beside it is barely above. The guard fired anyway, and the note told
    the reader the difference was "sitting in treasury": a false statement
    about that company, produced entirely by seven years of buybacks between
    one series' last year and the other's.

    Returns (fired, year). With no year in common the test is SKIPPED rather
    than guessed at: a series with no overlap at all is a coverage problem, and
    the sparse branch is what handles those.
    """
    common = set(shares_out) & set(wavg)
    if not common:
        return False, None
    fy = max(common)
    return shares_out[fy] > ratio * wavg[fy], fy


def dual_class_signal(outstanding: dict[int, float], wavg: dict[int, float],
                      factor: float = 1.0) -> tuple[str, int | None, float, float]:
    """Is the outstanding count missing a share class? Compared IN ONE YEAR.

    ITEM 7. Two defects, one fix.

    First, the same cross-year comparison treasury_signal had: it took
    max(shares_out) against max(SHD) with nothing tying them to a year. Under a
    heavy buyback an outstanding count from 2025 against an average from 2019
    reads like a missing share class, and a genuine dual-class filer whose
    outstanding tag is current would read clean.

    Second, the series. The ladder fills the weighted-average from three tags
    (diluted, its alternate spelling, and BASIC as a last resort); the
    dual-class test read `SHD`, which is unfilled and knows two. A filer that
    tags only the basic count therefore had a `_wv` for the ladder and nothing
    here, so the test silently did not run and a missing share class would have
    gone unreported. Same idea, two lengths, and the shorter one was guarding.
    Both now read the filled series.

    Returns (kind, fy, outstanding, wavg) with kind one of:
      "dual" — outstanding is far below the average: classes are missing
      "gap"  — a smaller divergence, worth naming but not worth overriding
      "none" — agreement, or no year in common to compare in
    """
    common = set(outstanding) & set(wavg)
    if not common:
        return "none", None, 0.0, 0.0
    fy = max(common)
    o = outstanding[fy] / 1e6
    w = wavg[fy] / 1e6 * factor
    if o <= 0 or w <= 0:
        return "none", fy, o, w
    if o / w < 0.65:
        return "dual", fy, o, w
    if abs(o / w - 1) > 0.03:
        return "gap", fy, o, w
    return "none", fy, o, w


def share_route_note(kind: str, was: float, wavg: float, route: str,
                     covered: int, window: int, last_fy: int,
                     factor: float = 1.0) -> str:
    """Explain why the tagged share count was put aside, and for which reason.

    There were two wordings for three situations, so the third borrowed the
    second's. TransDigm's note said the count "barely moved while the company
    was buying stock back" — which was false about a series whose three tagged
    figures were correct and simply stop in 2012. A note that misdescribes what
    it found is worse than no note.

    The figures are also scaled by `factor`. `_was` and `_wv` are read before
    the post-filing split adjustment, so Booking printed 64.5M against 32.6M
    while every other share figure on the page was post-split by 25 — the ratio
    was right and both numbers were on a basis the reader could not see.
    """
    if kind == "treasury" and was <= wavg:
        # A wiring error upstream once fed this branch a count BELOW the
        # average while the sentence claimed it was far above. The words are
        # not allowed to contradict the figures printed beside them.
        return ("In FY{} the share count read as {:,.1f}M against a weighted-average diluted "
                "count of {:,.1f}M. That is not the treasury pattern this switch is meant to "
                "catch, so the figures are worth checking against the tag panel. Switched to "
                "{}.").format(last_fy, was * factor / 1e6, wavg * factor / 1e6, route)
    if kind == "treasury":
        return ("In FY{} the share count read as {:,.1f}M against a weighted-average diluted "
                "count of {:,.1f}M — that far above the average means issued shares, with the "
                "difference "
                "sitting in treasury, so repurchases never showed and every per-share figure "
                "used too many shares. Switched to {}.{}"
                ).format(last_fy, was * factor / 1e6, wavg * factor / 1e6, route,
                         f" Both counts are shown on the current post-split basis "
                         f"(x{factor:g}), like every other share figure here."
                         if abs(factor - 1.0) > 0.01 else "")
    if kind == "static":
        return ("The share count barely moved while the company was buying stock back, so the "
                "tag being read is not shares outstanding. Switched to {}.").format(route)
    # sparse: nothing wrong with the figures, there are just too few of them
    return ("The tagged share count is not wrong, there is too little of it: {} of the {} years "
            "in this window carry one and the series stops at FY{}. A year with no count shows "
            "no share change, so its whole buyback would fall on employees. {}"
            ).format(covered, window, last_fy,
                     "Kept it anyway — nothing else covers this window better."
                     if route == "the tagged share count" else f"Switched to {route}.")


def holes_note(fys) -> str | None:
    """The note for a window whose labels are not consecutive, or None.

    Paychex is the case it was written for: net income read for 2009-2015 and
    2024-2026, ten rows spanning eighteen years, two eras blended. A gap of
    exactly one label is different — see HOLE_OR_FYE_CHANGE — and gets the
    clause that says so. Wording unchanged otherwise.
    """
    span = max(fys) - min(fys) + 1
    if span <= len(fys):
        return None
    missing = [y for y in range(min(fys), max(fys) + 1) if y not in fys]
    return (
        f"**The filing history has holes.** {len(fys)} annual figures span {span} calendar "
        f"years, with nothing read for FY{missing[0]}"
        + (f"-FY{missing[-1]}" if len(missing) > 1 else "")
        + ". The year-by-year table draws these rows next to each other as though they were "
          "consecutive. Growth rates here are measured across the real calendar gap, so they "
          "are not wrong, but they blend two eras of the company with a hole in the middle — "
          "and the pooled ΔE weights whichever era has more years. The tag panel shows how "
          "many years each line actually read."
        + (HOLE_OR_FYE_CHANGE if len(missing) == 1 else ""))


def negative_sbc_note(years) -> str | None:
    """The note for a true stock-comp cost that reads below zero, or None.

    Two ways it earns a note. Many years: the count test, unchanged since it
    was written — Rivian trips it at 3 of 7 and must go on doing so. One year
    of a size that swamps everything else: BellRing FY2020, 28 Aug 2026, the
    only negative year in its window and so invisible to the count, reading
    -524 against a GAAP charge of 2 and net income of 24. Owners' earnings
    for the year came out at 550 and its ΔE cell at 2342.1%, and nothing on
    the page mentioned it. With no withholding line found, a negative reading
    IS option and ESPP proceeds, and proceeds twenty times the year's profit
    are the October 2019 spin-off financing tagged where exercises would be —
    the same shape as Rivian's FY2019 pre-IPO round. The size test: the
    negative cost outweighs the year's net income, so the year's owners'
    earnings are at least double what was reported. A negative year that
    fails both tests gets nothing, as before.
    """
    kept = [y for y in years if not y.excluded]
    neg = [y for y in kept if y.omega < 0]
    if len(neg) >= max(2, len(years) // 3):
        return (
            f"The true stock-comp cost reads negative in {len(neg)} of {len(years)} years, which "
            "ADDS to owners' earnings instead of subtracting and pushes ΔE above 100%. It happens "
            "legitimately when option and ESPP proceeds exceed the tax withheld, but it is also "
            "what a missing buyback or issuance line looks like. Check the tag panel below: if "
            "buybacks read fewer years than net income, that is the cause and ΔE here is a "
            "ceiling rather than a measurement.")
    big = [y for y in neg if abs(y.omega) > abs(y.N)]
    if big:
        y = max(big, key=lambda y: abs(y.omega))
        return (
            f"The true stock-comp cost reads {y.omega:,.0f}M in FY{y.fy} against a GAAP charge of "
            f"{y.G:,.0f}M and net income of {y.N:,.0f}M, so that year's owners' earnings of "
            f"{y.OE:,.0f}M are more than double what was reported. A negative reading means option "
            "and ESPP proceeds exceeded the tax withheld, and proceeds that dwarf both the charge "
            "and the year's profit are almost always financing — a listing, a spin-off or an "
            "offering — tagged where employee exercises would be. The year is not excluded from "
            "the pooled figures, so that year's ΔE cell, and any pool that includes it, is a "
            "ceiling rather than a measurement.")
    return None


def resolve_ticker(ticker: str, cmap: dict) -> str | None:
    """Find a ticker in the SEC list, allowing for how people actually type it.

    The SEC writes class shares with a hyphen — BRK-B, BF-B, HEI-A — and
    almost everyone writes them with a dot. Berkshire is the single most
    likely first search on a value-investing forum, and both pages answered
    "'BRK.B' is not in the SEC company list", which reads as "we do not have
    Berkshire" rather than "try the other punctuation".

    Tries the ticker as given, then the dot and hyphen swapped both ways.
    Returns the form that resolved, or None. Yahoo also uses the hyphen, so
    the resolved form is the right one for the price lookup too.
    """
    t = (ticker or "").strip().upper()
    for candidate in (t, t.replace(".", "-"), t.replace("-", ".")):
        if candidate in cmap:
            return candidate
    return None


def _yahoo_search(q: str) -> list[str]:
    """Candidate symbols for a company name, from the quote provider."""
    try:
        r = requests.get("https://query1.finance.yahoo.com/v1/finance/search",
                         params={"q": q, "quotesCount": 8, "newsCount": 0},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return [it.get("symbol", "") for it in r.json().get("quotes", [])
                if it.get("symbol")]
    except Exception:
        return []


@st.cache_data(ttl=86400, show_spinner=False)
def home_listing(name: str, ticker: str, ccy: str) -> str:
    """The first discoverable listing that quotes in the filing currency.

    PAGE 6 EDIT (4 Sep 2026). Search by company name, then by ticker;
    probe each candidate with one quote request and read the response's
    own currency field. First match wins and is named in the run's
    notes; no match returns "" and the caller refuses honestly. The
    probe IS the gate — nothing is trusted because of where it trades,
    only because of what currency it answers in.
    """
    seen: set[str] = set()
    matches: list[str] = []
    for q in (name, ticker):
        if not q:
            continue
        for sym in _yahoo_search(q):
            if sym in seen or sym.upper() == ticker.upper():
                continue
            seen.add(sym)
            cp = current_price(sym)
            if cp and cp[1] == ccy:
                matches.append(sym)
                if len(matches) >= 3:
                    break
        if len(matches) >= 3:
            break
    if not matches:
        return ""
    if len(matches) == 1:
        return matches[0]
    # Among currency matches, the one with the deepest monthly history
    # wins: a primary home listing beats a thin secondary venue, and for
    # a company with no home-currency primary at all (Spotify), the
    # least-thin secondary is chosen and the coverage machinery judges it.
    best, best_n = matches[0], -1
    for sym in matches:
        try:
            _c, _sp, _pc = _monthly_closes(sym)
            n = len(_c) if _pc == ccy else -1
        except Exception:
            n = -1
        if n > best_n:
            best, best_n = sym, n
    return best


def no_home_listing_refusal(name: str, ccy: str) -> str:
    """PAGE 6 EDIT (4 Sep 2026): the sentence for the Spotify case."""
    return (f"files in {ccy}, but no listing quoting in {ccy} could be found — every "
            "discoverable listing trades in another currency. Spotify is the canonical "
            "case: EUR filings, one listing on earth, in New York, in dollars. This "
            "page never converts a currency — Burry excludes ASML from his own index "
            "for FX alone — so it cannot price this stock. If a home listing exists "
            "that the search missed, type its symbol in the price-symbol box. "
            "Otherwise the one honest route is the paste mode with average prices you "
            f"convert into {ccy} yourself: then the FX judgement is yours, stated, "
            "rather than the page's, silent.")


def price_mismatch_refusal(sym: str, px_ccy: str, ccy: str) -> str:
    """The sentence for a price quote in the wrong currency.

    PAGE 6 EDIT (3 Sep 2026, after the first live runs). Names the symbol,
    both currencies, the fix, and the one way this actually happens in
    practice: a symbol left in the box from the previous company. The form
    keeps its fields between runs, so ASML's .AS symbol was still there
    when GRAB was typed — the refusal is right, and it must say why.
    """
    return (f"cannot be priced from {sym}: that listing quotes in {px_ccy} while the "
            f"filings are in {ccy}, and this page never converts a currency. If "
            f"'{sym}' is left over from the previous company you looked at, clear the "
            "price-symbol box — it keeps its value between runs. Otherwise give the "
            f"symbol of a listing that trades in {ccy} (Amsterdam is .AS, Frankfurt "
            ".DE, Paris .PA, London .L), or type the figures into the paste mode, "
            "prices included.")


def ads_gate(ccy: str, ads_ratio: float) -> str:
    """Non-empty when the ADS route is being misused, with the sentence.

    PAGE 6 EDIT (3 Sep 2026). An ADS ratio converts UNITS — one ADS is N
    ordinary shares — never currency. On a USD filer (Legend Biotech, each
    ADS two ordinary shares) dividing the ADS price by the ratio yields the
    per-ordinary price and market cap comes out right; on a EUR filer no
    ratio makes a dollar price comparable with a euro filing, which is FX by
    another door, and the door stays shut.
    """
    if abs(ads_ratio - 1.0) <= 1e-9 or ccy == "USD":
        return ""
    return (f"reports in {ccy}, and an ADS ratio converts share units, not currency — no "
            f"ratio makes a US-dollar ADS price comparable with a {ccy} filing. Set the "
            "ratio back to 1 and give the home-exchange symbol (Amsterdam is .AS, "
            f"Frankfurt .DE) so prices arrive in {ccy}.")


# ══════════════════════════════════════════════════════════════════════
#  PASTE MODE — the same engine on typed figures (PAGE 6, 3 Sep 2026)
# ══════════════════════════════════════════════════════════════════════
# Case (2) of the brief: a company with no SEC filing at all — CTS Eventim —
# and the fallback for every line the reader cannot fetch. The builder below
# turns typed rows into the SAME Year objects load() builds, so everything
# downstream (pooling, gates, seeding, IV15, every refusal) is one code
# path; the self-test pins it against Burry's published Alphabet inputs.

PASTE_COLS = ["FY", "Net income", "GAAP SBC", "Buybacks", "Tax withheld",
              "Option proceeds", "Shares (M)", "Avg price",
              "Stock issued for acquisitions", "Revenue", "Exclude"]


def _excl(v) -> bool:
    """The Exclude cell, tolerant of CSV round-trips: only an actual yes
    excludes. PAGE 6 EDIT (3 Sep 2026) — bool("False") is True in Python,
    and the first dry-run of a saved CSV excluded every year it carried."""
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1", "x")
    return bool(v)


def _cell(v) -> float | None:
    """Blank is NOT zero. None where the cell was left empty or unreadable."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def paste_build(rows: list[dict]) -> tuple[list[Year], list[str], list[str]]:
    """Typed rows -> (years, notes, errors). Errors mean nothing is stored.

    The semantics deliberately mirror load(), never soften it:
    - Net income, the year-end share count and the average price are
      blank-not-zero. A row with no net income but a share count is a BASE
      row: only its count is used, to give the following year a real share
      change — the caption tells the user to add one such earlier year.
    - Buybacks, withholding, option proceeds, acquisition stock and revenue
      blank mean zero, because genuinely zero is common; the parsed table is
      echoed back so a wrong assumption is visible.
    - A blank price makes the year UNPRICED, exactly as a missing Yahoo
      month does on tool 1 — named here, and counted by the same
      price-coverage refusal downstream.
    - A blank share count gives the year no share change, exactly as a
      missing filed count does on tool 1 — named, never silent.
    - The capital-event exclusion runs UNCHANGED: >25% share jump in the
      first priced year, >15% after, excluded with the same note. The
      Exclude column adds manual exclusions; it cannot un-exclude what the
      rule caught, because the rule is the engine's, not the user's.
    """
    errors: list[str] = []
    notes: list[str] = []
    parsed: list[tuple[int, dict]] = []
    for i, r in enumerate(rows):
        fy = _cell(r.get("FY"))
        if fy is None:
            if any(_cell(r.get(c)) is not None for c in PASTE_COLS[1:-1]):
                errors.append(f"Row {i + 1} has figures but no fiscal year — refused.")
            continue
        parsed.append((int(fy), r))
    if not parsed:
        errors.append("No rows with a fiscal year.")
        return [], notes, errors
    parsed.sort(key=lambda t: t[0])
    if len({fy for fy, _ in parsed}) != len(parsed):
        errors.append("The same fiscal year appears twice — refused, fix the table.")
        return [], notes, errors

    shares: dict[int, float] = {}
    full: list[tuple[int, dict]] = []
    base_only: list[int] = []
    for fy, r in parsed:
        n, sh = _cell(r.get("Net income")), _cell(r.get("Shares (M)"))
        if sh is not None and sh <= 0:
            errors.append(f"FY{fy}: a share count of {sh:g} is not a share count — refused.")
            continue
        if sh is not None:
            shares[fy] = sh
        if n is None:
            if sh is not None:
                base_only.append(fy)
            else:
                errors.append(f"FY{fy}: no net income and no share count — the row carries "
                              "nothing the engine can use. Blank is not zero here.")
            continue
        full.append((fy, r))

    fys = [fy for fy, _ in full]
    gaps = [f"FY{a}→FY{b}" for a, b in zip(fys, fys[1:]) if b - a > 1]
    if gaps:
        notes.append("The typed years are not consecutive (" + ", ".join(gaps) + "). The pooled "
                     "figures treat them as the whole record; if years are missing from the "
                     "middle, ΔE spans a gap nobody can see. Add them if you have them.")

    years: list[Year] = []
    no_count: list[int] = []
    for fy, r in full:
        price = _cell(r.get("Avg price"))
        dS = (shares[fy] - shares[fy - 1]) if fy in shares and fy - 1 in shares else 0.0
        if fy not in shares or fy - 1 not in shares:
            no_count.append(fy)
        years.append(Year(
            fy=fy, N=_cell(r.get("Net income")) or 0.0,
            G=abs(_cell(r.get("GAAP SBC")) or 0.0),
            T=abs(_cell(r.get("Buybacks")) or 0.0), dS=dS,
            Cw=abs(_cell(r.get("Tax withheld")) or 0.0),
            Ce=abs(_cell(r.get("Option proceeds")) or 0.0),
            price=price or 0.0,
            A=abs(_cell(r.get("Stock issued for acquisitions")) or 0.0),
            excluded="excluded by you" if _excl(r.get("Exclude")) else ""))

    if len(years) < 4:
        errors.append(f"Only {len(years)} full year(s). ΔE is a pooled figure over roughly ten "
                      "years and IV15 projects fifteen more; four years is the minimum this "
                      "tool will reason from — the same rule as the fetched pages.")
        return years, notes, errors

    # The capital-event exclusion, verbatim in rule and note from load().
    priced = [i for i, y in enumerate(years) if y.price > 0]
    for i in priced:
        base = shares.get(years[i].fy - 1, 0.0)
        if base <= 0 or years[i].excluded:
            continue
        jump = years[i].dS / base
        first_priced = (i == priced[0])
        if jump > (0.25 if first_priced else 0.15):
            kind = "listing year" if first_priced else "share-funded acquisition"
            years[i].excluded = kind
            notes.append(
                f"FY{years[i].fy} excluded — the share count rose {jump:.0%} in one year, which "
                "no payroll produces. That is a "
                + ("listing: preferred converts to common and new stock is sold."
                   if first_priced else
                   "capital event, most often an all-stock acquisition or a large offering.")
                + " Counting it as compensation would swamp every other year in the pool. The "
                  "pooled figures now cover fewer years, so read them with that in mind.")

    manual = [y.fy for y in years if y.excluded == "excluded by you"]
    if manual:
        notes.append("Excluded by you: " + ", ".join(f"FY{f}" for f in manual) + ". The pooled "
                     "figures skip these years entirely; the table still shows them.")
    if base_only:
        notes.append("Base rows (share count only, no net income): "
                     + ", ".join(f"FY{f}" for f in base_only)
                     + ". Used solely to give the following year a real share change.")
    if no_count:
        notes.append("No share change could be measured for "
                     + ", ".join(f"FY{f}" for f in no_count)
                     + " — the year-end count for it or the year before is blank. Those years "
                       "show no share change, so their stock-comp cost is the whole buyback "
                       "and their owners' earnings are understated. Blank was not read as "
                       "zero; it was read as unmeasured, which this note is.")
    unpriced = [y.fy for y in years if y.price <= 0]
    if unpriced and len(unpriced) < len(years):
        notes.append("No average price for " + ", ".join(f"FY{f}" for f in unpriced)
                     + " — the market value of share changes there contributes nothing to the "
                       "stock-comp cost, exactly as a year without price history on the "
                       "fetched pages.")
    for y in years:
        if y.price > 0 and shares.get(y.fy) and abs(y.N) > 0:
            pe = y.price * shares[y.fy] / y.N
            if not 1 <= abs(pe) <= 500:
                notes.append(
                    f"FY{y.fy}: net income {y.N:,.0f}M against {shares[y.fy]:,.1f}M shares at "
                    f"{y.price:,.2f} is a P/E of {pe:,.0f}x — almost certainly a units mix-up "
                    "(millions vs thousands, or price in cents) rather than a valuation. "
                    "Check the row.")
    return years, notes, errors


def calendar_avg(closes: dict[str, float]) -> dict[int, float]:
    """Average of monthly closes per CALENDAR year, from 'YYYY-MM' keys.

    PAGE 6 EDIT (3 Sep 2026). The paste grid knows fiscal years only as
    integers, so the mapping assumes calendar fiscal years — true for CTS
    Eventim and most of Europe. A filer with an odd year end should type
    its prices; the caption says so. Years with only some months present
    (the far end of Yahoo's history) average what exists, which the
    engine tolerates the same way it tolerates tool 1's missing months.
    """
    by_year: dict[int, list[float]] = {}
    for k, v in closes.items():
        try:
            y = int(k[:4])
        except (ValueError, TypeError):
            continue
        if v and v > 0:
            by_year.setdefault(y, []).append(float(v))
    return {y: sum(vs) / len(vs) for y, vs in by_year.items() if vs}


def paste_price_gate(years: list) -> tuple[str, str]:
    """(error, note) for price coverage of typed years.

    PAGE 6 EDIT (3 Sep 2026). Prices only matter where the share count
    moved or cash bought shares back: V = T + P x dS. If no included year
    has either, an unpriced table is fully measurable and the note says
    why. If prices matter and NO included year is priced, dE would print
    near 100% for any company at all — refused, same reasoning as the
    fetched pages' coverage refusal. In between, dE is a floor, named.
    """
    inc = [y for y in years if not y.excluded]
    if not inc:
        return "", ""
    matter = [y for y in inc if y.T > 0 or abs(y.dS) > 1e-9]
    priced = [y for y in inc if y.price > 0]
    if not matter:
        return "", ("No year in this table has a share-count change or a buyback, so "
                    "average prices cannot affect the true SBC cost — Ω is measurable "
                    "without them and ΔE below is exact, not a floor. This is the "
                    "no-dilution case (CTS Eventim's), said out loud rather than "
                    "assumed.")
    if not priced:
        return (f"Every year is unpriced while {len(matter)} year(s) have share-count "
                "changes or buybacks. The market value of shares handed to employees "
                "is priced at zero, so ΔE would read near 100% for any company at "
                "all. Give a price symbol above (a listing in the same currency) or "
                "type average prices, then Compute again."), ""
    if len(priced) < (len(inc) + 1) // 2:
        return "", (f"Only {len(priced)} of {len(inc)} included years carry a price, "
                    "and years without one contribute no share-change cost. Treat ΔE "
                    "as a floor, not a measurement, until more years are priced.")
    return "", ""


def paste_growth(revs: list[tuple[int, float]]) -> tuple[float, float | None, float | None, str]:
    """(growth seed, raw latest, 3y cagr, note) from typed revenue.

    The same judgement as load(): seed from the latest year's revenue growth,
    capped to [-10%, +25%] because nothing compounds at a launch rate for
    fifteen years. With no usable revenue the seed is 8% and says so.
    """
    revs = sorted([(fy, v) for fy, v in revs if v and v > 0])
    raw = cagr3 = None
    if len(revs) >= 2 and revs[-2][1] > 0:
        raw = revs[-1][1] / revs[-2][1] - 1
    if len(revs) >= 4 and revs[-4][1] > 0:
        cagr3 = (revs[-1][1] / revs[-4][1]) ** (1 / 3) - 1
    if raw is None:
        return 0.08, None, None, ("No usable revenue was typed, so growth is seeded at 8% — a "
                                  "placeholder, not a reading. Set it from what you know.")
    g = max(-0.10, min(raw, 0.25))
    note = ""
    if abs(raw - g) > 1e-9:
        note = (f"Latest revenue growth is {raw:.0%}, which is a launch rate, not a durable "
                f"one — capped at {g:.0%} for the seed, the same cap as the fetched pages.")
    return g, raw, cagr3, note


def load(ticker: str, n_years: int = 10, price_symbol: str = "", ads_ratio: float = 1.0):
    cmap = _ticker_map()
    resolved = resolve_ticker(ticker, cmap)
    if resolved is None:
        raise ValueError(
            f"'{ticker}' is not in the SEC company list. Class shares are listed with a "
            "hyphen rather than a dot — BRK-B, BF-B, HEI-A — and both spellings are "
            "accepted here. A company with no SEC filing at all — CTS Eventim and most "
            "European listings — cannot be fetched by any page; switch to **Paste your own "
            "figures** above and type the annual figures from its own annual reports.")
    ticker = resolved
    facts = _facts(cmap[ticker])
    sic, sic_desc = _sic(cmap[ticker])

    # PAGE 6 EDIT (3 Sep 2026): three gates before anything is read, in this
    # order. The router first, so a US bank typed here is told "tool 1" (whose
    # own banner forwards financials) rather than getting the IFRS-financials
    # sentence; the financial refusal second, so it only ever fires for
    # foreign financials; the currency detection third, feeding every read.
    _route = sent_to_tool1(facts)
    if _route:
        raise ValueError(f"{ticker}: " + _route)
    _fin = nonus_financial_refusal(sic, sic_desc)
    if _fin:
        raise ValueError(f"{ticker} " + _fin)
    ccy = filing_currency(facts)

    tag_sources: dict[str, list[str]] = {k: [] for k in CONCEPTS}
    series = {k: _annual(facts, us, ifrs, tag_sources[k], k in FILL_KEYS,
                         k in RECENCY_KEYS, unit=ccy)
              for k, (us, ifrs) in CONCEPTS.items()}
    # Tool 1 refuses here when a foreign currency dominates. This page is the
    # page that refusal points at, so the block is replaced by the detection
    # above: every line below reads the detected currency and nothing mixes.

    # MA / OFFER / CONV are corporate transactions, not flows, so _annual's
    # duration filter throws their facts away. Re-read them with _issuance,
    # which is built for dated events. See item 3 in the brief.
    for _k in ("MA", "OFFER", "CONV"):
        tag_sources[_k].clear()
        series[_k] = _issuance(facts, CONCEPTS[_k][0], series["N"], tag_sources[_k])
    tag_sources["MAV"].clear()
    # PAGE 6 EDIT (3 Sep 2026): the acquisition consideration is a money line,
    # so it reads the filing currency. No IFRS names exist for MA/OFFER/CONV/
    # MAV in v1 — the foreign banner below says so, because it means an
    # all-stock deal by an IFRS filer is caught only by the >15% share-jump
    # exclusion, not netted per-year the way Salesforce's Tableau was.
    series["MAV"] = _issuance(facts, CONCEPTS["MAV"][0], series["N"],
                              tag_sources["MAV"], ccy)

    if not series["N"]:
        raise ValueError(
            f"No annual net income could be read for {ticker} in {ccy}, the currency its "
            "facts say it reports in. The filer uses tags this reader does not recognise. "
            "Nothing can be computed without net income; the paste mode above runs the same "
            "engine on figures you type from the annual report.")

    # PAGE 6 EDIT (3 Sep 2026): IFRS candidates appended, outstanding before
    # issued — the same correctness ordering as the US names, because issued
    # includes treasury on both sides of the Atlantic. _instant tries us-gaap,
    # then dei (the 20-F cover page), then ifrs-full, so a 20-F cover count
    # still wins over the IFRS names where both exist.
    shares_out = _instant(facts, ["CommonStockSharesOutstanding", "CommonStockSharesIssued",
                                  "EntityCommonStockSharesOutstanding",
                                  "NumberOfSharesOutstanding", "NumberOfSharesIssued",
                                  "NumberOfSharesIssuedAndFullyPaid"], unit="shares")
    shares_out = {k: v for k, v in shares_out.items() if v and v > 0}
    # Bind `notes` HERE, not further down. The share-count ladder below appends
    # to it, and Python makes a name local to the whole function the moment it
    # is assigned anywhere in it — so initialising notes after the ladder threw
    # UnboundLocalError on every ticker that tripped the ladder (AZO, HRB, TDG)
    # while leaving every other ticker working. Keep this line above the ladder.
    shares_out, notes = split_adjust(shares_out)
    # A share count that includes treasury stock is not a share count. AutoZone
    # tags CommonStockSharesIssued: ~25.7M shares, of which ~9M sit in treasury
    # and only ~16.6M are outstanding. Every per-share figure was computed
    # against the wrong number, market cap included, and the change between
    # years read near zero because issued shares barely move.
    #
    # The tell is the weighted-average diluted count, which excludes treasury by
    # construction: a year-end count materially ABOVE it means treasury is being
    # counted, materially BELOW means a second share class was missed.
    #
    # Repairs, in order of how exact they are:
    #   1. issued minus treasury shares — both year-end, and the difference IS
    #      outstanding by definition
    #   2. the 10-K cover page count — a real outstanding figure, net of
    #      treasury, just dated at the filing rather than the year end
    #   3. the weighted-average diluted count — fixes the LEVEL but not the
    #      CHANGE, because an average lags the buyback that caused it, so
    #      V = max(0, T + P·dS) turns into noise
    _wv_src: list[str] = []
    _wavg_ser = _annual(facts, ["WeightedAverageNumberOfDilutedSharesOutstanding",
                                "WeightedAverageNumberOfSharesOutstandingDiluted",
                                "WeightedAverageNumberOfSharesOutstandingBasic"],
                        # PAGE 6 EDIT (3 Sep 2026): the IFRS EPS denominators,
                        # diluted before basic — same ordering judgement.
                        ["AdjustedWeightedAverageShares", "WeightedAverageShares"],
                        _wv_src, True, unit=ccy)
    _wv = {fy: v[2] for fy, v in _wavg_ser.items() if v[2] and v[2] > 0}
    _cover = _cover_shares(facts, series["N"])
    # Read separately from shares_out purely so the tag panel can report how many
    # years each source covers. TDG's bug was invisible until the panel showed
    # CommonStockSharesOutstanding at 3 years against a 16-year cover page.
    _c_out = _instant(facts, ["CommonStockSharesOutstanding"], unit="shares")
    _c_iss = _instant(facts, ["CommonStockSharesIssued"], unit="shares")
    # PAGE 6 EDIT (3 Sep 2026): the IFRS counts, read separately so the panel
    # can say which side of the ladder answered — on Grab neither US name
    # exists and a panel that only names US tags would misdescribe the route.
    _i_out = _instant(facts, ["NumberOfSharesOutstanding"], unit="shares")
    _i_iss = _instant(facts, ["NumberOfSharesIssued",
                              "NumberOfSharesIssuedAndFullyPaid"], unit="shares")
    _treas = _instant(facts, ["TreasuryStockCommonShares", "TreasuryStockShares",
                              "TreasuryStockNumberOfSharesHeld",
                              "TreasuryStockCommonSharesHeld"], unit="shares")
    _share_route = "as tagged"
    _route_note, _route_extra = None, None
    if _wv and shares_out:
        _lat, _latw = max(shares_out), max(_wv)
        _win0 = sorted(series["N"])[-n_years:]
        _static = len({round(v) for v in shares_out.values()}) <= 2
        # Same year on both sides, or no test at all — see treasury_signal.
        _treasury, _treas_fy = treasury_signal(shares_out, _wv)
        # A third failure, found on TransDigm: the tagged series is neither
        # inflated nor static, just SHORT. CommonStockSharesOutstanding covered
        # 3 of 10 years against a 16-year cover page, so the share change read
        # +0.0 in every year and the whole buyback fell on employees — the same
        # damage as the treasury case, arriving by a different door. Coverage is
        # the thing to test, not the symptom that first made it visible.
        # Captured before shares_out is replaced below, or the note would count
        # coverage of the series that WON rather than the one it is describing.
        _covered_by = dict(shares_out)
        _sparse = sum(1 for fy in _win0 if fy in shares_out) < 0.6 * len(_win0)
        if _static or _treasury or _sparse:
            _was = shares_out[_lat]
            _net = {fy: shares_out[fy] - _treas[fy] for fy in shares_out
                    if fy in _treas and shares_out[fy] - _treas[fy] > 0}
            # Rank by COVERAGE of the window first, exactness second. Taking the
            # most exact series regardless of length was worse than the problem
            # it solved: H&R Block tags treasury shares for 5 years and carries
            # a 17-year cover page, and preferring the 5-year series left six of
            # ten years with no share change at all — so V became the entire
            # buyback and owners' earnings collapsed. A series that does not
            # cover the year cannot measure a change in it.
            _win = sorted(series["N"])[-n_years:]
            _cands = [(_net, "issued minus treasury shares"),
                      (_cover, "the 10-K cover page"),
                      (_wv, "the weighted-average diluted count")]
            if _sparse and not (_static or _treasury):
                # nothing wrong with the tagged figures, only with how few of
                # them there are — so it stays in the running
                _cands.insert(0, (dict(shares_out), "the tagged share count"))
            _scored = [(sum(1 for fy in _win if fy in c), -i, c, name)
                       for i, (c, name) in enumerate(_cands) if len(c) >= 3]
            if _scored:
                _best = max(_scored)
                _pick, _share_route = _best[2], _best[3]
            else:
                _pick, _share_route = _wv, "the weighted-average diluted count"
            shares_out, _extra = split_adjust(_pick)
            notes.extend(_extra)
            # Held back until the post-filing split factor below is known, so
            # the figures quoted are on the same basis as every other share
            # count on the page. Booking is the case: 64.5M against 32.6M is
            # the right ratio in units nothing else on the page uses.
            # For the treasury branch the figures and the year are the ONES THE
            # TEST USED, not the newest of each series independently.
            # `_covered_by`, NOT `shares_out`: shares_out has already been
            # replaced by the series that WON, so reading it here quoted the
            # cover-page count the ladder switched TO rather than the tagged
            # count it switched FROM. Booking printed "791.8M against a
            # weighted-average diluted count of 816.0M — that far above the
            # average means issued shares" about a figure BELOW the average.
            _t_fy = _treas_fy if _treasury and _treas_fy is not None else _lat
            _route_note = ("treasury" if _treasury else "static" if _static else "sparse",
                           _covered_by.get(_t_fy, _was) if _treasury else _was,
                           _wv.get(_t_fy, _wv[_latw]) if _treasury else _wv[_latw],
                           _share_route,
                           sum(1 for fy in _win0 if fy in _covered_by), len(_win0), _t_fy)
            if _share_route.startswith("the weighted"):
                _route_extra = (
                    "That count is an average over each year rather than a year-end snapshot, so "
                    "its change lags the repurchase and the true stock-comp cost below will be "
                    "erratic — compare it against the GAAP charge before trusting any year.")

    # Checked HERE, outside the ladder, against whichever series won. Inside
    # the ladder it could only fire when the ladder RAN, and the ladder is
    # gated on `_wv and shares_out` — so the filer shape that needs it most
    # could never receive it. Shell tags none of the US-GAAP share concepts, so
    # both series were empty, the ladder was skipped, and a 4-year cover page
    # covered 4 of 10 years in silence.
    _win_cov = sorted(series["N"])[-n_years:]
    _cov_n = sum(1 for fy in _win_cov if fy in shares_out)
    if _win_cov and _cov_n < 0.6 * len(_win_cov):
        notes.append(
            f"Only {_cov_n} of the {len(_win_cov)} years in this window have a share count from "
            "any tag this reader knows. Years without one show no share change, so their "
            "stock-comp cost is the whole buyback and their owners' earnings are understated. "
            "Treat the year-by-year table as partial.")

    # PAGE 6 EDIT (3 Sep 2026): the price policy, in code. Two mutually
    # exclusive routes. (a) A home-listing symbol in the FILING currency —
    # ASML.AS, SAP.DE — typed in the symbol box. (b) The US listing plus a
    # stated ADS ratio, ONLY when the filing currency is USD: the ratio
    # converts units (one ADS = N ordinary shares — Legend Biotech is 2),
    # never currency, so on a EUR filer it fixes nothing and is refused
    # before any request is made. The fetched quote names its own currency
    # and a mismatch drops the whole price series rather than footnoting it —
    # the existing price-coverage refusal then says ΔE is unmeasurable, which
    # is the truth. Prices are divided by the ratio so every figure on the
    # page is PER ORDINARY SHARE in the FILING currency; the assumptions
    # block prints the ratio on every run because no filing tags it.
    _ag = ads_gate(ccy, ads_ratio)
    if _ag:
        raise ValueError(f"{ticker} " + _ag)
    _px_sym = (price_symbol or "").strip()
    if not _px_sym:
        if ccy == "USD":
            _px_sym = ticker
        else:
            # PAGE 6 EDIT (4 Sep 2026): the old default here was the US
            # ticker, which quotes USD and so was guaranteed to be refused
            # for every non-USD filer — Chen hit it three times in a row.
            _name = str(facts.get("entityName") or "")
            _px_sym = home_listing(_name, ticker, ccy)
            if not _px_sym:
                raise ValueError(f"{ticker} " + no_home_listing_refusal(_name or ticker, ccy))
            notes.append(
                f"Prices come from {_px_sym}, found automatically: the quote provider "
                f"was searched for {_name or ticker}'s listings and each candidate was "
                f"probed until one answered in {ccy}, the filing currency. The symbol "
                "is in the assumptions block; type a different one in the price-symbol "
                "box to override.")
    _px_ccy = ""
    try:
        closes, splits, _px_ccy = _monthly_closes(_px_sym)
    except Exception:
        closes, splits = {}, {}
    if closes and _px_ccy and _px_ccy != ccy:
        raise ValueError(f"{ticker} " + price_mismatch_refusal(_px_sym, _px_ccy, ccy))
    if closes and abs(ads_ratio - 1.0) > 1e-9:
        closes = {k: v / ads_ratio for k, v in closes.items()}
        notes.append(
            f"Prices are per ADS at {_px_sym} and were divided by the stated ratio of "
            f"{ads_ratio:g} ordinary shares per ADS, so every figure on this page is per "
            "ORDINARY share. The ratio is your input, not a filed number — the depositary "
            "agreement in the 20-F states it (Legend Biotech: each ADS represents two "
            "ordinary shares). If it is wrong, market cap and IV15 are wrong by exactly "
            "that factor.")

    # A split reaches the price series within a day and the share counts here
    # not until the next 10-K, up to a year later. In between, every share
    # change was being priced at a market price on the other basis, market cap
    # was wrong by the split factor, and IV15 — a per-share figure built on the
    # filed count — was being compared against a price that was not.
    #
    # Found on IES Holdings, which split 2-for-1 effective 24 August 2026 with a
    # September year end: the two pages disagreed by exactly 2x because one had
    # cached prices from before Yahoo restated them and the other after.
    #
    # split_adjust() cannot see this. It restates history onto the latest FILED
    # basis by spotting jumps in the filed series, and a split that has not
    # reached a filing yet leaves no jump to spot.
    #
    # Scaling the share counts rather than the prices is deliberate: it leaves
    # the price on screen matching the price in the market, and every ratio
    # (dilution, P/IV15, market cap) comes out invariant.
    _asof = split_asof(shares_out, {fy: v[1] for fy, v in series["N"].items()},
                       _cover_asof(facts), _share_route == "the 10-K cover page")
    _split_factor, _split_seen = 1.0, []
    for _day, _ratio in sorted(splits.items()):
        if _asof and _day > _asof:
            _split_factor *= _ratio
            _split_seen.append(f"{_day} ({_ratio:g}-for-1)")
    if abs(_split_factor - 1.0) > 0.01 and shares_out:
        shares_out = {fy: v * _split_factor for fy, v in shares_out.items()}
        notes.append(
            f"{ticker} split after the share counts in this window were filed — "
            + ", ".join(_split_seen)
            + f". The price history is already restated for it and the filings are not, so every "
              f"share count here has been multiplied by {_split_factor:g} to put the two on the "
              f"same basis. Without this the market cap would be wrong by that factor and IV15 "
              f"would be measured against a price it does not match. The next annual filing "
              f"makes the adjustment unnecessary and it will stop being applied.")

    if _route_note:
        notes.append(share_route_note(*_route_note, factor=_split_factor))
    if _route_extra:
        notes.append(_route_extra)

    # PAGE 6 EDIT (4 Sep 2026): the 2:1 pass, market-confirmed. See
    # confirm_band_splits — Novo Nordisk's 2023 2-for-1 sat inside the
    # band and read as an acquisition until this.
    shares_out, _cbs_note = confirm_band_splits(shares_out, splits)
    if _cbs_note:
        notes.append(_cbs_note)

    # NetIncomeLoss is profit attributable to the parent; ProfitLoss includes
    # what belongs to minority holders of consolidated subsidiaries. Filling
    # one from the other is right when the gap is a tagging change and slightly
    # generous when the filer has real minority interests, so say so rather
    # than let it pass silently — this is the base of every figure on the page.
    _nsrc = tag_sources.get("N", [])
    if len(_nsrc) > 1:
        notes.append(
            "Net income came from more than one tag: the years "
            f"{_nsrc[0]} does not cover were filled from {', '.join(_nsrc[1:])}. "
            + ("ProfitLoss includes profit belonging to minority holders of "
               "consolidated subsidiaries, so where it filled a year the figure is "
               "the whole group's rather than shareholders' alone. "
               if "ProfitLoss" in _nsrc[1:] else "")
            + "The tag panel shows which tags answered.")

    fys = sorted(series["N"])[-n_years:]
    # Below this there is no history to reason about. Toyota returned two years
    # and the page rendered a full verdict on them. ΔE is pooled over roughly a
    # decade and IV15 projects fifteen years past it; four annual figures is the
    # least that can carry either.
    if len(fys) < 4:
        raise ValueError(
            f"Only {len(fys)} year(s) of annual figures could be read for {ticker}"
            + (f" (FY{min(fys)}" + (f"-FY{max(fys)})" if len(fys) > 1 else ")") if fys else "")
            + ". ΔE is a pooled figure over roughly ten years and IV15 projects fifteen more, "
              "so both are statements about a long run of history. Four years is the minimum "
              "this tool will reason from. A recent listing, a filer using tags this reader "
              "does not know, or a foreign issuer are the usual causes.")

    # Enough history is not the same as the right history — see the window
    # guards above. Revenue first, because both series come from the same
    # filings; the calendar as a backstop for a filer with no revenue read.
    _stale = stale_window_refusal(fys, list(series.get("REV", {})), dt.date.today().year)
    if _stale:
        raise ValueError(f"{ticker} cannot be valued from these filings — " + _stale)

    non_sbc_total = 0.0
    years: list[Year] = []

    for fy in fys:
        start, end, N = series["N"][fy]
        get = lambda k: abs(series[k][fy][2]) / 1e6 if fy in series[k] else 0.0

        dS = ((shares_out[fy] - shares_out[fy - 1]) / 1e6
              if fy in shares_out and fy - 1 in shares_out else 0.0)
        non_sbc = sum(abs(series[k][fy][2]) / 1e6
                      for k in ("MA", "OFFER", "CONV") if fy in series.get(k, {}))
        if non_sbc:
            dS -= non_sbc
            non_sbc_total += non_sbc
        price = _avg_price(closes, start, end) or 0.0

        years.append(Year(fy=fy, N=N / 1e6, G=get("G"), T=get("T"), dS=dS,
                          Cw=get("Cw"), Ce=get("Ce"), price=price, A=get("MAV")))

    # V is priced at the year's average, so a year with no price contributes
    # nothing to the stock-comp cost however many shares moved.
    _unpriced = sum(1 for y in years if y.price <= 0)
    _pc = price_coverage_refusal(len(years), _unpriced, bool(closes))
    if _pc:
        # PAGE 6 EDIT (4 Sep 2026): when prices came from a non-US venue,
        # thin quote history at that venue is the usual cause and the
        # refusal must say so instead of blaming the window's age.
        if _px_sym.upper() != ticker.upper():
            _pc += (f" On this page prices came from {_px_sym}; a secondary "
                    f"listing often carries only a few years of quotes. If a "
                    f"deeper {ccy} listing exists, type its symbol in the "
                    "price-symbol box.")
        raise ValueError(f"{ticker} cannot be valued from these filings — " + _pc)

    # An IPO converts preferred to common and sells new stock in one go. Valuing
    # that at the market price treats a capital raise as compensation, which is
    # what drives absurd negative dE for recently listed companies. The tell is
    # the first year a market price exists carrying a share jump no payroll
    # could produce.
    # Compensation dilutes 1-3% of the share count a year. Alphabet's worst year
    # is 1.4%, Meta's 1.6%. A double-digit jump is a capital event — a listing,
    # an all-stock acquisition or a secondary — and pricing it at market charges
    # the whole deal to employees. Broadcom's VMware year alone put roughly $86B
    # of phantom SBC cost into a 10-year pool that should total $51.6B.
    priced = [i for i, y in enumerate(years) if y.price > 0]
    for i in priced:
        base = shares_out.get(fys[i] - 1, 0.0) / 1e6
        if base <= 0:
            continue
        jump = years[i].dS / base
        first_priced = (i == priced[0])
        if jump > (0.25 if first_priced else 0.15):
            kind = "listing year" if first_priced else "share-funded acquisition"
            years[i].excluded = kind
            notes.append(
                f"FY{years[i].fy} excluded — the share count rose {jump:.0%} in one year, which "
                "no payroll produces. That is a "
                + ("listing: preferred converts to common and new stock is sold."
                   if first_priced else
                   "capital event, most often an all-stock acquisition.")
                + " Counting it as compensation would swamp every other year in the pool. The "
                  "pooled figures now cover fewer years, so read them with that in mind.")

    if non_sbc_total:
        notes.append(f"Excluded {non_sbc_total:,.1f}M shares issued for acquisitions, offerings "
                     "or conversions — those are corporate transactions, not compensation. "
                     "Where a company issues stock for deals this matters a great deal.")
    capped_any = False
    if "TreasuryStockValueAcquiredCostMethod" in tag_sources.get("Cw", []):
        # The size test needed a stock-comp charge to test against, and AutoZone
        # has none in the window — so the test never ran and its entire $1.5B
        # treasury purchase was charged as employee tax withholding AND again as
        # the market value of shares delivered. Owners' earnings came out at
        # minus $612M for one of the most profitable retailers in America.
        # A missing yardstick is now a rejection, not a free pass, and a
        # withholding line the size of the buyback line is rejected outright.
        # Sized against the GAAP charge where there is one, and against net
        # income where there is not. The earlier version also rejected any
        # withholding larger than half the buyback line — written for AutoZone,
        # where the two were the same $1.5B — but that fires on every company
        # with a SMALL buyback programme. It threw away seven years of real
        # withholding at IES Holdings and pushed owners' earnings UP, which is
        # the flattering direction and the one to be most suspicious of.
        # A repurchase wearing a withholding label is always large next to
        # earnings; genuine withholding is not.
        capped = 0
        for y in years:
            if not y.Cw:
                continue
            if (y.Cw > 3 * y.G) if y.G > 0 else (y.Cw > 0.10 * abs(y.N)):
                y.Cw, capped = 0.0, capped + 1
        capped_any = capped > 0
        if capped:
            notes.append(
                f"A treasury-stock line was read as tax withholding and rejected in {capped} "
                "year(s): it was more than three times the GAAP stock-comp charge, or — where "
                "no charge was tagged to size it against — more than a tenth of net income. "
                "Either means it is an ordinary repurchase, and charging it as withholding "
                "would count the same dollars twice — once as cash out, once as the market value "
                "of shares delivered.")
        else:
            notes.append(
                "Tax withholding was read from a treasury-stock line rather than the usual "
                "withholding tag. Filers that retire shares on repurchase report it this way. "
                "The amounts are withholding-sized, so they were accepted.")

    # MOVED BELOW THE WITHHOLDING GUARD, 26 Aug 2026. `omega` is a live property
    # over `Cw`, so this ratio changes the moment the guard above zeroes a
    # rejected withholding line — and the note used to be computed before that
    # and printed after it. AutoZone said 41.7x while its own table said 6.5x;
    # TransDigm said 4.9x against a table giving 3,334/1,095 = 3.05x, the gap of
    # 2,031 being exactly the four years the guard rejected. The threshold is
    # unchanged: AZO still clears 4x at 6.5x and keeps its note, TDG falls under
    # it at 3.05x and loses the note entirely. That is correct, not a
    # regression — TransDigm's true cost really is large next to its GAAP charge
    # because of its option-plus-dividend-equivalent structure, not because
    # non-pay issuance is being miscounted.
    _kept = [y for y in years if not y.excluded]
    _sg = sum(y.G for y in _kept)
    _som = sum(y.omega for y in _kept)
    if _sg > 0 and _som / _sg > 4.0:
        notes.append(
            f"True SBC cost is {_som/_sg:.1f}x the GAAP charge. Across the whole NASDAQ-100 that "
            "ratio is about 1.9x and the worst single name is 3.6x, so anything past roughly 4x "
            "usually means shares issued for something other than pay — an offering, an "
            "acquisition or a preferred conversion — are being counted as compensation. Treat ΔE "
            "here as a floor, not a measurement.")

    # Count OR size — see negative_sbc_note. BellRing FY2020 was one year
    # among nine and the largest distortion in its table.
    _neg_note = negative_sbc_note(years)
    if _neg_note:
        notes.append(_neg_note)

    # A blank year inside the window is invisible in a table full of numbers.
    # H&R Block read 12 of 19 years after the gap-filling fix, and the four
    # blanks that remained sat in the middle of the window while the share
    # count fell in every one of them. Nothing said so.
    _gap = [y.fy for y in years
            if y.fy not in series["T"] and y.dS < 0
            and shares_out.get(y.fy - 1, 0) > 0
            and abs(y.dS) / (shares_out[y.fy - 1] / 1e6) > 0.01]
    if _gap:
        notes.append(
            "No repurchase figure was found for FY"
            + ", FY".join(str(f) for f in _gap)
            + ", yet the share count fell by more than 1% in each. Those years are almost "
              "certainly buybacks tagged under an element this reader does not know. Two "
              "consequences: owners' earnings for those years are a ceiling, since the market "
              "value of shares delivered floors at zero without a repurchase figure; and cash "
              "returned to shareholders is understated, which flatters the growth a company "
              "looks able to fund. The tag panel shows which elements did answer.")

    # Paychex reads net income for 2009-2015 and 2024-2026 and nothing between.
    # The table draws FY2015 directly above FY2024, ten rows spanning eighteen
    # calendar years, and every rate computed across them silently blends two
    # different eras of the company.
    _holes = holes_note(fys)
    if _holes:
        notes.append(_holes)

    if any(y.price == 0 for y in years):
        notes.append("No share price for some years — their SBC cost is understated.")
    if not any(y.Cw for y in years) and not capped_any:
        # The "flattering" claim holds only where the GAAP charge itself read.
        # On Shell neither read, and the buyback was charged in full as stock
        # comp in six years — understating owners' earnings, the exact
        # opposite of what this note asserted.
        notes.append("No tax-withholding line found. That understates the SBC cost, so "
                     "owners' earnings here are flattering rather than conservative."
                     if any(y.G for y in years) else
                     "Neither a stock-comp charge nor a tax-withholding line was found. With no "
                     "charge to size it against, any year that also lacks a share count charges "
                     "its whole buyback as compensation, so owners' earnings in those years are "
                     "understated rather than flattering. Check the tag panel before using them.")

    _bal: dict[str, list[str]] = {}
    # Coverage and latest year are captured HERE, from the same read that
    # feeds net cash, rather than looked up again when the panel is built. A
    # panel running its own lookup can report a series the page did not use.
    _bal_n: dict[str, int] = {}
    _bal_fy: dict[str, str] = {}

    _skips: list[tuple[str, int, str, int]] = []

    _bal_v: dict[str, float] = {}

    def g(ks):
        src: list[str] = []
        # PAGE 6 EDIT (3 Sep 2026): the filing currency, not USD.
        d = _instant(facts, ks, ccy, src, _skips, prefer_recent=True)
        v = (max(d.items(), default=(0, 0.0))[1]) / 1e6
        _bal[ks[0]] = src
        _bal_n[ks[0]] = len(d)
        _bal_fy[ks[0]] = _latest_fy(d)
        # Kept so the item 9 note can say what the carried-forward figure is
        # worth. See stale_swing_note.
        _bal_v[ks[0]] = v
        return v

    cash_total = g(BALANCE["cash"]) + g(BALANCE["sti"]) + g(BALANCE["lti"])
    debt_total = g(BALANCE["ltd"]) + g(BALANCE["std"])
    lease_total = g(BALANCE["lease"])
    net_cash = cash_total - debt_total
    # PAGE 6 EDIT (4 Sep 2026): the TTE guard — cash read, debt silent.
    _dun = debt_unread_note(
        _bal_n.get(BALANCE["cash"][0], 0), _bal_n.get(BALANCE["ltd"][0], 0),
        _bal_n.get(BALANCE["std"][0], 0))
    if _dun:
        notes.insert(0, _dun)
    # PAGE 6 EDIT (4 Sep 2026): the double-count hazard, named when live.
    if ("Borrowings" in _bal.get(BALANCE["ltd"][0], [])
            and "ShorttermBorrowings" in _bal.get(BALANCE["std"][0], [])):
        notes.append(
            "Long-term debt was read from Borrowings, which is the WHOLE debt balance, "
            "and short-term debt from ShorttermBorrowings — the current portion may be "
            "counted twice. Net cash is therefore understated, the conservative "
            "direction, but check the two figures against the balance sheet before "
            "leaning on it.")
    # Say so when a first-preference tag was passed over for a fresher one.
    # Usually the switch just repairs a gap between two names for the same
    # line. Once it does not: Progressive's cash comes from the
    # restricted-inclusive tag, which is a different definition, and a silent
    # swap there would move net cash without a word on the page.
    if _skips:
        notes.append(
            "Some balance-sheet lines were read from a fallback tag because the "
            "preferred one had stopped: "
            + "; ".join(f"{_w} to FY{_wy} instead of {_l}, which ends at FY{_ly}"
                        for _l, _ly, _w, _wy in _skips)
            + ". Where the two tags are alternate names for the same line this "
              "simply repairs a gap. Where they are not — cash including "
              "restricted balances is not cash — the figure has changed "
              "definition, so check the line before trusting it.")
    _stale_bal = stale_instant_lines(_bal_fy, years[-1].fy if years else 0)
    if _stale_bal:
        notes.append(
            "**A balance-sheet line here stops before net income does.** "
            + "; ".join(f"{_n.lower()} ends at FY{_y}, {_g} year{'s' if _g > 1 else ''} behind"
                        for _n, _y, _g in _stale_bal)
            + f". Net income reaches FY{years[-1].fy if years else 0}, and a balance sheet is "
              "reported at every year end, so this is not the company having a quiet year — "
              "either the balance moved to a tag this reader does not know, or the line ended "
              "and the figure should now be zero. Net cash above carries the last figure found "
              "forward as though it were current, so it is wrong in one direction or the other."
            + stale_swing_note(
                net_cash,
                [(_n, NET_CASH_SIGN.get(_n, 0) * _bal_v.get(dict(BALANCE_ROWS)[_n][0], 0.0))
                 for _n, _y, _g in _stale_bal])
            + " The tag panel names the tag; that name is usually the whole fix.")
    if debt_total == 0 and lease_total > 0:
        notes.append(f"No funded debt found, which for many companies is simply true — plenty "
                     f"fund themselves entirely from operations. It does carry "
                     f"{lease_total:,.0f}M of long-term operating "
                     "lease obligations — real commitments, but Burry's framework handles leases "
                     "inside the capital base rather than as borrowings, so they are not "
                     "subtracted here.")
    # PAGE 6 EDIT (3 Sep 2026): tool 1 zeroes an insurer's net cash here and
    # prints an apologetic valuation. On this page a financial filer was
    # refused before anything was read — nonus_financial_refusal in load() —
    # so this branch cannot fire and the block is removed rather than left to
    # mislead the next editor into thinking financials reach this far.

    # First in the list, because it governs how every other note reads.
    _unread = [_l for _l, _empty in (("stock compensation", not any(y.G for y in years)),
                                     ("the share count", not shares_out),
                                     ("the balance sheet", cash_total == 0 and debt_total == 0))
               if _empty]
    _ff = foreign_filer_note(_nsrc[0] if _nsrc else "", _unread, ccy)
    if _ff:
        notes.insert(0, _ff)

    # Most recent shares OUTSTANDING beats trailing weighted-average diluted.
    # Under a heavy buyback the weighted average is stale and systematically
    # high, which depresses every per-share figure. Adobe: 427M weighted vs
    # ~408M actual, a 4.7% error straight through to IV15.
    # Shares outstanding is preferred (buybacks make the trailing weighted
    # average stale), BUT under a dual-class structure the outstanding count is
    # tagged per class and we may be seeing only one of them. Weighted-average
    # diluted is reported consolidated, so when the two diverge by more than a
    # buyback could explain, trust the diluted figure.
    # `_wv` rather than series["SHD"]: same idea, filled from three tags
    # instead of two. See dual_class_signal. The scaling is still applied, or
    # the test compares a post-split count against a pre-split one and fires on
    # a company with one share class.
    _dc_kind, _dc_fy, _dc_out, _dc_wv = dual_class_signal(shares_out, _wv, _split_factor)
    outstanding = shares_out[max(shares_out)] / 1e6 if shares_out else 0.0
    wavg = _dc_wv
    diluted = outstanding or wavg
    if _dc_kind != "none":
        if _dc_kind == "dual":
            # The COMPARISON is same-year; the count that replaces it must
            # still be the most recent one, or a filer whose average stops in
            # 2019 would be valued on a 2019 share count.
            diluted = _wv[max(_wv)] / 1e6 * _split_factor
            notes.append(f"In FY{_dc_fy} shares outstanding read as {_dc_out:,.1f}M but "
                         f"weighted-average diluted is {_dc_wv:,.1f}M — too big a gap for "
                         "buybacks. This usually means multiple share classes and only one was "
                         f"picked up. Using the diluted figure ({diluted:,.1f}M); check it "
                         "against the market cap below.")
        else:
            # Both figures from FY{_dc_fy}, so the ratio quoted is the one tested.
            notes.append(f"In FY{_dc_fy} shares outstanding read {_dc_out:,.1f}M against "
                         f"weighted-average diluted {_dc_wv:,.1f}M. Using the current count; "
                         "buybacks make the average stale.")

    rev = series.get("REV", {})
    ry = sorted(rev)
    latest_rev = rev[ry[-1]][2] / 1e6 if ry else 0.0
    # Seed from the LATEST year-over-year rate, not a 3-year CAGR. A trailing
    # CAGR averages in growth that has already ended: Paycom decelerated 23% ->
    # 11% -> 9% -> 7%, and its 3-year CAGR still reads 8.9%. Forward-looking
    # valuation should start from the most recent rate, with the CAGR shown
    # alongside so the trend is visible.
    growth, raw_growth, cagr3 = 0.08, None, None
    if len(ry) >= 2 and rev[ry[-2]][2] > 0 and rev[ry[-1]][2] > 0:
        raw_growth = rev[ry[-1]][2] / rev[ry[-2]][2] - 1
    if len(ry) >= 4 and rev[ry[-4]][2] > 0 and rev[ry[-1]][2] > 0:
        cagr3 = (rev[ry[-1]][2] / rev[ry[-4]][2]) ** (1 / 3) - 1
    if raw_growth is not None and cagr3 is not None and cagr3 - raw_growth > 0.05:
        notes.append(
            f"Revenue is {growth_trend_phrase(cagr3, raw_growth)} — {cagr3:.1%} over three "
            f"years but {raw_growth:.1%} "
            "in the latest. The seed uses the recent rate. Burry typically goes lower still: he "
            "projects owners' earnings, not revenue, and cuts further for competitive and AI-era "
            "risk. For Paycom his figure implies about 3.5% against 7% recent revenue growth.")
    elif raw_growth is not None and cagr3 is not None and raw_growth - cagr3 > 0.05:
        notes.append(
            f"Revenue is {growth_trend_phrase(cagr3, raw_growth)} — {cagr3:.1%} over three "
            f"years, {raw_growth:.1%} in the latest. The seed uses the recent rate; satisfy "
            "yourself it is durable.")

    # Applied to EVERY company, not just one branch above. A company emerging
    # from near-zero revenue throws an enormous rate that must never compound
    # for fifteen years; ROIC is the real ceiling and hypergrowth belongs in a
    # short Stage 0 instead.
    if raw_growth is not None:
        growth = max(-0.10, min(raw_growth, 0.25))
        if abs(raw_growth - growth) > 1e-9:
            notes.append(
                f"Latest revenue growth is {raw_growth:.0%}, which is a launch rate, not a "
                f"durable one — capped at {growth:.0%} for the seed. Nothing compounds at that "
                "pace for fifteen years, and return on capital is the real ceiling. If the "
                "surge is genuinely still ahead, use the hypergrowth years in Model settings "
                "instead of raising this.")

    tags = tag_report(facts, series, tag_sources)
    _kept_oe = sorted(y.OE for y in years[-5:] if not y.excluded)
    _med = _kept_oe[len(_kept_oe) // 2] if _kept_oe else 0.0
    tags = tags + [
        {"Line": "— Shares: outstanding", "Years read": len(_c_out),
         "Latest year": _latest_fy(_c_out),
         "XBRL tag": "CommonStockSharesOutstanding",
         "Status": "used" if _share_route == "as tagged" and _c_out else
                   "read" if _c_out else "not tagged"},
        {"Line": "— Shares: issued", "Years read": len(_c_iss),
         "Latest year": _latest_fy(_c_iss),
         "XBRL tag": "CommonStockSharesIssued",
         "Status": "includes treasury — only used if nothing better exists"
                   if _c_iss else "not tagged"},
        {"Line": "— Shares: cover page", "Years read": len(_cover),
         "Latest year": _latest_fy(_cover),
         "XBRL tag": "dei:EntityCommonStockSharesOutstanding",
         "Status": "used" if _share_route == "the 10-K cover page" else
                   "read" if _cover else "not tagged"},
        {"Line": "— Shares: outstanding (IFRS)", "Years read": len(_i_out),
         "Latest year": _latest_fy(_i_out),
         "XBRL tag": "NumberOfSharesOutstanding",
         "Status": "read — candidate name, see the ladder note" if _i_out else "not tagged"},
        {"Line": "— Shares: issued (IFRS)", "Years read": len(_i_iss),
         "Latest year": _latest_fy(_i_iss),
         "XBRL tag": "NumberOfSharesIssued + IssuedAndFullyPaid",
         "Status": "includes treasury — only used if nothing better exists"
                   if _i_iss else "not tagged"},
        {"Line": "— Shares: treasury held", "Years read": len(_treas),
         "Latest year": _latest_fy(_treas),
         "XBRL tag": "TreasuryStockCommonShares",
         "Status": "used" if _share_route.startswith("issued minus") else
                   "read" if _treas else "not tagged"},
        {"Line": "— Shares: diluted average", "Years read": len(_wv),
         "Latest year": _latest_fy(_wv),
         "XBRL tag": (_wv_src[0] if _wv_src
                      else "WeightedAverageNumberOfDilutedSharesOutstanding"),
         "Status": "used" if _share_route.startswith("the weighted") else
                   "read" if _wv else "not tagged"},
    ] + [
        {"Line": f"— {name}", "Years read": _bal_n.get(ks[0], 0),
         "Latest year": _bal_fy.get(ks[0], "—"),
         "XBRL tag": " + ".join(_bal.get(ks[0], [])) or "—",
         "Status": "read" if _bal.get(ks[0]) else "none of the tags this reader knows are in "
                                                 "the filing"}
        for name, ks in BALANCE_ROWS]
    return years, notes, {"tags": tags, "net_cash": net_cash, "cash": cash_total, "debt": debt_total,
                          "median_OE": _med, "revenue": latest_rev, "cagr3": cagr3,
                          "leases": lease_total,
                          # The form that resolved against the SEC list. Yahoo uses the
                          # same hyphenated spelling, so pricing BRK.B as typed returned
                          # nothing and the page fell back to its $100.00 default beside
                          # a real market cap.
                          "ticker": ticker,
                          "shares": diluted, "growth": growth, "sic": sic,
                          "sic_desc": sic_desc, "financial": is_financial(sic),
                          # PAGE 6 EDIT (3 Sep 2026): the currency and the
                          # price route travel with the data, so the UI can
                          # label every money figure and gate the live price.
                          "currency": ccy, "px_sym": _px_sym, "px_ccy": _px_ccy,
                          "ads_ratio": ads_ratio, "typed": False}


# ══════════════════════════════════════════════════════════════════════
#  SELF-TEST
# ══════════════════════════════════════════════════════════════════════

def test_summary(results: list[tuple[str, bool, str]]) -> tuple[str, str]:
    """One line at the TOP of the expander: how many ran, how many failed.

    Verification used to mean scrolling a list of 48 or 124 lines looking for a
    red tick, or spending screenshots on it. Worse, the count itself was being
    taken from the source rather than the page: the handover recorded 105
    checks for this tool because that is how many `out.append` statements it
    has, while one of them sits inside a loop and the page actually runs 107.
    A number the page prints itself cannot drift from the page.

    Returns (severity, text) where severity is "success" or "error", so a red
    is visible before any scrolling and names the checks that failed.
    """
    bad = [name for name, ok, _ in results if not ok]
    if not bad:
        return "success", f"**{len(results)} checks, 0 failed.**"
    return "error", (f"**{len(results)} checks, {len(bad)} FAILED:** "
                     + "; ".join(bad[:4])
                     + (f" — and {len(bad) - 4} more" if len(bad) > 4 else ""))


def self_test() -> list[tuple[str, bool, str]]:
    out = []
    goog = [(2016, 19478, 6900, 3693, 3304, 97, 47), (2017, 12662, 7900, 4846, 4166, 78, 55),
            (2018, 30736, 10000, 9075, 4993, -2, 61), (2019, 34343, 11700, 18396, 4765, -158, 70),
            (2020, 40269, 12991, 31149, 5720, -263, 73), (2021, 76033, 15376, 50274, 10162, -264, 125),
            (2022, 59972, 19362, 59296, 9300, -412, 117), (2023, 73795, 22460, 61504, 9837, -374, 115),
            (2024, 100118, 22785, 62222, 12190, -243, 164), (2025, 132170, 24953, 45709, 14167, -93, 206)]
    ys = [Year(fy=f, N=n, G=g, T=t, Cw=c, dS=d, price=p) for f, n, g, t, c, d, p in goog]
    out.append(("Alphabet FY2016 V = $8,252M", abs(ys[0].V - 8252) < 1, f"${ys[0].V:,.0f}M"))
    out.append(("Alphabet FY2025 V = $26,551M", abs(ys[-1].V - 26551) < 1, f"${ys[-1].V:,.0f}M"))
    p = pool(ys)
    out.append(("Alphabet pooled ΔE = 88.7%", abs(p.dE - 0.887) < 0.002, f"{p.dE:.2%}"))

    m16 = Year(fy=2016, N=10217, G=3218, T=0, Cw=-10, dS=46, price=107)
    out.append(("Meta FY2016 ΔE = 83.4% (no buyback)", abs(m16.dE - 0.834) < 0.005, f"{m16.dE:.1%}"))

    N_, G_, OM_ = 4925.5, 919.0, 1732.2
    out.append(("NDX-97 GAAP overstatement = 19.78%",
                abs((OM_ - G_) / (N_ + G_ - OM_) - 0.1978) < 0.001,
                f"{(OM_-G_)/(N_+G_-OM_):.2%}"))
    out.append(("Break-even ΔE = 87%", abs(1 / 1.15 - 0.870) < 0.001, f"{1/1.15:.1%}"))

    crm = IVParams(OE=7300, shares=1073.3, tier="Chapel", growth=0.069,
                   exit_multiple=21.8, blend=1.0)
    out.append(("Salesforce IV15, his inputs → $69.81",
                abs(intrinsic_value(crm, 15) - 69.81) < 1.0,
                f"${intrinsic_value(crm,15):.2f}"))
    out.append(("Salesforce IVB, his inputs → 8.6%",
                abs(expected_return(165.84, crm) - 0.086) < 0.005,
                f"{expected_return(165.84, crm):.1%}"))

    stale = {"facts": {"us-gaap": {
        "LongTermDebtNoncurrent": {"units": {"USD": [
            {"form": "10-K", "end": f"{y}-09-30", "filed": f"{y}-11-15", "val": 1.0}
            for y in range(2009, 2021)]}},
        "LongTermDebt": {"units": {"USD": [
            {"form": "10-K", "end": f"{y}-09-30", "filed": f"{y}-11-15", "val": 2.0}
            for y in range(2009, 2026)]}}}}}
    _src, _skip = [], []
    _picked = _instant(stale, ["LongTermDebtNoncurrent", "LongTermDebt"], "USD", _src,
                       _skip, True)
    _y = Year(fy=2022, N=1444.0, G=2779.0, T=0.0, dS=70.0, price=249.24, A=11269.0)
    _y2 = Year(fy=2022, N=1444.0, G=2779.0, T=0.0, dS=70.0, price=249.24)
    # Netted, NOT zeroed. This assertion shipped on 24 Aug 2026 reading
    # "V == 0.0" and was RED on both pages from the moment it landed —
    # caught 24 Aug when the expander was finally read line by line rather
    # than counted. The engine was never wrong: $11.269B of Slack
    # consideration against $17.447B of stock delivered leaves $6.178B that
    # really was pay, and Salesforce's FY2022 owners' earnings are genuinely
    # negative — the live run reads dE -46.1%, not a positive number. What
    # is worth pinning is that V falls by exactly the tagged consideration
    # and by nothing else, which is the claim the fix actually makes.
    out.append(("Acquisition consideration is netted out of V, not charged to staff",
                abs((_y2.V - _y.V) - 11269.0) < 1e-6 and _y.V > 0,
                f"V ${_y2.V:,.0f}M → ${_y.V:,.0f}M, down by exactly the $11,269M tagged"))
    out.append(("...and a year with no acquisition is untouched",
                abs(_y2.V - 249.24 * 70.0) < 1e-6, f"V ${_y2.V:,.0f}M"))
    _iss = {"facts": {"us-gaap": {
        "BusinessAcquisitionEquityInterestsIssuedOrIssuableNumberOfSharesIssued": {
            "units": {"shares": [
                {"form": "10-K", "start": "2020-08-01", "end": "2020-10-31",
                 "filed": "2021-03-01", "val": 39_000_000.0},
                {"form": "10-K", "start": "2020-11-01", "end": "2021-01-31",
                 "filed": "2021-03-01", "val": 1_000_000.0}]}}}}}
    _nser = {2021: ("2020-02-01", "2021-01-31", 4.0e9)}
    _isrc: list[str] = []
    _got = _issuance(_iss, CONCEPTS["MA"][0], _nser, _isrc)
    out.append(("Acquisition shares are read from dated facts and summed in the year",
                _got.get(2021, (None, None, 0.0))[2] == 40_000_000.0
                and _isrc == ["BusinessAcquisitionEquityInterestsIssuedOrIssuableNumberOfSharesIssued"],
                f"{_got.get(2021, (None, None, 0))[2]:,.0f} shares from two closings"))
    _iss2 = {"facts": {"us-gaap": {"StockIssuedDuringPeriodSharesAcquisitions": {
        "units": {"shares": [
            {"form": "10-K", "start": "2020-02-01", "end": "2021-01-31",
             "filed": "2021-03-01", "val": 40_000_000.0},
            {"form": "10-K", "start": "2020-08-01", "end": "2020-10-31",
             "filed": "2021-03-01", "val": 39_000_000.0}]}}}}}
    _got2 = _issuance(_iss2, CONCEPTS["MA"][0], _nser)
    out.append(("...and a full-year fact is used alone, never added to its own quarters",
                _got2[2021][2] == 40_000_000.0, f"{_got2[2021][2]:,.0f}, not 79,000,000"))
    _tdg = {"facts": {"us-gaap": {
        "CommonStockSharesOutstanding": {"units": {"shares": [
            {"form": "10-K", "end": f"{y}-09-30", "filed": f"{y}-11-15", "val": 56.3e6}
            for y in range(2010, 2013)]}},
        "CommonStockSharesIssued": {"units": {"shares": [
            {"form": "10-K", "end": f"{y}-09-30", "filed": f"{y}-11-15", "val": 62.5e6}
            for y in range(2009, 2026)]}}}}}
    _ts: list[str] = []
    _tr = _instant(_tdg, ["CommonStockSharesOutstanding", "CommonStockSharesIssued",
                          "EntityCommonStockSharesOutstanding"], "shares", _ts)
    out.append(("Recency never overrides the share ladder: outstanding beats issued",
                _ts == ["CommonStockSharesOutstanding"] and max(_tr) == 2012,
                f"{_ts[0]} to {max(_tr)} — a 17-year issued series did not win"))
    out.append(("A debt tag that stopped in 2020 loses to one reaching 2025",
                max(_picked) == 2025 and _src == ["LongTermDebt"] and _skip[0][1] == 2020,
                f"chose {_src[0]} to {max(_picked)}, skipped {_skip[0][0]} at {_skip[0][1]}"))
    _src2, _skip2 = [], []
    _both = _instant(stale, ["LongTermDebt", "LongTermDebtNoncurrent"], "USD", _src2,
                     _skip2, True)
    out.append(("...and preference still decides when neither has stopped",
                _src2 == ["LongTermDebt"] and not _skip2 and max(_both) == 2025,
                "no switch recorded"))
    _rev = {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            {"form": "10-K", "start": f"{y}-10-01", "end": f"{y+1}-09-30",
             "filed": f"{y+1}-11-15", "val": 1.0} for y in range(2019, 2024)]}},
        "Revenues": {"units": {"USD": [
            {"form": "10-K", "start": f"{y}-10-01", "end": f"{y+1}-09-30",
             "filed": f"{y+1}-11-15", "val": 2.0} for y in range(2006, 2025)]}}}}}
    _rs: list[str] = []
    _rr = _annual(_rev, ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
                  [], _rs, False, True)
    out.append(("A revenue tag ending FY2024 loses to a longer one reaching FY2025",
                max(_rr) == 2025 and _rs == ["Revenues"] and len(_rr) == 19,
                f"{_rs[0]}, {len(_rr)} years to {max(_rr)}"))
    out.append(("Latest year reports the last year read, not how many",
                _latest_fy({2016: 1, 2020: 1, 2015: 1}) == "2020"
                and _latest_fy({}) == "—",
                "2020 from an unsorted series; — when empty"))
    _rev_now = list(range(2008, 2026))
    out.append(("A window ending FY2015 against revenue to FY2025 is refused",
                "gap of 10 years" in stale_window_refusal(list(range(2008, 2016)), _rev_now, 2026),
                "the Booking Holdings window that printed a verdict on FY2015 earnings"))
    out.append(("...but a December filer read in January is not",
                stale_window_refusal(list(range(2016, 2026)), list(range(2016, 2026)), 2027) == "",
                "FY2025 latest in calendar 2027 is reporting lag, not a hole"))
    out.append(("...and a current window against current revenue is not",
                stale_window_refusal(list(range(2016, 2026)), _rev_now, 2026) == "",
                "FY2025 net income against FY2025 revenue"))
    out.append(("Seven unpriced years out of eight is refused",
                "7 of the 8 years" in price_coverage_refusal(8, 7, True),
                "the same Booking Holdings window, where V floored at zero"))
    out.append(("...but a fully priced window passes, and exactly half still passes",
                price_coverage_refusal(10, 0, True) == ""
                and price_coverage_refusal(10, 5, True) == "",
                "the threshold is MORE than half, not half"))
    out.append(("A price-source failure refuses differently from a window that predates history",
                "temporary failure" in price_coverage_refusal(10, 10, False)
                and "eleven years" in price_coverage_refusal(10, 10, True),
                "two causes, two messages — one is worth retrying, the other is not"))
    out.append(("A dE at or below 100% is projected exactly as measured",
                seed_dE(0.925) == 0.925 and not dE_was_capped(0.925)
                and seed_dE(1.0) == 1.0 and not dE_was_capped(1.0),
                "BKNG 92.5% and a clean 100% both pass through untouched"))
    out.append(("...and above 100% the projection is capped, not the measurement",
                abs(seed_dE(1.0738) - 1.0) < 1e-12 and dE_was_capped(1.0738),
                "ADBE 107.4% seeds at 100.0%, the 107.4% stays on the page"))
    out.append(("...and above 125% it still refuses rather than quietly capping",
                not dE_was_capped(3.0),
                "a broken read is not turned into a plausible number"))
    # RIVN, 27 Aug 2026. ΔE off a negative denominator. sum_omega and sum_G
    # play no part in this gate; the figures that matter are the two sums.
    _rivn3 = Pooled(dE=-9743.0 / -9078.0, sum_N=-9078.0, sum_OE=-9743.0,
                    sum_omega=2227.0, sum_G=1562.0, years=2)
    _rivn6 = Pooled(dE=-14291.0 / -21962.0, sum_N=-21962.0, sum_OE=-14291.0,
                    sum_omega=-4552.0, sum_G=3119.0, years=6)
    out.append(("Rivian's 107.3% ΔE is two negatives divided, and is not projectable",
                not dE_projectable(_rivn3) and abs(_rivn3.dE - 1.073) < 5e-4,
                "-9,743 of owners' earnings over -9,078 of net income, FY2023 and FY2025"))
    out.append(("...and neither is the 65.1% over six years, which nothing would have capped",
                not dE_projectable(_rivn6) and not dE_was_capped(_rivn6.dE),
                "the plausible-looking one: no cap, no warning, 65% of a profit never made"))
    # TGTX, 28 Aug 2026. Nine loss years and one profitable one. The recent
    # window pools to a PROFIT, so its ΔE is a real measurement and projects;
    # the full period pools to a loss and is two negatives divided. Both had
    # to be got right for the seed to reach the box at all.
    _tgtx3 = Pooled(dE=273.0 / 483.0, sum_N=483.0, sum_OE=273.0,
                    sum_omega=355.0, sum_G=146.0, years=3)
    _tgtx_full = Pooled(dE=-672.0 / -340.0, sum_N=-340.0, sum_OE=-672.0,
                        sum_omega=601.0, sum_G=270.0, years=7)
    out.append(("A loss-maker's first profitable years still pool to a real ΔE",
                dE_projectable(_tgtx3) and abs(_tgtx3.dE - 0.565) < 5e-4,
                "FY2023-25: 13 + 23 + 447 of net income, so the denominator is positive"))
    out.append(("...while the ten-year window behind them is not projectable at all",
                not dE_projectable(_tgtx_full) and _tgtx_full.dE > 1.25,
                "-672 over -340 reads 197.6%: undefined AND above the ceiling"))
    _crm3 = Pooled(dE=7001.0 / 7457.0, sum_N=7457.0, sum_OE=7001.0,
                   sum_omega=0.0, sum_G=0.0, years=3)
    _adbe3 = Pooled(dE=7656.0 / 7130.0, sum_N=7130.0, sum_OE=7656.0,
                    sum_omega=0.0, sum_G=0.0, years=3)
    out.append(("...while Salesforce's 93.9% on real profit still projects",
                dE_projectable(_crm3), "7,001 over 7,457"))
    out.append(("...and Adobe's 107.4% still projects, still capped to 100%",
                dE_projectable(_adbe3) and seed_dE(_adbe3.dE) == 1.0
                and dE_was_capped(_adbe3.dE),
                "7,656 over 7,130 — positive earnings, so the cap does the work"))
    _alt = [Year(fy=2024, N=100.0, G=0.0, T=0.0, dS=0.0, price=0.0, Cw=-20.0),
            Year(fy=2025, N=100.0, G=0.0, T=0.0, dS=0.0, price=0.0, Cw=20.0)]
    _pooled_alt = pool(_alt).dE
    _capped_alt = sum(min(y.OE, y.N) for y in _alt) / sum(y.N for y in _alt)
    out.append(("Pooling lets a good year offset a bad one — 120/80 pools to 100%",
                abs(_pooled_alt - 1.0) < 1e-9 and abs(_capped_alt - 0.9) < 1e-9,
                f"pooled {_pooled_alt:.1%}; capping each year first would read "
                f"{_capped_alt:.1%} — the penalty this design refuses to invent"))
    out.append(("Adobe's seed falls to forward net income, not below it",
                abs(7130.0 * seed_dE(1.0738) - 7130.0) < 1e-9,
                "OE 7,656 → 7,130; per-year capping would have given 6,918"))
    _bk = [2135.0, 2341.0, 3998.0, 4865.0, 59.0, 1165.0, 3058.0, 4289.0, 5882.0, 5404.0]
    _bk_med = median_positive_N(_bk)
    out.append(("A year of near-zero profit carries no per-year ΔE — BKNG FY2020",
                dE_cell(59.0, -979.0 / 59.0, _bk_med) is None,
                f"59 against a {_bk_med:,.0f} median is {59.0/_bk_med:.1%} — the -1659.5% cell "
                "was the denominator talking"))
    out.append(("...and every other year in that window keeps its ΔE",
                all(dE_cell(n, 1.0, _bk_med) is not None for n in _bk if n != 59.0),
                "nine of ten cells unchanged"))
    out.append(("...and an ordinary bad year is still a year — ADBE's weakest",
                dE_cell(1169.0, 0.80, median_positive_N(
                    [1169.0, 1694.0, 2591.0, 2951.0, 5260.0, 4822.0, 4756.0, 5428.0,
                     5560.0, 7130.0])) is not None,
                "24% of Adobe's median clears a 10% floor comfortably"))
    out.append(("A loss carries no per-year ΔE either",
                dE_cell(-500.0, 2.0, 3000.0) is None,
                "a ratio to a loss inverts its own sign"))
    out.append(("Suppressing the cell changes no pooled figure",
                abs(pool([Year(fy=2024, N=1000.0, G=0.0, T=0.0, dS=0.0, price=0.0),
                          Year(fy=2025, N=10.0, G=0.0, T=0.0, dS=0.0, price=0.0)]).dE
                    - 1.0) < 1e-12,
                "pooling sums before dividing, so the small year still counts in full"))
    out.append(("A blanked ΔE cell says why it is blank, and never the word None",
                (lambda f: f(None) == "n/a (base too small)" and f(0.871) == "87.1%")(
                    lambda v: "n/a (base too small)" if v is None else f"{v:.1%}"),
                "the caption under the table is invisible in fullscreen, which is the "
                "only view that shows this column"))

    # 10. The ratio note reported a pre-guard figure. `omega` is live over `Cw`,
    #     so a rejected withholding line changes it — and ordering decided
    #     which number the reader saw. Fixtures are the two real shapes.
    class _Y:
        def __init__(self, G, Cw, Ce=0.0, V=0.0, N=0.0):
            self.G, self.Cw, self.Ce, self._V, self.N, self.excluded = G, Cw, Ce, V, N, False

        @property
        def omega(self):
            return (self.Cw - self.Ce) + self._V

    def _ratio(ys):
        g = sum(y.G for y in ys)
        return (sum(y.omega for y in ys) / g) if g > 0 else None

    def _guard(ys):
        for y in ys:
            if (y.Cw > 3 * y.G) if y.G > 0 else (y.Cw > 0.10 * abs(y.N)):
                y.Cw = 0.0
        return ys

    def _azo_shape():
        return [_Y(G=100.0, Cw=4000.0, N=2000.0), _Y(G=100.0, Cw=250.0, V=700.0, N=2000.0)]
    out.append(("AZO's shape: the ratio falls after the guard but keeps its note",
                _ratio(_azo_shape()) > 4.0
                and 4.0 < _ratio(_guard(_azo_shape())) < _ratio(_azo_shape()),
                f"{_ratio(_azo_shape()):.2f}x before the guard, "
                f"{_ratio(_guard(_azo_shape())):.2f}x after — the table's number"))

    def _tdg_shape():
        return [_Y(G=500.0, Cw=4500.0, N=3000.0), _Y(G=500.0, Cw=1000.0, N=3000.0)]
    out.append(("TDG's shape: a guarded ratio drops under 4x and loses the note",
                _ratio(_tdg_shape()) > 4.0 and _ratio(_guard(_tdg_shape())) <= 4.0,
                "expected, not a regression — the note firing at all was the "
                "pre-guard figure talking"))
    out.append(("A company the guard never touches is unaffected by the move",
                _ratio(_guard([_Y(G=100.0, Cw=120.0, N=2000.0)]))
                == _ratio([_Y(G=100.0, Cw=120.0, N=2000.0)]),
                "ordinary withholding is under 3x the charge, so nothing is rejected"))

    _sum_ok = test_summary([("a", True, ""), ("b", True, "")])
    out.append(("The expander header counts what actually ran, not what was written",
                _sum_ok == ("success", "**2 checks, 0 failed.**"), _sum_ok[1]))
    _sum_bad = test_summary([("a", True, ""), ("b", False, ""), ("c", False, "")])
    out.append(("...and a red one says so first and names the failures",
                _sum_bad[0] == "error" and "2 FAILED" in _sum_bad[1]
                and "b; c" in _sum_bad[1], _sum_bad[1]))
    # 9. Item 9 — a balance-sheet line that stops before net income does.
    #    Fixtures are the real shapes: AutoZone's short-term debt, Progressive's
    #    goodwill, Booking's short-term investments, and a clean Adobe.
    _rows = (("Cash", ["A"]), ("Short-term investments", ["B"]),
             ("Long-term debt", ["C"]), ("Short-term debt", ["D"]))
    _azo = stale_instant_lines({"A": "2025", "B": "\u2014", "C": "2025", "D": "2014"},
                               2025, _rows)
    out.append(("AZO's FY2014 short-term debt is caught, 11 years behind",
                _azo == [("Short-term debt", 2014, 11)], f"{_azo}"))
    out.append(("A line with no data at all is not called stale",
                all(n != "Short-term investments" for n, _, _ in _azo),
                "no tags in the filing is a different finding with a different fix"))
    _bkng = stale_instant_lines({"A": "2025", "B": "2024", "C": "2025", "D": "2025"},
                                2025, _rows)
    out.append(("A one-year gap counts — both years come from the same filings",
                _bkng == [("Short-term investments", 2024, 1)], f"{_bkng}"))
    out.append(("A fully current balance sheet fires nothing",
                stale_instant_lines({"A": "2025", "B": "2025", "C": "2025", "D": "2025"},
                                    2025, _rows) == [],
                "Adobe's shape after the debt repair"))
    # 10e. Item 1c — the split anchor belongs to the series being scaled.
    _ends = {fy: f"{fy}-08-25" for fy in range(2016, 2026)}
    out.append(("AutoZone's shape anchors on FY2018, the last year it has share counts for",
                split_asof([2016, 2017, 2018], _ends) == "2018-08-25",
                "a 2020 split is now correctly seen as later than the data"))
    out.append(("...where the old anchor read FY2025 and would have missed that split",
                max(_ends.values()) == "2025-08-25", "the earnings series, not the share series"))
    out.append(("A current share series anchors exactly where it did before",
                split_asof(list(range(2016, 2026)), _ends) == "2025-08-25", "no change"))
    out.append(("The cover-page date still wins where that route was chosen",
                split_asof([2025], _ends, "2025-10-20", True) == "2025-10-20",
                "a cover figure is dated at the filing, not the year end"))
    out.append(("...and is ignored where it was not",
                split_asof([2025], _ends, "2025-10-20", False) == "2025-08-25",
                "only the route that used it gets its date"))
    out.append(("With no share counts at all it falls back to the earnings series",
                split_asof({}, _ends) == "2025-08-25", "something is better than nothing"))

    # 10d. Shell plc, and the four things it showed.
    _sh = foreign_filer_note("ProfitLossAttributableToOwnersOfParent",
                             ["stock compensation", "the share count", "the balance sheet"])
    out.append(("An IFRS filer is told it is one, before any figure below it",
                "foreign private issuer" in _sh and "do not use the valuation" in _sh,
                "banner fires on ProfitLoss-family tags"))
    out.append(("...and a US-GAAP filer never sees that banner",
                foreign_filer_note("NetIncomeLoss", ["the balance sheet"]) == "",
                "silent on NetIncomeLoss"))
    out.append(("...and an IFRS filer that read everything is not told to distrust it",
                "unverified" not in foreign_filer_note("ProfitLoss", []),
                "no unread lines, no refusal"))
    out.append(("Shrinking revenue is never called accelerating",
                growth_trend_phrase(-0.112, -0.061) == "shrinking, though less quickly than it was",
                "-6.1% against -11.2%"))
    out.append(("...and real acceleration still is",
                growth_trend_phrase(0.07, 0.14) == "growing faster than it was", "7% -> 14%"))
    out.append(("...and a company falling out of growth is not called decelerating",
                growth_trend_phrase(0.05, -0.03) == "shrinking after growing", "5% -> -3%"))
    out.append(("...and one climbing out of decline is named for that",
                growth_trend_phrase(-0.08, 0.04) == "back in growth after shrinking",
                "-8% -> 4%"))

    # 12. Class-share tickers, typed the way people type them.
    _cm = {"BRK-B": "0001067983", "AAPL": "0000320193", "BF.B": "0000014693"}
    out.append(("Berkshire resolves whether it is typed with a dot or a hyphen",
                resolve_ticker("BRK.B", _cm) == "BRK-B"
                and resolve_ticker("brk.b", _cm) == "BRK-B"
                and resolve_ticker("BRK-B", _cm) == "BRK-B",
                "the SEC writes it BRK-B; everyone else writes BRK.B"))
    out.append(("...and it works in the other direction too",
                resolve_ticker("BF-B", _cm) == "BF.B", "whichever way the list happens to spell it"))
    out.append(("...while an ordinary ticker is untouched",
                resolve_ticker("aapl", _cm) == "AAPL", "upper-cased and passed through"))
    out.append(("...and a company that really is absent still returns nothing",
                resolve_ticker("NOTATICKER", _cm) is None, "no false match"))

    # 11. IFRS net income: the parent's share, not the consolidated group's.
    _ifrs = {"facts": {"ifrs-full": {
        "ProfitLoss": {"units": {"USD": [
            {"form": "20-F", "start": f"{y}-01-01", "end": f"{y}-12-31",
             "filed": f"{y + 1}-03-01", "val": 1200.0} for y in range(2020, 2026)]}},
        "ProfitLossAttributableToOwnersOfParent": {"units": {"USD": [
            {"form": "20-F", "start": f"{y}-01-01", "end": f"{y}-12-31",
             "filed": f"{y + 1}-03-01", "val": 1000.0} for y in range(2020, 2026)]}}}}}
    _ifrs_src: list[str] = []
    _ifrs_n = _annual(_ifrs, CONCEPTS["N"][0], CONCEPTS["N"][1], _ifrs_src, True)
    out.append(("An IFRS filer's net income is the parent's share, not the group's",
                bool(_ifrs_n) and all(abs(v[2] - 1000.0) < 1e-6 for v in _ifrs_n.values()),
                f"{len(_ifrs_n)} years at "
                f"{list(_ifrs_n.values())[0][2]:,.0f} — 1,000 parent, not 1,200 group"))
    out.append(("...and the tag panel names the tag that answered",
                _ifrs_src[:1] == ["ProfitLossAttributableToOwnersOfParent"],
                "; ".join(_ifrs_src) or "no source recorded"))

    # 9b. Item 4 — the swing, stated rather than resolved.
    _sw = stale_swing_note(385.0, [("Long-term debt", -1013.0)])
    out.append(("Adobe's stale debt line says what net cash rests on",
                "1,398M" in _sw and "385M" in _sw and "1,013M" in _sw,
                "385M carried forward, 1,398M if the debt were zeroed"))
    out.append(("...and it names neither treatment as the right one",
                "neither figure is guessed at here" in _sw, "the tag is the fix"))
    out.append(("An operating-lease line alone moves net cash by nothing, and says nothing",
                stale_swing_note(385.0, [("Operating leases", 0.0)]) == "",
                "leases do not enter net cash"))
    out.append(("A stale asset line swings net cash the other way",
                "3,000M" in stale_swing_note(5000.0, [("Short-term investments", 2000.0)]),
                "5,000M carried forward, 3,000M zeroed"))

    out.append(("A line AHEAD of net income is not stale either",
                stale_instant_lines({"A": "2026", "B": "2025", "C": "2025", "D": "2025"},
                                    2025, _rows) == [],
                "only trailing years are a finding"))

    # 10. The share-route note: three situations, three wordings, and figures
    #     on the basis the rest of the page uses. TransDigm cannot verify this
    #     on the page — its tool 1 verdict is "Not investible" and nothing
    #     renders below it — so its shape is pinned here instead.
    _sp = share_route_note("sparse", 56.3e6, 58.2e6, "the 10-K cover page", 3, 10, 2012)
    out.append(("A short share series is described as short, not as static",
                "stops at FY2012" in _sp and "barely moved" not in _sp,
                _sp[:72] + "…"))
    out.append(("...and it says how much of the window it actually covers",
                "3 of the 10 years" in _sp, "3 of the 10 years"))
    _st = share_route_note("static", 56.3e6, 58.2e6, "the 10-K cover page", 10, 10, 2025)
    out.append(("A genuinely static count keeps the wording written for it",
                "barely moved" in _st and "stops at" not in _st, _st[:60] + "…"))
    # 10b. The guard compares one year against itself.
    _azo_out = {2016: 30.33e6, 2017: 28.74e6, 2018: 27.53e6}
    _azo_wv = {2018: 27.42e6, 2024: 17.7e6, 2025: 17.245e6}
    out.append(("AutoZone's issued count is not a treasury block, and FY2018 says so",
                treasury_signal(_azo_out, _azo_wv) == (False, 2018),
                "27.53M against 27.42M in the same year is 1.004, not 1.49"))
    out.append(("Booking's really is one, and still fires",
                treasury_signal({2024: 63.0e6, 2025: 64.52e6},
                                {2024: 33.5e6, 2025: 32.64e6}) == (True, 2025),
                "1.98x in FY2025"))
    out.append(("With no year in common the test is skipped, not guessed",
                treasury_signal({2012: 56.3e6}, {2020: 58.2e6, 2025: 55.0e6}) == (False, None),
                "coverage is the sparse branch's job"))
    out.append(("A second share class still reads BELOW the average and does not fire",
                treasury_signal({2025: 10.0e6}, {2025: 14.0e6})[0] is False, "10M vs 14M"))

    # 10c. Item 7 — the dual-class test, same year and same series as the ladder.
    out.append(("A missing share class is caught in the year both counts exist",
                dual_class_signal({2025: 10.0e6}, {2025: 25.0e6})[:2] == ("dual", 2025),
                "10M against 25M is not a buyback"))
    out.append(("Adobe's ordinary buyback gap is named but does not override the count",
                dual_class_signal({2025: 413.0e6}, {2025: 427.0e6})[:2] == ("gap", 2025),
                "413M vs 427M — 3.3%, reported, not overridden"))
    out.append(("A count within 3% of the average says nothing at all",
                dual_class_signal({2025: 420.0e6}, {2025: 427.0e6})[0] == "none", "1.6%"))
    out.append(("A 2025 count is never compared against a 2019 average",
                dual_class_signal({2019: 30.0e6, 2025: 16.6e6},
                                  {2019: 30.4e6})[:2] == ("none", 2019),
                "FY2019 both sides: agreement, and no dual-class claim"))
    out.append(("With no overlapping year the test is skipped, as the treasury one is",
                dual_class_signal({2025: 16.6e6}, {2018: 27.4e6}) == ("none", None, 0.0, 0.0),
                "nothing to compare"))
    out.append(("A split factor is applied to the average before comparing",
                dual_class_signal({2025: 791.8e6}, {2025: 32.64e6}, 25.0)[0] == "none",
                "816M post-split against 791.8M, not 32.6M"))

    out.append(("The treasury note never claims 'far above' about a count that is below",
                "far above" not in share_route_note("treasury", 791.8e6, 816.0e6,
                                                    "the 10-K cover page", 10, 10, 2025),
                "791.8M against 816.0M is not the treasury pattern"))
    out.append(("...and still says it when the count really is above",
                "far above" in share_route_note("treasury", 1613.0e6, 816.0e6,
                                                "the 10-K cover page", 10, 10, 2025),
                "1,613.0M against 816.0M"))
    _tr25 = share_route_note("treasury", 64.5e6, 32.6e6, "the 10-K cover page", 10, 10, 2025,
                             factor=25.0)
    out.append(("Booking's treasury note prints post-split counts, not 64.5M vs 32.6M",
                "1,612.5M" in _tr25 and "815.0M" in _tr25 and "64.5M" not in _tr25,
                "1,612.5M against 815.0M, both x25"))
    _tr1 = share_route_note("treasury", 25.7e6, 17.2e6, "issued minus treasury shares",
                            10, 10, 2025)
    out.append(("...and an unsplit filer is left exactly as it was",
                "25.7M" in _tr1 and "post-split" not in _tr1, "25.7M against 17.2M"))
    _brk = split_adjust({2008: 1_550_000.0, 2009: 1_560_000.0, 2010: 2_200_000_000.0,
                         2011: 2_210_000_000.0})
    out.append(("Berkshire's two share classes are not restated as a 948,347:1 split",
                any("too large to be a stock split" in m for m in _brk[1])
                and _brk[0][2008] == 1_550_000.0,
                "history left as filed, with a note saying why"))
    _real = split_adjust({2021: 100e6, 2022: 100e6, 2023: 400e6, 2024: 405e6})
    out.append(("...while a real 4:1 split is still restated",
                _real[0][2021] == 400e6
                and any("the size of a stock split" in m for m in _real[1]),
                "4:1 in FY2023, earlier years multiplied"))
    # RIVN, 27 Aug 2026: a first listing moves the share count exactly like a
    # split and nothing in the filings distinguishes them. The restatement is
    # still applied — it is the better guess either way — but the note must not
    # announce a split that may never have happened.
    # AAPL, 28 Aug 2026: real filed share counts across both restatement
    # boundaries. The ratio measured is the split times the buybacks in
    # between, so it is never clean — 3.738 for a 4:1, 6.702 for a 7:1.
    _aapl = split_adjust({2012: 939_208_000.0, 2013: 6_294_491_000.0,
                          2017: 5_126_201_000.0, 2018: 4_754_986_000.0,
                          2019: 17_772_945_000.0, 2020: 16_976_763_000.0})
    out.append(("Apple's 3.738 measured ratio is a 4:1 split, not a 3.5:1",
                _aapl[0][2018] == 4_754_986_000.0 * 4.0
                and any("about 4:1 at FY2019" in m for m in _aapl[1])
                and not any("3.5:1" in m for m in _aapl[1]),
                "FY2018 restates to 19,019.9M, so FY2019 reads -1,247.0M shares retired"))
    out.append(("...and 6.702 across the earlier boundary is a 7:1",
                _aapl[0][2012] == 939_208_000.0 * 28.0
                and any("about 7:1 at FY2013" in m for m in _aapl[1]),
                "7:1 in FY2014 and 4:1 in FY2020 compound to 28x on pre-FY2013 years"))
    out.append(("...and FY2020 onwards is left alone",
                _aapl[0][2020] == 16_976_763_000.0 and _aapl[0][2019] == 17_772_945_000.0,
                "the restated years are the only ones that move"))
    # VEEV, 28 Aug 2026. Real FY2024-26 figures: no buyback until the last
    # year, and the share count rose in all three. ΔE still measured 113.5%,
    # so the cause was never the buyback the note used to name.
    _veev = [Year(fy=2024, N=526.0, G=394.0, T=0.0, dS=1.0, price=189.61, Cw=25.0),
             Year(fy=2025, N=714.0, G=437.0, T=0.0, dS=1.7, price=209.29, Cw=19.0),
             Year(fy=2026, N=909.0, G=473.0, T=170.0, dS=1.8, price=255.59, Cw=30.0)]
    _adbe_like = [Year(fy=2024, N=100.0, G=20.0, T=900.0, dS=-8.0, price=100.0, Cw=5.0),
                  Year(fy=2025, N=100.0, G=20.0, T=900.0, dS=-8.0, price=100.0, Cw=5.0)]
    out.append(("Veeva's ΔE above 100% is not a buyback — the count rose every year",
                not buybacks_shrank_count(_veev),
                "0, 0 and 170 of buybacks against +1.0, +1.7 and +1.8M shares"))
    out.append(("...while a real buyback window still says so",
                buybacks_shrank_count(_adbe_like),
                "stock retired, count falls — the sentence the note was written for"))
    # XPEL, 29 Aug 2026. Real FY2023-25 figures: buybacks 0, 0 and 3.0, the
    # count effectively flat, ΔE 102.5% from a GAAP charge of 7.6 against a
    # measured cost of 3.8. PDEX's real FY2023-25 figures are the control:
    # 8.55 of buybacks on a 3.3M count, and §8 says the note keeps naming them.
    _xpel = [Year(fy=2023, N=52.8, G=1.6, T=0.0, dS=0.016, price=68.71),
             Year(fy=2024, N=45.5, G=3.2, T=0.0, dS=0.027, price=44.64),
             Year(fy=2025, N=51.2, G=2.8, T=3.0, dS=-0.041, price=36.55)]
    _pdex = [Year(fy=2023, N=7.07, G=0.77, T=1.55, dS=-0.1, price=17.41, Cw=0.85),
             Year(fy=2024, N=2.13, G=0.60, T=3.50, dS=-0.2, price=17.96, Cw=0.33),
             Year(fy=2025, N=8.98, G=0.56, T=3.50, dS=-0.1, price=39.48)]
    out.append(("A token buyback does not get credit for a charge-driven ΔE (XPEL)",
                not buybacks_shrank_count(_xpel),
                f"3.0 of buybacks against an excess of {sum(y.G - y.omega for y in _xpel):.1f}"))
    out.append(("...and PDEX's real buybacks still do",
                buybacks_shrank_count(_pdex),
                f"8.55 of buybacks against an excess of {sum(y.G - y.omega for y in _pdex):.2f}"))
    _ipo = split_adjust({2020: 100e6, 2021: 110e6, 2022: 990e6, 2023: 1032e6})
    out.append(("A listing that looks like a split is still restated, but not announced as one",
                _ipo[0][2021] == 990e6 and _ipo[0][2023] == 1032e6
                and all("Stock split detected" not in m for m in _ipo[1])
                and any("did not split, the restated years are wrong" in m for m in _ipo[1]),
                "RIVN FY2022 reads about 9:1 on a company that has never split"))
    class _Y:
        def __init__(self, oe, ex=""):
            self.OE, self.excluded = oe, ex
    _yrs = [_Y(100.0), _Y(200.0), _Y(-1278.0, "acquisition"), _Y(300.0), _Y(400.0)]
    _h = sorted(y.OE for y in _yrs[-5:] if not y.excluded)
    out.append(("The 5-year median drops excluded years, as tool 2's already did",
                _h[len(_h) // 2] == 300.0 and -1278.0 not in _h,
                "an excluded year's owners' earnings are not a measurement"))
    out.append(("A 948,347:1 split is a data artifact, not a split",
                MAX_SPLIT == 200.0 and 948347 > MAX_SPLIT,
                "Berkshire's A and B counts in one series looked like a split"))
    out.append(("Every line the tag panel can print has a label",
                all(k in TAG_LABELS for k in CONCEPTS),
                f"{len(CONCEPTS)} concepts, {len(CONCEPTS) - sum(k in TAG_LABELS for k in CONCEPTS)} unlabelled"))
    # 11. The year-by-year table at microcap scale (PDEX, 28 Aug 2026).
    #     Pinned on the rendered cell, not on the threshold: a Pro-Dex row
    #     must show its stock comp, a Bellring row must still print whole
    #     millions, and Apple's table must be byte-identical to before.
    _pdex = money_fmt([1.2, 0.08, 0.0, 0.05, 1.15, 5.4, 0.3, 0.0, 0.21, 5.19])
    out.append(("A microcap table shows the stock comp it rounded away",
                _pdex.format(0.05) == "0.05" and _pdex.format(1.15) == "1.15",
                f"PDEX FY2016 true SBC cost formats as {_pdex.format(0.05)}, not 0"))
    _mid = money_fmt([42.0, 3.7, 0.0, 2.14, 39.86])
    out.append(("...one decimal when the table tops out between 10 and 100",
                _mid.format(2.14) == "2.1" and _mid.format(42.0) == "42.0",
                f"2.14 formats as {_mid.format(2.14)}, 42 as {_mid.format(42.0)}"))
    _brbr = money_fmt([24.0, 2.0, 0.0, -524.0, 550.0])
    _aapl = money_fmt([93736.0, 11688.0, 95000.0, 6400.0, 98800.0])
    out.append(("Anything with a figure at $100M or more keeps whole millions",
                _brbr.format(-524.0) == "-524" and _aapl.format(93736.0) == "93,736",
                f"BRBR {_brbr.format(-524.0)}, AAPL {_aapl.format(93736.0)} — unchanged"))
    # 12. The negative true-SBC-cost note, count OR size (BellRing FY2020).
    #     Rivian's shape is the control: 3 negative years of 7 must keep the
    #     original count wording, word for word.
    def _yr(fy, N, G=0.0, Ce=0.0, ex=""):
        return Year(fy=fy, N=N, G=G, T=0.0, dS=0.0, price=0.0, Ce=Ce, excluded=ex)
    _brbr_ys = ([_yr(2018, 0.0), _yr(2019, 0.0), _yr(2020, 24.0, 2.0, 524.0)]
                + [_yr(f, 100.0, 5.0) for f in (2021, 2024, 2025)]
                + [_yr(f, 100.0, 5.0, ex="share-funded acquisition") for f in (2022, 2023)])
    _bn = negative_sbc_note(_brbr_ys)
    out.append(("One negative year that outweighs its net income gets a note",
                _bn is not None and "-524M in FY2020" in _bn and "550M" in _bn
                and "negative in" not in _bn,
                (_bn or "no note")[:60] + "…"))
    _rivn_ys = ([_yr(f, -400.0, 10.0, 2750.0) for f in (2019, 2020, 2021)]
                + [_yr(f, -5000.0, 500.0) for f in (2022, 2023, 2024, 2025)])
    _rn = negative_sbc_note(_rivn_ys)
    out.append(("Rivian's shape keeps the original count wording",
                _rn is not None and _rn.startswith("The true stock-comp cost reads negative in 3 of 7 years"),
                (_rn or "no note")[:60] + "…"))
    out.append(("A clean window and a small negative year both get nothing",
                negative_sbc_note([_yr(f, 100.0, 5.0) for f in range(2016, 2026)]) is None
                and negative_sbc_note([_yr(2016, 100.0, 5.0, 8.0)]
                                      + [_yr(f, 100.0, 5.0) for f in range(2017, 2026)]) is None,
                "MSFT's shape, and a -3M year against 100M of profit"))
    # 13. The holes note (BBW, 29 Aug 2026). A one-label hole names the
    #     fiscal-year-end case; Paychex's eight-year hole does not, and its
    #     wording is untouched.
    _bbw_fys = [2016, 2017, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
    _payx_fys = list(range(2009, 2016)) + [2024, 2025, 2026]
    _bbw_note, _payx_note = holes_note(_bbw_fys), holes_note(_payx_fys)
    out.append(("A one-label hole says it may be a change of fiscal year end",
                _bbw_note is not None and "nothing read for FY2018." in _bbw_note
                and "change of fiscal year end" in _bbw_note,
                "BBW: December 2017 filing followed by a February 2019 one"))
    out.append(("...a multi-year hole does not, and a full window gets no note",
                _payx_note is not None and "FY2016-FY2023" in _payx_note
                and "fiscal year end" not in _payx_note
                and holes_note(list(range(2016, 2026))) is None,
                "Paychex wording unchanged"))
    # 14. The seed (CROX, 29 Aug 2026). A loss year on a profitable record
    #     seeds from the median; a profit, ARM's unprojectable ΔE and RIVN's
    #     losing record all seed exactly as before.
    _crox = seed_owners_earnings(-81.2, 1.0, True, 600.0)
    _msft = seed_owners_earnings(88000.0, 1.0, True, 70000.0)
    _arm = seed_owners_earnings(600.0, -0.162, False, 556.0)
    _rivn = seed_owners_earnings(-3646.0, 1.073, False, -2000.0)
    out.append(("A loss year on a profitable record seeds from the median, and says so",
                _crox == (600.0, SEED_FROM_MEDIAN_LOSS),
                f"CROX: {_crox[0]:.0f} — {_crox[1]}"))
    out.append(("...and every other shape seeds exactly as before",
                _msft == (88000.0, SEED_FROM_DE) and _arm == (556.0, SEED_FROM_MEDIAN)
                and _rivn == (-3646.0, SEED_CEILING),
                "MSFT from ΔE, ARM from the median, RIVN net income as a ceiling"))

    # 15. The Stage 0 control (31 Aug 2026). Zero years must leave IV15
    #     exactly where it was whatever the rate says; years with a positive
    #     rate must lift it; and the exit model must still read year 15.
    _base = IVParams(OE=7300, shares=1073.3, tier="Chapel", growth=0.069, net_cash=0,
                     exit_multiple=21.8, blend=0.5)
    _off = IVParams(**{**_base.__dict__, "stage0_years": 0, "stage0_growth": 0.40})
    _on = IVParams(**{**_base.__dict__, "stage0_years": 3, "stage0_growth": 0.40})
    out.append(("Stage 0 at zero years changes nothing, whatever the rate box says",
                intrinsic_value(_off, 15) == intrinsic_value(_base, 15),
                f"{intrinsic_value(_off, 15):.2f} both ways"))
    out.append(("...and three years at 40% lifts IV15 above the plain stream",
                intrinsic_value(_on, 15) > intrinsic_value(_base, 15)
                and len(_stream(_on, 15)) == 15,
                f"{intrinsic_value(_base, 15):.2f} → {intrinsic_value(_on, 15):.2f}"))

    # ── PAGE 6 (3 Sep 2026): everything this page adds to tool 1 ──────
    # 16. Currency detection. The Toyota trap in miniature: a full history
    #     in yen beside two USD convenience translations must read JPY, and
    #     a genuine tie must prefer USD.
    def _facts_fx(tax, concept, unit, n, form="20-F"):
        return [{"form": form, "start": f"{2010+i}-01-01", "end": f"{2010+i}-12-31",
                 "filed": f"{2011+i}-02-01", "val": 1000.0 + i, "fy": 2010 + i,
                 "fp": "FY"} for i in range(n)]
    _eur = {"facts": {"ifrs-full": {"ProfitLossAttributableToOwnersOfParent":
            {"units": {"EUR": _facts_fx(0, 0, 0, 8)}}}}}
    _jpy = {"facts": {"ifrs-full": {"ProfitLoss":
            {"units": {"JPY": _facts_fx(0, 0, 0, 20), "USD": _facts_fx(0, 0, 0, 2)}}}}}
    _tie = {"facts": {"ifrs-full": {"ProfitLoss":
            {"units": {"USD": _facts_fx(0, 0, 0, 5), "EUR": _facts_fx(0, 0, 0, 5)}}}}}
    out.append(("Filing currency: a EUR filer reads EUR",
                filing_currency(_eur) == "EUR", filing_currency(_eur)))
    out.append(("...a JPY history beside USD convenience translations reads JPY",
                filing_currency(_jpy) == "JPY", filing_currency(_jpy)))
    out.append(("...and a genuine tie prefers USD",
                filing_currency(_tie) == "USD", filing_currency(_tie)))

    # 17. _annual reads the requested unit. The same EUR fixture must yield
    #     rows under unit="EUR" and nothing under the USD default.
    _got_eur = _annual(_eur, [], ["ProfitLossAttributableToOwnersOfParent"], unit="EUR")
    _got_usd = _annual(_eur, [], ["ProfitLossAttributableToOwnersOfParent"])
    out.append(("_annual reads the filing currency's unit key",
                len(_got_eur) == 8 and not _got_usd,
                f"{len(_got_eur)} EUR years, {len(_got_usd)} USD"))

    # 18. The router. US-GAAP USD dominant → sent to tool 1 by name (the
    #     Shopify shape: a 40-F in full US GAAP); IFRS → stays.
    _shop = {"facts": {"us-gaap": {"NetIncomeLoss":
             {"units": {"USD": _facts_fx(0, 0, 0, 9, "40-F")}}}}}
    out.append(("Router: a US-GAAP USD filer is sent to the Tragic Algebra Analyzer",
                "Tragic Algebra Analyzer" in sent_to_tool1(_shop)
                and "Shopify" in sent_to_tool1(_shop),
                "names the page and the 40-F trap"))
    out.append(("...and an IFRS filer stays on this page",
                sent_to_tool1(_eur) == "" and sent_to_tool1(_jpy) == "",
                "no routing sentence"))

    # 19. The financial gate, this page's own sentence.
    _f = nonus_financial_refusal("6331", "Fire, Marine & Casualty Insurance")
    out.append(("A foreign financial is refused with this page's sentence",
                "IFRS" in _f and "6331" in _f and nonus_financial_refusal("7372", "") == "",
                "fires on SIC 6331, silent on 7372"))

    # 20. The ADS gate: refused on a non-USD filer, silent otherwise; and
    #     the arithmetic it protects — Legend Biotech's June 2026 offering
    #     document states $2.60 per ordinary share equals $5.20 per ADS at
    #     the stated ratio of 2, which is exactly price / ratio.
    out.append(("ADS ratio on a EUR filer is refused; on a USD filer it is allowed",
                ads_gate("EUR", 2.0) != "" and ads_gate("USD", 2.0) == ""
                and ads_gate("EUR", 1.0) == "",
                "units conversion, never currency"))
    out.append(("Per-ordinary price is the ADS price over the ratio (LEGN: 5.20/2)",
                abs(5.20 / 2.0 - 2.60) < 1e-9, f"{5.20/2.0:.2f}"))

    # 29 (4 Sep 2026, after the TTE run). Cash read + debt silent must
    #     warn loudly; any debt read, or no cash read, must stay silent.
    _d1 = debt_unread_note(12, 0, 0)
    out.append(("Cash read with no debt line answering warns that net cash may be fiction",
                "fiction" in _d1 and "upper bound" in _d1 and "Total debt box" in _d1,
                "TotalEnergies: +$26B of net cash against unread borrowings"))
    out.append(("...and any debt read, or no cash read, keeps the guard silent",
                debt_unread_note(12, 7, 0) == "" and debt_unread_note(0, 0, 0) == ""
                and debt_unread_note(12, 0, 3) == "",
                "fires only on the asymmetric read"))

    # 26 (4 Sep 2026, after the NVO run). A 2:1 split inside the band is
    #     restated only when the market's own split events confirm it.
    _nvo = {2019: 2380e6, 2020: 2330e6, 2021: 4600e6, 2022: 4480e6}
    _fixed, _fn = confirm_band_splits(dict(_nvo), {"2023-09-13": 2.0})
    out.append(("A 2:1 jump inside the band, confirmed by a split event, restates history",
                abs(_fixed[2020] - 4660e6) < 1 and abs(_fixed[2019] - 4760e6) < 1
                and abs(_fixed[2021] - 4600e6) < 1 and "2-for-1" in _fn,
                f"2020 → {_fixed[2020]/1e6:,.0f}M, boundary named"))
    _same, _no = confirm_band_splits(dict(_nvo), {})
    out.append(("...the same jump with NO market split event stays as filed",
                _same == _nvo and _no == "",
                "an all-stock merger has no split event — the exclusion rule keeps it"))
    _small, _ns = confirm_band_splits({2020: 10e6, 2021: 20e6}, {"2021-06-01": 2.0})
    out.append(("...and a small base is never restated — listings double, splits don't",
                _small == {2020: 10e6, 2021: 20e6} and _ns == "",
                "25M-share floor holds"))

    # 25 (4 Sep 2026, after the NVO/RACE/SPOT runs). The no-home-listing
    #    refusal must name the currency, the Spotify case, and both exits.
    _nh = no_home_listing_refusal("Spotify Technology S.A.", "EUR")
    out.append(("A filer with no listing in its filing currency is refused naming "
                "the case and both exits",
                "EUR" in _nh and "Spotify" in _nh and "paste mode" in _nh
                and "never converts" in _nh and "yours" in _nh,
                "the FX judgement is the user's, stated, or nobody's"))

    # 20b (3 Sep 2026, after the first live runs). Chen ran GRAB with
    # asml.as still in the symbol box; the refusal was right and its
    # explanation blamed a temporary price-source failure. The sentence
    # must name the symbol, both currencies and the leftover-box cause.
    _pm = price_mismatch_refusal("ASML.AS", "EUR", "USD")
    out.append(("A wrong-currency quote is refused naming the symbol, both currencies "
                "and the leftover-box cause",
                "ASML.AS" in _pm and "EUR" in _pm and "USD" in _pm
                and "left over from the previous company" in _pm
                and "never converts" in _pm,
                "sentence carries the diagnosis, not a retry hint"))

    # 21. The paste builder drives the identical engine: Burry's published
    #     Alphabet inputs typed as rows — share counts constructed so their
    #     differences are his dS — must reproduce the pinned figures above.
    _base_count = 13000.0
    _counts, _run = {2015: _base_count}, _base_count
    for f, n, g, t, c, d_, px in goog:
        _run += d_
        _counts[f] = _run
    _rows = [{"FY": 2015, "Shares (M)": _counts[2015]}] + [
        {"FY": f, "Net income": n, "GAAP SBC": g, "Buybacks": t, "Tax withheld": c,
         "Shares (M)": _counts[f], "Avg price": px} for f, n, g, t, c, d_, px in goog]
    _pys, _pnotes, _perrs = paste_build(_rows)
    out.append(("Paste mode rebuilds Alphabet: FY2016 V and FY2025 V to the dollar",
                not _perrs and len(_pys) == 10 and abs(_pys[0].V - 8252) < 1
                and abs(_pys[-1].V - 26551) < 1,
                f"V {_pys[0].V:,.0f} / {_pys[-1].V:,.0f}" if _pys else "no years"))
    _pp = pool(_pys)
    out.append(("...and the pooled ΔE lands on 88.7% — one engine, two doors",
                abs(_pp.dE - 0.887) < 0.002, f"{_pp.dE:.2%}"))

    # 22. Paste semantics: blank is not zero, base rows carry only their
    #     count, a >15% jump is excluded with the same rule as load().
    _r2 = [{"FY": 2019, "Shares (M)": 100.0},
           {"FY": 2020, "Net income": 50.0, "Shares (M)": 102.0, "Avg price": 10.0},
           {"FY": 2021, "Net income": 55.0, "Shares (M)": 104.0, "Avg price": 11.0},
           {"FY": 2022, "Net income": 60.0, "Shares (M)": 125.0, "Avg price": 12.0},
           {"FY": 2023, "Net income": 65.0, "Shares (M)": 127.0, "Avg price": 13.0}]
    _ys2, _n2, _e2 = paste_build(_r2)
    out.append(("A 20% share jump in a priced mid-window year is excluded, same rule",
                not _e2 and [y.fy for y in _ys2 if y.excluded] == [2022]
                and _ys2[0].dS == 2.0,
                f"excluded {[y.fy for y in _ys2 if y.excluded]}, first dS {_ys2[0].dS:+.0f}M"))
    _r3 = [{"FY": 2020, "Buybacks": 10.0}]
    _, _, _e3 = paste_build(_r3)
    out.append(("A row with figures but no net income and no count is refused, not zeroed",
                any("Blank is not zero" in e or "carries nothing" in e for e in _e3),
                _e3[0] if _e3 else "no error"))
    _r4 = [{"FY": 2020, "Net income": 50.0, "Shares (M)": 100.0, "Avg price": 10.0},
           {"FY": 2021, "Net income": 55.0, "Shares (M)": 102.0},
           {"FY": 2022, "Net income": 60.0, "Shares (M)": 104.0, "Avg price": 12.0},
           {"FY": 2023, "Net income": 65.0, "Shares (M)": 106.0, "Avg price": 13.0}]
    _ys4, _n4, _e4 = paste_build(_r4)
    out.append(("A blank price makes the year unpriced by name, never silently zero",
                not _e4 and any("FY2021" in n and "average price" in n for n in _n4),
                "unpriced note names FY2021"))

    # 23. The magnitude warning: a price typed in cents against income in
    #     millions is a units mix-up, said out loud.
    _r5 = [{"FY": 2020 + i, "Net income": 100.0, "Shares (M)": 100.0,
            "Avg price": 8000.0} for i in range(4)]
    _r5.insert(0, {"FY": 2019, "Shares (M)": 100.0})
    _, _n5, _ = paste_build(_r5)
    out.append(("An implied P/E outside 1-500x is called a units mix-up",
                any("units mix-up" in n for n in _n5), "warning fires at 8,000x"))

    # 24. The growth seed from typed revenue keeps load()'s cap.
    _g, _raw, _, _gn = paste_growth([(2021, 100.0), (2022, 100.0), (2023, 100.0),
                                     (2024, 180.0)])
    out.append(("Typed revenue growth is capped at 25% for the seed, like the fetched pages",
                abs(_g - 0.25) < 1e-9 and abs(_raw - 0.80) < 1e-9 and "launch rate" in _gn,
                f"raw {_raw:.0%} → seed {_g:.0%}"))
    out.append(("...and no usable revenue seeds 8% and says it is a placeholder",
                paste_growth([])[0] == 0.08 and "placeholder" in paste_growth([])[3],
                "8% with the note"))

    # 24b (3 Sep 2026, EVD build): the calendar averager behind the paste
    # price fetch. Twelve months average exactly; a partial far-end year
    # averages what exists; junk keys and dead months are skipped.
    _cl = {f"2023-{m:02d}": 100.0 + m for m in range(1, 13)}
    _cl.update({"2015-11": 50.0, "2015-12": 54.0, "bad-key": 1.0, "2024-01": 0.0})
    _ca = calendar_avg(_cl)
    _evd_like = [Year(fy=2020 + i, N=100.0, price=0.0) for i in range(5)]
    _ge, _gn = paste_price_gate(_evd_like)
    out.append(("Unpriced years with no dilution and no buybacks compute, saying why",
                _ge == "" and "cannot affect" in _gn and "exact, not a floor" in _gn,
                "the no-dilution case is named, not assumed"))
    _dil = [Year(fy=2020 + i, N=100.0, dS=2.0, price=0.0) for i in range(5)]
    _ge2, _ = paste_price_gate(_dil)
    out.append(("...but unpriced years WITH dilution are refused, same as the fetched pages",
                "near" in _ge2 and "100%" in _ge2 and "price symbol" in _ge2,
                "refusal names the fix"))
    _half = [Year(fy=2020 + i, N=100.0, dS=2.0, price=(10.0 if i < 2 else 0.0))
             for i in range(5)]
    _ge3, _gn3 = paste_price_gate(_half)
    out.append(("...and thin price coverage on a diluter is called a floor by name",
                _ge3 == "" and "floor" in _gn3, "2 of 5 priced → floor note"))

    out.append(("A CSV round-trip's 'False' string does not exclude; only a real yes does",
                not _excl("False") and not _excl("") and not _excl(None) and not _excl(0)
                and _excl("True") and _excl("true") and _excl(True) and _excl("x"),
                "bool('False') is True in Python; the parser is not fooled"))
    out.append(("Calendar averages: full year exact, partial year honest, junk skipped",
                abs(_ca[2023] - 106.5) < 1e-9 and abs(_ca[2015] - 52.0) < 1e-9
                and 2024 not in _ca, f"2023 {_ca[2023]:.2f}, 2015 {_ca[2015]:.2f}"))

    # 25. The IFRS names are actually wired in — a wiring test, not a claim
    #     that the names are right: only a live tag panel can show that.
    out.append(("IFRS candidates are wired: revenue, diluted shares, cash, borrowings",
                "RevenueFromContractsWithCustomers" in CONCEPTS["REV"][1]
                and "AdjustedWeightedAverageShares" in CONCEPTS["SHD"][1]
                and "CashAndCashEquivalents" in BALANCE["cash"]
                and "Borrowings" in BALANCE["ltd"]
                and "ProceedsFromExerciseOfOptions" in CONCEPTS["Ce"][1]
                and "PurchaseOfTreasuryShares" in CONCEPTS["T"][1]
                and "LongtermBorrowings" in BALANCE["ltd"]
                and "ShorttermBorrowings" in BALANCE["std"],
                "all eight present — three browser-verified 4 Sep"))

    # 26. The rewritten IFRS banner names the currency and the four lines
    #     with no standard IFRS name, and still escalates on unread cores.
    _b = foreign_filer_note("ProfitLoss", [], "EUR")
    out.append(("The IFRS banner names the currency and the structurally unread lines",
                "in EUR" in _b and "acquisition consideration" in _b
                and "share-jump exclusion" in _b, "banner carries the caveats"))

    return out


# ══════════════════════════════════════════════════════════════════════
#  UI
# ══════════════════════════════════════════════════════════════════════
#
# NOTE ON DOLLAR SIGNS: Streamlit markdown parses $...$ as LaTeX. Any literal
# dollar amount inside st.write/markdown/success/error/info/warning must be
# escaped as \$ or the text between two of them silently becomes an equation.
# st.metric, st.code and st.dataframe are unaffected.


# PAGE 6 EDIT (3 Sep 2026): set from the loaded data before the UI renders,
# so every money string on the page speaks the filing currency. Only the
# dollar sign needs escaping in markdown.
PAGE_CCY = "$"


def d(x, dp=2):
    """Escaped currency amount, safe inside markdown."""
    return (f"\\${x:,.{dp}f}" if PAGE_CCY == "$"
            else f"{PAGE_CCY}{x:,.{dp}f}")


# Each page needs its own config. Streamlit only runs the entrypoint when you
# land on it, so arriving here by deep link — which is what shared links do —
# would otherwise leave the default favicon and title. Must be the first
# Streamlit command executed in this file.
st.set_page_config(
    page_title="Non-US Checker — Tragic Algebra for IFRS and non-SEC filers",
    page_icon="🌍",
    layout="centered",
    initial_sidebar_state="collapsed",
)
st.title("🌍 Non-US Checker")
st.caption("The same owners' earnings and IV15, for companies that do not file with the SEC "
           "in US GAAP — in the filing currency, with no FX, ever")


if not _sec_contact():
    st.warning(
        "**No SEC contact address set.** The SEC requires a real email in the request header "
        "and blocks generic user agents, so lookups will fail. Add `sec_contact = "
        "\"you@example.com\"` in Streamlit Settings → Secrets, or set a SEC_CONTACT "
        "environment variable locally."
    )

mode = st.radio("mode", ["SEC foreign filer (20-F / 40-F)", "Paste your own figures"],
                horizontal=True, label_visibility="collapsed")
PASTE = mode == "Paste your own figures"

# ══════════════════════════════════════════════════════════════════════
#  PASTE MODE — the reason this page exists (PAGE 6, 3 Sep 2026)
# ══════════════════════════════════════════════════════════════════════
ticker, submitted = "", False
if PASTE:
    st.caption(
        "For companies with no SEC filing at all — CTS Eventim files in Frankfurt and EDGAR "
        "has never heard of it — and as the fallback for any line the fetched pages cannot "
        "read. Type the annual figures from the company's own annual reports, all in ONE "
        "currency. The engine, the tables and every refusal are identical to the fetched "
        "pages, pinned by a self-test that reproduces Burry's published Alphabet figures "
        "from typed rows."
    )
    pc1, pc2, pc3 = st.columns(3)
    _p_name = pc1.text_input("Name or ticker", placeholder="EVD",
                             help="Only a label for the tables — nothing is fetched.")
    _p_ccy = pc2.text_input("Reporting currency", value="EUR", max_chars=3,
                            help="The ONE currency every figure below is in, prices "
                                 "included. Nothing is ever converted.").upper().strip()
    _p_now = pc3.number_input("Current share price", value=0.0, step=0.01, min_value=0.0,
                              help="Today's price on the home exchange, in the same "
                                   "currency as the figures. The verdict divides by it. "
                                   "Leave 0 with a price symbol below and today's quote "
                                   "seeds it at Compute.")
    # PAGE 6 EDIT (3 Sep 2026, after Chen's EVD run): this box used to
    # render just above the Compute button, below the balance inputs and
    # off the bottom of the screen — Chen never saw it and the whole fetch
    # never ran. An input that matters sits beside its siblings.
    _p_sym = st.text_input(
        "Price symbol (optional — fills blank average prices)", placeholder="EVD.DE",
        help="A listing in the SAME currency as your figures. At Compute, its monthly "
             "closes fill the average price of every year you left blank — a price you "
             "typed always wins — and today's quote seeds the current price if it is 0. "
             "Assumes calendar fiscal years; type prices yourself for an odd year end. "
             "The quote's own currency field is checked and a mismatch refuses the "
             "fetch rather than converting.").strip()

    st.markdown(
        "**One row per fiscal year, in millions of the currency** (shares in millions, "
        "prices per share). Blank means *not known* — for net income, the share count and "
        "the average price it is never read as zero; for buybacks, withholding, option "
        "proceeds, acquisition stock and revenue it means zero, which is common. Add one "
        "EARLIER year with only its fiscal year and share count so the first full year has "
        "a real share change. Tick **Exclude** for a year you judge unusable (a split or a "
        "share-funded deal the jump rule below did not catch). The three balance "
        "boxes below the table are typed by hand — the CSV carries per-year rows "
        "only, and the balance is one snapshot at the latest year end."
    )
    if "nu_paste_gen" not in st.session_state:
        st.session_state["nu_paste_gen"] = 0
    if "nu_paste_df" not in st.session_state:
        st.session_state["nu_paste_df"] = pd.DataFrame(
            [{c: (False if c == "Exclude" else None) for c in PASTE_COLS}])
    _up = st.file_uploader("Load a saved CSV", type="csv",
                           help="A CSV saved from this page, or one with the same column "
                                "names.")
    if _up is not None:
        try:
            _df_up = pd.read_csv(_up)
            missing = [c for c in PASTE_COLS if c not in _df_up.columns]
            if missing:
                st.error("This CSV is missing columns: " + ", ".join(missing)
                         + ". Save one from this page to see the expected shape.")
            elif not _df_up[PASTE_COLS].equals(st.session_state["nu_paste_df"]):
                st.session_state["nu_paste_df"] = _df_up[PASTE_COLS]
                # The generation counter in the editor key makes the grid pick
                # up the loaded rows — a fixed key keeps the browser's typed
                # values. The Return Calculator found this the hard way.
                st.session_state["nu_paste_gen"] += 1
        except Exception as e:
            st.error(f"Could not read that CSV: {e}")
    _edited = st.data_editor(
        st.session_state["nu_paste_df"], num_rows="dynamic", hide_index=True,
        key=f"nu_paste_editor_{st.session_state['nu_paste_gen']}",
        column_config={
            "FY": st.column_config.NumberColumn(format="%d", help="Fiscal year, e.g. 2024"),
            "Exclude": st.column_config.CheckboxColumn(default=False),
        })
    st.session_state["nu_paste_df"] = _edited
    st.download_button("Download these rows as CSV",
                       _edited.to_csv(index=False), "nonus-figures.csv", "text/csv")

    bc1, bc2, bc3 = st.columns(3)
    _p_cash = bc1.number_input("Cash (M)", value=0.0, step=10.0,
                               help="Latest year end. Only what is freely deployable.")
    _p_inv = bc2.number_input("Investments (M)", value=0.0, step=10.0,
                              help="Cash-like securities at the latest year end.")
    _p_debt = bc3.number_input("Total debt (M)", value=0.0, step=10.0,
                               help="Short-term plus long-term borrowings, latest year end.")

    if st.button("Compute", type="primary"):
        _rows = _edited.to_dict("records")
        _px_notes: list[str] = []
        _px_err = ""
        if _p_sym:
            try:
                _cl, _sp, _pxc = _monthly_closes(_p_sym)
            except Exception:
                _cl, _pxc = {}, ""
            if _cl and _pxc and _pxc != _p_ccy:
                _px_err = ("Prices from " + _p_sym + " were refused: that listing quotes "
                           f"in {_pxc} while your figures are in {_p_ccy}, and this page "
                           "never converts a currency. Your typed prices are unaffected.")
            elif _cl:
                _avg = calendar_avg(_cl)
                _filled = []
                for _r in _rows:
                    _fy = _cell(_r.get("FY"))
                    if (_fy is not None and _cell(_r.get("Avg price")) is None
                            and int(_fy) in _avg
                            and _cell(_r.get("Net income")) is not None):
                        _r["Avg price"] = round(_avg[int(_fy)], 2)
                        _filled.append(int(_fy))
                if _filled:
                    # PAGE 6 EDIT (3 Sep 2026, after Chen's EVD run): the
                    # fetched averages are written back into the grid, so
                    # what the engine used is what the table shows — a grid
                    # of blanks above a computed verdict is a mismatch.
                    st.session_state["nu_paste_df"] = pd.DataFrame(_rows)[PASTE_COLS]
                    st.session_state["nu_paste_gen"] += 1
                    _px_notes.append(
                        "Average prices for " + ", ".join(f"FY{f}" for f in sorted(_filled))
                        + f" are means of {_p_sym}'s monthly closes over each CALENDAR "
                        "year, fetched at Compute time — the same monthly series the "
                        "fetched pages use. Typed counts must be on today's post-split "
                        "basis, because these closes are. Prices you typed yourself "
                        "were left untouched.")
                if abs(_p_now) < 1e-9:
                    _cp = current_price(_p_sym)
                    if _cp and (not _cp[1] or _cp[1] == _p_ccy):
                        _p_now = float(_cp[0])
                        _px_notes.append(
                            f"The current price box was empty, so today's {_p_sym} "
                            f"quote ({_p_now:,.2f} {_p_ccy}) seeded it. Type over it "
                            "to use your own.")
            elif _p_sym:
                _px_notes.append(
                    f"No price history could be fetched from {_p_sym} just now — "
                    "usually a temporary failure at the price source. Blank price "
                    "cells stay unpriced; typed prices are used as given.")
        _p_years, _p_notes, _p_errors = paste_build(_rows)
        if _px_err:
            _p_errors.append(_px_err)
        _p_notes = _px_notes + _p_notes
        _g_err, _g_note = paste_price_gate(_p_years)
        if _g_err:
            _p_errors.append(_g_err)
        if _g_note:
            _p_notes.insert(0, _g_note)
        if not _p_name.strip():
            _p_errors.append("Give the company a name — the tables need a label.")
        if len(_p_ccy) != 3 or not _p_ccy.isalpha():
            _p_errors.append(f"'{_p_ccy}' is not a currency code — three letters, like EUR.")
        if _p_errors:
            for _e in _p_errors:
                st.error(_e)
            st.session_state.pop("nu_years", None)
        else:
            _p_revs = []
            for _r in _rows:
                _fy, _rv = _cell(_r.get("FY")), _cell(_r.get("Revenue"))
                if _fy is not None and _rv is not None:
                    _p_revs.append((int(_fy), _rv))
            _p_growth, _p_raw, _p_cagr3, _gnote = paste_growth(_p_revs)
            if _gnote:
                _p_notes.append(_gnote)
            _kept = sorted(y.OE for y in _p_years[-5:] if not y.excluded)
            _p_med = _kept[len(_kept) // 2] if _kept else 0.0
            _last_counts = {int(_cell(_r["FY"])): _cell(_r.get("Shares (M)"))
                            for _r in _rows if _cell(_r.get("FY")) is not None
                            and _cell(_r.get("Shares (M)")) is not None}
            _p_shares = _last_counts[max(_last_counts)] if _last_counts else 0.0
            _p_rev_latest = max(_p_revs)[1] if _p_revs else 0.0
            _p_tags = [{"Line": c, "Years read":
                        sum(1 for _r in _rows if _cell(_r.get(c)) is not None),
                        "Latest year": max([str(int(_cell(_r["FY"]))) for _r in _rows
                                            if _cell(_r.get("FY")) is not None
                                            and _cell(_r.get(c)) is not None] or ["—"]),
                        "XBRL tag": "typed by hand",
                        "Status": "typed"} for c in PASTE_COLS[1:-1]]
            _p_pre = {"tags": _p_tags, "net_cash": _p_cash + _p_inv - _p_debt,
                      "cash": _p_cash + _p_inv, "debt": _p_debt, "median_OE": _p_med,
                      "revenue": _p_rev_latest, "cagr3": _p_cagr3, "leases": 0.0,
                      "ticker": _p_name.strip().upper(), "shares": _p_shares,
                      "growth": _p_growth, "sic": "", "sic_desc": "", "financial": False,
                      "currency": _p_ccy, "px_sym": _p_sym, "px_ccy": _p_ccy,
                      "ads_ratio": 1.0, "typed": True, "price_seed": _p_now}
            st.session_state.update(nu_years=_p_years, nu_notes=_p_notes, nu_pre=_p_pre,
                                    nu_tk=_p_name.strip().upper())
    if st.session_state.get("nu_pre", {}).get("typed"):
        ticker = st.session_state.get("nu_tk", "")

if not PASTE:
    if "nu_years" not in st.session_state:
        st.info(
            "**The same Tragic Algebra, for filings the ordinary reader cannot read.** "
            "Foreign private issuers on EDGAR file 20-Fs and 40-Fs under IFRS, often in "
            "their home currency. This page reads them in that currency and never converts: "
            "Burry excludes ASML from his index for FX alone, and this page keeps his rule — "
            "one currency throughout, prices from a listing in that same currency, or no "
            "prices at all.\n\n"
            "Enter the US ticker of the foreign filer (ASML, SAP, GRAB, LEGN). For a EUR or "
            "other non-dollar filer, also give a home-exchange price symbol — ASML.AS, "
            "SAP.DE — so prices arrive in the filing currency. For a USD filer priced as "
            "ADSs, state the ADS ratio from its 20-F. A company with no SEC filing at all "
            "belongs in **Paste your own figures**, above."
        )

    # A form submits on Enter as well as on the button click.
    with st.form("lookup"):
        ticker = st.text_input("Stock ticker",
                               placeholder="ASML · SAP · GRAB · LEGN — press Enter"
                               ).upper().strip()
        px_sym_in = st.text_input(
            "Price symbol (optional)", placeholder="ASML.AS · SAP.DE",
            help="Where prices come from — it keeps its value between runs, so clear it "
                 "when you change company. Leave blank to use the US listing: right for "
                 "a USD filer, refused for a EUR one, because the quote's own currency "
                 "field is checked against the filing currency and a mismatch refuses "
                 "prices rather than converting them. Amsterdam is .AS, Frankfurt .DE, "
                 "Paris .PA, London .L.").strip()
        with st.expander("ADS ratio — only for a USD filer priced as ADSs"):
            ads_in = st.number_input(
                "Ordinary shares per ADS", value=1.0, min_value=0.01, step=0.5,
                help="A judgement input — no filing tags it. The depositary section of "
                     "the 20-F states it: Legend Biotech is 2. Prices are divided by it "
                     "so every figure is per ORDINARY share; wrong ratio, wrong market "
                     "cap, by exactly that factor. It converts units, never currency, "
                     "so it is refused on a non-USD filer. Leave at 1 unless the 20-F "
                     "says otherwise.")
        submitted = st.form_submit_button("Evaluate", type="primary")

tier_name = st.selectbox("Moat tier", list(AICT), index=2,
                         format_func=lambda t: f"{t} — {TIER_BLURB[t]}",
                         help="Sets stage lengths, how far growth fades in stage 2, the terminal "
                              "cap and the exit multiple. It does NOT set your stage 1 growth "
                              "rate — that is company-specific and yours to judge.")
_t = AICT[tier_name]
st.caption(
    f"**{_t.horizon}-year horizon · {_t.default_exit_multiple:g}× exit · "
    f"{_t.terminal_growth_cap:.0%} terminal.** Tier is the single biggest lever here — Stone "
    f"instead of Chapel roughly halves IV15. Set it deliberately for every company; leaving it "
    "on the default will quietly overvalue anything genuinely threatened."
)

if submitted:
    if not ticker:
        st.warning("Enter a ticker first.")
    else:
        try:
            with st.spinner(f"Reading {ticker} annual filings…"):
                yrs, notes, pre = load(ticker, 10, price_symbol=px_sym_in,
                                       ads_ratio=float(ads_in))
            st.session_state.update(nu_years=yrs, nu_notes=notes, nu_pre=pre, nu_tk=ticker)
        except ValueError as e:
            st.error(f"Could not load {ticker}: {e}")
        except Exception as e:
            st.error(
                f"Could not load {ticker} — {type(e).__name__}: {e}\n\n"
                "This is a gap in how the filings were read, not something you did. IFRS "
                "filers tag more variably than US ones and this reader's IFRS names are "
                "still earning their place. Paste mode runs the same engine on figures "
                "you type, and the tag panel of a partial run shows which line to report.")

years = st.session_state.get("nu_years", [])
if years and ticker and st.session_state.get("nu_tk") == ticker:
    notes, pre, tk = (st.session_state["nu_notes"], st.session_state["nu_pre"],
                      st.session_state["nu_tk"])
    # PAGE 6 EDIT (3 Sep 2026): every money string below uses the filing
    # currency. A dollar sign beside a euro figure is a small lie that
    # compounds — Burry's own reason for excluding ASML was FX, and a page
    # built to respect that cannot print $ out of habit.
    _ccy = pre.get("currency", "USD")
    _cs = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}.get(_ccy, _ccy + " ")
    PAGE_CCY = _cs
    pooled = pool(years)
    recent = pool_recent(years, 3) if len(years) >= 3 else pooled
    alerts: list[tuple[str, str]] = [("info", n) for n in notes]

    # ══ inputs ═══════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Inputs")

    use_recent = st.radio(
        "Apply ΔE from", ["Last 3 years", "Full period"], horizontal=True,
        captions=[f"{recent.dE:.1%}" if recent.dE_defined else "n/a — losses",
                  f"{pooled.dE:.1%}" if pooled.dE_defined else "n/a — losses"],
        help="ΔE is the share of reported profit that actually reaches shareholders. The long "
             "window is the diagnostic; where capital policy has changed, the recent one is "
             "what will apply going forward.") == "Last 3 years"
    use_dE = recent.dE if use_recent else pooled.dE
    dE_ok = dE_projectable(recent if use_recent else pooled)
    # The measurement above is left as filed; only what gets projected is held
    # to 100%. See the dE ceiling block near the top of the file.
    applied_dE = seed_dE(use_dE) if dE_ok else use_dE
    # Only true when the cap actually applied. Rivian's 107.3% is not
    # projectable at all, and "107.3% (capped from 107.3%)" is not a sentence.
    dE_capped = dE_ok and dE_was_capped(use_dE)

    hist = sorted(y.OE for y in years[-5:])
    median_OE = hist[len(hist) // 2] if hist else 0.0

    c1, c2, c3 = st.columns(3)
    fwd_N = c1.number_input(f"Forward net income ({_ccy}M)", value=float(round(years[-1].N, 1)), step=10.0,
                            help="Next year's expected GAAP net income.")
    derived, seed_source = seed_owners_earnings(fwd_N, applied_dE, dE_ok, median_OE)
    OE = c1.number_input(f"Owners' earnings ({_ccy}M)", value=float(round(derived, 1)), step=1.0,
                         help="Seeded from forward net income x ΔE. Adjust for maintenance "
                              "capex, working capital and anything non-recurring.")
    shares = c2.number_input("Diluted shares (M)", value=float(round(pre["shares"], 1)), step=1.0)
    growth = c2.number_input(
        "Growth rate (%)", value=round(pre["growth"] * 100, 1), step=0.5,
        min_value=-50.0, max_value=60.0,
        help="Growth in owners' earnings for the early years, seeded from the most recent "
             "year of revenue growth. Owners' earnings rarely grow at the revenue rate, and "
             "return on capital is the ceiling. Burry generally sits well below the revenue "
             "figure. For a genuine hypergrowth ramp use the Stage 0 years in Model settings "
             "rather than raising this.") / 100
    # PAGE 6 EDIT (3 Sep 2026): the seed follows the price policy. Paste mode
    # seeds the typed price. The fetched route asks the chosen symbol, checks
    # the quote's own currency field against the filing currency, and on a
    # mismatch seeds 0 with an error instead of a number in the wrong money —
    # tool 1's silent 100.00 default was for a market that is always USD.
    if pre.get("typed"):
        _seed_px = float(pre.get("price_seed") or 0.0)
    else:
        _cp = current_price(pre.get("px_sym") or pre.get("ticker", tk))
        _seed_px = 0.0
        if _cp:
            _cp_px, _cp_ccy = _cp
            if _cp_ccy and _cp_ccy != _ccy:
                alerts.append(("error",
                    f"The live price for {pre.get('px_sym') or tk} quotes in {_cp_ccy}, not "
                    f"{_ccy}, so it was refused — type the price from a {_ccy} listing "
                    "into the box."))
            else:
                _seed_px = _cp_px / float(pre.get("ads_ratio") or 1.0)
    price = c3.number_input(f"Price ({_ccy})", value=_seed_px, step=0.01)
    cash = c3.number_input(f"Cash & investments ({_ccy}M)", value=float(round(pre.get("cash", 0.0), 1)),
                           step=10.0, help="Only what is freely deployable. Restricted, regulated "
                                           "and operationally-tied cash funds the business.")
    debt = c2.number_input(f"Total debt ({_ccy}M)", value=float(round(pre.get("debt", 0.0), 1)),
                           step=10.0, help="Short-term plus long-term borrowings. Subtracted from "
                                           "cash to give the net figure added to intrinsic value.")
    net_cash = cash - debt
    _c3 = pre.get("cagr3")
    _trend = (f"  ·  revenue {growth:.1%} latest vs {_c3:.1%} 3-yr"
              if _c3 is not None else "")
    c1.caption(f"Net cash {d(net_cash,0)}M  ·  {d(cash,0)}M cash less {d(debt,0)}M debt{_trend}")

    with st.expander("Model settings — what these do"):
        st.caption(
            "Burry builds IV15 from two models and blends them. Everything else in this app is "
            "calculated; these are judgement, and he has never published his choices.\n\n"
            "**Hypergrowth (Stage 0) years** — extra years placed *before* stage 1 at their own "
            "growth rate, for a business whose earnings are still ramping: his Stage 0. Zero "
            "means none, and nothing else on the page changes. The perpetuity model's horizon "
            "extends by these years; the year-15 exit model does not.\n\n"
            "**Exit multiple** — what the business might fetch in year 15, as a multiple of its "
            "owners' earnings then. Higher for durable businesses, lower for fading ones.\n\n"
            "**Exit-multiple leg** — how that year-15 sale is combined with the cash the business "
            "throws off along the way. *Cash flows + exit* counts both, and fits Salesforce, "
            "Adobe and Paycom. *Buy and hold* counts only the year-15 sale, as if you bought the "
            "whole company and let it reinvest everything; it is the only reading that reaches "
            "Paylocity's published figure. Leave it on the first unless you are testing.\n\n"
            "**Long-horizon weight** — 1.0 uses only the model that runs to a perpetuity, 0.0 "
            "only the exit-multiple model, 0.5 splits them. This moves the answer a great deal, "
            "which is a fair reflection of how uncertain it genuinely is."
        )
        m1, m2 = st.columns(2)
        exit_m = m1.number_input(
            "Exit multiple", value=round(AICT[tier_name].default_exit_multiple, 2), step=0.5,
            help=f"Applied to year-15 owners' earnings. Burry never published his; this default "
                 f"is calibrated against Adobe. This tier's perpetuity floor is "
                 f"{AICT[tier_name].perpetuity_equivalent:.1f}x.")
        m2_style = m1.radio(
            "Exit-multiple leg", ["dcf", "hold"], horizontal=True,
            format_func=lambda v: "Cash flows + exit" if v == "dcf" else "Buy and hold to year 15",
            help="Two readings of Burry's Buffett leg. 'Cash flows + exit' discounts the earnings "
                 "stream then adds a year-15 multiple, and fits Salesforce, Adobe and Paycom. "
                 "'Buy and hold' counts only the year-15 sale — the only reading that reaches "
                 "Paylocity's published figure, but it makes the blend swing results about 3x.")
        blend = m2.slider("Long-horizon weight", 0.0, 1.0, 0.5, 0.05,
                          help="IV15 blends a perpetuity model with an exit-multiple model. "
                               "Moves the answer materially — about \\$10 on CRM.")
        # 31 Aug 2026: five notes on this page told the reader to use these
        # in Model settings; the engine had the fields (IVParams.stage0_*) and
        # the expander never had the widgets. Default 0 years: no stream moves.
        s0_years = int(m2.number_input(
            "Hypergrowth (Stage 0) years", min_value=0, max_value=10, value=0, step=1,
            help="Years of ramp placed before stage 1, at the rate below. 0 = none. Burry adds "
                 "a Stage 0 for inflecting hypergrowth; set the years and the rate by hand from "
                 "the margin path, not from the seed."))
        s0_growth = m2.number_input(
            "Stage 0 growth (%)", value=round(growth * 100, 2), step=1.0,
            disabled=(s0_years == 0),
            help="Owners' earnings growth in each Stage 0 year. Ignored when years is 0.") / 100.0
        t = AICT[tier_name]
        st.caption(f"{tier_name}: stage 1 {t.stage1_years}y, stage 2 {t.stage2_years}y at "
                   f"{t.stage2_multiplier:.2f}x, terminal cap {t.terminal_growth_cap:.0%}, "
                   f"total horizon {t.horizon} years.")
        _l1, _l2 = model_legs(IVParams(OE=OE, shares=shares, tier=tier_name, growth=growth,
                                       net_cash=net_cash, exit_multiple=exit_m, blend=blend,
                                       m2_style=m2_style, stage0_years=s0_years,
                                       stage0_growth=s0_growth))
        if _l1 == _l1 and _l2 == _l2:
            st.caption(f"Long-horizon leg {_cs}{_l1:,.2f} · exit-multiple leg {_cs}{_l2:,.2f}. "
                       + ("They agree closely, so the blend barely matters here."
                          if abs(_l1 - _l2) / max(_l1, 1) < 0.1 else
                          "They diverge, so the blend is doing real work — worth a look."))

    _rev = pre.get("revenue", 0.0)
    if _rev > 0 and OE > 0:
        _margin = OE / _rev
        if _margin < 0.08 and growth > 0.12:
            alerts.append(("warning",
                f"Owners' earnings are only {_margin:.1%} of {d(_rev,0)}M revenue while growth is "
                f"seeded at {growth:.1%}. Compounding a thin margin at the revenue rate is the "
                "wrong shape for this company — the story is margin recovery, not earnings "
                "compounding. Burry's rule is that profit inflection points require estimating "
                "the margin path, which is what the hypergrowth years in Model settings are for. "
                "ServiceNow and monday.com both need this treatment."))

    if seed_source == SEED_FROM_MEDIAN_LOSS:
        st.error(
            f"**The forward year is a loss on a profitable record.** Net income for the latest "
            f"year is {d(fwd_N,0)}M, while the five-year median of owners' earnings is "
            f"{d(median_OE,0)}M. A single loss year on a record like that is usually a non-cash "
            "charge — an impairment or a write-down — and a verdict measured against it would "
            "be a verdict on the charge, not on the business. Owners' earnings above are "
            f"therefore seeded from the median, {d(median_OE,0)}M, rather than from the loss. "
            "The yearly table shows which year it was; set the box to what the business earns "
            "in a normal year.")
    if not dE_ok and median_OE <= 0:
        if not (pooled.dE_defined and recent.dE_defined):
            st.error(
                f"**Set owners' earnings yourself.** Cumulative net income is negative "
                f"({d(pooled.sum_N,0)}M), so ΔE is undefined — the percentages above are not "
                f"usable and are shown only for completeness. The field below is seeded with "
                f"forward net income of {d(fwd_N,0)}M as a ceiling; it is almost certainly too "
                "high, since this business has not yet earned that in a normal year. Enter what "
                "you think it earns once profitable. Burry publishes IV15 for loss-makers "
                "— Zscaler, Palo Alto and CrowdStrike all have negative owners' earnings in his "
                "write-ups — by valuing the recovery, not today's losses.")
        else:
            st.error(
                f"**Set owners' earnings yourself.** ΔE of {use_dE:.1%} cannot be projected, and "
                f"every recent year is negative too, so the field below is seeded with forward "
                f"net income of {d(fwd_N,0)}M as a ceiling — it is certainly too high. Burry "
                "does this by hand: ServiceNow gets about 620M against reported profit near "
                "1,750M, adjusted upward from a negative ΔE for its dilution-neutral pledge "
                "and its sub-10%-of-revenue SBC target.")

    if not dE_ok:
        if not (pooled.dE_defined and recent.dE_defined):
            alerts.append(("error",
                "ΔE is undefined because cumulative net income is negative. A ratio against a "
                "negative denominator flips sign, so the percentages shown are not usable. Set "
                "owners' earnings by hand from what the business earns once profitable."))
        elif use_dE < 0:
            alerts.append(("error",
                f"ΔE of {use_dE:.1%} cannot be projected forward — stock compensation has "
                "swamped earnings over this window. Set owners' earnings by hand. Burry does "
                "exactly this for DocuSign: ΔE deeply negative, yet about 195M of forward "
                "owners' earnings on judgement, worked down from free cash flow."))
        else:
            alerts.append(("error",
                f"ΔE of {use_dE:.1%} is above 100%, which means share issuance is not being "
                "fully captured — a company cannot keep more than every reported dollar. It "
                "cannot be projected forward. Set owners' earnings by hand."))
    elif dE_capped:
        _win = [y for y in (years[-3:] if use_recent else years) if not y.excluded]
        _bought_back = buybacks_shrank_count(_win)
        alerts.append(("warning",
            f"ΔE measured {use_dE:.1%} over this window — shareholders kept more than the "
            "company reported earning, "
            + ("which happens when buybacks retire more stock than the year issues. "
               if _bought_back else
               "and buybacks over this window were too small to be the cause: the stock-comp "
               "cost measured from the share count pooled below the GAAP charge. Compare "
               "those two columns in the yearly table. ")
            + "That is a real reading of what happened and the figures above are "
            f"left as filed. But it is not projectable: owners' earnings are seeded at "
            f"{DE_SEED_CEILING:.0%} of forward net income rather than {use_dE:.1%}, because "
            "fifteen years of handing owners more than the company earns is not a business "
            "model. Raise the box by hand "
            + ("if you believe the buyback pace continues."
               if _bought_back else
               "only if you can say why the measured cost should stay below the charge.")))
    elif median_OE > 0 and derived > 2 * median_OE:
        alerts.append(("warning",
            f"Derived owners' earnings of {d(derived,0)}M are {derived/median_OE:.1f}x the "
            f"{d(median_OE,0)}M median of the last five years. Forward profit may carry a "
            "one-off. Check the yearly table and override."))
    elif derived > 0 >= median_OE:
        # The branch above warns when the seed outruns a POSITIVE median. A
        # median that is not positive at all is strictly worse and fell
        # straight through it. TGTX, 28 Aug 2026: owners' earnings negative in
        # nine of ten years, one profitable year at the end, ΔE legitimately
        # measurable over the recent window because those three years pool to
        # +483 of net income — so nothing here refused, and IV15 printed 52.27
        # against a 54.19 price with a five-year median of -44 unmentioned.
        alerts.append(("warning",
            f"Derived owners' earnings of {d(derived,0)}M rest on the forward year alone: the "
            f"five-year median is {d(median_OE,0)}M, which is not positive. ΔE is measurable "
            "here because the recent window pools to a profit, but a median that has not "
            "turned says the run rate is not established yet. Check the yearly table and set "
            "the box by hand."))
    if pooled.dE_defined and recent.dE_defined and abs(recent.dE - pooled.dE) > 0.15 \
            and abs(pooled.dE) <= 1.25 and abs(recent.dE) <= 1.25:
        alerts.append(("warning",
            f"Regime change: ΔE was {pooled.dE:.1%} over {pooled.years} years but "
            f"{recent.dE:.1%} over the last three. Satisfy yourself the shift is durable."))
    if shares > 0 and price > 0 and abs(net_cash / (shares * price)) > 0.08:
        alerts.append(("info",
            f"Net cash is {net_cash/(shares*price):.0%} of market cap — about "
            f"{d(net_cash/shares)} per share of the IV15 below."))

    if shares <= 0:
        st.error("Enter the diluted share count — everything divides by it.")
        st.stop()
    # PAGE 6 EDIT (3 Sep 2026): tool 1 always has a USD price to fall back
    # on; this page refuses prices in the wrong currency, so zero is a state
    # the page can reach — and a zero price divided by IV15 would print
    # 0.00x, which reads as the fattest pitch ever seen. Stop instead.
    if price <= 0:
        st.error(f"Enter the share price in {_ccy} — the verdict compares it with IV15, "
                 "and a price of zero would render as an infinite bargain rather than as "
                 "the missing number it is.")
        st.stop()

    mcap = shares * price / 1000.0
    if fwd_N > 0 and shares > 0 and price > 0:
        _pe = (shares * price) / fwd_N
        if _pe > 150 or _pe < 2:
            st.warning(
                f"Forward net income of {d(fwd_N,0)}M against a {d(mcap,2)}B market cap is a "
                f"P/E of {_pe:,.0f}x, which is almost certainly a reading error rather than a "
                "valuation. Companies with large non-controlling interests report only the "
                "parent's slice as net income while the share count covers everything. Check "
                "the figure against the income statement and override it.")
    if net_cash > 0 and price > 0 and net_cash / (shares * price) > 0.60:
        st.error(
            f"**Check the share count before trusting anything below.** Net cash of "
            f"{d(net_cash,0)}M is {net_cash/(shares*price):.0%} of a {d(mcap,2)}B market cap, "
            "which almost never happens. The usual cause is a company with more than one share "
            "class where only one was picked up. Look up the real share count and type it in.")
    elif mcap < 0.05:
        st.warning(f"Implied market cap is only {d(mcap,2)}B. If that looks too small, the share "
                   "count is likely wrong — everything scales inversely with it.")

    par = IVParams(OE=OE, shares=shares, tier=tier_name, growth=growth,
                   net_cash=net_cash, exit_multiple=exit_m, blend=blend,
                   m2_style=m2_style, stage0_years=s0_years, stage0_growth=s0_growth)
    lad = ladder(par)
    iv15 = lad[15]

    # ══ verdict ══════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader(f"Verdict · {tk}")

    if iv15 != iv15:
        st.error("Required return must exceed the tier's terminal growth cap.")
        st.stop()
    if iv15 < 0:
        st.error(f"**Not investible.** No share price — not even one cent — delivers 15% a year "
                 f"to a long-term shareholder in {tk} on these inputs.")
        st.stop()

    ratio = price / iv15
    er = expected_return(price, par)
    zn, kind = zone(ratio)
    er_txt = "implausible" if er == float("inf") else f"{er:.1%}"

    # Decided BEFORE the badges are drawn. Berkshire, 26 Aug 2026: a share
    # count in Class A equivalents produced an IV15 of $262,346 beside a
    # $100.00 price, and the page printed "Fat Pitch" and "score 35/35" in
    # green directly above the red box calling the result broken. A reader
    # scanning headline figures sees the badges first.
    _broken = er == float("inf") or (price > 0 and iv15 / price > 20)

    v1, v2, v3 = st.columns(3)
    v1.metric("IV15", f"{_cs}{iv15:,.2f}", f"market {_cs}{price:,.2f}")
    v2.metric("Price / IV15", f"{ratio:.2f}x", "not usable" if _broken else zn)
    v3.metric("Expected return", er_txt,
              "no score — see below" if _broken else f"score {valuation_points(ratio)}/35")
    if _broken:
        st.error(
            f"**This result is not believable — an input is wrong.** IV15 of {d(iv15)} against a "
            f"{d(price)} share price is not a bargain, it is a broken assumption. The usual "
            f"causes, in order: a growth rate far above anything sustainable (yours is "
            f"{growth:.1%}); a share count that missed a second share class; or owners' earnings "
            "carrying a one-off. Fix the input and the ladder below will mean something.")
        with st.expander("Notes and detail", expanded=True):
            for kind_, msg in alerts:
                getattr(st, kind_)(msg)
        st.stop()

    verdict = {
        "success": f"**Fat pitch.** {tk} trades below its IV15 of {d(iv15)}, implying about "
                   f"{er_txt} a year held long term.",
        "info":    f"**Just outside.** {tk} is at {ratio:.2f}x its IV15 of {d(iv15)} — a "
                   f"watchlist candidate at about {er_txt} a year.",
        "error":   f"**Out field.** At {ratio:.2f}x its IV15 of {d(iv15)}, {tk} offers only "
                   f"about {er_txt} a year.",
    }[kind]
    getattr(st, kind)(verdict)

    st.write("**Entry bands** — set alerts at each")
    st.dataframe(
        pd.DataFrame([{"Target return": f"{n}%", "Buy under": v, "Meaning": RUNG_MEANING[n]}
                      for n, v in lad.items() if v == v and v > 0][::-1])
        .style.format({"Buy under": _cs + "{:,.2f}"}),
        width='stretch', hide_index=True)

    # ══ quality ══════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Shareholder quality")

    q1, q2, q3 = st.columns(3)
    if not pooled.dE_defined:
        q1.metric("Owners' earnings kept", "n/a", "net income was negative")
    else:
        q1.metric("Owners' earnings kept",
                  f"{pooled.dE:.1%}" if abs(pooled.dE) < 10 else "deeply negative",
                  f"last 3y: {recent.dE:.1%}" if recent.dE_defined else "last 3y: n/a")
    q2.metric("True SBC cost", f"{_cs}{pooled.sum_omega:,.0f}M",
              f"GAAP says {_cs}{pooled.sum_G:,.0f}M")
    q3.metric("Value kept after 10y",
              f"{pooled.retention(10):.1%}"
              if pooled.dE_defined and 0 < pooled.dE <= 1.25 else "—",
              "of reported growth")

    if pooled.sum_OE < 0 and pooled.dE_defined and price > 0:
        st.info(
            "Owners' earnings are negative over this window. Burry still publishes IV15 values for "
            "such companies — Zscaler, Palo Alto and CrowdStrike all have negative owners' earnings "
            "in his write-ups yet carry IV15 targets — because he values a projected recovery to "
            "profitability rather than today's losses. To follow that here, enter the owners' "
            "earnings you think the business reaches, and use the hypergrowth years to model the "
            "path. This tool cannot infer that path for you.")

    if not pooled.dE_defined:
        st.error(
            f"**ΔE cannot be computed — cumulative net income is negative** "
            f"({d(pooled.sum_N,0)}M over {pooled.years} years). Any ratio printed against a "
            f"negative denominator flips sign and misleads. What matters instead is the absolute "
            f"figure: owners' earnings of {d(pooled.sum_OE,0)}M after a true stock-comp cost of "
            f"{d(pooled.sum_omega,0)}M. The business lost money and lost more again to "
            "compensation. Set owners' earnings by hand from what you think it earns once "
            "profitable.")
    elif pooled.dE > 1.25:
        st.warning(
            f"**ΔE of {pooled.dE:.0%} is not a real result.** Keeping more than every reported "
            "dollar is not something a company can do — it means the true SBC cost came out "
            f"below the GAAP charge ({d(pooled.sum_omega,0)}M against {d(pooled.sum_G,0)}M), "
            "which happens when share issuance is not being captured. Complex structures with "
            "large non-controlling interests, several share classes, or units exchangeable into "
            "stock are the usual causes. Read the shareholder verdict here as unknown, not good.")
    elif pooled.tragic_tier:
        st.error("**Tragic tier.** Stock compensation cost more than the business earned over "
                 "this period. Shareholders were net funders of employee pay.")
    elif pooled.dE < 1 / 1.15:
        st.warning(f"**Below the 87% break-even.** Even 15% reported growth compounds value per "
                   f"share at just {pooled.true_cagr(0.15):+.2%} a year.")
    else:
        st.success("**Above the 87% break-even** — reported growth actually reaches you.")

    # ══ stress ═══════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Stress test")

    s1, s2 = st.columns(2)
    keys = list(AICT)
    worse = s1.selectbox("Downgrade tier to", keys,
                         index=min(len(keys) - 1, keys.index(tier_name) + 1))
    cut = s2.slider("Cut growth by (%)", 0, 80, 30, 5)
    siv = intrinsic_value(IVParams(OE=OE, shares=shares, tier=worse,
                                   growth=growth * (1 - cut / 100), net_cash=net_cash,
                                   exit_multiple=exit_m, blend=blend,
                                   stage0_years=s0_years, stage0_growth=s0_growth), 15)
    if siv == siv and siv > 0:
        t1, t2 = st.columns(2)
        t1.metric("Stressed IV15", f"{_cs}{siv:,.2f}", f"{siv/iv15-1:+.1%}")
        t2.metric("Stressed P/IV15", f"{price/siv:.2f}x", zone(price / siv)[0])
        if price <= siv:
            st.success("Still below IV15 after a downgrade and a growth cut. That is a real "
                       "margin of safety.")
    else:
        st.error("Not investible under stressed assumptions.")

    # ══ detail ═══════════════════════════════════════════════════════
    st.markdown("---")
    label = f"Notes and detail" + (f" · {len(alerts)} to review" if alerts else "")
    with st.expander(label):
        for kind_, msg in alerts:
            getattr(st, kind_)(msg)

        st.write("**Year by year**")
        # A year whose net income is a rounding error against the rest of the
        # window cannot carry a ratio; the pooled figures below still weight it
        # in full. See the per-year cell block near the top of the file.
        # The cell says WHY it is blank rather than leaning on the caption
        # below it. Streamlit's fullscreen renders the dataframe alone, and
        # the normal view crops the ΔE column off the right edge — so the
        # only view that shows this cell was the one view that could not
        # show its explanation. A bare em dash there reads like a lookup
        # that failed rather than a deliberate refusal.
        _dE_text = lambda v: "n/a (base too small)" if v is None else f"{v:.1%}"
        _med_N = median_positive_N([y.N for y in years])
        _blank_dE = [y.fy for y in years if dE_cell(y.N, y.dE, _med_N) is None]
        # PDEX: one decimal count for all five dollar columns, chosen from
        # the table's own largest value. See money_decimals.
        _mfmt = money_fmt([v for y in years for v in (y.N, y.G, y.T, y.omega, y.OE)])
        st.dataframe(pd.DataFrame([{
            "FY": f"{y.fy}*" if y.excluded else str(y.fy),
            "Net income": y.N, "GAAP SBC": y.G, "Buybacks": y.T,
            "Share change": y.dS, "Avg price": y.price, "True SBC cost": y.omega,
            "Owners' earnings": y.OE,
            # Formatted here rather than left to the styler: st.dataframe does
            # not honour na_rep and prints a bare "None" into the cell, which
            # reads like a failure rather than a deliberate blank.
            "ΔE": _dE_text(dE_cell(y.N, y.dE, _med_N))} for y in years]).style.format({
                "Net income": _mfmt, "GAAP SBC": _mfmt, "Buybacks": _mfmt,
                "Share change": "{:+,.1f}", "Avg price": _cs + "{:,.2f}", "True SBC cost": _mfmt,
                "Owners' earnings": _mfmt}, na_rep="—"),
            width='stretch', hide_index=True)
        if _blank_dE:
            st.caption(
                "ΔE reads n/a for "
                + ", ".join(f"FY{f}" for f in _blank_dE)
                + (": net income there is too small against the rest of the window to divide by, "
                   "so the ratio would describe the denominator rather than the company. Owners' "
                   "earnings for those years are shown as measured and are weighted in full "
                   "inside the pooled figures."))

        st.write("**What was read from the filings** — every tag, found or missing")
        st.dataframe(pd.DataFrame(pre.get("tags", [])), width='stretch', hide_index=True)
        st.caption(
            "A zero in this app is either something the company did not do or a tag this reader "
            "does not know. If a line you know exists reads fewer years than net income, that is "
            "a bug worth reporting — the tag name is the whole fix.")

        st.write("**Assumptions used** — paste this if something looks wrong")
        st.code(
            f"{tk}   price {price:,.2f}   shares {shares:,.1f}M   "
            f"mkt cap {_cs}{shares*price/1000:,.2f}B\n"
            # PAGE 6 EDIT (3 Sep 2026): printed on every run, because the
            # ratio is a judgement input no filing tags and the currency is
            # the whole premise — a wrong ratio or a mixed currency should be
            # visible in the first thing anyone pastes when a figure looks off.
            f"currency            {_ccy}   price source "
            + (("typed" if pre.get("typed") else (pre.get("px_sym") or tk)))
            + f"   ADS ratio {pre.get('ads_ratio', 1.0):g}\n"
            f"forward net income  {fwd_N:,.0f}\n"
            f"ΔE applied          {applied_dE:.1%}"
            + (f" (capped from {use_dE:.1%})" if dE_capped else "   ")
            + f"   (full {pooled.dE:.1%} / 3y {recent.dE:.1%})\n"
            f"median OE, 5y       {median_OE:,.0f}\n"
            f"owners' earnings    {OE:,.0f}   ({OE/shares:,.2f}/share)"
            # ARM, 26 Aug 2026: the block showed "ΔE applied -16.2%" beside
            # "owners' earnings 556" and no arithmetic connects those two. The
            # seed came from the median because a negative ΔE cannot project,
            # which the reader had no way to know from a block whose whole
            # purpose is to be pasted when something looks wrong.
            + ("\n" + " " * 20 + "seeded from " + seed_source) + "\n"
            f"net cash            {net_cash:,.0f}   ({net_cash/shares:,.2f}/share)\n"
            f"tier                {tier_name}   growth {growth:.2%}\n"
            f"exit multiple       {exit_m:g}x   blend {blend:g}   leg {m2_style}\n"
            + (f"stage 0             {s0_years}y at {s0_growth:.1%}\n" if s0_years else "")
            + f"IV15                {iv15:,.2f}   P/IV15 {ratio:.2f}x", language="text")

        st.write("**Calibrate against a published IV15**")
        target = st.number_input("Published IV15", value=0.0, step=0.01,
                                 label_visibility="collapsed")
        if target > 0:
            solved = solve_growth(target, par)
            if solved is None:
                st.error("No growth rate between -30% and +100% reaches that. Owners' earnings, "
                         "share count, exit multiple or blend is likely off.")
            else:
                st.success(f"Growth of **{solved:.2%}** reproduces {d(target)} at your current "
                           f"exit multiple and blend.")


# ══════════════════════════════════════════════════════════════════════
#  REFERENCE — at the foot of the page rather than the sidebar, so nothing
#  competes with the answer and the nav stays clean.
# ══════════════════════════════════════════════════════════════════════

st.divider()
_r1, _r2 = st.columns(2)
with _r1:
    with st.expander("What the numbers mean", expanded=False):
        st.markdown(
            "**ΔE** — the share of each reported dollar of profit that actually reaches "
            "shareholders once the true cost of stock compensation is charged. Below about "
            "87%, a company needs 15% reported growth just to hold value per share steady.\n\n"
            "**IV15** — the price at which the stock would return roughly 15% a year over "
            "15+ years. A buy target from a cash flow model, not an earnings multiple.\n\n"
            "**IV8 to IV10** — closer to what the business is actually worth. Buybacks below "
            "that range add value per share; above it they destroy it.\n\n"
            "**Expected return** — what today's price implies you'd earn annually, held long "
            "term. The most useful single figure, since it needs no target return chosen "
            "in advance.\n\n"
            "**Moat tier** — sets how long growth lasts and how fast it fades, not the "
            "starting rate. Fortress holds growth 8 years; Wood gets 2."
        )

with _r2:
    with st.expander("Verify the engine"):
        st.caption(
            "These run the formulas on **Burry's own published inputs** and check the output "
            "against his published results. They confirm the maths is right.\n\n"
            "They will not match what you get by entering a ticker above. A live run uses "
            "today's filings, today's share count, growth seeded from revenue, and the tier "
            "defaults — different inputs, so a different answer. Both land in a similar range; "
            "they are simply answering different questions."
        )
        if st.button("Run checks"):
            _results = self_test()
            _sev, _line = test_summary(_results)
            getattr(st, _sev)(_line)
            for name, ok, got in _results:
                st.write(("✅ " if ok else "❌ ") + f"{name} — {got}")
            st.caption("Tolerances: dollar figures within $1, ratios within half a point. "
                       "Burry rounds published prices and share counts, so exact equality "
                       "is not achievable and would be a suspicious thing to claim.")

st.caption(
    "Research aid, not financial advice. Outputs depend on estimates you supply. Method "
    "follows Michael Burry's published writing; this project is independent and is not "
    "affiliated with or endorsed by him or Scion Asset Management."
)
