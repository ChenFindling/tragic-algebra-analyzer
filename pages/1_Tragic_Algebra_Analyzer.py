"""
Tragic Algebra Analyzer
===================
Owners' earnings adjusted for the true cost of stock compensation, then the
intrinsic value ladder that follows from them.

THE KEY SIMPLIFICATION
----------------------
The published cost formula is  V = T x (W + dS) / W  , which needs W, the number
of shares repurchased. W is almost never tagged in XBRL — it lives in the share
repurchase footnote.

But P = T / W, so:

    V = T x (W + dS)/W  =  T + (T/W) x dS  =  T + P x dS

W cancels. Only the average share price is needed, and that is always
obtainable. Verified exact against all ten published Alphabet years.

So:
    V  = max(0, T + P x dS)      market value of shares handed to employees
    C  = Cw - Ce                 net cash award payments
    Om = C + V                   true SBC cost, replaces GAAP's estimate
    OE = N + G - Om              owners' earnings
    dE = OE / N                  fraction of reported profit that is really yours

Pooled over ~10 years as sum(OE)/sum(N) — never an average of annual ratios.

Run:  streamlit run app.py
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
# clicking around never gets close; ten people sharing an app, or one watchlist
# run, easily does. All SEC traffic funnels through _sec_get, which spaces
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
    "G":  (["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
           ["ShareBasedPaymentsExpense"]),
    "T":  (["PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity",
            "PaymentsForRepurchaseOfCommonStockAndRestrictedStockUnits",
            "StockRepurchasedAndRetiredDuringPeriodValue",
            "StockRepurchasedDuringPeriodValue"],
           ["PaymentsToAcquireOrRedeemEntitysShares"]),
    "Cw": (["PaymentsRelatedToTaxWithholdingForShareBasedCompensation",
            "TreasuryStockValueAcquiredCostMethod"], []),
    "Ce": (["ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlans",
            "ProceedsFromStockOptionsExercised", "ProceedsFromIssuanceOfTreasuryStock",
            "ProceedsFromSaleOfTreasuryStock", "ProceedsFromStockPlans",
            "ProceedsFromEmployeeStockPurchasePlan", "ProceedsFromIssuanceOfCommonStock"], []),
    "REV": (["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
             "RevenueFromContractWithCustomerIncludingAssessedTax"], ["Revenue"]),
    "SHD": (["WeightedAverageNumberOfDilutedSharesOutstanding",
             "WeightedAverageNumberOfSharesOutstandingDiluted"], []),
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
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
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
    "ltd":  ["LongTermDebtNoncurrent", "LongTermDebt",
             "DebtLongtermAndShorttermCombinedAmount"],
    "std":  ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings", "CommercialPaper"],
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

DE_SEED_CEILING = 1.00    # highest dE that may be projected forward
DE_UNUSABLE_ABOVE = 1.25  # above this, refuse rather than cap


def seed_dE(measured: float) -> float:
    """The dE to project forward. Never above 100%; the measurement is untouched."""
    return min(measured, DE_SEED_CEILING)


def dE_was_capped(measured: float) -> bool:
    """True when the projection is being held below what the filings measured."""
    return DE_SEED_CEILING < measured <= DE_UNUSABLE_ABOVE


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
            prefer_recent: bool = False) -> dict[int, tuple[str, str, float]]:
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
            for row in units.get("USD", []) or units.get("shares", []):
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
def _monthly_closes(ticker: str) -> tuple[dict[str, float], dict[str, float]]:
    """Monthly closes for ~11 years keyed 'YYYY-MM', plus split events.

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
    return out, splits


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
def current_price(ticker: str) -> float | None:
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        m = r.json()["chart"]["result"][0]["meta"]
        return float(m.get("regularMarketPrice") or m.get("chartPreviousClose"))
    except Exception:
        return None


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
            # A share count that MULTIPLIES from a small base is usually a
            # listing, not a split. Splits move a large, established count.
            if ratio > 2.85 and shares[fys[i - 1]] < 25e6:
                continue
            if ratio > 0 and (ratio > 2.85 or ratio < 0.35):
                # Round to a plausible split ratio. Reverse splits must be
                # rounded on the reciprocal: round(0.1 * 2) / 2 is zero.
                if ratio >= 1:
                    clean = round(ratio * 2) / 2
                    label = f"{clean:g}:1"
                else:
                    inv = round((1 / ratio) * 2) / 2
                    clean = 1 / inv if inv > 0 else 0.0
                    label = f"1:{inv:g}"
                if clean > 0:
                    factor *= clean
                    notes.append(f"Stock split detected in FY{fy} (about {label}). Earlier "
                                 "share counts restated onto the current basis — without this "
                                 "the SBC cost would be wildly overstated.")
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


def foreign_filer_note(net_income_tag: str, unread: list[str]) -> str:
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
    head = ("**This is a foreign private issuer reporting under IFRS.** Net income was read "
            f"from {net_income_tag}, which is right, but this reader knows US-GAAP tag names "
            "for the other lines and an IFRS filing does not use them. ")
    if not unread:
        return head + "Check each line in the tag panel before trusting any figure below."
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


def load(ticker: str, n_years: int = 10):
    cmap = _ticker_map()
    if ticker not in cmap:
        raise ValueError(f"'{ticker}' is not in the SEC company list.")
    facts = _facts(cmap[ticker])
    sic, sic_desc = _sic(cmap[ticker])

    tag_sources: dict[str, list[str]] = {k: [] for k in CONCEPTS}
    series = {k: _annual(facts, us, ifrs, tag_sources[k], k in FILL_KEYS,
                         k in RECENCY_KEYS)
              for k, (us, ifrs) in CONCEPTS.items()}
    # Ask the currency question ALWAYS, not only when nothing was found. A
    # foreign filer with a couple of USD convenience translations used to sail
    # straight past this and out the other side with two years of data, an
    # ADR price and an ordinary share count multiplied together.
    _ccy = currency_facts(facts, CONCEPTS["N"][0] + CONCEPTS["N"][1])
    _foreign = {u: n for u, n in _ccy.items() if u != "USD"}
    if _foreign:
        _main, _n = max(_foreign.items(), key=lambda kv: kv[1])
        if _n >= _ccy.get("USD", 0):
            raise ValueError(
                f"{ticker} reports in {_main}, not US dollars — {_n} annual figures in {_main} "
                f"against {_ccy.get('USD', 0)} in USD. Every figure here assumes one currency "
                "throughout, and the few USD facts a foreign issuer tags are usually convenience "
                "translations for one or two old years. Worse, the share count in the filing is "
                "ordinary shares while the price you see is an ADR, and one ADR is rarely one "
                "share — multiplying them gives a market cap that is wrong by whatever the ADR "
                "ratio happens to be. Foreign private issuers are not supported.")

    # MA / OFFER / CONV are corporate transactions, not flows, so _annual's
    # duration filter throws their facts away. Re-read them with _issuance,
    # which is built for dated events. See item 3 in the brief.
    for _k in ("MA", "OFFER", "CONV"):
        tag_sources[_k].clear()
        series[_k] = _issuance(facts, CONCEPTS[_k][0], series["N"], tag_sources[_k])
    tag_sources["MAV"].clear()
    series["MAV"] = _issuance(facts, CONCEPTS["MAV"][0], series["N"],
                              tag_sources["MAV"], "USD")

    if not series["N"]:
        ccy = reporting_currency(facts, CONCEPTS["N"][0] + CONCEPTS["N"][1])
        if ccy and ccy != "USD":
            raise ValueError(
                f"{ticker} reports in {ccy}, not US dollars. Every figure here assumes one "
                "currency throughout, and mixing a euro income statement with a dollar share "
                "price would produce numbers that look fine and are wrong. Foreign private "
                "issuers filing in their home currency are not supported.")
        raise ValueError(
            f"No annual net income found for {ticker}. The filer uses tags this reader does "
            "not recognise, which happens with unusual structures and some foreign issuers. "
            "Nothing can be computed without it.")

    shares_out = _instant(facts, ["CommonStockSharesOutstanding", "CommonStockSharesIssued",
                                  "EntityCommonStockSharesOutstanding"], unit="shares")
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
    _wavg_ser = _annual(facts, ["WeightedAverageNumberOfDilutedSharesOutstanding",
                                "WeightedAverageNumberOfSharesOutstandingDiluted",
                                "WeightedAverageNumberOfSharesOutstandingBasic"], [],
                        None, True)
    _wv = {fy: v[2] for fy, v in _wavg_ser.items() if v[2] and v[2] > 0}
    _cover = _cover_shares(facts, series["N"])
    # Read separately from shares_out purely so the tag panel can report how many
    # years each source covers. TDG's bug was invisible until the panel showed
    # CommonStockSharesOutstanding at 3 years against a 16-year cover page.
    _c_out = _instant(facts, ["CommonStockSharesOutstanding"], unit="shares")
    _c_iss = _instant(facts, ["CommonStockSharesIssued"], unit="shares")
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
            _t_fy = _treas_fy if _treasury and _treas_fy is not None else _lat
            _route_note = ("treasury" if _treasury else "static" if _static else "sparse",
                           shares_out[_t_fy] if _treasury else _was,
                           _wv[_t_fy] if _treasury else _wv[_latw], _share_route,
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

    try:
        closes, splits = _monthly_closes(ticker)
    except Exception:
        closes, splits = {}, {}

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
    _asof = max((v[1] for v in series["N"].values()), default="")
    if _share_route == "the 10-K cover page":
        _asof = max(_asof, _cover_asof(facts))
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

    _neg = [y for y in years if y.omega < 0 and not y.excluded]
    if len(_neg) >= max(2, len(years) // 3):
        notes.append(
            f"The true stock-comp cost reads negative in {len(_neg)} of {len(years)} years, which "
            "ADDS to owners' earnings instead of subtracting and pushes ΔE above 100%. It happens "
            "legitimately when option and ESPP proceeds exceed the tax withheld, but it is also "
            "what a missing buyback or issuance line looks like. Check the tag panel below: if "
            "buybacks read fewer years than net income, that is the cause and ΔE here is a "
            "ceiling rather than a measurement.")

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
    _span = max(fys) - min(fys) + 1
    if _span > len(fys):
        _missing = [y for y in range(min(fys), max(fys) + 1) if y not in fys]
        notes.append(
            f"**The filing history has holes.** {len(fys)} annual figures span {_span} calendar "
            f"years, with nothing read for FY{_missing[0]}"
            + (f"-FY{_missing[-1]}" if len(_missing) > 1 else "")
            + ". The year-by-year table draws these rows next to each other as though they were "
              "consecutive. Growth rates here are measured across the real calendar gap, so they "
              "are not wrong, but they blend two eras of the company with a hole in the middle — "
              "and the pooled ΔE weights whichever era has more years. The tag panel shows how "
              "many years each line actually read.")

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
        d = _instant(facts, ks, "USD", src, _skips, prefer_recent=True)
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
    if is_financial(sic):
        notes.append(f"{sic_desc or 'Financial company'} (SIC {sic}). Investments here back "
                     "policyholder or depositor liabilities rather than belonging to "
                     "shareholders, so net cash has been set to zero. The Tragic Algebra still "
                     "works, but treat the valuation as indicative — this framework was built "
                     "for software, and insurers, banks and REITs need book-value and "
                     "combined-ratio thinking it does not contain.")
        cash_total = debt_total = net_cash = 0.0

    # First in the list, because it governs how every other note reads.
    _unread = [_l for _l, _empty in (("stock compensation", not any(y.G for y in years)),
                                     ("the share count", not shares_out),
                                     ("the balance sheet", cash_total == 0 and debt_total == 0))
               if _empty]
    _ff = foreign_filer_note(_nsrc[0] if _nsrc else "", _unread)
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
        {"Line": "— Shares: treasury held", "Years read": len(_treas),
         "Latest year": _latest_fy(_treas),
         "XBRL tag": "TreasuryStockCommonShares",
         "Status": "used" if _share_route.startswith("issued minus") else
                   "read" if _treas else "not tagged"},
        {"Line": "— Shares: diluted average", "Years read": len(_wv),
         "Latest year": _latest_fy(_wv),
         "XBRL tag": "WeightedAverageNumberOfDilutedSharesOutstanding",
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
                          "shares": diluted, "growth": growth, "sic": sic,
                          "sic_desc": sic_desc, "financial": is_financial(sic)}


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

    _tr25 = share_route_note("treasury", 64.5e6, 32.6e6, "the 10-K cover page", 10, 10, 2025,
                             factor=25.0)
    out.append(("Booking's treasury note prints post-split counts, not 64.5M vs 32.6M",
                "1,612.5M" in _tr25 and "815.0M" in _tr25 and "64.5M" not in _tr25,
                "1,612.5M against 815.0M, both x25"))
    _tr1 = share_route_note("treasury", 25.7e6, 17.2e6, "issued minus treasury shares",
                            10, 10, 2025)
    out.append(("...and an unsplit filer is left exactly as it was",
                "25.7M" in _tr1 and "post-split" not in _tr1, "25.7M against 17.2M"))
    out.append(("Every line the tag panel can print has a label",
                all(k in TAG_LABELS for k in CONCEPTS),
                f"{len(CONCEPTS)} concepts, {len(CONCEPTS) - sum(k in TAG_LABELS for k in CONCEPTS)} unlabelled"))
    return out


# ══════════════════════════════════════════════════════════════════════
#  UI
# ══════════════════════════════════════════════════════════════════════
#
# NOTE ON DOLLAR SIGNS: Streamlit markdown parses $...$ as LaTeX. Any literal
# dollar amount inside st.write/markdown/success/error/info/warning must be
# escaped as \$ or the text between two of them silently becomes an equation.
# st.metric, st.code and st.dataframe are unaffected.


def d(x, dp=2):
    """Escaped dollar amount, safe inside markdown."""
    return f"\\${x:,.{dp}f}"


# Each page needs its own config. Streamlit only runs the entrypoint when you
# land on it, so arriving here by deep link — which is what shared links do —
# would otherwise leave the default favicon and title. Must be the first
# Streamlit command executed in this file.
st.set_page_config(
    page_title="Tragic Algebra & IV15 Analyzer — Michael Burry Owners' Earnings Calculator",
    page_icon="🎯",
    layout="centered",
    initial_sidebar_state="collapsed",
)
st.title("🎯 Tragic Algebra Analyzer")
st.caption("True owners' earnings after stock compensation, then the price ladder that follows")


if not _sec_contact():
    st.warning(
        "**No SEC contact address set.** The SEC requires a real email in the request header "
        "and blocks generic user agents, so lookups will fail. Add `sec_contact = "
        "\"you@example.com\"` in Streamlit Settings → Secrets, or set a SEC_CONTACT "
        "environment variable locally."
    )

mode = st.radio("mode", ["Single stock", "Watchlist"], horizontal=True,
                label_visibility="collapsed")

# ══════════════════════════════════════════════════════════════════════
#  WATCHLIST — screens on Tragic Algebra alone, which needs no judgement
# ══════════════════════════════════════════════════════════════════════
if mode == "Watchlist":
    st.caption(
        "ΔE needs no assumptions from you — it is arithmetic on the filings. That makes it the "
        "one thing here worth running across a whole list at once. Ranked worst first, because "
        "the bottom of this table is where the money quietly leaves."
    )
    # A form lets Ctrl+Enter inside the box submit, matching the hint Streamlit
    # shows under a text area. The button still works for anyone who prefers it.
    with st.form("screen"):
        raw = st.text_area("Tickers", placeholder="ADBE, CRM, NOW, GOOGL, META, WDAY",
                           help="Separated by commas, spaces or new lines. Up to 25. "
                                "Ctrl+Enter to run.")
        w_tier = st.selectbox(
            "Assume this tier for every name", list(AICT), index=2,
            format_func=lambda t: f"{t} — {TIER_BLURB[t]}",
            help="Tier is a per-company judgement, so one setting across a list is a rough "
                 "cut. Open anything interesting in Single stock and set its tier properly.")
        screen = st.form_submit_button("Screen", type="primary")
    if screen:
        tickers = [t.strip().upper() for t in raw.replace(",", " ").replace("\n", " ").split()]
        tickers = list(dict.fromkeys([t for t in tickers if t]))[:25]
        if not tickers:
            st.warning("Enter at least one ticker.")
        else:
            rows, failed, bar = [], [], st.progress(0.0, "Reading filings…")
            for i, tk_ in enumerate(tickers):
                bar.progress((i + 1) / len(tickers), f"{tk_} ({i+1} of {len(tickers)})")
                try:
                    ys, _, pf = load(tk_, 10)
                    full = pool(ys)
                    rec = pool_recent(ys, 3) if len(ys) >= 3 else full
                    latest = ys[-1]
                    px_ = current_price(tk_) or 0.0
                    sh_ = pf.get("shares") or 0.0
                    fwd_ = latest.N

                    # IV15 is only shown where the automatic inputs can be
                    # trusted. Printing a confident number for a company whose
                    # earnings base is broken is worse than printing nothing.
                    iv, pv, er_, why = None, None, None, ""
                    use = rec.dE if 0 < rec.dE <= 1.25 else (full.dE if 0 < full.dE <= 1.25 else None)
                    oe_ = fwd_ * use if use else (pf.get("median_OE") or 0.0)
                    if pf.get("financial"):
                        why = "financial — framework does not apply"
                    elif sh_ <= 0 or px_ <= 0:
                        why = "no share count or price"
                    elif oe_ <= 0:
                        why = "owners' earnings need setting by hand"
                    elif not (2 <= (sh_ * px_) / fwd_ <= 150 if fwd_ > 0 else False):
                        why = "earnings base looks misread"
                    else:
                        p_ = IVParams(OE=oe_, shares=sh_, tier=w_tier,
                                      growth=pf.get("growth", 0.08),
                                      net_cash=pf.get("net_cash", 0.0),
                                      exit_multiple=AICT[w_tier].default_exit_multiple,
                                      blend=0.5)
                        iv = intrinsic_value(p_, 15)
                        if iv != iv or iv <= 0 or px_ / iv > 20:
                            iv, why = None, "inputs give an implausible value"
                        else:
                            pv = px_ / iv
                            e = expected_return(px_, p_)
                            er_ = None if e == float("inf") else e

                    rows.append({
                        "Ticker": tk_,
                        "ΔE full": full.dE,
                        "ΔE 3y": rec.dE,
                        "Owners' earnings": latest.OE,
                        "True SBC cost": full.sum_omega,
                        "GAAP says": full.sum_G,
                        "Price": px_ or None,
                        "IV15": iv,
                        "P/IV15": pv,
                        "Expected return": er_,
                        "Verdict": ("Tragic tier" if full.tragic_tier
                                    else "ΔE not meaningful" if full.dE > 1.25
                                    else "Below break-even" if full.dE < 1 / 1.15
                                    else "Passes"),
                        "IV15 note": why,
                    })
                except Exception as e:
                    failed.append(f"{tk_}: {e}")
            bar.empty()
            st.session_state["screen_results"] = (rows, failed)

    rows, failed = st.session_state.get("screen_results", ([], []))
    if rows:
        df = pd.DataFrame(rows).sort_values("ΔE full")
        st.dataframe(df.style.format({
            "ΔE full": "{:.1%}", "ΔE 3y": "{:.1%}", "Owners' earnings": "{:,.0f}",
            "True SBC cost": "{:,.0f}", "GAAP says": "{:,.0f}", "Price": "${:,.2f}",
            "IV15": "${:,.2f}", "P/IV15": "{:.2f}x", "Expected return": "{:.1%}"}, na_rep="—"),
            width="stretch", hide_index=True)

        n_tragic = sum(r["Verdict"] == "Tragic tier" for r in rows)
        n_below = sum(r["Verdict"] == "Below break-even" for r in rows)
        k = st.columns(3)
        k[0].metric("Screened", len(rows))
        k[1].metric("Below 87% break-even", n_below + n_tragic)
        k[2].metric("Tragic tier", n_tragic)
        st.caption(
            "ΔE is arithmetic and can be trusted. **IV15 here is indicative only** — it uses one "
            "tier for every name, growth seeded from revenue, and owner earnings straight from "
            "ΔE, with no normalising. It is blank wherever those inputs cannot be trusted, and "
            "the reason is in the last column. Treat it as a sort order, not a valuation, and "
            "open anything interesting in Single stock.\n\n"
            "Below 87%, a company needs 15% reported growth just to hold intrinsic value per "
            "share steady. Tragic tier means owners' earnings were negative across the whole "
            "window — shareholders funded employee pay."
        )
        st.download_button("Download CSV", df.to_csv(index=False),
                           "tragic-algebra-screen.csv", "text/csv")
    if failed:
        with st.expander(f"{len(failed)} could not be read"):
            for f in failed:
                st.write("· " + f)
    st.stop()

if "years" not in st.session_state:
    st.info(
        "**Reported profit is not what reaches you.** Shares handed to employees cost real "
        "money the income statement never shows. This works out what is left, then the price "
        "at which the stock would return about 15% a year over the long run.\n\n"
        "Enter a US-listed ticker to start, or switch to Watchlist to screen many at once. "
        "Built for software and other operating companies — banks, insurers and REITs need "
        "tools this one does not contain."
    )

# A form submits on Enter as well as on the button click.
with st.form("lookup"):
    ticker = st.text_input("Stock ticker",
                           placeholder="ADBE · CRM · NOW · GOOGL — press Enter").upper().strip()
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
                yrs, notes, pre = load(ticker, 10)
            st.session_state.update(years=yrs, notes=notes, pre=pre, tk=ticker)
        except ValueError as e:
            st.error(f"Could not load {ticker}: {e}")
        except Exception as e:
            st.error(
                f"Could not load {ticker} — {type(e).__name__}: {e}\n\n"
                "This is a gap in how the filings were read, not something you did. Filers with "
                "several share classes, recent listings and foreign issuers are the usual "
                "causes. The Watchlist tab will skip a name like this and carry on.")

years = st.session_state.get("years", [])
if years and ticker and st.session_state.get("tk") == ticker:
    notes, pre, tk = st.session_state["notes"], st.session_state["pre"], st.session_state["tk"]
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
    dE_ok = 0.0 < use_dE <= DE_UNUSABLE_ABOVE
    # The measurement above is left as filed; only what gets projected is held
    # to 100%. See the dE ceiling block near the top of the file.
    applied_dE = seed_dE(use_dE) if dE_ok else use_dE
    dE_capped = dE_was_capped(use_dE)

    hist = sorted(y.OE for y in years[-5:])
    median_OE = hist[len(hist) // 2] if hist else 0.0

    c1, c2, c3 = st.columns(3)
    fwd_N = c1.number_input("Forward net income ($M)", value=float(round(years[-1].N, 1)), step=10.0,
                            help="Next year's expected GAAP net income.")
    if dE_ok:
        derived = fwd_N * applied_dE
    elif median_OE > 0:
        derived = median_OE
    else:
        # Every recent year is negative. Seeding zero makes IV15 collapse to
        # net cash per share, which looks like an answer but is not one.
        # Forward net income is at least a defensible ceiling to revise down from.
        derived = fwd_N
    OE = c1.number_input("Owners' earnings ($M)", value=float(round(derived, 1)), step=1.0,
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
    price = c3.number_input("Price", value=float(current_price(tk) or 100.0), step=0.01)
    cash = c3.number_input("Cash & investments ($M)", value=float(round(pre.get("cash", 0.0), 1)),
                           step=10.0, help="Only what is freely deployable. Restricted, regulated "
                                           "and operationally-tied cash funds the business.")
    debt = c2.number_input("Total debt ($M)", value=float(round(pre.get("debt", 0.0), 1)),
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
            "calculated; these three are judgement, and he has never published his choices.\n\n"
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
        t = AICT[tier_name]
        st.caption(f"{tier_name}: stage 1 {t.stage1_years}y, stage 2 {t.stage2_years}y at "
                   f"{t.stage2_multiplier:.2f}x, terminal cap {t.terminal_growth_cap:.0%}, "
                   f"total horizon {t.horizon} years.")
        _l1, _l2 = model_legs(IVParams(OE=OE, shares=shares, tier=tier_name, growth=growth,
                                       net_cash=net_cash, exit_multiple=exit_m, blend=blend,
                                       m2_style=m2_style))
        if _l1 == _l1 and _l2 == _l2:
            st.caption(f"Long-horizon leg ${_l1:,.2f} · exit-multiple leg ${_l2:,.2f}. "
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
        alerts.append(("warning",
            f"ΔE measured {use_dE:.1%} over this window — shareholders kept more than the "
            "company reported earning, which happens when buybacks retire more stock than the "
            "year issues. That is a real reading of what happened and the figures above are "
            f"left as filed. But it is not projectable: owners' earnings are seeded at "
            f"{DE_SEED_CEILING:.0%} of forward net income rather than {use_dE:.1%}, because "
            "fifteen years of handing owners more than the company earns is not a business "
            "model. Raise the box by hand if you believe the buyback pace continues."))
    elif median_OE > 0 and derived > 2 * median_OE:
        alerts.append(("warning",
            f"Derived owners' earnings of {d(derived,0)}M are {derived/median_OE:.1f}x the "
            f"{d(median_OE,0)}M median of the last five years. Forward profit may carry a "
            "one-off. Check the yearly table and override."))
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
                   m2_style=m2_style)
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

    v1, v2, v3 = st.columns(3)
    v1.metric("IV15", f"${iv15:,.2f}", f"market ${price:,.2f}")
    v2.metric("Price / IV15", f"{ratio:.2f}x", zn)
    v3.metric("Expected return", er_txt, f"score {valuation_points(ratio)}/35")
    if er == float("inf") or (price > 0 and iv15 / price > 20):
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
        .style.format({"Buy under": "${:,.2f}"}),
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
    q2.metric("True SBC cost", f"${pooled.sum_omega:,.0f}M", f"GAAP says ${pooled.sum_G:,.0f}M")
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
                                   exit_multiple=exit_m, blend=blend), 15)
    if siv == siv and siv > 0:
        t1, t2 = st.columns(2)
        t1.metric("Stressed IV15", f"${siv:,.2f}", f"{siv/iv15-1:+.1%}")
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
        st.dataframe(pd.DataFrame([{
            "FY": f"{y.fy}*" if y.excluded else str(y.fy),
            "Net income": y.N, "GAAP SBC": y.G, "Buybacks": y.T,
            "Share change": y.dS, "Avg price": y.price, "True SBC cost": y.omega,
            "Owners' earnings": y.OE,
            # Formatted here rather than left to the styler: st.dataframe does
            # not honour na_rep and prints a bare "None" into the cell, which
            # reads like a failure rather than a deliberate blank.
            "ΔE": _dE_text(dE_cell(y.N, y.dE, _med_N))} for y in years]).style.format({
                "Net income": "{:,.0f}", "GAAP SBC": "{:,.0f}", "Buybacks": "{:,.0f}",
                "Share change": "{:+,.1f}", "Avg price": "${:,.2f}", "True SBC cost": "{:,.0f}",
                "Owners' earnings": "{:,.0f}"}, na_rep="—"),
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
            f"mkt cap ${shares*price/1000:,.2f}B\n"
            f"forward net income  {fwd_N:,.0f}\n"
            f"ΔE applied          {applied_dE:.1%}"
            + (f" (capped from {use_dE:.1%})" if dE_capped else "   ")
            + f"   (full {pooled.dE:.1%} / 3y {recent.dE:.1%})\n"
            f"median OE, 5y       {median_OE:,.0f}\n"
            f"owners' earnings    {OE:,.0f}   ({OE/shares:,.2f}/share)\n"
            f"net cash            {net_cash:,.0f}   ({net_cash/shares:,.2f}/share)\n"
            f"tier                {tier_name}   growth {growth:.2%}\n"
            f"exit multiple       {exit_m:g}x   blend {blend:g}   leg {m2_style}\n"
            f"IV15                {iv15:,.2f}   P/IV15 {ratio:.2f}x", language="text")

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
