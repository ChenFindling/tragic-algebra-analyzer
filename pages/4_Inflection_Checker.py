"""
Inflection Checker
==================
A page for companies whose income statement looks terrible but whose trend
tells a story: revenue compounding, losses narrowing, operating leverage
appearing. It answers two questions in order — is that story actually visible
in the annual filings, and if it continues, what is it worth at 15%.

THREE PARTS, AND WHICH IS WHOSE
-------------------------------
1. The trend evidence is this app's own design. Ten years of GAAP operating
   margin, gross margin, incremental margin, total costs against revenue,
   cash burn against cash, and dilution as the price of the runway. No
   published framework — it is what a careful reader tabulates.
2. The pricing is Burry's Stage 0, on tool 1's engine: project the margin
   geometrically from the company's own trend to a stated terminal margin
   over stated years, then run the normal stages at the 15% required return.
   A geometric margin path times a geometric revenue path IS a constant
   growth rate, so IVParams.stage0_years / stage0_growth carry it exactly.
3. The refusals are this app's, and they fire often. The rule that does not
   move: the page never prints a number it cannot stand behind.

The reader below is tool 1's, copied verbatim (a Streamlit page cannot be
imported without executing its UI) with four lines added that tools 1 and 2
do not fetch: operating income, gross profit / cost of revenue, cash from
operations and capital expenditure. Everything else — the Year dataclass,
pooling, the IV ladder, the seed helper, split handling, the share ladder,
the tag panel — is unchanged, and the self-test pins the ported engine
against tool 1's Alphabet figures.

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
    # ── Added for this page only (31 Aug 2026). Tools 1 and 2 do not read an
    # operating line; this page projects one. Signed, NOT abs() — a loss is
    # the whole point. Gross profit is optional: many filers tag only cost
    # of revenue, so the page derives it and says so; where neither is tagged
    # the gross-margin cell is refused rather than guessed.
    "OI":   (["OperatingIncomeLoss"], ["ProfitLossFromOperatingActivities"]),
    "GP":   (["GrossProfit"], ["GrossProfit"]),
    "COGS": (["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold",
              "CostOfServices"], ["CostOfSales"]),
    "CFO":  (["NetCashProvidedByUsedInOperatingActivities",
              "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
             ["CashFlowsFromUsedInOperatingActivities"]),
    "CAPEX": (["PaymentsToAcquirePropertyPlantAndEquipment",
               "PaymentsToAcquireProductiveAssets"],
              ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"]),
    # IFRS names on OI, COGS, CFO and CAPEX (1 Sep 2026): Grab reports in
    # USD under IFRS, so tool 1's net-income fallback let it load and this
    # page then read no operating line at all.
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


FILL_KEYS = {"T", "Cw", "Ce", "DIV", "INT", "LEASEPAY", "CAPEX", "MA", "OFFER", "CONV", "G", "N",
             "OI", "GP", "COGS", "CFO"}   # the four lines this page adds


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
    "OI": "Operating income", "GP": "Gross profit", "COGS": "Cost of revenue",
    "CFO": "Cash from operations",
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

    PORTED FROM PAGE 6, 5 Sep 2026 (its 4 Sep edit). Only ratios split_adjust's band lets
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


def load(ticker: str, n_years: int = 10):
    cmap = _ticker_map()
    resolved = resolve_ticker(ticker, cmap)
    if resolved is None:
        raise ValueError(
            f"'{ticker}' is not in the SEC company list. Class shares are listed with a "
            "hyphen rather than a dot — BRK-B, BF-B, HEI-A — and both spellings are "
            "accepted here, so this is more likely a delisted, foreign or private company.")
    ticker = resolved
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

    # PORTED FROM PAGE 6, 5 Sep 2026: the 2:1 pass, market-confirmed. See
    # confirm_band_splits — Novo Nordisk's 2023 2-for-1 sat inside the
    # band and read as an acquisition until this; a US filer with a plain
    # 2:1 hits the identical mislabel.
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

    if "ProceedsFromIssuanceOfCommonStock" in tag_sources.get("Ce", []):
        # Carvana, 1 Sep 2026 (page 4's run): the broad issuance-proceeds tag
        # was the only Ce name that answered, and it carried the ATM equity
        # programme — hundreds of millions a year of capital raising read as
        # option and ESPP proceeds. Omega came out at -1,751M over FY2024-25:
        # the raise was credited to owners' earnings. Same disease, same cure
        # as the treasury-as-withholding gate above: genuine employee proceeds
        # are small next to the GAAP charge; a raise is not. Sized against the
        # charge where there is one, net income where there is not. The gate
        # runs only when the broad tag is a Ce source at all — the narrow
        # names alone are never gated, matching the Cw gate's own behaviour.
        _ce_capped = 0
        for y in years:
            if not y.Ce:
                continue
            if (y.Ce > 3 * y.G) if y.G > 0 else (y.Ce > 0.10 * abs(y.N)):
                y.Ce, _ce_capped = 0.0, _ce_capped + 1
        if _ce_capped:
            notes.append(
                f"An issuance-proceeds line was read as option and ESPP proceeds and rejected "
                f"in {_ce_capped} year(s): it was more than three times the GAAP stock-comp "
                "charge, or — where no charge was tagged to size it against — more than a "
                "tenth of net income. Proceeds of that size are an equity raise — an offering "
                "or an ATM programme — tagged under the broad issuance name, not employee "
                "exercises, and crediting them to owners' earnings would book the raise as "
                "profit. Those years' true SBC cost is computed without the credit.")

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
    # ── Added for this page: the operating lines by fiscal year, in $M, for
    # the same window as `years`. Absent means the tag did not answer for that
    # year; the page refuses the cell rather than reading zero. OI, GP and
    # CFO keep their sign; cost of revenue and capex are outflows and read
    # positive. `shares_by_fy` is the split-restated year-end count the page
    # shows dilution from — the same series every dS above was built on.
    _signed = lambda k, fy: (series[k][fy][2] / 1e6) if fy in series.get(k, {}) else None
    _outflow = lambda k, fy: (abs(series[k][fy][2]) / 1e6) if fy in series.get(k, {}) else None
    _trend = {fy: {"rev": _signed("REV", fy), "oi": _signed("OI", fy),
                   "gp": _signed("GP", fy), "cogs": _outflow("COGS", fy),
                   "cfo": _signed("CFO", fy), "capex": _outflow("CAPEX", fy)}
              for fy in fys}
    _shares_by_fy = {fy: shares_out[fy] / 1e6 for fy in fys if fy in shares_out}
    return years, notes, {"tags": tags, "net_cash": net_cash, "cash": cash_total, "debt": debt_total,
                          "trend": _trend, "shares_by_fy": _shares_by_fy,
                          "median_OE": _med, "revenue": latest_rev, "cagr3": cagr3,
                          "leases": lease_total,
                          # The form that resolved against the SEC list. Yahoo uses the
                          # same hyphenated spelling, so pricing BRK.B as typed returned
                          # nothing and the page fell back to its $100.00 default beside
                          # a real market cap.
                          "ticker": ticker,
                          "shares": diluted, "growth": growth, "sic": sic,
                          "sic_desc": sic_desc, "financial": is_financial(sic)}


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


# ══════════════════════════════════════════════════════════════════════
#  INFLECTION — the trend evidence (this app's design, not Burry's)
# ══════════════════════════════════════════════════════════════════════
#
# Everything from here to the self-test is new. The reader above is tool
# 1's. The line between the two matters: nothing below changes a figure
# the other pages compute, and the year-by-year table this page prints is
# identical to tool 1's for the same ticker — the self-test pins it.
#
# What is projected, and why that choice is ours. Burry says Stage 0
# projects "the trajectory of the prior few years' margin as well as
# mature competitors' margin ... geometrically" and does not say which
# margin. This page uses GAAP OPERATING margin: it is one tag in every
# 10-K, it sits above interest, tax and one-offs (Uber's FY2024 tax
# benefit cannot pollute it), and it is what anyone means by "a mature
# competitor earns 30%". Net margin is shown beside it and drives nothing.

def d(x, dp=2):
    """Escaped dollar amount, safe inside markdown."""
    return f"\\${x:,.{dp}f}"


TREND_STEPS = 3            # the trend rule reads the last four years, three steps
STAGE0_DEFAULT_YEARS = 5   # a convention in a judgement box, not evidence
STAGE0_GROWTH_CAP = 0.50   # revenue growth through Stage 0 is capped here for the seed
STAGE1_GROWTH_CAP = 0.25   # tool 1's own cap for the seed after Stage 0
TAX_DEFAULT = 0.21         # US statutory; Burry normalises per company (MSFT 19%, ADBE 18%)
INCR_MIN_REV_CHANGE = 0.05 # revenue must move 5% for an incremental-margin cell to mean anything
BELOW_LINE_MIN = 1.05      # net income above operating income by this much -> something is below the line


@dataclass
class TrendYear:
    """One fiscal year of the operating lines, $M. None means the tag did
    not answer for that year — the cell is refused, never read as zero."""
    fy: int
    rev: float | None
    oi: float | None
    gp: float | None
    gp_source: str          # "GrossProfit", "revenue − cost of revenue", or ""
    cfo: float | None
    capex: float | None
    N: float                # net income, from the Year row
    omega: float            # true SBC cost, from the Year row
    shares: float | None    # year-end count, M, split-restated
    excluded: str           # carried from the Year row; trend columns ignore it

    @property
    def opm(self) -> float | None:
        return self.oi / self.rev if self.rev and self.oi is not None else None

    @property
    def gm(self) -> float | None:
        return self.gp / self.rev if self.rev and self.gp is not None else None

    @property
    def nm(self) -> float | None:
        return self.N / self.rev if self.rev else None

    @property
    def costs(self) -> float | None:
        """Total costs = revenue − operating income. An identity, so exact."""
        return self.rev - self.oi if self.rev is not None and self.oi is not None else None

    @property
    def fcf(self) -> float | None:
        if self.cfo is None:
            return None
        return self.cfo - (self.capex or 0.0)

    @property
    def omega_pct(self) -> float | None:
        return self.omega / self.rev if self.rev else None


def gross_profit(rev: float | None, gp: float | None, cogs: float | None) -> tuple[float | None, str]:
    """Tagged gross profit first; revenue less cost of revenue second; refused third."""
    if gp is not None:
        return gp, "GrossProfit"
    if rev is not None and cogs is not None:
        return rev - cogs, "revenue − cost of revenue"
    return None, ""


def build_trend(years: list[Year], trend: dict, shares_by_fy: dict) -> list[TrendYear]:
    rows = []
    for y in years:
        t = trend.get(y.fy, {})
        gp, src = gross_profit(t.get("rev"), t.get("gp"), t.get("cogs"))
        rows.append(TrendYear(fy=y.fy, rev=t.get("rev"), oi=t.get("oi"), gp=gp, gp_source=src,
                              cfo=t.get("cfo"), capex=t.get("capex"), N=y.N, omega=y.omega,
                              shares=shares_by_fy.get(y.fy), excluded=y.excluded))
    return rows


def growth_pct(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or prev <= 0:
        return None
    return cur / prev - 1.0


def incremental_margin(prev: TrendYear, cur: TrendYear) -> float | None:
    """How much of each new revenue dollar reached operating income.

    Refused when revenue fell, or moved less than INCR_MIN_REV_CHANGE — a
    full-year swing in costs divided by a 1% change in revenue is a number
    that describes the denominator, not the company. Same reasoning as
    tool 1's dE_cell.
    """
    if None in (prev.rev, cur.rev, prev.oi, cur.oi) or prev.rev <= 0:
        return None
    d_rev = cur.rev - prev.rev
    if d_rev <= 0 or d_rev < INCR_MIN_REV_CHANGE * prev.rev:
        return None
    return (cur.oi - prev.oi) / d_rev


def window_incremental(rows: list[TrendYear]) -> float | None:
    """Incremental margin from the first year with both lines to the last."""
    have = [r for r in rows if r.rev is not None and r.oi is not None]
    if len(have) < 2:
        return None
    a, b = have[0], have[-1]
    d_rev = b.rev - a.rev
    if d_rev <= 0:
        return None
    return (b.oi - a.oi) / d_rev


def post_crossing_incremental(rows: list[TrendYear], cross_fy: int | None) -> float | None:
    """Incremental margin on the revenue added AFTER the crossing — first
    profitable year to the latest, at least two years. TG Therapeutics,
    1 Sep 2026: the window incremental read 76.8% because the window opened
    at 7M of revenue and most of the 468M swing was the loss disappearing;
    on the 382M added after the crossing the company kept 26.7%."""
    if cross_fy is None:
        return None
    have = [r for r in rows if r.fy >= cross_fy and r.rev is not None and r.oi is not None]
    if len(have) < 2:
        return None
    a, b = have[0], have[-1]
    d_rev = b.rev - a.rev
    if d_rev <= 0 or d_rev < INCR_MIN_REV_CHANGE * a.rev:
        return None
    return (b.oi - a.oi) / d_rev


def margin_pace(margins: list[float]) -> float | None:
    """Average step in operating margin over the last TREND_STEPS steps, as a fraction."""
    if len(margins) < TREND_STEPS + 1:
        return None
    m = margins[-(TREND_STEPS + 1):]
    return sum(m[i + 1] - m[i] for i in range(TREND_STEPS)) / TREND_STEPS


def runway_years(cash: float, cfo: float | None, capex: float | None) -> tuple[float | None, float]:
    """(years of cash at the latest burn, the burn). None where there is no burn."""
    if cfo is None:
        return None, 0.0
    fcf = cfo - (capex or 0.0)
    if fcf >= 0:
        return None, 0.0
    burn = -fcf
    # Cash at or below zero is a balance that was not read, not a balance
    # of nothing — Grab, 1 Sep 2026, "0M of cash lasts 0.0 years" for a
    # company holding billions under IFRS tag names this reader lacks.
    return (cash / burn if cash > 0 else None), burn


# ══════════════════════════════════════════════════════════════════════
#  SHAPES — where each sign pattern of operating income is sent
# ══════════════════════════════════════════════════════════════════════
#
# Classified on the SIGN of operating income across the window, oldest to
# newest. Never on ΔE, because ΔE across loss years is exactly what §1 of
# the brief says this page must not project.

SHAPE_ALL_POSITIVE = "all-positive"
SHAPE_PROFIT_THEN_LOSS = "profitable-then-loss"
SHAPE_DIP = "dip-and-recovery"
SHAPE_INFLECTION = "inflection"
SHAPE_ALL_NEGATIVE = "all-negative"
SHAPE_NOISY = "noisy"
SHAPE_TOO_FEW = "too-few"
SHAPE_STALE = "stale-crossing"

SENT_TO_TOOL_1 = (SHAPE_ALL_POSITIVE, SHAPE_PROFIT_THEN_LOSS, SHAPE_DIP, SHAPE_STALE)


def _span(a: int, b: int) -> str:
    return f"in FY{a}" if a == b else f"from FY{a} to FY{b}"


def _fy_range(fys: list[int]) -> str:
    """FY2025, or FY2023–FY2025. Remitly printed 'FY2025–FY2025'."""
    return f"FY{fys[0]}" if len(fys) == 1 else f"FY{fys[0]}–FY{fys[-1]}"


def op_shape(pts: list[tuple[int, float]]) -> tuple[str, str]:
    """(kind, sentence naming the years and signs) for [(fy, operating income)]."""
    pts = [(fy, oi) for fy, oi in pts if oi is not None]
    if len(pts) < 2:
        return SHAPE_TOO_FEW, "Fewer than two years of operating income could be read."
    pos = [oi > 0 for _, oi in pts]
    fys = [fy for fy, _ in pts]
    crossings = sum(1 for i in range(1, len(pos)) if pos[i] != pos[i - 1])
    first, last = pos[0], pos[-1]
    signs = ", ".join(f"FY{fy} {'+' if p else '−'}" for (fy, _), p in zip(pts, pos))
    if all(pos):
        return SHAPE_ALL_POSITIVE, (
            f"Operating income was positive in every year from FY{fys[0]} to FY{fys[-1]}. "
            "There is no loss to inflect from — this company does not need this page.")
    if first and not last:
        neg_from = next(fy for fy, p in zip(fys, pos) if not p)
        return SHAPE_PROFIT_THEN_LOSS, (
            f"Operating income was positive at FY{fys[0]} and negative at FY{fys[-1]} — losses "
            f"appeared on a profitable record from FY{neg_from}. That is deterioration or a "
            "write-down, not an inflection.")
    if first and last:
        loss_fys = [fy for fy, p in zip(fys, pos) if not p]
        return SHAPE_DIP, (
            f"Operating income was positive at both ends of the window with a loss in "
            f"FY{', FY'.join(str(f) for f in loss_fys)} — a loss year on a profitable record. "
            "Tool 1's seed helper already handles this shape.")
    if not first and last:
        if crossings == 1:
            cross = next(fy for fy, p in zip(fys, pos) if p)
            # Crocs, 1 Sep 2026: a -6M operating loss on 1,036M of revenue in
            # FY2016, nine profitable years after it, and the page called it
            # "one crossing, at FY2017". The trend rule reads the last four
            # years; a crossing older than that is outside the evidence this
            # page reasons from, and a margin still rising from a profitable
            # base is tool 1's growth rate, with its own Stage 0 control.
            window = fys[-(TREND_STEPS + 1):]
            if len(fys) > TREND_STEPS and cross < window[0] + 1:
                return SHAPE_STALE, (
                    f"Operating income crossed to positive at FY{cross}, and every year of the "
                    f"trend window (FY{window[0]}–FY{window[-1]}) is profitable — the turn is older "
                    f"than the four years this page reasons from. A margin path from a profitable "
                    "base is tool 1's growth rate; its Model settings carry a Stage 0 for a ramp "
                    "you judge is still ahead.")
            return SHAPE_INFLECTION, (
                f"Operating income was negative {_span(fys[0], cross - 1)} and positive "
                f"{_span(cross, fys[-1])} — one crossing, at FY{cross}.")
        return SHAPE_NOISY, (
            f"The sign of operating income changed {crossings} times across the window "
            f"({signs}). One crossing is an inflection; several is noise this page will not price.")
    if any(pos):
        return SHAPE_NOISY, (
            f"Operating income is negative at both ends of the window with profitable years in "
            f"between ({signs}) — {crossings} sign changes. Not a trend this page will price.")
    return SHAPE_ALL_NEGATIVE, (
        f"Operating income was negative in every year from FY{fys[0]} to FY{fys[-1]}.")


def crossing_year(pts: list[tuple[int, float]]) -> int | None:
    pts = [(fy, oi) for fy, oi in pts if oi is not None]
    for i in range(len(pts) - 1, -1, -1):
        if pts[i][1] <= 0:
            return pts[i + 1][0] if i + 1 < len(pts) else None
    return pts[0][0] if pts else None


# ══════════════════════════════════════════════════════════════════════
#  GATES — each returns the refusal sentence, or "" to pass
# ══════════════════════════════════════════════════════════════════════
#
# In order. The first that fires stops the pricing; the trend table above
# always prints. Every sentence names the years and the figures, because a
# refusal that does not say what it found is worse than no refusal.

def _pct(m: float) -> str:
    return f"{m:+.1%}" if m < 0 else f"{m:.1%}"


def margin_gate(margins: list[tuple[int, float]]) -> str:
    """The trend rule, on the last TREND_STEPS+1 years of operating margin.

    Three tests: the latest step must improve (a trend that has just broken
    is not one the filings support); it must not be the ONLY step that
    improved (one year is not a trend); and the margin at the end must be
    above the margin at the start, with at least two of the three steps
    improving (one dip is tolerated, flat or noisy is not).
    """
    pts = [(fy, m) for fy, m in margins if m is not None]
    if len(pts) < TREND_STEPS + 1:
        return (f"Only {len(pts)} year(s) of operating margin could be read; the trend rule "
                f"needs {TREND_STEPS + 1}.")
    w = pts[-(TREND_STEPS + 1):]
    steps = [(w[i + 1][0], w[i + 1][1] - w[i][1]) for i in range(TREND_STEPS)]
    improving = [s > 0 for _, s in steps]
    path = " → ".join(f"FY{fy} {_pct(m)}" for fy, m in w)
    if not improving[-1]:
        return (f"The latest year reversed the trend: operating margin went from "
                f"{_pct(w[-2][1])} in FY{w[-2][0]} to {_pct(w[-1][1])} in FY{w[-1][0]}. "
                f"Path: {path}.")
    if sum(improving) == 1:
        return (f"One year is not a trend: operating margin improved only in the latest step, "
                f"FY{w[-2][0]} {_pct(w[-2][1])} → FY{w[-1][0]} {_pct(w[-1][1])}, after two steps "
                f"that did not. Path: {path}.")
    if w[-1][1] <= w[0][1]:
        return (f"Flat or noisy: operating margin is {_pct(w[-1][1])} in FY{w[-1][0]} against "
                f"{_pct(w[0][1])} in FY{w[0][0]}, {sum(improving)} of {TREND_STEPS} steps "
                f"improving. Path: {path}.")
    return ""


def coverage_gate(rows: list[TrendYear]) -> str:
    """Operating income read but revenue not (or the reverse) — say which
    line is the gap, before the trend rule counts margins. Sophia Genetics,
    1 Sep 2026: six years of operating income, no revenue under an IFRS name
    this reader lacks, and the page said 'only 0 years of operating margin'."""
    oi_n = sum(1 for r in rows if r.oi is not None)
    rev_n = sum(1 for r in rows if r.rev is not None and r.rev != 0)
    both = sum(1 for r in rows if r.opm is not None)
    if both >= TREND_STEPS + 1 or oi_n == both or oi_n < TREND_STEPS + 1:
        return ""
    return (f"Operating income was read for {oi_n} years but revenue for {rev_n}, so a margin "
            f"exists for only {both}; the trend rule needs {TREND_STEPS + 1}. The revenue line is "
            "the gap — the tag panel names what answered.")


def margin_trend_sentence(margins: list[tuple[int, float]]) -> str:
    """What a PASSING trend looks like, in words, for the page."""
    pts = [(fy, m) for fy, m in margins if m is not None][-(TREND_STEPS + 1):]
    steps = sum(1 for i in range(len(pts) - 1) if pts[i + 1][1] > pts[i][1])
    path = " → ".join(f"FY{fy} {_pct(m)}" for fy, m in pts)
    return (f"Operating margin improved in {steps} of {len(pts) - 1} steps, {_pct(pts[0][1])} to "
            f"{_pct(pts[-1][1])}: {path}.")


def revenue_gate(revs: list[tuple[int, float]]) -> str:
    """This page prices growth-driven leverage. Without revenue growth the
    leverage has no engine, and a margin recovery on flat revenue is a cost
    story — tool 1's median seed, not a Stage 0."""
    pts = [(fy, r) for fy, r in revs if r is not None]
    if len(pts) < TREND_STEPS + 1:
        return f"Only {len(pts)} year(s) of revenue could be read; the trend rule needs {TREND_STEPS + 1}."
    w = pts[-(TREND_STEPS + 1):]
    if w[0][1] <= 0 or w[-2][1] <= 0:
        return f"Revenue reads zero or negative in FY{w[0][0]} or FY{w[-2][0]} — no base to grow from."
    cagr = (w[-1][1] / w[0][1]) ** (1 / TREND_STEPS) - 1
    latest = w[-1][1] / w[-2][1] - 1
    if cagr <= 0 or latest <= 0:
        return (f"Revenue is not compounding: {w[0][1]:,.0f}M in FY{w[0][0]} to {w[-1][1]:,.0f}M "
                f"in FY{w[-1][0]} ({cagr:.1%} a year), {latest:+.1%} in the latest year. A margin "
                "that improves on flat revenue is a cost story, and this page prices growth-driven "
                "leverage.")
    return ""


def start_gate(fy: int, opm: float | None) -> str:
    """A geometric path cannot begin at or below zero. No extrapolation to
    break-even is printed: for Rivian the trailing pace is dominated by one
    early step and would print a number that reads as a forecast."""
    if opm is None:
        return f"Operating margin for FY{fy} could not be read."
    if opm <= 0:
        return (f"No positive start: operating margin was {_pct(opm)} in FY{fy}. A margin path "
                "projected geometrically to a terminal margin has to begin above zero, and this "
                "one does not. Nothing to price.")
    return ""


@dataclass
class OpPooled:
    """ΔE on the base this page projects. Burry's OE = N + G − Ω with N
    replaced by normalised after-tax operating income, pooled over the
    profitable years since the crossing. TG Therapeutics, 1 Sep 2026: on
    net income the FY2023–25 pool read 56.7% and priced a Fat Pitch, and
    FY2025 net income was 3.6x operating income on a tax benefit; take the
    benefit out and the pool is negative. A ratio measured on one base and
    applied to another is a number the page cannot stand behind."""
    fys: list[int]
    sum_base: float     # Σ operating income × (1 − tax)
    sum_G: float
    sum_omega: float
    # Years in the pool whose share change could not be measured — no
    # year-end count for the year or the one before. Carvana, 1 Sep 2026:
    # no count in any of ten years, so V read zero everywhere, Ω came out
    # at −1,751M (equity-raise proceeds read as option proceeds, minus
    # withholding) and "ΔE" printed 185.4%. A ratio whose inputs are
    # missing is not a measurement, and it must not seed the box.
    missing_shares: list[int] = field(default_factory=list)

    @property
    def measurable(self) -> bool:
        return not self.missing_shares

    @property
    def years(self) -> int:
        return len(self.fys)

    @property
    def sum_OE(self) -> float:
        return self.sum_base + self.sum_G - self.sum_omega

    @property
    def dE(self) -> float:
        return self.sum_OE / self.sum_base


def operating_pool(years: list[Year], rows: list[TrendYear], since_fy: int | None,
                   tax: float, shares_by_fy: dict | None = None) -> OpPooled | None:
    by_fy = {r.fy: r for r in rows}
    kept = [(y, by_fy[y.fy]) for y in years
            if y.fy in by_fy and by_fy[y.fy].oi is not None and by_fy[y.fy].oi > 0
            and not y.excluded and (since_fy is None or y.fy >= since_fy)]
    if not kept:
        return None
    sbf = shares_by_fy or {}
    missing = ([y.fy for y, _ in kept if y.fy not in sbf or y.fy - 1 not in sbf]
               if shares_by_fy is not None else [])
    return OpPooled(fys=[y.fy for y, _ in kept],
                    sum_base=sum(r.oi * (1.0 - tax) for _, r in kept),
                    sum_G=sum(y.G for y, _ in kept),
                    sum_omega=sum(y.omega for y, _ in kept),
                    missing_shares=missing)


def financial_sentence(sic_desc: str | None, sic) -> str:
    """Tool 1's SIC 6000–6799 rule, worded for everyone it catches — Compass
    (6531, a brokerage) and Affirm (6141, a lender) were told their revenue
    was 'not an insurer's revenue'."""
    return (f"{sic_desc or 'Financial company'} (SIC {sic}). Tool 1 treats SIC 6000–6799 — "
            "banks, insurers, lenders, brokers, REITs — as financial: their investments back "
            "customer liabilities rather than belonging to shareholders, and for many of them "
            "the revenue concept this page's trend table is built on is not their revenue. Tool "
            "1 values them indicatively with net cash set to zero; this page does not price "
            "them at all, and the table is not shown.")


def shares_gate(shares: float) -> str:
    """Nothing per share exists without a count, and the true SBC cost —
    which needs the year-end count to price the shares delivered — cannot
    be measured either. Carvana reads no count from any tag this reader
    knows (a dual-class filer tagging each class with a dimension)."""
    if shares > 0:
        return ""
    return ("No share count was read from any tag this reader knows — the notes above say which "
            "years. Nothing per share can be computed, and the true SBC cost, which prices the "
            "shares delivered at the year's average, cannot be measured without the year-end "
            "count. Type the diluted count to continue; ΔE will still need setting by hand.")


def profitable_pool(years: list[Year], since_fy: int | None = None) -> tuple[Pooled | None, list[int]]:
    """ΔE pooled over the profitable years SINCE THE CROSSING only — net
    income above zero, from the year operating income turned positive, not
    excluded as a capital event. Loss years are never in this pool; the
    brief's rule is that ΔE is not projected from them.

    `since_fy` matters. Uber's FY2018 net income was positive on divestiture
    gains, five years before operating income crossed, and a pool that takes
    any year with N > 0 measured ΔE over FY2018–FY2025 and called it the
    post-inflection figure. A gain year before the turn is not evidence
    about what shareholders keep after it."""
    kept = [y for y in years if y.N > 0 and not y.excluded
            and (since_fy is None or y.fy >= since_fy)]
    if not kept:
        return None, []
    try:
        return pool(kept), [y.fy for y in kept]
    except ValueError:
        return None, [y.fy for y in kept]


def dE_gate(box_dE: float, measured, fys: list[int]) -> tuple[str, float]:
    """(refusal, applied ΔE). The box is seeded from the measured figure, so
    by default this refuses whenever owners' earnings have not inflected; a
    reader who types a positive ΔE takes Burry's judgement route and the
    assumptions block says so."""
    if box_dE <= 0:
        if measured is not None and getattr(measured, "missing_shares", []):
            _m = measured.missing_shares
            return (f"ΔE cannot be measured: no year-end share count for FY{', FY'.join(str(f) for f in _m)}"
                    + (" (or the year before)" if len(_m) == 1 else " (or the years before)")
                    + ", so the shares delivered in those years priced at zero and the true SBC cost of "
                    f"{measured.sum_omega:,.0f}M is not a measurement. The tag panel names what was read. "
                    "Set ΔE yourself to price this; the assumptions block will record it as set by hand."), 0.0
        if measured is None:
            return ("No profitable year to measure ΔE on, and the box is at or below zero. "
                    "Set ΔE yourself to price this; the assumptions block will record it as "
                    "set by hand."), 0.0
        if measured.dE > 0:
            # The box, not the filings, is what is at zero — say that, not
            # "have not inflected" about a pool that did.
            return (f"ΔE is set at {box_dE:.1%}; nothing above zero reaches shareholders on that "
                    f"input, so there is nothing to price. The measured figure over "
                    f"{_fy_range(fys)} is {measured.dE:.1%}."), 0.0
        _base = getattr(measured, "sum_base", None)
        _base_txt = (f"after-tax operating income totalled {_base:,.0f}M" if _base is not None
                     else f"net income totalled {measured.sum_N:,.0f}M")
        return (f"Owners' earnings have not inflected. Over {_fy_range(fys)} (the "
                f"profitable year{'s' if len(fys) > 1 else ''} since the crossing) {_base_txt} and the GAAP "
                f"stock-comp charge {measured.sum_G:,.0f}M, but the true SBC cost was "
                f"{measured.sum_omega:,.0f}M, so owners' earnings were {measured.sum_OE:,.0f}M "
                f"and ΔE {measured.dE:.1%}. GAAP inflected; what reaches shareholders did not. "
                "The page will not price that. Type a positive ΔE to take the judgement route "
                "Burry takes for DocuSign and ServiceNow — the assumptions block will record "
                "it as set by hand."), 0.0
    if box_dE > DE_UNUSABLE_ABOVE:
        return (f"ΔE of {box_dE:.1%} is above {DE_UNUSABLE_ABOVE:.0%}, tool 1's line past which "
                "share issuance is not being captured. A company cannot keep more than every "
                "reported dollar. Not projectable."), 0.0
    return "", seed_dE(box_dE)


def gross_margin_gate(terminal: float, gm: float | None) -> str:
    """An identity, so a refusal not a warning: operating margin cannot
    exceed gross margin."""
    if gm is not None and terminal > gm:
        return (f"Terminal operating margin of {terminal:.1%} is above the latest gross margin of "
                f"{gm:.1%}. Operating margin cannot exceed gross margin; no path reaches it.")
    return ""


def runway_gate(years_needed: int, runway: float | None, burn: float, cash: float) -> str:
    if burn > 0 and cash <= 0:
        return (f"Runway cannot be measured: a burn of {burn:,.0f}M (cash from operations less "
                "capex) is filed but no cash balance was read — the tag panel shows which lines "
                "answered. A path that needs funding cannot be priced without knowing the funding. "
                "Type the cash balance to continue.")
    if runway is None or years_needed <= 0:
        return ""
    if runway < years_needed:
        return (f"Runway shorter than the path: reaching the terminal margin needs {years_needed} "
                f"years, and {cash:,.0f}M of cash and investments lasts {runway:.1f} years at the "
                f"latest burn of {burn:,.0f}M (cash from operations less capex). The dilution "
                "that closes that gap is not in this projection. Shorten the path or it stands.")
    return ""


def pace_path_text(m_latest: float, pace: float | None, years: int, gm: float | None) -> str:
    """What the trailing pace would reach in `years` — but never a margin the
    identity forbids. TG Therapeutics printed "reaches 13118.4% in 5y": a
    pace dominated by the FY2022 step, extended past every ceiling there is.
    Above gross margin (or 100% where gross margin is unreadable) the text
    says so instead of printing the arithmetic."""
    if pace is None:
        return "—"
    path = m_latest + pace * years
    ceiling = gm if gm is not None else 1.0
    if path > ceiling:
        return (f"above {'gross margin' if gm is not None else '100%'} within {years}y")
    return f"{path:.1%} in {years}y"


def terminal_default(incr: float | None, m_latest: float, pace: float | None,
                     years: int, gm: float | None, post_incr: float | None = None) -> tuple[float, str]:
    """The lowest of three margins the filings show — the window incremental,
    the incremental on revenue added after the crossing, and the trailing
    pace extrapolated (latest margin plus pace × years) — then capped at
    gross margin. Each fails in a known way: the window figure counts the
    loss disappearing as margin on new revenue; the pace is dominated by one
    early step when losses were deep; the post-crossing figure is a single
    step when the turn is young. Taking the lowest is the conservative side,
    and all three are shown beside the box. Returns (value, what bound it)."""
    cands: list[tuple[float, str]] = []
    if incr is not None:
        cands.append((incr, "the window incremental margin"))
    if post_incr is not None:
        cands.append((post_incr, "the incremental margin since the crossing"))
    if pace is not None:
        cands.append((m_latest + pace * years, f"the trailing pace ({pace:+.1%} × {years}y)"))
    if not cands:
        return m_latest, "the latest margin — no trend to extend"
    v, src = min(cands)
    if gm is not None and v > gm:
        v, src = gm, "gross margin — the ceiling"
    if v < m_latest:
        v, src = m_latest, "the latest margin — neither candidate is above it"
    return v, src


# ══════════════════════════════════════════════════════════════════════
#  STAGE 0 — Burry's pricing step, on tool 1's engine
# ══════════════════════════════════════════════════════════════════════
#
# Margin goes geometrically from m0 to mT over n years; revenue grows at g.
# Then owners' earnings in year t are
#     R0 (1+g)^t · m0 (mT/m0)^(t/n) · (1−tax) · ΔE
# which is R0·m0·(1−tax)·ΔE times a constant factor to the t — a constant
# growth rate. So IVParams.stage0_growth carries the path exactly and
# intrinsic_value() runs untouched. The self-test builds the stream by hand
# and asserts the engine reproduces it.
#
# ΔE is held constant through Stage 0. As margins expand SBC usually falls
# as a share of profit, so ΔE rises; holding it is the conservative side.

def stage0_rate(g_rev: float, m0: float, mT: float, n: int) -> float:
    if m0 <= 0 or mT <= 0 or n < 1:
        raise ValueError("Stage 0 needs a positive starting margin, a positive terminal margin and n ≥ 1")
    return (1.0 + g_rev) * (mT / m0) ** (1.0 / n) - 1.0


def stage0_stream_explicit(rev0: float, m0: float, mT: float, n: int, g_rev: float,
                           tax: float, dE: float) -> list[float]:
    out = []
    for t in range(1, n + 1):
        rev_t = rev0 * (1.0 + g_rev) ** t
        m_t = m0 * (mT / m0) ** (t / n)
        out.append(rev_t * m_t * (1.0 - tax) * dE)
    return out


def oe_seed(rev0: float, m0: float, tax: float, dE: float) -> float:
    return rev0 * m0 * (1.0 - tax) * dE


def revenue_rates(rows: list[TrendYear]) -> tuple[float | None, float | None]:
    """(latest year-over-year growth, 3-year CAGR) from the trend rows — the
    same revenue series the reader used, recomputed here because load()
    returns tool 1's CAPPED growth and this page needs the filed rate.
    Palantir: the box seeded 25% (tool 1's cap) where the filed figures
    were 56.2% latest and 32.9% over three years."""
    revs = [r.rev for r in rows if r.rev is not None]
    latest = revs[-1] / revs[-2] - 1 if len(revs) >= 2 and revs[-2] > 0 and revs[-1] > 0 else None
    cagr3 = (revs[-1] / revs[-4]) ** (1 / 3) - 1 if len(revs) >= 4 and revs[-4] > 0 and revs[-1] > 0 else None
    return latest, cagr3


def stage0_growth_seed(latest: float | None, cagr3: float | None) -> tuple[float, float | None]:
    """(seed, the filed rate it came from): the lower of the latest year and
    the 3-year CAGR, capped at STAGE0_GROWTH_CAP, never below zero."""
    cands = [v for v in (latest, cagr3) if v is not None]
    if not cands:
        return 0.08, None
    raw = min(cands)
    return max(0.0, min(raw, STAGE0_GROWTH_CAP)), raw


# Tool 1's reader writes three notes about how TOOL 1 seeds growth — "the
# seed uses the recent rate", "capped at 25% for the seed ... use the
# hypergrowth years in Model settings instead". On this page every one of
# them misdescribes what happened: this page seeds from the lower of two
# rates, caps at 50%, and IS the Stage 0 those notes point to. A note that
# misdescribes what it found is worse than no note, so they are dropped
# here and the box carries the page's own sentence.
TOOL1_SEED_NOTE_PREFIXES = ("Revenue is ", "Latest revenue growth is ")


def page_notes(notes: list[str]) -> list[str]:
    return [n for n in notes if not n.startswith(TOOL1_SEED_NOTE_PREFIXES)]


def cell(v, fmt: str, blank: str = "—") -> str:
    """Text for one table cell. Streamlit's grid ignores the Styler's na_rep
    and prints the word None (or NaN) into a refused cell — tool 1 found
    this on its ΔE column, and Uber's gross-margin column, refused in every
    year, printed None nine times after coercing to NaN did nothing. So
    every cell is formatted here and the table is handed over as text."""
    if v is None:
        return blank
    try:
        if v != v:
            return blank
    except TypeError:
        pass
    return fmt.format(v)


def ladder_caption(lad: dict) -> str:
    """Escaped, or Streamlit reads the span between two dollar signs as
    LaTeX — the ladder printed half its figures in equation type."""
    return "Ladder: " + "  ·  ".join(f"IV{n} {d(v)}" for n, v in lad.items() if v == v)


def below_line_note(flagged: list[tuple[int, float, float]]) -> str | None:
    """One note for every flagged year, not one paragraph per year."""
    if not flagged:
        return None
    yrs = "; ".join(f"FY{fy} net income {n:,.0f}M against operating income {oi:,.0f}M ({n / oi:.1f}x)"
                    for fy, n, oi in flagged)
    return (f"Net income exceeds operating income in {len(flagged)} profitable year"
            f"{'s' if len(flagged) > 1 else ''} — {yrs}. Something below the operating line — "
            "interest on cash, a tax benefit, a gain — is in net income, and ΔE is measured on that "
            "figure — tool 1's ΔE is measured on it and flattered by it. Nothing here is: this "
            "page measures ΔE on after-tax operating income and projects operating margin.")


TERMINAL_TOL = 0.0005   # half of the box's one-decimal display unit


def above_incremental(terminal: float, incr: float | None) -> bool:
    """The box rounds to one decimal; the comparison must not be finer than
    that, or a terminal seeded FROM the incremental margin reads as above it."""
    return incr is not None and terminal > incr + TERMINAL_TOL


def stage1_seed(growth_latest: float | None, g0_seed: float) -> float:
    """Tool 1's rule (latest revenue growth capped at 25%), then never above
    the Stage 0 rate. A first synthetic run seeded 13% through Stage 0 and
    15% after it — growth accelerating once the ramp is over is the wrong
    shape, and no reader should have to notice that."""
    g = min(growth_latest if growth_latest is not None else 0.08, STAGE1_GROWTH_CAP)
    return min(g, g0_seed)


def below_line_years(rows: list[TrendYear]) -> list[tuple[int, float, float]]:
    """Profitable years where net income exceeds OPERATING income — something
    below the operating line (interest on cash, a tax benefit, a gain) is in
    it. Uber FY2024: $9.9B against $2.8B. Informational since 1 Sep: ΔE here
    is measured on after-tax operating income, so these items never enter it."""
    return [(r.fy, r.N, r.oi) for r in rows
            if r.oi is not None and r.oi > 0 and r.N > BELOW_LINE_MIN * r.oi]


def growth_sensitivity(base: IVParams, rev0: float, m0: float, tax: float, dE: float,
                       growths: list[float], years_list: list[int], terminal: float) -> list[list[float]]:
    """IV15 for each Stage 0 revenue growth (rows) by years (columns) at the
    entered terminal margin — the third lever, and the one a launch rate
    inflates. Rows run at or below the seed, since that is where the risk is."""
    grid = []
    for g in growths:
        row = []
        for n in years_list:
            p = IVParams(**{**base.__dict__, "OE": oe_seed(rev0, m0, tax, dE),
                            "stage0_years": n, "stage0_growth": stage0_rate(g, m0, terminal, n)})
            row.append(intrinsic_value(p, 15))
        grid.append(row)
    return grid


def sensitivity(base: IVParams, rev0: float, m0: float, tax: float, dE: float, g_rev: float,
                terminals: list[float], years_list: list[int], gm: float | None) -> list[list[float | None]]:
    """IV15 for each terminal margin (rows) by years (columns). Every cell is
    the same engine call with one input moved; a cell above gross margin is
    refused."""
    grid = []
    for mT in terminals:
        row = []
        for n in years_list:
            if mT <= 0 or n < 1 or (gm is not None and mT > gm):
                row.append(None)
                continue
            p = IVParams(**{**base.__dict__, "OE": oe_seed(rev0, m0, tax, dE),
                            "stage0_years": n, "stage0_growth": stage0_rate(g_rev, m0, mT, n)})
            row.append(intrinsic_value(p, 15))
        grid.append(row)
    return grid


# ══════════════════════════════════════════════════════════════════════
#  SELF-TEST
# ══════════════════════════════════════════════════════════════════════

def self_test() -> list[tuple[str, bool, str]]:
    out = []

    # 1. The ported engine still agrees with tool 1, to the dollar.
    goog = [(2016, 19478, 6900, 3693, 3304, 97, 47), (2017, 12662, 7900, 4846, 4166, 78, 55),
            (2018, 30736, 10000, 9075, 4993, -2, 61), (2019, 34343, 11700, 18396, 4765, -158, 70),
            (2020, 40269, 12991, 31149, 5720, -263, 73), (2021, 76033, 15376, 50274, 10162, -264, 125),
            (2022, 59972, 19362, 59296, 9300, -412, 117), (2023, 73795, 22460, 61504, 9837, -374, 115),
            (2024, 100118, 22785, 62222, 12190, -243, 164), (2025, 132170, 24953, 45709, 14167, -93, 206)]
    ys = [Year(fy=f, N=n, G=g, T=t, Cw=c, dS=d, price=p) for f, n, g, t, c, d, p in goog]
    out.append(("Ported engine: Alphabet FY2016 V = $8,252M", abs(ys[0].V - 8252) < 1, f"${ys[0].V:,.0f}M"))
    out.append(("Ported engine: Alphabet pooled ΔE = 88.7%", abs(pool(ys).dE - 0.887) < 0.002,
                f"{pool(ys).dE:.2%}"))
    crm = IVParams(OE=7300, shares=1073.3, tier="Chapel", growth=0.069, exit_multiple=21.8, blend=1.0)
    out.append(("Ported engine: Salesforce IV15, his inputs → $69.81",
                abs(intrinsic_value(crm, 15) - 69.81) < 1.0, f"${intrinsic_value(crm, 15):,.2f}"))

    # 2. The Stage 0 identity. A geometric margin path times a geometric
    #    revenue path is a constant growth rate — the engine must reproduce
    #    the hand-built stream to the cent, and land on the terminal margin.
    rev0, m0, mT, n, g, tax, dE = 4000.0, 0.06, 0.30, 5, 0.25, 0.21, 0.80
    explicit = stage0_stream_explicit(rev0, m0, mT, n, g, tax, dE)
    p = IVParams(OE=oe_seed(rev0, m0, tax, dE), shares=100, tier="Chapel", growth=0.15,
                 stage0_years=n, stage0_growth=stage0_rate(g, m0, mT, n))
    engine = _stream(p, n)
    out.append(("Stage 0 identity: engine stream equals the explicit margin path, 5 years",
                all(abs(a - b) < 1e-6 for a, b in zip(explicit, engine)),
                f"year 5: engine {engine[-1]:,.4f} vs explicit {explicit[-1]:,.4f}"))
    _end_margin = engine[-1] / (rev0 * (1 + g) ** n) / ((1 - tax) * dE)
    out.append(("...and year n sits exactly on the terminal margin",
                abs(_end_margin - mT) < 1e-9, f"{_end_margin:.4%} vs {mT:.0%}"))
    _n1 = _stream(IVParams(**{**p.__dict__, "stage0_years": 1,
                             "stage0_growth": stage0_rate(g, m0, mT, 1)}), 1)[0]
    out.append(("...and with n = 1 the whole expansion lands in year one",
                abs(_n1 - rev0 * (1 + g) * mT * (1 - tax) * dE) < 1e-6, f"{_n1:,.2f}"))
    out.append(("Stage 0 refuses a non-positive starting margin",
                _raises(lambda: stage0_rate(g, 0.0, mT, n)) and _raises(lambda: stage0_rate(g, -0.1, mT, n)),
                "ValueError both ways"))

    # 3. Shapes.
    inf = [(2019, -300.0), (2020, -200.0), (2021, -100.0), (2022, -20.0), (2023, 50.0), (2024, 120.0)]
    out.append(("Shape: negative then positive, one crossing → inflection at FY2023",
                op_shape(inf)[0] == SHAPE_INFLECTION and crossing_year(inf) == 2023, op_shape(inf)[1]))
    allp = [(2016 + i, 100.0 + i) for i in range(10)]
    out.append(("Shape: positive throughout → sent to tool 1",
                op_shape(allp)[0] == SHAPE_ALL_POSITIVE, op_shape(allp)[1][:60]))
    ptl = [(2020, 300.0), (2021, 350.0), (2022, 100.0), (2023, -900.0), (2024, -400.0)]
    out.append(("Shape: profitable then losses (TTWO) → sent to tool 1",
                op_shape(ptl)[0] == SHAPE_PROFIT_THEN_LOSS, op_shape(ptl)[1][:60]))
    dip = [(2021, 500.0), (2022, 600.0), (2023, 700.0), (2024, 800.0), (2025, -80.0)]
    out.append(("Shape: a loss at the END of a profitable record is profitable-then-loss, not a dip",
                op_shape(dip)[0] == SHAPE_PROFIT_THEN_LOSS, op_shape(dip)[1][:60]))
    dip2 = [(2021, 500.0), (2022, -600.0), (2023, 700.0), (2024, 800.0)]
    out.append(("Shape: positive at both ends with a loss between (CROX) → sent to tool 1",
                op_shape(dip2)[0] == SHAPE_DIP, op_shape(dip2)[1][:60]))
    alln = [(2021, -4000.0), (2022, -6900.0), (2023, -5700.0), (2024, -4700.0), (2025, -3500.0)]
    out.append(("Shape: negative throughout (RIVN) → all-negative",
                op_shape(alln)[0] == SHAPE_ALL_NEGATIVE, op_shape(alln)[1][:60]))
    noisy = [(2020, -100.0), (2021, 50.0), (2022, -80.0), (2023, 60.0), (2024, 90.0)]
    out.append(("Shape: three sign changes → noisy",
                op_shape(noisy)[0] == SHAPE_NOISY, op_shape(noisy)[1][:60]))
    noisy2 = [(2020, -100.0), (2021, 50.0), (2022, -80.0)]
    out.append(("Shape: negative at both ends with a profit between → noisy, not all-negative",
                op_shape(noisy2)[0] == SHAPE_NOISY, op_shape(noisy2)[1][:60]))
    crox = [(2016, -6.0), (2017, 17.0), (2018, 63.0), (2019, 129.0), (2020, 214.0), (2021, 683.0),
            (2022, 851.0), (2023, 1037.0), (2024, 1022.0), (2025, 150.0)]
    _ck, _cs = op_shape(crox)
    out.append(("Shape: Crocs — a crossing nine years old with a profitable trend window → sent to tool 1, naming the window",
                _ck == SHAPE_STALE and "FY2017" in _cs and "FY2022–FY2025" in _cs, _cs[:90]))
    _uber_next = [(2019, -8596.0), (2020, -4863.0), (2021, -3834.0), (2022, -1832.0), (2023, 1110.0),
                  (2024, 2799.0), (2025, 5565.0), (2026, 7000.0)]
    out.append(("Shape: a crossing that is the second year of the window is still an inflection; one year older is stale",
                op_shape(_uber_next[:-1])[0] == SHAPE_INFLECTION and op_shape(_uber_next)[0] == SHAPE_STALE,
                "UBER FY2025 window: inflection; FY2026 window: stale"))
    out.append(("Shape: a single-year loss run reads 'in FY2016', not 'from FY2016 to FY2016'",
                "negative in FY2016 and" in op_shape([(2016, -6.0), (2017, 17.0), (2018, 63.0)])[1],
                op_shape([(2016, -6.0), (2017, 17.0), (2018, 63.0)])[1]))
    out.append(("Shape: a year with no operating income is skipped, not read as zero",
                op_shape([(2020, -10.0), (2021, None), (2022, 5.0)])[0] == SHAPE_INFLECTION,
                "FY2021 absent → still one crossing"))

    # 4. The trend rule.
    ok_m = [(2022, -0.057), (2023, 0.030), (2024, 0.064), (2025, 0.090)]
    out.append(("Trend: three improving steps pass", margin_gate(ok_m) == "", margin_trend_sentence(ok_m)))
    one_dip = [(2022, -0.20), (2023, -0.10), (2024, -0.12), (2025, 0.02)]
    out.append(("Trend: one dip in the middle is tolerated", margin_gate(one_dip) == "", "2 of 3 improving, end above start"))
    rev_ = [(2022, -0.10), (2023, 0.02), (2024, 0.08), (2025, 0.05)]
    out.append(("Trend: the latest year reversed → refused, naming both years",
                margin_gate(rev_).startswith("The latest year reversed") and "FY2024" in margin_gate(rev_)
                and "FY2025" in margin_gate(rev_), margin_gate(rev_)[:80]))
    one_yr = [(2022, -0.30), (2023, -0.32), (2024, -0.35), (2025, -0.10)]
    out.append(("Trend: improvement only in the latest step → 'one year is not a trend'",
                margin_gate(one_yr).startswith("One year is not a trend"), margin_gate(one_yr)[:80]))
    flat = [(2022, -0.30), (2023, -0.35), (2024, -0.33), (2025, -0.31)]
    out.append(("Trend: two improving steps but still below the start → flat or noisy",
                margin_gate(flat).startswith("Flat or noisy"), margin_gate(flat)[:80]))
    out.append(("Trend: fewer than four margins → refused", margin_gate(ok_m[1:]).startswith("Only 3"),
                margin_gate(ok_m[1:])))
    _soph = [TrendYear(2020 + i, None, -30.0 - i, None, "", None, None, 0, 0, None, "") for i in range(6)]
    out.append(("Coverage: operating income read for 6 years and revenue for none names the revenue line as the gap",
                coverage_gate(_soph).startswith("Operating income was read for 6 years but revenue for 0"),
                coverage_gate(_soph)[:70]))
    _full = [TrendYear(2022 + i, 1000.0 + i, 10.0 + i, None, "", None, None, 0, 0, None, "") for i in range(4)]
    out.append(("Coverage: silent when both lines cover four years, or neither does",
                coverage_gate(_full) == "" and coverage_gate(_soph[:3]) == "", ""))
    out.append(("Trend: only the last four years are read — an old collapse does not fail it",
                margin_gate([(2018, 0.50)] + ok_m) == "", "FY2018 at 50% ignored"))

    # 5. Revenue and start gates.
    out.append(("Revenue: compounding passes", revenue_gate([(2022, 100.0), (2023, 130.0), (2024, 160.0), (2025, 190.0)]) == "", ""))
    _rg = revenue_gate([(2022, 100.0), (2023, 120.0), (2024, 110.0), (2025, 105.0)])
    out.append(("Revenue: a fall in the latest year refuses, with the figures",
                _rg.startswith("Revenue is not compounding") and "105M" in _rg, _rg[:80]))
    out.append(("Start: a negative latest margin refuses with the year and figure",
                start_gate(2025, -0.60).startswith("No positive start") and "-60.0%" in start_gate(2025, -0.60),
                start_gate(2025, -0.60)[:70]))
    out.append(("Start: a positive latest margin passes", start_gate(2025, 0.09) == "", ""))

    # 6. ΔE from the profitable years only.
    mixed = [Year(fy=2021, N=-500, G=300, dS=40, price=20), Year(fy=2022, N=-100, G=350, dS=30, price=15),
             Year(fy=2023, N=200, G=400, T=0, dS=20, price=20), Year(fy=2024, N=500, G=450, T=100, dS=10, price=40)]
    _pp, _fys = profitable_pool(mixed)
    out.append(("ΔE pool takes FY2023–FY2024 only; loss years never enter it",
                _fys == [2023, 2024] and _pp.sum_N == 700, f"years {_fys}, ΣN {_pp.sum_N:,.0f}"))
    _pp_exp = pool(mixed[2:])
    out.append(("...and equals tool 1's pool() on those two rows", abs(_pp.dE - _pp_exp.dE) < 1e-12, f"{_pp.dE:.1%}"))
    mixed[3].excluded = "listing year"
    _pp2, _fys2 = profitable_pool(mixed)
    out.append(("...an excluded profitable year drops out", _fys2 == [2023] and _pp2.years == 1, f"years {_fys2}"))
    out.append(("...no profitable year → None, not a ratio", profitable_pool(mixed[:2]) == (None, []), "None"))
    gain_first = [Year(fy=2018, N=997, G=172, dS=0, price=0)] + mixed[:3]
    out.append(("...a profitable GAIN year before the crossing (Uber FY2018) stays out when since_fy is the crossing",
                profitable_pool(gain_first, since_fy=2023) == (pool([mixed[2]]), [2023]),
                f"years {profitable_pool(gain_first, since_fy=2023)[1]}"))
    _pl_rows = [TrendYear(2022, 1906.0, -161.0, None, "", None, None, 0, 0, None, ""),
                TrendYear(2023, 2225.0, 120.0, None, "", None, None, 0, 0, None, ""),
                TrendYear(2024, 2866.0, 310.0, None, "", None, None, 0, 0, None, ""),
                TrendYear(2025, 4476.0, 1414.0, None, "", None, None, 0, 0, None, "")]
    _lat, _c3 = revenue_rates(_pl_rows)
    out.append(("Revenue rates from the rows: Palantir 56.2% latest, 32.9% over three years",
                abs(_lat - 0.562) < 0.001 and abs(_c3 - 0.329) < 0.001, f"{_lat:.1%} / {_c3:.1%}"))
    _sd, _raw = stage0_growth_seed(_lat, _c3)
    out.append(("Stage 0 growth seeds from the LOWER filed rate, not tool 1's 25% cap",
                abs(_sd - 0.329) < 0.001 and _raw == _sd, f"{_sd:.1%}"))
    out.append(("...and caps at 50% with the filed rate kept for the caption",
                stage0_growth_seed(0.90, 0.70) == (0.50, 0.70) and stage0_growth_seed(None, None) == (0.08, None), ""))
    _n = ["Revenue is growing faster than it was — x", "Latest revenue growth is 56%, which is a launch rate",
          "No share price for some years — their SBC cost is understated."]
    out.append(("Tool 1's three growth-seeding notes are dropped; every other note is kept",
                page_notes(_n) == _n[2:], f"{len(page_notes(_n))} of 3 kept"))
    out.append(("A refused cell is the dash as TEXT — None and NaN both — and a value keeps its format",
                cell(None, "{:+.1%}") == "—" and cell(float("nan"), "{:,.0f}") == "—" and cell(0.219, "{:+.1%}") == "+21.9%",
                ""))
    _lc = ladder_caption({8: 281.57, 15: 93.96, 20: float("nan")})
    out.append(("Ladder caption escapes every dollar sign and drops a NaN rung",
                _lc.count("\\$") == 2 and _lc.count("$") == 2 and "IV20" not in _lc, _lc))
    out.append(("Terminal seeded FROM the incremental margin is not 'above' it after rounding",
                not above_incremental(0.219, 0.21887) and above_incremental(0.225, 0.21887), ""))
    _bl = below_line_note([(2023, 1887.0, 1110.0), (2024, 9856.0, 2799.0)])
    out.append(("Below-the-line note is ONE note naming every year with its ratio",
                _bl.startswith("Net income exceeds operating income in 2 profitable years") and "FY2024" in _bl and "3.5x" in _bl
                and below_line_note([]) is None, _bl[:80]))
    out.append(("Stage 1 seed never sits above the Stage 0 rate",
                stage1_seed(0.15, 0.13) == 0.13 and stage1_seed(0.40, 0.50) == 0.25 and stage1_seed(0.10, 0.30) == 0.10,
                "13%, 25% cap, 10%"))
    # ΔE on the operating base. Uber-shaped: after-tax operating income
    # 7,484 over three years, GAAP SBC 5,500, true cost 9,707 → 43.8%. The
    # net-income pool on the same rows reads far higher because FY2024–25
    # carry ~14B of tax benefits and gains.
    _ub_y = [Year(fy=2022, N=-9141, G=1793, dS=90, price=30), Year(fy=2023, N=1887, G=1900, dS=0, price=45, T=1500),
             Year(fy=2024, N=9856, G=1800, dS=0, price=70, T=3000), Year(fy=2025, N=10053, G=1800, dS=0, price=80, T=3300)]
    _ub_r = [TrendYear(2022, 31877.0, -1832.0, None, "", None, None, -9141, 0, None, ""),
             TrendYear(2023, 37281.0, 1110.0, None, "", None, None, 1887, 0, None, ""),
             TrendYear(2024, 43978.0, 2799.0, None, "", None, None, 9856, 0, None, ""),
             TrendYear(2025, 52017.0, 5565.0, None, "", None, None, 10053, 0, None, "")]
    _op = operating_pool(_ub_y, _ub_r, 2023, 0.21)
    _exp_base = 0.79 * (1110 + 2799 + 5565)
    out.append(("Operating ΔE: base is Σ operating income × (1 − tax) over the post-crossing years only",
                _op.fys == [2023, 2024, 2025] and abs(_op.sum_base - _exp_base) < 1e-9, f"{_op.sum_base:,.0f}"))
    _exp_dE = (_exp_base + 5500 - 7800) / _exp_base
    out.append(("...ΔE = (base + ΣG − ΣΩ) / base, and the net-income pool on the same rows is far higher",
                abs(_op.dE - _exp_dE) < 1e-9 and profitable_pool(_ub_y, 2023)[0].dE > _op.dE + 0.2,
                f"operating {_op.dE:.1%} vs net-income {profitable_pool(_ub_y, 2023)[0].dE:.1%}"))
    _ub_r[3].oi = None
    out.append(("...a year without an operating line is out of the pool, not read as zero",
                operating_pool(_ub_y, _ub_r, 2023, 0.21).fys == [2023, 2024], ""))
    _ub_r[3].oi = 5565.0
    out.append(("...no operating-profit year since the crossing → None",
                operating_pool(_ub_y, _ub_r, 2026, 0.21) is None, ""))
    _cv_y = [Year(fy=2024, N=404, G=90, dS=0, price=150, Ce=900), Year(fy=2025, N=800, G=97, dS=0, price=300, Ce=850)]
    _cv_r = [TrendYear(2024, 13673.0, 990.0, None, "", None, None, 404, 0, None, ""),
             TrendYear(2025, 20322.0, 1881.0, None, "", None, None, 800, 0, None, "")]
    _cv = operating_pool(_cv_y, _cv_r, 2024, 0.21, shares_by_fy={})
    out.append(("Carvana-shaped: no share count in any year → the pool is flagged unmeasurable, not a 185% ΔE",
                _cv.missing_shares == [2024, 2025] and not _cv.measurable and _cv.dE > 1.25, f"raw {_cv.dE:.1%}, flagged"))
    _cv_msg = dE_gate(0.0, _cv, _cv.fys)[0]
    out.append(("...and the refusal names the missing years, not the 125% line",
                _cv_msg.startswith("ΔE cannot be measured: no year-end share count for FY2024, FY2025"), _cv_msg[:80]))
    out.append(("...a count for the year but not the one before still flags that year",
                operating_pool(_cv_y, _cv_r, 2024, 0.21, shares_by_fy={2024: 200.0, 2025: 210.0}).missing_shares == [2024]
                and operating_pool(_cv_y, _cv_r, 2024, 0.21, shares_by_fy={2023: 190.0, 2024: 200.0, 2025: 210.0}).measurable, ""))
    out.append(("IFRS names for the operating lines are in the reader (Grab reports in USD under IFRS)",
                CONCEPTS["OI"][1] == ["ProfitLossFromOperatingActivities"] and "CostOfSales" in CONCEPTS["COGS"][1]
                and CONCEPTS["CFO"][1] and CONCEPTS["CAPEX"][1], ""))
    out.append(("Financial sentence names lenders and brokers too, and what tool 1 does instead",
                "lenders, brokers" in financial_sentence("Real Estate Agents", "6531")
                and "indicatively" in financial_sentence(None, "6141") and "insurer's revenue" not in financial_sentence(None, "6141"), ""))
    out.append(("Shares gate: a zero count refuses before anything per share is printed",
                shares_gate(0.0).startswith("No share count was read") and shares_gate(155.3) == "", ""))
    _gx = operating_pool([Year(fy=2025, N=447, G=50, dS=8, price=40)],
                         [TrendYear(2025, 616.0, 123.0, None, "", None, None, 447, 0, None, "")], 2025, 0.21)
    out.append(("...a tax-benefit year cannot flatter it: TGTX-shaped 447 net on 123 operating → ΔE on 97, not on 447",
                abs(_gx.sum_base - 123 * 0.79) < 1e-9 and _gx.dE < 0, f"{_gx.dE:.1%}"))
    _rg = dE_gate(_gx.dE, _gx, _gx.fys)[0]
    out.append(("...and the refusal sentence names the after-tax operating base",
                _rg.startswith("Owners' earnings have not inflected") and "after-tax operating income totalled 97M" in _rg, _rg[:90]))
    _one = dE_gate(_gx.dE, _gx, [2025])[0]
    out.append(("A one-year pool reads 'Over FY2025 (the profitable year since the crossing)', not FY2025–FY2025",
                "Over FY2025 (the profitable year since" in _one and _fy_range([2023, 2025]) == "FY2023–FY2025", _one[:70]))
    # PLTR-shaped: profits, but shares handed out at market dwarf them.
    pltr = [Year(fy=2023, N=210, G=476, dS=90, price=15), Year(fy=2024, N=462, G=692, dS=120, price=35)]
    _pl, _plf = profitable_pool(pltr)
    _ref, _app = dE_gate(_pl.dE, _pl, _plf)
    out.append(("ΔE gate: GAAP inflected, owners' earnings did not → refused, with the totals",
                _pl.dE < 0 and _ref.startswith("Owners' earnings have not inflected") and "FY2023–FY2024" in _ref
                and _app == 0.0, f"ΔE {_pl.dE:.1%}"))
    out.append(("ΔE gate: a positive box passes and is capped at 100% like tool 1",
                dE_gate(0.85, _pl, _plf) == ("", 0.85) and dE_gate(1.10, _pl, _plf) == ("", 1.0), "0.85 → 0.85, 1.10 → 1.00"))
    out.append(("ΔE gate: above 125% refused", dE_gate(1.30, _pl, _plf)[0].startswith("ΔE of 130.0% is above"), ""))
    _pos = pool([Year(fy=2024, N=500, G=100, dS=1, price=10)])
    out.append(("ΔE gate: a zero BOX against a positive measured pool names the box, not a failed inflection",
                dE_gate(0.0, _pos, [2024])[0].startswith("ΔE is set at 0.0%") and "118.0%" in dE_gate(0.0, _pos, [2024])[0],
                dE_gate(0.0, _pos, [2024])[0][:70]))
    out.append(("ΔE gate: no profitable year and box at zero → refused, asks for a hand-set figure",
                dE_gate(0.0, None, [])[0].startswith("No profitable year"), ""))

    # 7. Incremental margin cells and the window figure.
    a = TrendYear(2023, 1000.0, -50.0, None, "", None, None, 0, 0, None, "")
    b = TrendYear(2024, 1300.0, 40.0, None, "", None, None, 0, 0, None, "")
    c = TrendYear(2025, 1290.0, 60.0, None, "", None, None, 0, 0, None, "")
    e = TrendYear(2025, 1330.0, 60.0, None, "", None, None, 0, 0, None, "")
    out.append(("Incremental margin: (40 − −50) / 300 = 30.0%", abs(incremental_margin(a, b) - 0.30) < 1e-12,
                f"{incremental_margin(a, b):.1%}"))
    out.append(("Incremental margin: refused when revenue fell", incremental_margin(b, c) is None, "—"))
    out.append(("Incremental margin: refused when revenue moved under 5%", incremental_margin(b, e) is None, "+2.3%"))
    out.append(("Window incremental: first to last with both lines, 110/330 = 33.3%",
                abs(window_incremental([a, b, e]) - 110 / 330) < 1e-12, f"{window_incremental([a, b, e]):.1%}"))
    out.append(("Window incremental: refused when revenue did not grow over the window",
                window_incremental([b, c]) is None, "—"))
    tg = [TrendYear(2021, 7.0, -345.0, None, "", None, None, 0, 0, None, ""),
          TrendYear(2022, 3.0, -218.0, None, "", None, None, 0, 0, None, ""),
          TrendYear(2023, 234.0, 21.0, None, "", None, None, 0, 0, None, ""),
          TrendYear(2024, 329.0, 42.0, None, "", None, None, 0, 0, None, ""),
          TrendYear(2025, 616.0, 123.0, None, "", None, None, 0, 0, None, "")]
    out.append(("Post-crossing incremental: TG Therapeutics FY2023→25 keeps 26.7% of new revenue, against a 76.8% window figure",
                abs(post_crossing_incremental(tg, 2023) - 102 / 382) < 1e-12 and abs(window_incremental(tg) - 468 / 609) < 1e-12,
                f"{post_crossing_incremental(tg, 2023):.1%} vs {window_incremental(tg):.1%}"))
    out.append(("...refused with fewer than two years after the crossing, or no crossing",
                post_crossing_incremental(tg, 2025) is None and post_crossing_incremental(tg, None) is None, ""))

    # 8. Gross margin: tagged, derived, refused.
    out.append(("Gross profit: tagged wins", gross_profit(100.0, 60.0, 50.0) == (60.0, "GrossProfit"), ""))
    out.append(("Gross profit: derived from revenue less cost of revenue when not tagged",
                gross_profit(100.0, None, 45.0) == (55.0, "revenue − cost of revenue"), ""))
    out.append(("Gross profit: refused when neither is tagged", gross_profit(100.0, None, None) == (None, ""), ""))
    out.append(("Gross-margin ceiling: terminal above gross margin refused",
                gross_margin_gate(0.40, 0.35).startswith("Terminal operating margin of 40.0%")
                and gross_margin_gate(0.30, 0.35) == "" and gross_margin_gate(0.90, None) == "", ""))

    # 9. Terminal default: the lower of the two, capped, never below latest.
    _v5, _s5 = terminal_default(0.768, 0.20, 26.2, 5, 0.837, post_incr=0.267)
    out.append(("Terminal default: the post-crossing incremental bounds TGTX at 26.7%, not 76.8%",
                abs(_v5 - 0.267) < 1e-12 and _s5.startswith("the incremental margin since"), f"{_v5:.1%}"))
    out.append(("...and does not move Uber, whose post-crossing figure (30.2%) is above the window's (21.9%)",
                abs(terminal_default(0.219, 0.107, 0.055, 5, None, post_incr=0.302)[0] - 0.219) < 1e-12, ""))
    _v, _s = terminal_default(0.34, 0.09, 0.03, 5, 0.40)
    out.append(("Terminal default: pace path 9% + 3×5 = 24% beats a 34% window incremental",
                abs(_v - 0.24) < 1e-12 and _s.startswith("the trailing pace"), f"{_v:.1%} — {_s}"))
    _v2, _s2 = terminal_default(0.20, 0.09, 0.03, 5, 0.40)
    out.append(("...incremental wins when lower", abs(_v2 - 0.20) < 1e-12 and _s2.startswith("the window incremental"), f"{_v2:.1%}"))
    _v3, _s3 = terminal_default(0.34, 0.09, 0.03, 5, 0.18)
    out.append(("...gross margin caps both", abs(_v3 - 0.18) < 1e-12 and _s3.startswith("gross margin"), f"{_v3:.1%}"))
    out.append(("Pace path: a pace that runs past gross margin is described, not printed",
                pace_path_text(0.20, 26.2, 5, 0.837) == "above gross margin within 5y"
                and pace_path_text(0.20, 26.2, 5, None) == "above 100% within 5y"
                and pace_path_text(0.107, 0.055, 5, None) == "38.2% in 5y" and pace_path_text(0.1, None, 5, 0.5) == "—",
                pace_path_text(0.20, 26.2, 5, 0.837)))
    _v4, _s4 = terminal_default(None, 0.09, None, 5, None)
    out.append(("...no trend at all → the latest margin, and says so", _v4 == 0.09 and "no trend" in _s4, _s4))

    # 10. Runway.
    out.append(("Runway: 7,000 cash at a 2,500 burn = 2.8 years", runway_years(7000.0, -2000.0, 500.0) == (2.8, 2500.0), ""))
    out.append(("Runway: positive free cash flow → no runway to compute", runway_years(7000.0, 800.0, 300.0) == (None, 0.0), ""))
    out.append(("Runway: no cash-flow line → None, not zero", runway_years(7000.0, None, 300.0) == (None, 0.0), ""))
    out.append(("Runway: cash not read with a burn filed → no years figure, and the gate refuses on that ground",
                runway_years(0.0, -10.0, 8.0) == (None, 18.0)
                and runway_gate(5, None, 18.0, 0.0).startswith("Runway cannot be measured: a burn of 18M"),
                runway_gate(5, None, 18.0, 0.0)[:60]))
    _rw = runway_gate(5, 2.8, 2500.0, 7000.0)
    out.append(("Runway gate: 2.8 years against a 5-year path refuses with the figures",
                _rw.startswith("Runway shorter") and "2.8 years" in _rw and "2,500M" in _rw, _rw[:70]))
    out.append(("Runway gate: passes when the cash outlasts the path or there is no burn",
                runway_gate(2, 2.8, 2500.0, 7000.0) == "" and runway_gate(5, None, 0.0, 7000.0) == "", ""))

    # 11. Pace, below-the-line, sensitivity.
    out.append(("Pace: mean of the last three steps", abs(margin_pace([-0.5, -0.30, -0.20, -0.10, 0.02]) - 0.32 / 3) < 1e-12,
                f"{margin_pace([-0.5, -0.30, -0.20, -0.10, 0.02]):+.2%}"))
    uber = [TrendYear(2023, 37281.0, 1110.0, None, "", None, None, 1887.0, 0, None, ""),
            TrendYear(2024, 43978.0, 2799.0, None, "", None, None, 9856.0, 0, None, ""),
            TrendYear(2022, 31877.0, -1832.0, None, "", None, None, -9141.0, 0, None, "")]
    # Both profitable years flag: FY2023's 1,887 against 1,110 is a real
    # below-the-line gain (equity stakes), FY2024's 9,856 against 2,799 is
    # the tax benefit. The loss year cannot flag — there is no operating
    # profit to compare against.
    out.append(("Below the line: Uber FY2023 (1.7x) and FY2024 (3.5x) are flagged; the loss year is not",
                [f for f, _, _ in below_line_years(uber)] == [2023, 2024], str(below_line_years(uber))))
    base = IVParams(OE=1.0, shares=100.0, tier="Chapel", growth=0.15, net_cash=0.0, exit_multiple=14.5, blend=0.5)
    grid = sensitivity(base, 4000.0, 0.06, 0.21, 0.8, 0.25, [0.20, 0.30, 0.45], [3, 5, 7], 0.40)
    out.append(("Sensitivity: IV15 rises with the terminal margin in every column",
                all(grid[0][j] < grid[1][j] for j in range(2)), f"{grid[0][1]:.2f} < {grid[1][1]:.2f}"))
    out.append(("Sensitivity: a terminal margin above gross margin is a refused cell, not a number",
                grid[2] == [None, None, None], ""))
    _mid = IVParams(**{**base.__dict__, "OE": oe_seed(4000.0, 0.06, 0.21, 0.8), "stage0_years": 5,
                       "stage0_growth": stage0_rate(0.25, 0.06, 0.30, 5)})
    out.append(("Sensitivity: the centre cell is the page's own IV15, same engine call",
                abs(grid[1][1] - intrinsic_value(_mid, 15)) < 1e-9, f"{grid[1][1]:.2f}"))
    ggrid = growth_sensitivity(base, 4000.0, 0.06, 0.21, 0.8, [0.125, 0.1875, 0.25], [3, 5, 7], 0.30)
    out.append(("Growth grid: IV15 rises with Stage 0 growth in every column, and the seed row is the page's own IV15",
                all(ggrid[0][j] < ggrid[1][j] < ggrid[2][j] for j in range(3)) and abs(ggrid[2][1] - grid[1][1]) < 1e-9,
                f"{ggrid[0][1]:.2f} < {ggrid[1][1]:.2f} < {ggrid[2][1]:.2f}"))

    # 12. The shared seed helper still behaves as tool 1's does.
    out.append(("Shared seed helper: a loss on a profitable record still seeds from the median",
                seed_owners_earnings(-81.0, 1.009, True, 600.0) == (600.0, SEED_FROM_MEDIAN_LOSS), ""))
    # 16. The broad issuance-proceeds tag gated like the treasury line
    #     (Carvana, 1 Sep 2026). A raise the size of Carvana's is rejected;
    #     ordinary exercise proceeds and the small no-charge case survive.
    def _ce_gate_keeps(Ce, G, N):
        return not ((Ce > 3 * G) if G > 0 else (Ce > 0.10 * abs(N)))
    out.append(("An ATM raise read as employee proceeds is rejected",
                not _ce_gate_keeps(900.0, 80.0, 210.0) and not _ce_gate_keeps(600.0, 0.0, 450.0),
                "CVNA-shaped: 900 against a charge of 80; 600 with no charge against 450 income"))
    out.append(("...ordinary exercise proceeds are not",
                _ce_gate_keeps(60.0, 100.0, 500.0) and _ce_gate_keeps(30.0, 0.0, 450.0),
                "60 against a 100 charge; 30 with no charge against 450 income"))

    # 18. Ported from page 6 (its tests, verbatim): a market-confirmed 2:1
    #     inside the tolerance band restates; no event or a small base does not.
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

    return out


def _raises(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


# ══════════════════════════════════════════════════════════════════════
#  UI
# ══════════════════════════════════════════════════════════════════════
#
# NOTE ON DOLLAR SIGNS: Streamlit markdown parses $...$ as LaTeX. Any literal
# dollar amount inside st.write/markdown/success/error/info/warning must be
# escaped as \$ or the text between two of them silently becomes an equation.
# st.metric, st.code and st.dataframe are unaffected.


def _fmt_pct(v):
    return "—" if v is None else f"{v:.1%}"


st.set_page_config(
    page_title="Inflection Checker — is the turn visible in the filings, and what is it worth at 15%",
    page_icon="🌱",
    layout="centered",
    initial_sidebar_state="collapsed",
)
st.title("🌱 Inflection Checker")
st.caption("For companies whose income statement looks terrible but whose trend tells a story. "
           "Is the turn actually in the filings — and if it continues, what is it worth at 15%?")

if not _sec_contact():
    st.warning(
        "**No SEC contact address set.** The SEC requires a real email in the request header "
        "and blocks generic user agents, so lookups will fail. Add `sec_contact = "
        "\"you@example.com\"` in Streamlit Settings → Secrets, or set a SEC_CONTACT "
        "environment variable locally.")

if "inf_years" not in st.session_state:
    st.info(
        "**Three parts, and which is whose.** The trend evidence — ten years of operating "
        "margin, incremental margin, costs against revenue, burn against cash, dilution — is "
        "this app's own tabulation, no published framework. The pricing is Burry's Stage 0: "
        "the margin projected geometrically from the company's own trend to a terminal margin "
        "you set, then his normal stages at 15%. The refusals are this app's, and they fire "
        "often. Annual filings only, so a turn that happened this year reaches this page with "
        "the next 10-K.\n\n"
        "Enter a US-listed ticker. Companies profitable throughout, or with a loss year on a "
        "profitable record, are sent to the Tragic Algebra Analyzer.")

with st.form("inf_lookup"):
    ticker = st.text_input("Stock ticker", placeholder="UBER · FRSH · PLTR — press Enter").upper().strip()
    submitted = st.form_submit_button("Evaluate", type="primary")

tier_name = st.selectbox("Moat tier", list(AICT), index=2,
                         format_func=lambda t: f"{t} — {TIER_BLURB[t]}",
                         help="Sets stage lengths after Stage 0, how far growth fades in stage 2, "
                              "the terminal cap and the exit multiple. Tool 1's tiers, unchanged.")

if submitted:
    if not ticker:
        st.warning("Enter a ticker first.")
    else:
        try:
            with st.spinner(f"Reading {ticker} annual filings…"):
                yrs, notes, pre = load(ticker, 10)
            st.session_state.update(inf_years=yrs, inf_notes=notes, inf_pre=pre, inf_tk=ticker)
        except ValueError as e:
            st.error(f"Could not load {ticker}: {e}")
        except Exception as e:
            st.error(
                f"Could not load {ticker} — {type(e).__name__}: {e}\n\n"
                "This is a gap in how the filings were read, not something you did. Filers with "
                "several share classes, recent listings and foreign issuers are the usual causes.")

years = st.session_state.get("inf_years", [])
if years and ticker and st.session_state.get("inf_tk") == ticker:
    notes, pre, tk = st.session_state["inf_notes"], st.session_state["inf_pre"], st.session_state["inf_tk"]
    rows = build_trend(years, pre.get("trend", {}), pre.get("shares_by_fy", {}))
    alerts: list[tuple[str, str]] = [("info", n) for n in page_notes(notes)]

    # A financial filer is refused BEFORE the table. Oscar Health, 1 Sep
    # 2026: SIC 6324, and the table printed revenue of 21M and an operating
    # margin of +278% for a company with $9B of premiums — filed numbers,
    # but not an insurer's revenue. Every other refusal keeps the table,
    # because for every other filer the table is right.
    if pre.get("financial"):
        st.markdown("---")
        st.subheader("Is the turn in the filings?")
        st.error("**Not this page.** " + financial_sentence(pre.get("sic_desc"), pre.get("sic")))
        with st.expander("Notes and detail", expanded=False):
            for kind_, msg in alerts:
                getattr(st, kind_)(msg)
            st.write("**What was read from the filings** — every tag, found or missing")
            st.dataframe(pd.DataFrame(pre.get("tags", [])), width='stretch', hide_index=True)
        st.stop()

    # ══ trend evidence — always printed, before any refusal ══════════
    st.markdown("---")
    st.subheader(f"Trend evidence · {tk}")
    st.caption("Every column is a filed number or a named identity on two filed numbers. "
               "A blank cell is a refused cell, not zero. Years marked * are excluded from the "
               "stock-comp columns as capital events; their revenue and margins are still read.")

    _incr_cells = [None] + [incremental_margin(rows[i - 1], rows[i]) for i in range(1, len(rows))]
    _mfmt = money_fmt([v for r in rows for v in (r.rev, r.oi, r.gp, r.N, r.fcf, r.omega)])
    st.dataframe(pd.DataFrame([{
        "FY": f"{r.fy}*" if r.excluded else str(r.fy),
        "Revenue": cell(r.rev, _mfmt),
        "Rev growth": cell(growth_pct(r.rev, rows[i - 1].rev) if i else None, "{:+.1%}"),
        "Gross margin": cell(r.gm, "{:.1%}"),
        "Operating income": cell(r.oi, _mfmt),
        "Op margin": cell(r.opm, "{:+.1%}"),
        "Costs growth": cell(growth_pct(r.costs, rows[i - 1].costs) if i else None, "{:+.1%}"),
        "Incremental margin": cell(_incr_cells[i], "{:+.1%}"),
        "Net margin": cell(r.nm, "{:+.1%}"),
        "FCF": cell(r.fcf, _mfmt),
        "True SBC cost / rev": cell(r.omega_pct, "{:.1%}"),
        "Shares (M)": cell(r.shares, "{:,.1f}"),
        "Share change": cell(growth_pct(r.shares, rows[i - 1].shares) if i else None, "{:+.1%}"),
    } for i, r in enumerate(rows)]), width='stretch', hide_index=True)

    _gp_src = {r.gp_source for r in rows if r.gp_source}
    _missing_gp = [r.fy for r in rows if r.gp is None]
    _missing_oi = [r.fy for r in rows if r.oi is None]
    _cap = (("Gross margin is " + (" / ".join(sorted(_gp_src)) if _gp_src else "not readable")
             + (f"; refused for FY{', FY'.join(str(f) for f in _missing_gp)}" if _missing_gp and _gp_src else "")
             + ". ") if (_gp_src or _missing_gp) else "")
    if _missing_oi:
        _cap += f"Operating income could not be read for FY{', FY'.join(str(f) for f in _missing_oi)}. "
    _cap += ("Costs growth is total costs (revenue less operating income) year over year — when it runs "
             "below revenue growth, that is operating leverage. Incremental margin is the share of each "
             "new revenue dollar that reached operating income; refused where revenue fell or moved "
             "under 5%. FCF is cash from operations less capex.")
    st.caption(_cap)

    # ── summaries ──
    _opm_pts = [(r.fy, r.opm) for r in rows if r.opm is not None]
    _oi_pts = [(r.fy, r.oi) for r in rows]
    _rev_pts = [(r.fy, r.rev) for r in rows]
    _w_incr = window_incremental(rows)
    _pace = margin_pace([m for _, m in _opm_pts])
    _first_both = next((r for r in rows if r.rev is not None and r.oi is not None), None)
    _last = rows[-1]
    _rw, _burn = runway_years(pre.get("cash", 0.0), _last.cfo, _last.capex)
    s1, s2, s3 = st.columns(3)
    s1.metric("Incremental margin, window", _fmt_pct(_w_incr),
              f"FY{_first_both.fy}→FY{_last.fy}" if _first_both else "not readable")
    s2.metric("Margin pace, last 3 steps", "—" if _pace is None else f"{_pace:+.1%}/yr",
              f"to {_fmt_pct(_last.opm)} in FY{_last.fy}")
    s3.metric("Runway", "no burn" if _burn == 0 and _last.cfo is not None else
              "—" if _rw is None else f"{_rw:.1f} years",
              f"burn {_burn:,.0f}M on {pre.get('cash', 0.0):,.0f}M cash" if _rw is not None else
              ("cash-flow line not read" if _last.cfo is None else
               f"burn {_burn:,.0f}M, cash not read" if _burn > 0 else f"FCF +{_last.fcf:,.0f}M, FY{_last.fy}"))

    # ══ refusals, in order ═══════════════════════════════════════════
    st.markdown("---")
    st.subheader("Is the turn in the filings?")
    kind, sentence = op_shape(_oi_pts)
    stop = ""
    if kind in SENT_TO_TOOL_1:
        stop = sentence + " Use the Tragic Algebra Analyzer."
    elif kind in (SHAPE_NOISY, SHAPE_TOO_FEW):
        stop = sentence
    if stop:
        st.error(f"**Not this page.** {stop}")
    else:
        st.write(sentence)
        _mg = coverage_gate(rows) or margin_gate(_opm_pts)
        _rg = revenue_gate(_rev_pts)
        _sg = start_gate(_last.fy, _last.opm)
        if _mg:
            st.error(f"**No inflection visible.** {_mg}")
            stop = _mg
        else:
            st.success("**Losses narrowing.** " + margin_trend_sentence(_opm_pts))
        if not stop and _rg:
            st.error(f"**Not compounding.** {_rg}")
            stop = _rg
        if not stop and _sg:
            _head, _rest = _sg.split(":", 1)
            st.error(f"**{_head}.** {_rest.strip()[:1].upper() + _rest.strip()[1:]}")
            stop = _sg
        if kind == SHAPE_ALL_NEGATIVE and _sg and _mg:
            # both told: what the trend says AND that there is nothing to price
            st.caption("Both apply: the trend rule fails and there is no positive margin either way.")

    if stop:
        with st.expander("Notes and detail", expanded=False):
            for kind_, msg in alerts:
                getattr(st, kind_)(msg)
            st.write("**What was read from the filings** — every tag, found or missing")
            st.dataframe(pd.DataFrame(pre.get("tags", [])), width='stretch', hide_index=True)
        st.stop()

    # ══ judgement inputs ═════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Pricing — Burry's Stage 0, on tool 1's engine")
    st.caption("Filed numbers above; judgement in the boxes below, and the assumptions block says "
               "which is which. An inflection valuation is mostly the margin assumption — the "
               "grid under the verdict shows how much.")

    _cross_fy = crossing_year(_oi_pts)
    _post_incr = post_crossing_incremental(rows, _cross_fy)
    _gm_latest = _last.gm
    m0 = _last.opm
    rev0 = _last.rev

    j1, j2, j3 = st.columns(3)
    s0_years = j2.number_input("Years to terminal margin", min_value=1, max_value=10,
                               value=STAGE0_DEFAULT_YEARS, step=1,
                               help="The length of Stage 0. Five is a convention, not evidence; "
                                    "the pace beside the margin box says what the trend would do.")
    _t_default, _t_src = terminal_default(_w_incr, m0, _pace, int(s0_years), _gm_latest, _post_incr)
    terminal = j1.number_input("Terminal operating margin (%)", value=round(_t_default * 100, 1), step=1.0,
                               min_value=0.1, max_value=95.0,
                               help="The margin the business reaches at the end of Stage 0 — "
                                    "mature competitors' margins are your judgement; enter them. "
                                    "Seeded from " + _t_src + ".") / 100.0
    j1.caption(f"Seeded from {_t_src}. Window incremental {_fmt_pct(_w_incr)}; since the FY{_cross_fy} "
               f"crossing {_fmt_pct(_post_incr)}; trailing pace {pace_path_text(m0, _pace, int(s0_years), _gm_latest)}; "
               f"gross margin {_fmt_pct(_gm_latest)}. The lowest seeds the box.")

    _lat_g, _cagr3 = revenue_rates(rows)
    _g0_seed, _g0_raw = stage0_growth_seed(_lat_g, _cagr3)
    g_rev = j3.number_input("Revenue growth through Stage 0 (%)", value=round(_g0_seed * 100, 1), step=1.0,
                            min_value=-50.0, max_value=100.0,
                            help="Seeded from the lower of the latest year and the 3-year CAGR, capped "
                                 f"at {STAGE0_GROWTH_CAP:.0%}.") / 100.0
    j3.caption(f"Filed: latest year {_fmt_pct(_lat_g)}, 3-year CAGR {_fmt_pct(_cagr3)}; seeded from the lower"
               + (f", capped at {STAGE0_GROWTH_CAP:.0%}" if _g0_raw is not None and _g0_raw > STAGE0_GROWTH_CAP else "")
               + ". A launch rate does not compound for fifteen years — the years box bounds it.")
    g1 = j3.number_input("Growth after Stage 0 (%)", value=round(stage1_seed(pre.get("growth"), _g0_seed) * 100, 1),
                         step=0.5, min_value=-50.0, max_value=60.0,
                         help="Stage 1 growth once the margin path is done. Tool 1's own rule (latest "
                              "revenue growth capped at 25%), and never above the Stage 0 rate; it should "
                              "usually be lower still after years of hypergrowth.") / 100.0
    tax = j2.number_input("Tax rate (%)", value=TAX_DEFAULT * 100, step=1.0, min_value=0.0, max_value=60.0,
                          help="Normalised, not the filed rate. Burry normalises per company. Also the "
                               "rate ΔE below is measured at.") / 100.0

    _measured = operating_pool(years, rows, _cross_fy, tax, pre.get("shares_by_fy", {}))
    _prof_fys = _measured.fys if _measured is not None else []
    _dE_seed = _measured.dE if _measured is not None and _measured.measurable else 0.0
    dE_box = j2.number_input("ΔE (%)", value=round(_dE_seed * 100, 1), step=1.0,
                             help="Share of after-tax operating profit that reaches shareholders after the "
                                  "true cost of stock comp: (operating income × (1 − tax) + GAAP SBC − true "
                                  "SBC cost) ÷ operating income × (1 − tax), pooled over the profitable "
                                  "years since the crossing. Measured on the base this page projects, so "
                                  "no tax benefit or gain below the operating line can flatter it; tool 1's "
                                  "ΔE on net income will read differently. Held constant through Stage 0, "
                                  "the conservative side.") / 100.0
    _dE_set_by_hand = _measured is None or not _measured.measurable or abs(dE_box - _measured.dE) > 5e-4
    if _measured is not None and not _measured.measurable:
        j2.caption("Not measurable: no year-end share count for FY"
                   + ", FY".join(str(f) for f in _measured.missing_shares)
                   + " (or the year before), so the shares delivered priced at zero. Set it by hand.")
    elif _measured is not None:
        j2.caption(f"Measured {_measured.dE:.1%} on after-tax operating income over "
                   f"{_fy_range(_prof_fys)}"
                   + (" (one year)" if len(_prof_fys) == 1 else f" ({len(_prof_fys)} years)")
                   + f": base {_measured.sum_base:,.0f}M, GAAP SBC {_measured.sum_G:,.0f}M, true cost "
                   f"{_measured.sum_omega:,.0f}M.")
    else:
        j2.caption(f"No operating-profit year since the FY{_cross_fy} crossing to measure it on.")

    k1, k2, k3 = st.columns(3)
    price = k1.number_input("Price", value=float(current_price(pre.get("ticker", tk)) or 100.0), step=0.01)
    shares = k2.number_input("Diluted shares (M)", value=float(round(pre["shares"], 1)), step=1.0)
    cash = k3.number_input("Cash & investments ($M)", value=float(round(pre.get("cash", 0.0), 1)), step=10.0)
    debt = k3.number_input("Total debt ($M)", value=float(round(pre.get("debt", 0.0), 1)), step=10.0)
    net_cash = cash - debt
    _rw, _burn = runway_years(cash, _last.cfo, _last.capex)

    with st.expander("Model settings — tool 1's, unchanged"):
        m1, m2 = st.columns(2)
        exit_m = m1.number_input("Exit multiple", value=round(AICT[tier_name].default_exit_multiple, 2), step=0.5)
        m2_style = m1.radio("Exit-multiple leg", ["dcf", "hold"], horizontal=True,
                            format_func=lambda v: "Cash flows + exit" if v == "dcf" else "Buy and hold to year 15")
        blend = m2.slider("Long-horizon weight", 0.0, 1.0, 0.5, 0.05)
        t = AICT[tier_name]
        st.caption(f"{tier_name}: after Stage 0, stage 1 {t.stage1_years}y, stage 2 {t.stage2_years}y at "
                   f"{t.stage2_multiplier:.2f}x, terminal cap {t.terminal_growth_cap:.0%}.")

    # ── live gates on the boxes ──
    _ref8b = shares_gate(shares)
    _ref9, applied_dE = dE_gate(dE_box, _measured, _prof_fys)
    _ref10 = gross_margin_gate(terminal, _gm_latest)
    _ref11 = runway_gate(int(s0_years), _rw, _burn, cash)
    for _r in (_ref8b, _ref9, _ref10, _ref11):
        if _r:
            st.error("**Refused.** " + _r)
    if _ref8b or _ref9 or _ref10 or _ref11:
        with st.expander("Notes and detail", expanded=False):
            for kind_, msg in alerts:
                getattr(st, kind_)(msg)
            st.write("**What was read from the filings** — every tag, found or missing")
            st.dataframe(pd.DataFrame(pre.get("tags", [])), width='stretch', hide_index=True)
        st.stop()

    if above_incremental(terminal, _w_incr):
        alerts.append(("warning",
                       f"Terminal margin of {terminal:.1%} is above the incremental margin of "
                       f"{_w_incr:.1%} the company achieved on each new revenue dollar over the window. "
                       "The path assumes leverage not yet demonstrated in the filings."))
    _bl_note = below_line_note(below_line_years(rows))
    if _bl_note:
        alerts.append(("warning", _bl_note))
    if _measured is not None and len(_prof_fys) == 1 and not _dE_set_by_hand:
        alerts.append(("warning", f"ΔE is measured on a single profitable year, FY{_prof_fys[0]}. One year "
                                  "of stock-comp cost against one year of profit is a reading, not a rate."))
    if dE_was_capped(dE_box):
        alerts.append(("warning", f"ΔE measured {dE_box:.1%}; projected at 100% — a company cannot keep "
                                  "more than every reported dollar. Same cap as tool 1."))

    OE0 = oe_seed(rev0, m0, tax, applied_dE)
    g0 = stage0_rate(g_rev, m0, terminal, int(s0_years))
    par = IVParams(OE=OE0, shares=shares, tier=tier_name, growth=g1, net_cash=net_cash,
                   exit_multiple=exit_m, blend=blend, m2_style=m2_style,
                   stage0_years=int(s0_years), stage0_growth=g0)
    iv15 = intrinsic_value(par, 15)

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
    zn, zkind = zone(ratio)
    er_txt = "implausible" if er == float("inf") else f"{er:.1%}"
    _broken = er == float("inf") or (price > 0 and iv15 / price > 20)
    v1, v2, v3 = st.columns(3)
    v1.metric("IV15", f"${iv15:,.2f}", f"market ${price:,.2f}")
    v2.metric("Price / IV15", f"{ratio:.2f}x", "not usable" if _broken else zn)
    v3.metric("Expected return", er_txt, "no score — see below" if _broken else f"score {valuation_points(ratio)}/35")
    if _broken:
        st.error(f"**This result is not believable — an input is wrong.** IV15 of {d(iv15)} against a "
                 f"{d(price)} share price is a broken assumption, not a bargain. Check the terminal "
                 "margin, the growth through Stage 0 and the share count.")
    else:
        getattr(st, zkind)(f"**{zn}** at {ratio:.2f}x IV15 — implied {er_txt} a year.")
    _lad = ladder(par)
    st.caption(ladder_caption(_lad))
    st.caption(f"Stage 0: owners' earnings seeded at {OE0:,.0f}M (revenue {rev0:,.0f}M × margin {m0:.1%} × "
               f"(1 − {tax:.0%}) × ΔE {applied_dE:.1%}), growing {g0:.1%} a year for {int(s0_years)} years to "
               f"{rev0 * (1 + g_rev) ** int(s0_years) * terminal * (1 - tax) * applied_dE:,.0f}M "
               f"(revenue {rev0 * (1 + g_rev) ** int(s0_years):,.0f}M at {terminal:.1%}). Then {tier_name}'s stages at {g1:.1%}.\n\n"
               "This is not tool 1's IV15 for the same ticker, and should not be. Tool 1 seeds owners' "
               "earnings from forward net income × ΔE with no Stage 0; this page seeds from operating "
               "income — above interest, tax and one-offs — and projects the margin. Same filings, same "
               "year-by-year table, a different question.")

    # ── sensitivity ──
    st.write("**What the assumptions do to IV15** — terminal margin down the side, years across")
    _terms = [max(0.005, terminal - 0.05), terminal, terminal + 0.05]
    _yrs = list(dict.fromkeys([max(1, int(s0_years) - 2), int(s0_years), int(s0_years) + 2]))
    _grid = sensitivity(par, rev0, m0, tax, applied_dE, g_rev, _terms, _yrs, _gm_latest)
    st.dataframe(pd.DataFrame([{"Terminal margin": f"{mT:.1%}",
                                **{f"{n}y": cell(v, "${:,.2f}", "above gross margin") for n, v in zip(_yrs, _grid[i])}}
                               for i, mT in enumerate(_terms)]), width='stretch', hide_index=True)
    st.caption("Every cell is the same engine call with one input moved. Market price for reference: "
               f"{d(price)}.")
    st.write(f"**…and growth through Stage 0** — at the {terminal:.1%} terminal margin, years across")
    _gs = [g_rev * 0.5, g_rev * 0.75, g_rev]
    _ggrid = growth_sensitivity(par, rev0, m0, tax, applied_dE, _gs, _yrs, terminal)
    st.dataframe(pd.DataFrame([{"Stage 0 growth": f"{g:.1%}" + (" (box)" if i == 2 else ""),
                                **{f"{n}y": cell(v, "${:,.2f}") for n, v in zip(_yrs, _ggrid[i])}}
                               for i, g in enumerate(_gs)]), width='stretch', hide_index=True)
    st.caption("Rows run at half, three-quarters and the box's rate: a launch rate inflates this lever, "
               "so the risk is below the seed, not above it.")

    with st.expander("Notes and detail", expanded=False):
        for kind_, msg in alerts:
            getattr(st, kind_)(msg)

        st.write("**Owners' earnings, year by year** — identical to tool 1 for the same ticker")
        _dE_text = lambda v: "n/a (base too small)" if v is None else f"{v:.1%}"
        _med_N = median_positive_N([y.N for y in years])
        _mfmt2 = money_fmt([v for y in years for v in (y.N, y.G, y.T, y.omega, y.OE)])
        st.dataframe(pd.DataFrame([{
            "FY": f"{y.fy}*" if y.excluded else str(y.fy),
            "Net income": y.N, "GAAP SBC": y.G, "Buybacks": y.T,
            "Share change": y.dS, "Avg price": y.price, "True SBC cost": y.omega,
            "Owners' earnings": y.OE,
            "ΔE": _dE_text(dE_cell(y.N, y.dE, _med_N))} for y in years]).style.format({
                "Net income": _mfmt2, "GAAP SBC": _mfmt2, "Buybacks": _mfmt2,
                "Share change": "{:+,.1f}", "Avg price": "${:,.2f}", "True SBC cost": _mfmt2,
                "Owners' earnings": _mfmt2}, na_rep="—"),
            width='stretch', hide_index=True)

        st.write("**What was read from the filings** — every tag, found or missing")
        st.dataframe(pd.DataFrame(pre.get("tags", [])), width='stretch', hide_index=True)
        st.caption("A zero in this app is either something the company did not do or a tag this reader "
                   "does not know. If a line you know exists reads fewer years than net income, that is "
                   "a bug worth reporting — the tag name is the whole fix.")

        st.write("**Assumptions used** — paste this if something looks wrong")
        st.code(
            f"{tk}   price {price:,.2f}   shares {shares:,.1f}M   mkt cap ${shares*price/1000:,.2f}B\n"
            f"FILED   revenue FY{_last.fy}        {rev0:,.0f}\n"
            f"FILED   operating margin       {m0:.1%}   (path: {' → '.join(_pct(m) for _, m in _opm_pts[-4:])})\n"
            f"FILED   incremental, window    {_fmt_pct(_w_incr)}   since crossing {_fmt_pct(_post_incr)}   pace {'—' if _pace is None else f'{_pace:+.1%}/yr'}\n"
            f"FILED   gross margin           {_fmt_pct(_gm_latest)}\n"
            f"FILED   runway                 {'no burn' if _rw is None else f'{_rw:.1f}y at {_burn:,.0f}M'}\n"
            f"JUDGE   terminal margin        {terminal:.1%}   (seed: {_t_src})\n"
            f"JUDGE   years to terminal      {int(s0_years)}\n"
            f"FILED   revenue growth         latest {_fmt_pct(_lat_g)}   3y CAGR {_fmt_pct(_cagr3)}\n"
            f"JUDGE   growth through stage 0 {g_rev:.1%}   after {g1:.1%}\n"
            f"JUDGE   tax                    {tax:.0%}\n"
            f"JUDGE   ΔE                     {applied_dE:.1%}   "
            + (f"set by hand (measured {_measured.dE:.1%}, {_fy_range(_prof_fys)})" if _dE_set_by_hand and _measured is not None and _measured.measurable
               else "set by hand (not measurable — share count missing)" if _dE_set_by_hand and _measured is not None
               else "set by hand (nothing to measure)" if _dE_set_by_hand
               else f"measured {_fy_range(_prof_fys)}" + (" (capped)" if dE_was_capped(dE_box) else ""))
            + ", operating basis\n"
            f"ENGINE  stage 0                {int(s0_years)}y at {g0:.2%}   OE seed {OE0:,.0f}\n"
            f"ENGINE  tier {tier_name}   exit {exit_m:g}x   blend {blend:g}   leg {m2_style}\n"
            f"ENGINE  net cash               {net_cash:,.0f}   ({net_cash/shares:,.2f}/share)\n"
            f"IV15                {iv15:,.2f}   P/IV15 {ratio:.2f}x", language="text")


# ══════════════════════════════════════════════════════════════════════
#  REFERENCE
# ══════════════════════════════════════════════════════════════════════

st.divider()
_r1, _r2 = st.columns(2)
with _r1:
    with st.expander("What the numbers mean", expanded=False):
        st.markdown(
            "**Operating margin** — GAAP operating income over revenue. The line this page "
            "projects: one tag in every 10-K, above interest, tax and one-offs.\n\n"
            "**Incremental margin** — the share of each new revenue dollar that reached operating "
            "income. When it runs above the current margin, leverage is appearing.\n\n"
            "**Stage 0** — Burry's extra stage for inflecting hypergrowth: the margin projected "
            "geometrically to a terminal margin over stated years, then his normal stages. "
            "That path is a constant growth rate, so tool 1's engine carries it exactly.\n\n"
            "**ΔE** — the share of profit that reaches shareholders after the true cost of stock "
            "comp. Measured here over the profitable years only, never across the losses.\n\n"
            "**The refusals** — this app's. Wrong shape, a trend that reversed or is one year old, "
            "flat or noisy margins, no positive start, owners' earnings that never inflected, a "
            "terminal margin above gross margin, a runway shorter than the path.")

with _r2:
    with st.expander("Verify the engine"):
        st.caption("Ported-engine checks against tool 1's figures, the Stage 0 identity built by hand, "
                   "and every shape, gate and refusal on synthetic series. They do not use live filings.")
        if st.button("Run checks"):
            _results = self_test()
            _sev, _line = test_summary(_results)
            getattr(st, _sev)(_line)
            for name, ok, got in _results:
                st.write(("✅ " if ok else "❌ ") + f"{name} — {got}")

st.caption(
    "Research aid, not financial advice. Outputs depend on estimates you supply. The pricing "
    "follows Michael Burry's published writing; the trend evidence and the refusals are this "
    "project's own. Independent, not affiliated with or endorsed by him or Scion Asset Management.")
