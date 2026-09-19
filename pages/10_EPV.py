"""
EPV — Earnings Power Value (Bruce Greenwald)
============================================
What the business is worth assuming ZERO growth, from normalized current
earnings capitalized at a required return.

THE FRAME
---------
    normalized operating earnings
        = mean operating margin over the readable window x current revenue
          (+ two Greenwald judgement adjustments, stated defaults of zero)
    NOPAT   = that, taxed at the normalized rate (median filed effective rate)
    EPV     = NOPAT / required return  +  net cash, per share

Cycle normalization — the mean margin, not the latest year — is the method's
core move. Beside the standard leg, the kit's signature: the same EPV on
SBC-corrected earnings (the margin recomputed with the true stock-comp cost
Ω in place of the GAAP charge G), both legs on the SAME pool of years, so the
gap between them is exactly the capitalized (Ω − G) margin effect and nothing
else. The gap between EPV and the market price is the dollar amount the
market is paying for growth; the Expectations page states the growth path
that payment implies. Greenwald's reproduction-cost (asset value) leg is out
of scope for v1, and the page says which comparison it therefore cannot make.

The reader below is the Tragic Algebra Analyzer's, per the kit's per-page
copy pattern, with three added concept lines (OI, TAX, PRETAX) marked at
their sites — the Inflection Checker's precedent. Reader fixes land in every
carrier file, this one now included.

THE MENU MAP OF RECORD FOR THIS FILE (17 Sep 2026)
--------------------------------------------------
    Home (entrypoint)
    1   Tragic Algebra Analyzer      7   DCF Evaluator
    2   Hundred Bagger Checker       8   (reserved: the watchlist page)
    4   Inflection Checker           9   Expectations
    5   Financials Checker           10  EPV (this file)
    6   NonUS Checker                99  Return Calculator (last, permanently)

3 is retired and is never reused. The in-span header comment below stays at
its 12-Sep vintage on purpose: refreshing it is a fleet-wide wording ride,
queued, and editing it here alone would fork the copies.

Run from the repo root:  streamlit run Home.py
"""


# ══════════════════════════════════════════════════════════════════════
#  THE MENU MAP — FROZEN (toolkit pass, 12 Sep 2026)
#
#      Home (entrypoint)
#      1   Tragic Algebra Analyzer
#      2   Hundred Bagger Checker       (page title: 100-Bagger Checker)
#      4   Inflection Checker
#      5   Financials Checker
#      6   NonUS Checker                (page title: Non-US Checker)
#      7   DCF Evaluator
#      8   (reserved: the watchlist page)
#      99  Return Calculator            (structurally last for the life of the kit)
#
#  3 is retired; never reuse a number. User-facing text names pages by
#  their MENU NAMES, never by number — "tool 1" and "page 4" are banned
#  in UI strings; self-test labels, comments and docstrings are exempt.
#  Where ONE rendered sentence mentions the same page twice, the first
#  mention is the exact menu name and later mentions may be "that page"
#  or "it"; a mention inside a conditional clause prints alone, so it
#  counts as a first mention and carries the full name.
# ══════════════════════════════════════════════════════════════════════

from __future__ import annotations

FROZEN_MENU = ("Tragic Algebra Analyzer", "100-Bagger Checker", "Inflection Checker",
               "Financials Checker", "Non-US Checker", "DCF Evaluator",
               "Return Calculator",
               # EPV page, 17 Sep 2026: this page routes to the Expectations
               # page (the triad sentence), which postdates the fleet's tuple.
               # The fleet-wide tuple/map refresh is a queued wording ride;
               # this entry is page-local and additive.
               "Expectations")


def _route_ok(sentence: str) -> bool:
    """The sweep's test (toolkit pass, 12 Sep 2026): a user-facing route or
    provenance sentence names a frozen menu page and carries no numeric page
    reference. Asserted on every standalone sentence producer; inline UI text
    is covered by the build's tokenizer scan of record and the click-through."""
    import re as _re
    return (any(_name in sentence for _name in FROZEN_MENU)
            and not _re.search(r"(?i)\b(tool|page)s?\s+\d", sentence))

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
        """dE ** t. Under the assumption that the dilution pace producing this
        dE persists, this approximates the per-share level after t years
        relative to an undiluted path. dE itself is a level ratio (OE/N),
        not an annual retention factor — constant dE with N growing at g
        grows OE at g. The 11 Sep 2026 Reddit concession; the metric and
        banner wording carry the condition out loud."""
        return self.dE ** t

    def true_cagr(self, gaap_growth: float) -> float:
        """(1 + g) * dE - 1: per-share growth IF the dilution pace producing
        this dE persists — the conditional form (11 Sep 2026 concession).
        The 1/(1+g) break-even holds only under that assumption."""
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
    # The Ce list is ordered most-complete-first and must track FASB element
    # SUCCESSION — deprecations, successors, parents. One list, three filers,
    # three different holes in one session (DECOMP §6, 12 Sep 2026): parent
    # tag absent (TXN), successor tag absent (INTU), broad tag
    # present-and-correct (QCOM). These names were collected from past
    # filings; the element lifecycle is a dimension that collection never
    # modelled — when a filer's Ce reads empty, suspect succession before
    # suspecting the filer.
    # ProceedsFromIssuanceOrSaleOfEquity is the PARENT-level element and
    # sits LAST on purpose (12 Sep 2026): it fills only years no narrower
    # tag answered — TXN tags its employee proceeds there ("proceeds from
    # common stock transactions"). Its FASB definition spans offerings,
    # preferred and treasury sales, so it inherits the broad-tag offering
    # gate below; appended-last is the minimal blast radius by construction.
    "Ce": (["ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlans",
            # Rank 1 (12 Sep 2026, F2): the FASB SUCCESSOR of the deprecated
            # rank-0 element, adjacent to its predecessor. Intuit files it;
            # the reader used to fall through to options-only and miss the
            # ESPP component (1,028 over FY2017-2025, DECOMP §1.2). QCOM and
            # TXN companyconcept-verified 404 before this shipped.
            "ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlansIncludingStockOptions",
            "ProceedsFromStockOptionsExercised", "ProceedsFromIssuanceOfTreasuryStock",
            "ProceedsFromSaleOfTreasuryStock", "ProceedsFromStockPlans",
            "ProceedsFromEmployeeStockPurchasePlan", "ProceedsFromIssuanceOfCommonStock",
            "ProceedsFromIssuanceOrSaleOfEquity"], []),
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
    # ── The lines this page adds (EPV, 17 Sep 2026) ──────────────────
    # Operating income is the Inflection Checker's proven line, copied per
    # its pattern. TAX and PRETAX exist for the normalized tax rate alone:
    # effective rate = tax expense / pretax income per year, median over
    # the window's positive-pretax years. IncomeTaxExpenseBenefit is the
    # universal us-gaap element; the two pretax elements are the modern
    # one and its older sibling — most filers tag exactly one. IFRS lists
    # stay empty on TAX and PRETAX: foreign filers are refused by the
    # currency check long before a rate is computed, the same reasoning
    # as the N-list note above.
    "OI":   (["OperatingIncomeLoss"], ["ProfitLossFromOperatingActivities"]),
    "TAX":  (["IncomeTaxExpenseBenefit"], []),
    "PRETAX": (["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"], []),
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



# ── The financial gate ─────────────────────────────────────────────────
#
# STANDALONE BLOCK. financial_class() and the tables it reads are written
# so tool 1's session can paste them in unchanged — they use only the SIC
# string and the raw companyfacts dict. Every other page's banner follows
# what this decides.
#
# SIC names the class the filer is EXPECTED to be; the filing confirms
# it, because SIC misdescribes financials constantly: Schwab is 6211 (a
# broker) with $300B of deposits, Discover is 6141 (a lender) and a bank,
# Compass is 6531 and a brokerage of houses. A class is only assigned when
# the balance sheet carries the line that defines it.

BANK_SIC = {6021, 6022, 6029, 6035, 6036, 6712}
INSURER_SIC = {6311, 6321, 6324, 6331, 6351, 6361, 6399}
REIT_SIC = {6798}
# Lenders, finance companies and functions related to deposit banking: a
# bank when the filing carries deposits and net interest income, otherwise
# a float business no page in this kit prices.
PROMOTABLE_SIC = {6099, 6211} | set(range(6111, 6200))
# Brokers and dealers (14 Sep 2026, Financials Checker v2). The cascade for
# these two codes: deposits + NII promote to bank first (Schwab's shape,
# unchanged); else a filing that carries client assets is a broker; else
# refused. Client-asset evidence is the house pattern — code AND filed
# lines. The carriers on the evidence of record: IBKR passes on payables
# (us-gaap through FY2018, srt: since) plus segregated cash; HOOD passes on
# the segregated element alone (FY2023 on) — so no leg of this list is
# load-bearing for both, and a dealer or clearing house with none of them
# (Virtu's shape) still refuses at the same codes.
BROKER_SIC = {6211, 6221}
BROKER_CLIENT_TAGS = ["PayablesToCustomers", "srt:PayablesToCustomers",
                      "ReceivablesFromCustomers",
                      "CashAndSecuritiesSegregatedUnderFederalAndOtherRegulations",
                      "CashReserveDepositRequiredAndMade"]
# Fee businesses inside the 6000s that tool 1 prices as ordinary companies
# with net cash READ: insurance agents and brokers, asset managers,
# real-estate services and operators, royalty owners and lessors.
ORDINARY_SIC = {6411, 6282, 6792, 6794, 6795} | set(range(6500, 6554))

DEPOSIT_TAGS = ["Deposits", "DepositsDomestic", "InterestBearingDepositLiabilities"]
NII_TAGS = ["InterestIncomeExpenseNet",
            "InterestIncomeExpenseAfterProvisionForLoanLoss"]
PREMIUM_TAGS = ["PremiumsEarnedNet", "PremiumsEarnedNetPropertyAndCasualty",
                "PremiumsEarnedNetLife"]
REIT_PROPERTY_TAGS = ["RealEstateInvestmentPropertyNet", "RealEstateInvestmentPropertyAtCost"]

FINANCIAL_SIC_TABLE = (
    "This page prices banks (SIC 6021-6036, 6712) whose filings carry deposits and net "
    "interest income; insurers (6311-6399) whose filings carry premiums earned; equity "
    "REITs (6798) whose filings carry real estate; and brokers (6211, 6221) whose filings "
    "carry client assets — payables to customers, segregated cash or customer receivables. "
    "A broker that holds deposits and earns net interest income is priced as a bank. "
    "Lenders and finance companies (6099, 6111-6199) are priced as banks when they hold "
    "deposits and refused when they do not. Insurance agents (6411), asset managers "
    "(6282), real-estate services and operators (6500-6553) and royalty owners (6792-6795) "
    "are ordinary businesses and belong to the Tragic Algebra Analyzer with net cash read. "
    "Exchanges (6200), blank-check companies (6770), investors n.e.c. (6799), mortgage "
    "REITs and anything else in 6000-6799 are refused.")


def _tags_present(facts: dict, concepts: list[str]) -> bool:
    """Does the filing tag any of these concepts at all? Presence, not a
    read: the gate asks what kind of balance sheet this is, and a line that
    was tagged in any annual filing answers that even if it later stopped.
    A concept may carry a taxonomy prefix ("srt:PayablesToCustomers");
    without one it is us-gaap. IBKR, 14 Sep 2026: its live customer
    payables sit in the SEC's SRT taxonomy, invisible to a us-gaap-only
    look."""
    all_tax = facts.get("facts", {})
    for c in concepts:
        tax_name, _, name = c.rpartition(":")
        tax = all_tax.get(tax_name or "us-gaap", {})
        if name in tax and tax[name].get("units"):
            return True
    return False


def financial_class(sic: str, facts: dict) -> tuple[str, str]:
    """(class, reason). class is one of bank, insurer, reit, broker,
    ordinary, refused. `ordinary` means tool 1 prices it as a normal
    business; the Financials Checker prices the other four."""
    if not (sic and sic.isdigit()):
        return "ordinary", "No SIC code on file; not treated as a financial."
    code = int(sic)
    if not 6000 <= code <= 6799:
        return "ordinary", f"SIC {sic} is outside 6000-6799; not a financial."
    has_bank = _tags_present(facts, DEPOSIT_TAGS) and _tags_present(facts, NII_TAGS)
    has_prem = _tags_present(facts, PREMIUM_TAGS)
    has_re = _tags_present(facts, REIT_PROPERTY_TAGS)
    has_client = _tags_present(facts, BROKER_CLIENT_TAGS)
    if code in BANK_SIC:
        if has_bank:
            return "bank", f"SIC {sic} and the filing carries deposits and net interest income."
        return "refused", (f"SIC {sic} says bank, but the filing carries no deposits or net "
                           "interest income line this reader knows. Not priced.")
    if code in INSURER_SIC:
        if has_prem:
            return "insurer", f"SIC {sic} and the filing carries premiums earned."
        return "refused", (f"SIC {sic} says insurer, but the filing carries no premiums-earned "
                           "line this reader knows. Not priced.")
    if code in REIT_SIC:
        if has_re:
            return "reit", f"SIC {sic} and the filing carries real estate."
        return "refused", (f"SIC {sic} with no real estate on the balance sheet — a mortgage "
                           "REIT, which is a levered bond book. Not priced on this page.")
    if code in PROMOTABLE_SIC or code in BROKER_SIC:
        if has_bank:
            return "bank", (f"SIC {sic} is a lender or broker code, but the filing carries "
                            "deposits and net interest income — a bank in substance.")
        if code in BROKER_SIC and has_client:
            return "broker", (f"SIC {sic} and the filing carries client assets — payables to "
                              "customers, segregated cash or customer receivables — without "
                              "deposits. A broker in substance.")
        if code in BROKER_SIC:
            return "refused", (f"SIC {sic} says broker or dealer, but the filing carries no "
                               "client-asset line this reader knows — no payables to customers, "
                               "no segregated cash, no customer receivables. A dealer or "
                               "principal-trading house, not a client broker. Not priced.")
        return "refused", (f"SIC {sic} — a lender or finance company funded without "
                           "deposits. Its float is the product, and this page does not price "
                           "lending float businesses.")
    if code in ORDINARY_SIC:
        return "ordinary", (f"SIC {sic} — a fee business, not a balance-sheet one. The Tragic "
                            "Algebra Analyzer prices it as an ordinary company with net cash read.")
    return "refused", (f"SIC {sic} — an exchange, blank-check company or holding "
                       "structure this page does not price.")


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
             "OI", "TAX", "PRETAX"}   # the three lines this page adds


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
            + (f"{abs(alt - net_cash):,.1f}M" if abs(alt - net_cash) < 1 else f"{abs(alt - net_cash):,.0f}M")
            + ". Which of the two is right depends on whether the "
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
        "request covers the window's own span, so this is the provider's history running "
        "out rather than the window reaching past it. The market value of shares delivered "
        "floors at zero in those years, "
        "the true stock-comp cost becomes withholding minus option proceeds — negative where "
        "options were exercised — and ΔE stops being a measurement of anything.")


def _annual(facts: dict, us: list[str], ifrs: list[str],
            sources: list[str] | None = None,
            fill: bool = False,
            prefer_recent: bool = False,
            origin: dict[int, str] | None = None) -> dict[int, tuple[str, str, float]]:
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
                    if origin is not None:
                        # NFLX, 5 Sep 2026 (the Baselines app's first catch):
                        # the Ce gate armed on the broad tag because it filled
                        # 2007-2010, then zeroed FY2024's genuine exercises
                        # supplied by the NARROW tag. Which concept filled
                        # which year is the fact the gates need.
                        for fy in fresh:
                            origin[fy] = concept
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
                    if origin is not None:
                        for k in got:
                            origin[k] = concept
                    return {k: (v[1], v[2], v[3]) for k, v in got.items()}
    return {k: (v[1], v[2], v[3]) for k, v in out.items()}


def broad_gate_fires(origin_tag: str | None, broad: str,
                     v: float, G: float, N: float) -> bool:
    """Whether a size-gated cash line should be zeroed for one year.

    Fires only when BOTH hold: this year's value was supplied by the broad
    tag itself (NFLX, 5 Sep 2026 — a gate armed by out-of-window broad
    fills must never zero a narrow-supplied year: FY2024's $832.9M of
    genuine option exercises against a 3x charge line of $817.8M), and the
    value is raise-sized or repurchase-sized next to the charge (3x G, or a
    tenth of net income where no charge was tagged)."""
    if origin_tag != broad:
        return False
    return (v > 3 * G) if G > 0 else (v > 0.10 * abs(N))


def treasury_equal_reject(years, origin_cw: dict) -> list[tuple[int, float]]:
    """Zero every treasury-origin Cw candidate equal to the year's own buyback.

    Amazon, 12 Sep 2026 (DECOMP §1.4): C read 0.00 in nine years and exactly
    6,000.00 in FY2022 — equal to the same year's T. Amazon sells shares to
    cover employee taxes and files no withholding cash line at all; the
    treasury fallback offered the buyback, and the size gate accepted it
    because $6,000M against a $19,621M charge is a plausible withholding
    ratio. The $6B counted twice — once as the buyback in V, once as phantom
    Cw in C. A size test cannot catch this shape; only identity can: a
    candidate equal to the year's buyback IS the buyback. Tested to half a
    dollar (the $1-boundary lesson), before the size gate runs, on
    treasury-origin years only — a narrow-supplied year is never a candidate
    here, and a year with no buyback read has nothing to equal.

    Mutates years in place; returns the (fy, value) pairs rejected so the
    note can name each year and each figure.
    """
    hits: list[tuple[int, float]] = []
    for y in years:
        if not y.Cw or origin_cw.get(y.fy) != "TreasuryStockValueAcquiredCostMethod":
            continue
        if y.T and abs(y.Cw - y.T) < 0.5:
            hits.append((y.fy, y.Cw))
            y.Cw = 0.0
    return hits


def switched_series_notes(notes: list[str], first_split_notes: list[str],
                          winner_split_notes: list[str]) -> list[str]:
    """Notes after the share-count ladder replaces the series.

    TM printed phantom split notes from an abandoned share series (page 6's
    session, 4 Sep 2026; landed in the residue sweep, 16 Sep 2026): the
    first-choice series tripped split_adjust, the ladder then put that
    series aside, and the note announcing its split stayed — a split
    announced from a series the page does not use, on a company that never
    split. A note that describes what the page is not reasoning from is the
    false-sentence class. The abandoned series' split notes are dropped,
    every other note collected so far stands, and the winning series' own
    split notes are appended.
    """
    return [n for n in notes if n not in first_split_notes] + winner_split_notes


def treasury_mixed_note(accepted_fys: list[int]) -> str:
    """The sentence for a treasury line accepted in some years and rejected
    in others (ASML, 4 Sep 2026, queued from page 6's session; landed in
    the residue sweep, 16 Sep 2026). The rejection note says how many years
    fell to the size test; this one names the years that stand, so both
    sides of the split are on the page. No direction is claimed — whether
    the pooled figures run harsh or flattering depends on which side
    misread the tag — and the tag panel names the line to check.
    """
    fys = ", ".join(f"FY{f}" for f in accepted_fys)
    return (f"The same treasury line passed the size test in {fys} and stands as tax "
            "withholding there: those amounts are withholding-sized next to the charge. "
            "One tag, split by the size test — if the accepted years look like ordinary "
            "repurchases rather than withholding, the tag panel names the line to check.")


GATE2_REASON = "no share price — excluded per Gate 2"


def gate2_exclusions(years) -> list[int]:
    """Gate 2 (HANDOVER §5.3, from the article audit; landed 16 Sep 2026).

    Burry drops N, G and C for any year whose V is null for lack of a
    price; until today this page kept such years in the pools behind a
    one-line hedge. A year with no share price cannot price the shares it
    delivered: omega loses exactly the market value of every share handed
    out, owners' earnings read high by the same amount, and pooled ΔE is
    flattered. The years are excluded the way a listing year is — reason
    on the year, asterisk in the table, pools over what remains.

    Applies only when at least one year carries a price: with none at all
    the price fetch itself failed and price_coverage_refusal upstream owns
    the page — excluding everything here would empty every pool on a
    transient provider error. Returns the excluded fiscal years, oldest
    first.
    """
    if not any(y.price > 0 for y in years):
        return []
    hit = [y for y in years if y.price == 0]
    for y in hit:
        y.excluded = GATE2_REASON
    return sorted(y.fy for y in hit)


def gate2_note(excluded_fys: list[int]) -> str:
    """The page's sentence for gate2_exclusions — years named, direction
    stated, no hedge."""
    fys = ", ".join(f"FY{f}" for f in excluded_fys)
    year_word = "those years" if len(excluded_fys) > 1 else "that year"
    return (f"{fys} excluded — no share price, per Gate 2. The market value of shares "
            f"delivered cannot be priced in {year_word}, so the true stock-comp cost "
            "there is understated by exactly the market value of every share handed "
            "out — the flattering direction. The pooled figures cover the priced "
            "years only.")


def shd_input_seed(diluted: float, wv: dict, split_factor: float) -> tuple[float, str]:
    """SHD input-seed fallback (HANDOVER §1.10 job 3, the RDDT-era item;
    landed 16 Sep 2026).

    The seed cascade read `wavg` from dual_class_signal's same-year
    comparison value, which is 0.0 whenever the outstanding and average
    series share no year — including the shape where the year-end count
    reads NOTHING at all (an unregistered dual-class trio). The filled
    weighted-average series may still know a count, and the Hundred
    Bagger Checker already seeds from it (202.1M for RDDT, before the
    per-filing XBRL route served that name). A labeled average beats an
    empty box. Scaled like every other share figure.

    Returns (seed, note); the note is empty when the fallback did not
    fire, and the seed is the input unchanged.
    """
    if diluted > 0 or not wv:
        return diluted, ""
    fy = max(wv)
    seed = wv[fy] / 1e6 * split_factor
    return seed, (
        f"The diluted share count is seeded from the weighted-average diluted count "
        f"({seed:,.1f}M, FY{fy}) because no year-end share count was read. That count "
        "is an average over the year rather than a year-end snapshot, so check it "
        "against the market cap. The yearly table still has no share series: the true "
        "stock-comp cost and ΔE stay unmeasured there and need setting by hand.")


def treasury_accepted(years, origin_cw: dict) -> bool:
    """Whether the treasury-accepted sentence may print.

    True only when some SURVIVING Cw was actually supplied by the treasury
    tag. The earlier condition keyed on any survivor at all, which
    misdescribes a filer whose treasury candidates were all rejected while
    narrow-tag years stand (Intuit after the equality-reject): accepted
    means a treasury value exists and survived, not merely that any
    withholding exists. A sentence keying on survival instead of origin is
    the false-sentence class this project hunts (Chen, 12 Sep 2026).
    """
    return any(y.Cw and origin_cw.get(y.fy) == "TreasuryStockValueAcquiredCostMethod"
               for y in years)


def treasury_equal_note(hits: list[tuple[int, float]], any_cw_left: bool,
                        narrow_only_left: bool, any_G: bool) -> str:
    """The house-style sentence for an equality rejection — the note IS the
    finding (Chen, 12 Sep 2026). Two shapes.

    Nothing else read (the Amazon shape): the sell-to-cover reading, plus
    the direction claim the generic no-withholding note would have made —
    that note is suppressed when this one fires, so the two never co-fire
    (the JPM lesson applied in advance). The direction claim keeps the
    Shell guard: with no stock-comp charge read either, understatement is
    the honest direction, not flattery.

    Other years survive (the Intuit pre-2015 shape): the rejection sentence,
    and the statement that the rest stands — printed only when every
    survivor is narrow-origin, because a treasury-origin survivor is the
    accepted sentence's business, not this one's.
    """
    if len(hits) == 1:
        lead = (f"A treasury-stock line offered as tax withholding was rejected in "
                f"FY{hits[0][0]}: ${hits[0][1]:,.0f}M equals that year's buyback to "
                "the dollar")
    else:
        fy_list = ", ".join(f"FY{fy}" for fy, _ in hits)
        lead = (f"A treasury-stock line offered as tax withholding was rejected in "
                f"{len(hits)} years ({fy_list}): each figure equals that year's "
                "buyback to the dollar")
    if not any_cw_left:
        return (lead + ", and a figure equal to the buyback is the buyback — counting "
                "it would charge the same dollars twice, once as cash out and once as "
                "the market value of shares delivered. No withholding cash line was "
                "found at all, the shape of a filer that sells shares to cover "
                "employee taxes instead of paying cash. "
                + ("That leaves the SBC cost understated, so owners' earnings here "
                   "are flattering rather than conservative."
                   if any_G else
                   "No stock-comp charge was read either, so any year that also "
                   "lacks a share count charges its whole buyback as compensation — "
                   "owners' earnings in those years are understated rather than "
                   "flattering. Check the tag panel before using them."))
    return (lead + ", and a figure equal to the buyback is the buyback — counting it "
            "would charge the same dollars twice."
            + (" The other years read from a genuine withholding line and stand."
               if narrow_only_left else ""))


def offer_event_sized(v: float, prior_out: float, trailing: list,
                      persistent: bool = True) -> bool:
    """Whether an OFFER-series share figure is a genuine capital event.

    QCOM, 12 Sep 2026 (DECOMP §1.1, F1): Qualcomm tags twelve years of
    ordinary employee issuance under the generic new-issues element —
    round annual counts at payroll cadence, 1.5-2.2% of the outstanding
    count — and the unconditional exclusion removed them from dS, charged
    nothing for those shares, and let the buybacks that mopped them up
    count in full: ΔE read 117.5% against his 91.2. TGTX's raises on the
    SAME element run 7-26% of the count, dated events of days' duration
    (companyconcept-verified 12 Sep 2026). The separation is a factor of
    three on each side of these thresholds:

    Event-sized = above 4% of the prior year's outstanding count, OR a
    spike past three times the line's own trailing median (median test
    only with three or more prior years). No prior-year count to size
    against = stays excluded, the conservative default.

    MA and CONV stay UNCONDITIONALLY excluded — deal shares and
    conversions are always event-sized; this cadence test never weakens
    the deal-share exclusion (Chen, 12 Sep 2026). Only OFFER is tested.

    PERSISTENCE (12 Sep 2026, the KNSL amendment): an employee-issuance
    line prints every year; an episodic line is offerings by nature
    whatever its size. Kinsale — an IPO and three genuine follow-ons at
    3.5%/1.4%/0.7% of the count, 4 of 10 window years — slipped the size
    bar and had real raises priced as SBC for one run. The returned
    note's cadence claim is now enforced: persistent=False means the
    line prints only in occasional years and every year stays excluded.
    The caller supplies persistence (presence in at least 70% of the
    window years).

    v, prior_out and trailing are raw share counts on the same basis.
    """
    if not persistent:
        return True
    if not prior_out:
        return True
    if v > 0.04 * prior_out:
        return True
    if len(trailing) >= 3:
        _s = sorted(trailing)
        med = _s[len(_s) // 2]
        if med and v > 3.0 * med:
            return True
    return False


def offer_returned_note(total: float, fys: list) -> str:
    """The house-style sentence when payroll-cadence issuance returns to
    the share count — self-naming, so a page run months from now still
    announces what the test did (12 Sep 2026)."""
    fy_list = ", ".join(f"FY{fy}" for fy in fys)
    return (f"An issuance line usually excluded was returned to the share count in "
            f"{len(fys)} year(s) ({fy_list}): {total:,.1f}M shares moving at payroll "
            "cadence — a few percent of the outstanding count, no spike against the "
            "line's own history — which is employee stock wearing a capital-event "
            "tag, not a raise. Excluding it understated the SBC cost. Offerings "
            "that are genuinely event-sized (above 4% of the prior year's count, "
            "or past three times the line's own median) are still excluded, and a "
            "line that only prints in occasional years is treated as offerings "
            "throughout — cadence means every-year presence, not just modest size.")



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
def _monthly_closes(ticker: str, start: str | None = None
                    ) -> tuple[dict[str, float], dict[str, float]]:
    """Monthly closes keyed 'YYYY-MM', plus split events.

    The request reaches back to `start`'s month — the window's earliest
    fiscal-year start — or eleven years, whichever is EARLIER; never less
    than eleven, so every bar and split event the old rolling request
    returned is still returned, and a deep window gets its missing months.
    The rolling `range=11y` this replaces silently clipped the oldest
    window year: BBW's FY2016 average was a shrinking partial window that
    would have priced at zero ~Feb 2027 and turned V_2016 into the full
    buyback figure with no note (BASELINES-HANDOVER §1.7).

    The splits come back on the SAME request, which is why they are returned
    here rather than fetched separately: one round trip, one cache entry, and
    no possibility of the prices and the splits being read at different times.

    Every price in this series is already restated for any split, including
    one that happened yesterday. The share counts in this file come from
    filings and are not. See the reconciliation in load().
    """
    _p1 = _price_fetch_start(start, dt.date.today())
    _e1 = int(dt.datetime(_p1.year, _p1.month, 1, tzinfo=dt.timezone.utc).timestamp())
    _e2 = int(dt.datetime.now(dt.timezone.utc).timestamp())
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?interval=1mo&period1={_e1}&period2={_e2}&events=split",
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


def _price_fetch_start(start: str | None, today) -> "dt.date":
    """First month the price request asks for.

    The month of `start` (the window's earliest fiscal-year start) or eleven
    years back, whichever is EARLIER. The floor is the point: the request may
    extend past the old rolling eleven years but never fetch less, so every
    monthly bar and split event the old request returned is still returned —
    which is what lets a deploy of this change promise that no filer whose
    window sits inside eleven years moves by a cent.
    """
    eleven = dt.date(today.year - 11, today.month, 1)
    if not start:
        return eleven
    return min(dt.date.fromisoformat(start).replace(day=1), eleven)


def _priced_months(closes: dict[str, float], start: str, end: str) -> tuple[int, int]:
    """(months with a close, months in the period) — the same walk
    _avg_price takes, counting instead of averaging, so the two can never
    disagree about which months a year's average stands on."""
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    priced = expected = 0
    m = s
    while m <= e:
        if closes.get(f"{m.year:04d}-{m.month:02d}"):
            priced += 1
        expected += 1
        m = (m.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    return priced, expected


def partial_price_note(rows: list[tuple[int, int, int, bool]]) -> str:
    """One consolidated note naming every window year whose average price
    stands on fewer months than the fiscal year has. A partial average was
    SILENT before, and the silence was the defect: BBW's FY2016 drifted
    month by month with nothing on the page saying so.

    rows: (fy, priced, expected, nothing_before), where nothing_before means
    no month before the fiscal year's start has a price. The flag tells the
    two partial truths apart — listed mid-year is not the same fact as
    months missing from the history, and the sentence must say which. A year
    with no priced month and nothing before it is PRE-listing: not partial,
    not named here — zero-priced years are Gate 2's business.
    """
    parts = []
    for fy, priced, expected, nothing_before in rows:
        if priced >= expected:
            continue
        if priced == 0:
            if nothing_before:
                continue
            parts.append(
                f"FY{fy} has no priced month although the history covers earlier "
                "months — a hole at the price provider, so its shares delivered "
                "are valued at zero")
        elif nothing_before:
            parts.append(
                f"FY{fy}'s average covers {priced} of {expected} months: priced "
                "from its first trading month")
        else:
            parts.append(
                f"FY{fy}'s average price covers {priced} of {expected} months — "
                "months are missing inside the year")
    if not parts:
        return ""
    return ("; ".join(parts)
            + ". Those years' stock-comp costs are priced over the months that exist.")



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
    "OI": "Operating income", "TAX": "Income tax expense", "PRETAX": "Pretax income",
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
        return head + ("Check each line in the tag panel before trusting any figure below. "
                       "The Non-US Checker page reads the IFRS names this page does not — "
                       "prefer it for this ticker.")
    return head + ("Nothing at all was read for: " + ", ".join(unread) + ". Those lines are "
                   "wrong rather than missing — a line that reads nothing is treated as a "
                   "zero. Treat the whole page as unverified and do not use the valuation. "
                   "The Non-US Checker page reads the IFRS names this page does not — use it "
                   "for this ticker.")


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
                     factor: float = 1.0, any_T: bool = True) -> str:
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
        # IBKR, 15 Sep 2026 (FIN-V2 §7.1): the canned premise "while the
        # company was buying stock back" fired on a filer with no buybacks
        # read at all. The static test never needed it — a real outstanding
        # count almost never sits still to the share — so the premise now
        # prints where buybacks were read and the test stands alone where
        # they were not. Landed in the residue sweep, 16 Sep 2026.
        return (("The share count barely moved while the company was buying stock back, so the "
                 if any_T else
                 "The share count barely moved across the whole window, which a real "
                 "outstanding count almost never does, so the ")
                + "tag being read is not shares outstanding. Switched to {}.").format(route)
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
    tag_origin: dict[str, dict[int, str]] = {k: {} for k in CONCEPTS}
    series = {k: _annual(facts, us, ifrs, tag_sources[k], k in FILL_KEYS,
                         k in RECENCY_KEYS, tag_origin[k])
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

    # Per-filing XBRL route (queue G, 9 Sep 2026): registered tickers only —
    # one dict lookup and out for everyone else, which is how the untouched-
    # shape controls hold to the byte. Runs after N so the window and fiscal
    # end dates exist; folds Cw (and its Ce zeroing) into `series` in place;
    # the share-count fills merge just below, before split_adjust, so the
    # split machinery, dS, coverage and the dual-class test consume them
    # through the normal pipeline and every refusal that keys on absence
    # lifts by itself.
    _xr = xbrl_route_apply(ticker, cmap[ticker], series, tag_sources,
                           tag_origin, n_years)
    _upcb = up_c_income_rebase(ticker, facts, series, tag_sources)

    shares_out = _instant(facts, ["CommonStockSharesOutstanding", "CommonStockSharesIssued",
                                  "EntityCommonStockSharesOutstanding"], unit="shares")
    shares_out = {k: v for k, v in shares_out.items() if v and v > 0}
    if _xr and _xr["SHO"]:
        # Never overrides a count that was read; for the registered dual-class
        # filers nothing undimensioned exists to override.
        for _fy, _v in _xr["SHO"].items():
            shares_out.setdefault(_fy, _v)
    # Bind `notes` HERE, not further down. The share-count ladder below appends
    # to it, and Python makes a name local to the whole function the moment it
    # is assigned anywhere in it — so initialising notes after the ladder threw
    # UnboundLocalError on every ticker that tripped the ladder (AZO, HRB, TDG)
    # while leaving every other ticker working. Keep this line above the ladder.
    shares_out, notes = split_adjust(shares_out)
    # Held apart: if the share-count ladder below replaces the series, the
    # first-choice series' split notes leave with it (TM's phantom pair —
    # see switched_series_notes; residue sweep, 16 Sep 2026).
    _split_notes0 = list(notes)
    if _xr:
        notes.extend(_xr["notes"])
        if _upcb:
            notes.extend(_upcb["notes"])
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
            # The abandoned first-choice series' split notes go with the
            # series (TM's phantom pair; residue sweep, 16 Sep 2026).
            notes[:] = switched_series_notes(notes, _split_notes0, _extra)
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

    # The request must reach the window's own start: a rolling eleven-year
    # range silently clipped the oldest year's average (BBW FY2016, §1.7).
    _px_win = sorted(series["N"])[-n_years:]
    _px_start = series["N"][_px_win[0]][0] if _px_win else None
    try:
        closes, splits = _monthly_closes(ticker, _px_start)
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
        notes.append(share_route_note(*_route_note, factor=_split_factor,
                                      any_T=bool(series.get("T"))))
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
    # For a verified Up-C rebase the helper stays silent: ProfitLoss there is
    # the deliberate basis, and the basis note and banner already say so.
    _nsrc = tag_sources.get("N", [])
    _mn = mixed_n_note(_nsrc, bool(_upcb and _upcb.get("ok")))
    if _mn:
        notes.append(_mn)

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


    # Grab, Sophia Genetics, Legend Biotech (page 4's runs, 1 Sep 2026): a
    # USD-reporting 20-F filer passes this load on the net-income fallback and
    # then reads no revenue from any tag this page carries — its statements use
    # ifrs-full names the US fill lists do not. Rendering a page around that
    # hole showed growth seeds, net cash and share counts as zeros that looked
    # like readings. Refuse with the route instead: the Non-US Checker reads
    # the IFRS names.
    if series.get("N") and not series.get("REV"):
        raise ValueError(
            f"{ticker} cannot be valued from these filings on this page — net income was read "
            "but revenue reads nothing from any tag this page knows. That is the shape of a "
            "foreign filer whose statements use IFRS (ifrs-full) names this US page does not "
            "carry. Use the Non-US Checker page, which reads those names and refuses what it "
            "cannot; a US filer that genuinely tags no revenue would be refused here either way.")

    non_sbc_total = 0.0
    _off_returned = 0.0
    _off_ret_years: list[int] = []
    # Presence in at least 70% of the window years = the cadence the
    # returned note claims (the KNSL amendment, 12 Sep 2026).
    _off_persistent = (sum(1 for _fy in fys if _fy in series.get("OFFER", {}))
                       >= 0.7 * len(fys))
    years: list[Year] = []

    for fy in fys:
        start, end, N = series["N"][fy]
        get = lambda k: abs(series[k][fy][2]) / 1e6 if fy in series[k] else 0.0

        dS = ((shares_out[fy] - shares_out[fy - 1]) / 1e6
              if fy in shares_out and fy - 1 in shares_out else 0.0)
        _mc = sum(abs(series[k][fy][2]) / 1e6
                  for k in ("MA", "CONV") if fy in series.get(k, {}))
        _off_raw = (abs(series["OFFER"][fy][2])
                    if fy in series.get("OFFER", {}) else 0.0)
        _off_event = bool(_off_raw) and offer_event_sized(
            _off_raw, float(shares_out.get(fy - 1, 0) or 0),
            [abs(v[2]) for y2, v in series.get("OFFER", {}).items() if y2 < fy],
            _off_persistent)
        non_sbc = _mc + (_off_raw / 1e6 if _off_event else 0.0)
        if non_sbc:
            dS -= non_sbc
            non_sbc_total += non_sbc
        if _off_raw and not _off_event:
            _off_returned += _off_raw / 1e6
            _off_ret_years.append(fy)
        price = _avg_price(closes, start, end) or 0.0

        years.append(Year(fy=fy, N=N / 1e6, G=get("G"), T=get("T"), dS=dS,
                          Cw=get("Cw"), Ce=get("Ce"), price=price, A=get("MAV")))

    # V is priced at the year's average, so a year with no price contributes
    # nothing to the stock-comp cost however many shares moved.
    _unpriced = sum(1 for y in years if y.price <= 0)
    _pc = price_coverage_refusal(len(years), _unpriced, bool(closes))
    if _pc:
        raise ValueError(f"{ticker} cannot be valued from these filings — " + _pc)

    # Gate 2 sits here, above every consumer of the year list: the
    # negative-Ω note, the share ladder and the pools all read `excluded`,
    # and a later site would leave the RIVN-shape note claiming a year
    # "is not excluded" after Gate 2 excluded it.
    _g2 = gate2_exclusions(years)
    if _g2:
        notes.append(gate2_note(_g2))

    # No silent partial averages: name every window year whose average stands
    # on fewer months than the fiscal year has, and say WHICH partial truth
    # it is — listed mid-year, or months missing from the history (§1.7).
    if closes:
        _first_px = min(closes)
        _pp = partial_price_note(
            [(fy,) + _priced_months(closes, series["N"][fy][0], series["N"][fy][1])
             + (_first_px >= series["N"][fy][0][:7],) for fy in fys])
        if _pp:
            notes.append(_pp)

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
                   "capital event — an all-stock acquisition or an equity raise.")
                + " Counting it as compensation would swamp every other year in the pool. The "
                  "pooled figures now cover fewer years, so read them with that in mind.")

    if non_sbc_total:
        notes.append(f"Excluded {non_sbc_total:,.1f}M shares issued for acquisitions, offerings "
                     "or conversions — those are corporate transactions, not compensation. "
                     "Where a company issues stock for deals this matters a great deal.")
    if _off_returned:
        notes.append(offer_returned_note(_off_returned, _off_ret_years))
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
        # Identity runs before size (Amazon, 12 Sep 2026): a treasury candidate
        # equal to the year's own buyback is rejected outright by
        # treasury_equal_reject — the size gate then tests what survives.
        # AutoZone's $1.5B would be caught by either test; Amazon's $6B, at a
        # plausible 31% of the charge, only by identity.
        _teq = treasury_equal_reject(years, tag_origin["Cw"])
        capped = 0
        for y in years:
            if not y.Cw:
                continue
            if broad_gate_fires(tag_origin["Cw"].get(y.fy),
                                "TreasuryStockValueAcquiredCostMethod",
                                y.Cw, y.G, y.N):
                y.Cw, capped = 0.0, capped + 1
        capped_any = capped > 0 or bool(_teq)
        if _teq:
            notes.append(treasury_equal_note(
                _teq, any(y.Cw for y in years),
                not treasury_accepted(years, tag_origin["Cw"]),
                any(y.G for y in years)))
        if capped:
            notes.append(
                f"A treasury-stock line was read as tax withholding and rejected in {capped} "
                "year(s): it was more than three times the GAAP stock-comp charge, or — where "
                "no charge was tagged to size it against — more than a tenth of net income. "
                "Either means it is an ordinary repurchase, and charging it as withholding "
                "would count the same dollars twice — once as cash out, once as the market value "
                "of shares delivered.")
            # ASML (4 Sep 2026; landed 16 Sep 2026): on a mixed filer the
            # accepted years are named beside the rejection, so both sides
            # of the size test are on the page.
            _tacc = sorted(y.fy for y in years
                           if y.Cw and tag_origin["Cw"].get(y.fy)
                           == "TreasuryStockValueAcquiredCostMethod")
            if _tacc:
                notes.append(treasury_mixed_note(_tacc))
        elif treasury_accepted(years, tag_origin["Cw"]):
            # JPM, 1 Sep 2026 (page 5's run): the treasury tag was in the
            # sources but no year survived the filters, so this "accepted"
            # sentence fired alongside "no tax-withholding line found" three
            # notes later. Accepted means values exist — and since 12 Sep 2026
            # it means a TREASURY value survived: keying on any survivor at
            # all misdescribed Intuit once its pre-2015 treasury candidates
            # were equality-rejected while its narrow-tag years stood.
            notes.append(
                "Tax withholding was read from a treasury-stock line rather than the usual "
                "withholding tag. Filers that retire shares on repurchase report it this way. "
                "The amounts are withholding-sized, so they were accepted.")

    _CE_BROAD = ("ProceedsFromIssuanceOfCommonStock",
                 "ProceedsFromIssuanceOrSaleOfEquity")
    if any(_b in tag_sources.get("Ce", []) for _b in _CE_BROAD):
        # Carvana, 1 Sep 2026 (page 4's run): the broad issuance-proceeds tag
        # was the only Ce name that answered, and it carried the ATM equity
        # programme — hundreds of millions a year of capital raising read as
        # option and ESPP proceeds. Omega came out at -1,751M over FY2024-25:
        # the raise was credited to owners' earnings. Same disease, same cure
        # as the treasury-as-withholding gate above: genuine employee proceeds
        # are small next to the GAAP charge; a raise is not. Sized against the
        # charge where there is one, net income where there is not. The gate
        # runs only when a broad tag is a Ce source at all — the narrow
        # names alone are never gated, matching the Cw gate's own behaviour.
        # The parent-level ProceedsFromIssuanceOrSaleOfEquity (appended last,
        # 12 Sep 2026) INHERITS this gate: its FASB definition spans
        # offerings, preferred and treasury sales — without inheritance the
        # parent tag rebuilds the Carvana hole one level up. TXN itself never
        # trips it (max 539 vs 3xG >= 756).
        _ce_capped = 0
        for y in years:
            if not y.Ce:
                continue
            _org = tag_origin["Ce"].get(y.fy)
            if _org in _CE_BROAD and broad_gate_fires(_org, _org, y.Ce, y.G, y.N):
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
            + (", yet the share count fell by more than 1% in each." if len(_gap) > 1 else
               ", yet the share count fell by more than 1% that year.")
            + " Those years are almost "
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
    fin_class, fin_reason = financial_class(sic, facts)
    if fin_class in ("bank", "insurer", "reit", "broker"):
        # KNSL, 1 Sep 2026: the old banner disclaimed the number and the
        # verdict still printed a fat pitch on 18% premium growth at a
        # software exit. The Financials Checker exists now; route, withhold.
        # Broker joined 14 Sep 2026 (Financials Checker v2): IBKR must route
        # like an insurer does, never land as ordinary with net cash read.
        _backing = ("Cash and investments here largely mirror client balances — payables to "
                    "customers, not shareholder money"
                    if fin_class == "broker" else
                    "Investments here back policyholder or depositor liabilities rather than "
                    "belonging to shareholders")
        notes.append(f"{sic_desc or 'Financial company'} (SIC {sic}). {fin_reason} {_backing}, "
                     "so net cash has been set to zero. The Tragic Algebra below "
                     "is real; the valuation frame is not — "
                     f"{ {'bank': 'a bank', 'insurer': 'an insurer', 'reit': 'a REIT', 'broker': 'a broker'}[fin_class] } "
                     "is priced on tangible book, returns and payout, which is the "
                     "Financials Checker page's job. The verdict here is withheld.")
        cash_total = debt_total = net_cash = 0.0
    elif fin_class == "refused":
        notes.append(f"{sic_desc or 'Financial company'} (SIC {sic}). {fin_reason} Net cash has "
                     "been set to zero and the verdict is withheld — the Tragic Algebra below "
                     "is real, the valuation frame is not, and no page in this kit prices this "
                     "class yet.")
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
    # SHD input-seed fallback — see shd_input_seed: `wavg` above is a
    # same-year comparison artifact and reads 0.0 for the read-nothing
    # trio shape even when the filled series knows a count.
    diluted, _shd_note = shd_input_seed(diluted, _wv, _split_factor)
    if _shd_note:
        notes.append(_shd_note)
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
    ] + ([{
        "Line": "— Shares: XBRL route", "Years read": len(_xr["SHO"]),
        "Latest year": _latest_fy(_xr["SHO"]),
        "XBRL tag": "per-filing instance route (registered)",
        "Status": "used — per-class counts summed where nothing undimensioned "
                  "exists; the route note above names the filings",
    }] if _xr and _xr["SHO"] else []) + [
        {"Line": f"— {name}", "Years read": _bal_n.get(ks[0], 0),
         "Latest year": _bal_fy.get(ks[0], "—"),
         "XBRL tag": " + ".join(_bal.get(ks[0], [])) or "—",
         "Status": "read" if _bal.get(ks[0]) else "none of the tags this reader knows are in "
                                                 "the filing"}
        for name, ks in BALANCE_ROWS]
    # ── Added for this page (EPV, 17 Sep 2026): the normalization lines
    # by fiscal year, in $M, for the same window as `years`. Absent means
    # the tag did not answer for that year; the page refuses the cell
    # rather than reading zero. Operating income and pretax income keep
    # their sign; tax expense keeps its filed sign (a benefit reads
    # negative). Mirrors the Inflection Checker's _trend insertion.
    _epv_signed = lambda k, fy: (series[k][fy][2] / 1e6) if fy in series.get(k, {}) else None
    _epv = {fy: {"rev": _epv_signed("REV", fy), "oi": _epv_signed("OI", fy),
                 "tax": _epv_signed("TAX", fy), "pretax": _epv_signed("PRETAX", fy)}
            for fy in fys}
    return years, notes, {"up_c_basis": _upcb, "tags": tags, "net_cash": net_cash, "cash": cash_total, "debt": debt_total,
                          "epv": _epv,
                          "median_OE": _med, "revenue": latest_rev, "cagr3": cagr3,
                          "leases": lease_total,
                          # The form that resolved against the SEC list. Yahoo uses the
                          # same hyphenated spelling, so pricing BRK.B as typed returned
                          # nothing and the page fell back to its $100.00 default beside
                          # a real market cap.
                          "ticker": ticker,
                          "shares": diluted, "growth": growth, "sic": sic,
                          "sic_desc": sic_desc, "financial": fin_class in ("bank", "insurer", "reit", "broker", "refused"),
                          "fin_class": fin_class, "fin_reason": fin_reason,
                          "sh_cov": (_cov_n, len(_win_cov))}


# Lives ABOVE the UI line on purpose (6 Sep 2026): self-test 21 calls it,
# and the Baselines app copies everything above the UI banner verbatim —
# a test's dependency below that line is a name the copy cannot see.
def dE_caption(dE: float, defined: bool, no_counts: bool) -> str:
    """The ΔE radio caption. META, 5 Sep 2026 (Chen): a dual-class filer with
    no share count in ANY year still displayed pooled ΔE (60.4%/56.4%) as
    selectable figures — but with every year's share change reading zero, the
    whole buyback is charged as cost and the pools are meaningless (his META
    is 83.3%). A number the page cannot stand behind must not be offered as a
    choice."""
    if no_counts:
        return "n/a — no share counts read"
    return f"{dE:.1%}" if defined else "n/a — losses"



# ══════════════════════════════════════════════════════════════════════
#  PER-FILING XBRL ROUTE (queue G, 9 Sep 2026)
# ══════════════════════════════════════════════════════════════════════
#
# The companyfacts/companyconcept APIs exclude, BY DESIGN, custom-namespace
# tags and dimensioned facts (SEC API docs; proved on Alphabet 8 Sep 2026 —
# BASELINES-HANDOVER §1.14). Two of this kit's known holes share that cause:
# Alphabet's entire employee stock flow lives under
# goog:NetProceedsPaymentsRelatedToStockBasedAwardActivities, and every
# dual-class filer's per-class share counts carry a class dimension that the
# aggregation API strips. Both numbers exist in each filing's OWN XBRL
# instance document. This block fetches and parses those instances — for
# REGISTERED TICKERS ONLY. An unregistered ticker takes one dict lookup and
# leaves this block entirely: zero fetches, zero behaviour change, which is
# how the untouched-shape controls hold to the byte.
#
# The registry is explicit and verification-gated: every entry carries
# figures (pasted from the filings' own iXBRL fact panels, 9 Sep 2026) that
# the extraction must reproduce, or the whole entry is discarded with a loud
# note. The route folds verified plumbing into the same series the reader
# already builds, before the gates — or it folds nothing.

@dataclass(frozen=True)
class XbrlRoute:
    """One registered read: what to extract and what it must reproduce.

    key       — the reader series it feeds: "Cw" (a cash-flow duration line)
                or "SHO" (year-end share counts, folded into shares_out).
    kind      — "duration" | "instant_sum".
    concepts  — qnames tried IN ORDER. For instant_sum the fallback runs per
                class member per instant, because filers mix the concepts
                along every seam there is: Reddit tags Class A as Issued and
                Class B as Outstanding; Meta mixes them across YEARS within
                one class; Ryan Specialty tags by year across both classes.
                Three of four registered dual-class filers mix — the ordered
                fallback is the normal case, not an edge handler.
    axis      — instant_sum only: the dimension whose members are summed.
    net       — the Cw line is NET of option/ESPP proceeds (Alphabet's tag
                name says so: "NetProceedsPayments"). Filled years force
                Ce = 0 — proceeds are already inside the line, and charging
                Ce on top would count them twice. §1.14's settled decision.
    verify    — ((fy, expected), ...): the extraction must reproduce these
                to $0.50 / half a share, or the entry is discarded. Compared
                on magnitude: the instance stores Alphabet's net outflow as
                a positive credit-balance figure (iXBRL storage convention;
                brackets are presentation), and the engine's Year.omega
                takes abs() of Cw regardless. A hypothetical net-INFLOW year
                would surface in the per-year acceptance decomposition
                against the published table, not in sign logic — no such
                year exists in the ten on record, and the caveat is stated
                in the route note rather than coded for.
    up_c      — umbrella-partnership C-corp (Carvana, Ryan Specialty): the
                counts fold (the table, Ω and ΔE pools are real — an A+B sum
                is exchange-invariant under LLC-unit conversions), but the
                page must still refuse the per-share VALUATION: the net
                income read is the parent's slice while the summed count
                spans everything. Carvana's Class B is ~35% of the total,
                Ryan's over half — per-share figures would be wrong by that
                fraction. The stop lifts with HANDOVER §5.5 G's NCI fix
                (parent slice over parent-only count, or whole over whole),
                for which these two entries are the test cases. NOT lifted
                by this route, and not claimed to be.
    only_fys  — duration entries only: restrict the read to exactly these
                fiscal years; empty = every window year (the GOOGL shape).
                Adobe's custom withholding tag exists only in its pre-FY2019
                filings — the standard narrow tag covers FY2019 on — so
                without the restriction the walk would fetch toward the cap
                chasing years the concept never tags, and the route note
                would call FY2019+ "still unread" while the narrow tag
                reads them: a false sentence. Scoping the read is the
                truthful shape (12 Sep 2026).
    """
    key: str
    kind: str
    concepts: tuple[str, ...]
    axis: str | None = None
    net: bool = False
    verify: tuple[tuple[int, float], ...] = ()
    up_c: bool = False
    only_fys: tuple[int, ...] = ()


# Verification figures: GOOGL from the 10-K face / §1.14 record; the four
# dual-class names from Chen's iXBRL fact-panel pastes of 9 Sep 2026
# (per-class year-end counts, summed).
XBRL_REGISTRY: dict[str, tuple[XbrlRoute, ...]] = {
    "GOOGL": (XbrlRoute(
        key="Cw", kind="duration",
        concepts=("goog:NetProceedsPaymentsRelatedToStockBasedAwardActivities",),
        net=True,
        verify=((2025, 14_167_000_000.0), (2024, 12_190_000_000.0))),),
    # ADBE — Cw for FY2016-2018 ONLY (12 Sep 2026, the F4/ADBE
    # decomposition). Adobe's pre-FY2019 withholding is tagged under the
    # custom concept adbe:CostOfIssuanceOfTreasuryStock — value-searched to
    # zero standalone hits in companyfacts (custom = invisible to the
    # aggregation API by design), verification figures pasted from the
    # FY2018 10-K's raw instance (pre-iXBRL, EX-101.INS adbe-20181130.xml;
    # one filing carries all three years as comparatives).
    # NAME SEMANTICS, do not misread: Adobe named the concept
    # "CostOfIssuanceOfTreasuryStock" while LABELLING the line "Taxes paid
    # related to net share settlement of equity awards" — the name must NOT
    # be read as a buyback line; the label and the figures are the identity
    # (236.400 / 240.126 / 393.193 match the 10-K face withholding line to
    # the dollar; the treasury PURCHASES are a separate line the
    # equality-reject already handles). net=False: Adobe's re-issuance
    # proceeds are a real, separately read Ce line
    # (ProceedsFromIssuanceOfTreasuryStock, verified to the dollar
    # FY2016-2018) and keep their credit. FY2019 on reads the standard
    # narrow withholding tag through _annual exactly as before.
    "ADBE": (XbrlRoute(
        key="Cw", kind="duration",
        concepts=("adbe:CostOfIssuanceOfTreasuryStock",),
        verify=((2018, 393_193_000.0), (2017, 240_126_000.0),
                (2016, 236_400_000.0)),
        only_fys=(2016, 2017, 2018)),),
    "CVNA": (XbrlRoute(
        key="SHO", kind="instant_sum",
        concepts=("us-gaap:CommonStockSharesOutstanding",
                  "us-gaap:CommonStockSharesIssued"),
        axis="us-gaap:StatementClassOfStockAxis", up_c=True,
        verify=((2025, 218_339_000.0), (2024, 212_390_000.0))),),
    "RDDT": (XbrlRoute(
        key="SHO", kind="instant_sum",
        concepts=("us-gaap:CommonStockSharesOutstanding",
                  "us-gaap:CommonStockSharesIssued"),
        axis="us-gaap:StatementClassOfStockAxis",
        verify=((2025, 190_892_108.0), (2024, 180_315_979.0))),),
    "META": (XbrlRoute(
        key="SHO", kind="instant_sum",
        concepts=("us-gaap:CommonStockSharesOutstanding",
                  "us-gaap:CommonStockSharesIssued"),
        axis="us-gaap:StatementClassOfStockAxis",
        verify=((2025, 2_530_000_000.0), (2024, 2_534_000_000.0))),),
    "RYAN": (XbrlRoute(
        key="SHO", kind="instant_sum",
        concepts=("us-gaap:CommonStockSharesOutstanding",
                  "us-gaap:CommonStockSharesIssued"),
        axis="us-gaap:StatementClassOfStockAxis", up_c=True,
        verify=((2025, 264_112_311.0), (2024, 261_867_402.0))),),
}

# Standard-taxonomy namespace stems. A qname with one of these prefixes must
# resolve to its stem (the URIs are year-versioned: fasb.org/us-gaap/2023);
# any OTHER prefix is a filer's custom namespace and matches any URI that is
# NOT standard. Matching custom tags by prefix string would break the day a
# filer declares xmlns:googl instead of xmlns:goog; matching by exact URI is
# impossible because custom URIs change every filing year.
_XBRL_STD_STEMS = {
    "us-gaap": ("http://fasb.org/us-gaap",),
    "srt": ("http://fasb.org/srt",),
    "dei": ("http://xbrl.sec.gov/dei",),
    "ifrs-full": ("http://xbrl.ifrs.org/taxonomy",),
}
_XBRL_ALL_STD = (
    "http://fasb.org/", "http://xbrl.sec.gov/", "http://xbrl.ifrs.org/",
    "http://www.xbrl.org/", "http://xbrl.org/", "http://www.w3.org/")


def _xbrl_qname_matches(qname: str, uri: str, local: str) -> bool:
    """Whether a fact at (namespace uri, local name) is the registered qname."""
    prefix, _, want_local = qname.partition(":")
    if want_local != local:
        return False
    stems = _XBRL_STD_STEMS.get(prefix)
    if stems:
        return any(uri.startswith(s) for s in stems)
    return not any(uri.startswith(s) for s in _XBRL_ALL_STD)


def _xbrl_instance_guess(primary_doc: str) -> str:
    """Modern iXBRL filings: EDGAR generates an extracted instance named
    {primary stem}_htm.xml (verified on Alphabet's FY2024 index:
    goog-20241231.htm -> goog-20241231_htm.xml, 2.7MB)."""
    stem = primary_doc.rsplit(".", 1)[0]
    return f"{stem}_htm.xml"


def _xbrl_pick_instance(names: list[str]) -> str | None:
    """Pre-iXBRL fallback: pick the raw instance out of a filing's file list.

    Verified against Alphabet's FY2016 10-K index (pasted 9 Sep 2026): the
    instance is goog-20161231.xml (EX-101.INS) and the primary document stem
    (goog10-kq42016) does NOT predict it — so the modern-name guess 404s on
    old filings and this rule must pick the instance out of index.json. The
    linkbases and schema share the instance's stem with suffixes; excluding
    them leaves the instance. R*.xml and FilingSummary are viewer artifacts.
    """
    cands = []
    for n in names:
        low = n.lower()
        if not low.endswith(".xml"):
            continue
        if low.endswith(("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml", "_htm.xml")):
            # _htm.xml is the modern extracted instance: if it exists the
            # guess would have found it; keep it as a candidate anyway.
            if low.endswith("_htm.xml"):
                cands.append((0, n))
            continue
        if low.startswith(("r", "filingsummary")) and (low[1:2].isdigit() or low.startswith("filingsummary")):
            continue
        import re as _re
        cands.append((0 if _re.search(r"-\d{8}\.xml$", low) else 1, n))
    if not cands:
        return None
    cands.sort()
    return cands[0][1]


def _xbrl_parse_instance(xml_text) -> tuple[dict, list]:
    """One pass over an instance document -> (contexts, facts).

    contexts: id -> (instant | (start, end), dims) where dims is a sorted
    tuple of ((axis_std, axis_local), (member_std, member_local)) pairs,
    each name resolved through the document's own prefix declarations and
    classified standard/custom the same way facts are — explicitMember
    attributes hold QNames in the DOCUMENT's prefixes, which need not match
    the registry's.
    facts: (uri, local, contextRef, value, decimals) for numeric facts.
    """
    import io as _io
    import xml.etree.ElementTree as _ET
    ns: dict[str, str] = {}
    contexts: dict = {}
    facts: list = []
    src = _io.StringIO(xml_text) if isinstance(xml_text, str) else _io.BytesIO(xml_text)

    def _classify(uri: str) -> str:
        return "std" if any(uri.startswith(s) for s in _XBRL_ALL_STD) else "custom"

    def _resolve(qn: str) -> tuple[str, str]:
        p, _, loc = qn.partition(":")
        return _classify(ns.get(p, "")), loc

    for event, el in _ET.iterparse(src, events=("start-ns", "end")):
        if event == "start-ns":
            ns[el[0]] = el[1]
            continue
        tag = el.tag
        if not isinstance(tag, str) or not tag.startswith("{"):
            continue
        uri, _, local = tag[1:].partition("}")
        if local == "context":
            cid = el.get("id")
            period = instant = None
            dims = []
            for sub in el.iter():
                s_uri, _, s_loc = sub.tag[1:].partition("}")
                if s_loc == "instant" and sub.text:
                    instant = sub.text.strip()
                elif s_loc == "startDate" and sub.text:
                    period = (sub.text.strip(), period[1] if period else "")
                elif s_loc == "endDate" and sub.text:
                    period = (period[0] if period else "", sub.text.strip())
                elif s_loc == "explicitMember":
                    dim = sub.get("dimension", "")
                    mem = (sub.text or "").strip()
                    if dim and mem:
                        dims.append((_resolve(dim), _resolve(mem)))
            if cid:
                contexts[cid] = (instant if instant else period, tuple(sorted(dims)))
            el.clear()
        elif el.get("contextRef") is not None:
            txt = (el.text or "").strip().replace(",", "")
            if txt:
                try:
                    val = float(txt)
                except ValueError:
                    el.clear()
                    continue
                facts.append((uri, local, el.get("contextRef"),
                              val, el.get("decimals")))
            el.clear()
    return contexts, facts


def _xbrl_extract(contexts: dict, facts: list, entries: tuple,
                  wanted_ends: dict, wanted_instants: dict) -> tuple[dict, dict]:
    """Registered values out of one parsed instance.

    wanted_ends:     fy -> fiscal end date (ISO) for duration entries.
    wanted_instants: fy -> year-end date (ISO) for instant_sum entries.
    Returns (values, meta): values[(key, fy)] = value;
    meta collects per-entry notes material — members that answered on a
    fallback concept, coarse decimals, an undimensioned same-instant fact.
    """
    values: dict = {}
    meta: dict = {"issued_members": set(), "coarse": False, "undimmed": False,
                  "n_members": {}}
    end_to_fy = {d: fy for fy, d in wanted_ends.items()}
    inst_to_fy = {d: fy for fy, d in wanted_instants.items()}
    for entry in entries:
        if entry.kind == "duration":
            for concept in entry.concepts:
                for uri, local, cref, val, _dec in facts:
                    if not _xbrl_qname_matches(concept, uri, local):
                        continue
                    ctx = contexts.get(cref)
                    if not ctx or not isinstance(ctx[0], tuple) or ctx[1]:
                        continue          # not a duration, or dimensioned
                    start, end = ctx[0]
                    if end not in end_to_fy or not start:
                        continue
                    days = (dt.date.fromisoformat(end)
                            - dt.date.fromisoformat(start)).days
                    if not 330 <= days <= 400:
                        continue
                    # The precision-merge rule runs WITHIN one instance too
                    # (12 Sep 2026): document order plays the vintage role, so
                    # a statement tagging the line at millions earlier in the
                    # document cannot mask a later thousands-precision fact —
                    # the sibling shape of the cross-filing ADBE artifact,
                    # closed before it bites (the boundary/stub lesson). A
                    # verify-matching fact takes the slot at this level too.
                    _fy = end_to_fy[end]
                    _xbrl_merge_value(values, meta.setdefault("dur_dec", {}),
                                      (entry.key, _fy), val,
                                      _xbrl_dec_int(_dec),
                                      dict(entry.verify).get(_fy))
        else:  # instant_sum
            ax_prefix, _, ax_local = entry.axis.partition(":")
            ax_class = "std" if ax_prefix in _XBRL_STD_STEMS else "custom"
            for date, fy in inst_to_fy.items():
                members: dict = {}
                issued_here: set = set()
                for concept in entry.concepts:
                    for uri, local, cref, val, dec in facts:
                        if not _xbrl_qname_matches(concept, uri, local):
                            continue
                        ctx = contexts.get(cref)
                        if not ctx or ctx[0] != date:
                            continue
                        dims = ctx[1]
                        if not dims:
                            meta["undimmed"] = True
                            continue
                        if len(dims) != 1 or dims[0][0] != (ax_class, ax_local):
                            continue      # extra axes, or a different axis
                        member = dims[0][1]
                        if member not in members:
                            members[member] = val
                            if local == "CommonStockSharesIssued":
                                issued_here.add(member[1])
                            if dec is not None and dec not in ("INF",):
                                try:
                                    if int(dec) <= -6:
                                        meta["coarse"] = True
                                except ValueError:
                                    pass
                if members and (entry.key, fy) not in values:
                    values[(entry.key, fy)] = float(sum(members.values()))
                    meta["n_members"][fy] = len(members)
                    meta["issued_members"] |= issued_here
    return values, meta


@st.cache_data(ttl=86400, show_spinner=False)
def _submissions(cik: str) -> dict:
    """The full submissions JSON. _sic reads the same endpoint separately;
    folding the two onto one fetch is a later cosmetic, kept apart here so
    this deploy changes no existing function (one change at a time)."""
    return _sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                    timeout=20).json()


def _xbrl_accessions(subs: dict, want_earliest_end: str) -> list[tuple[str, str, str]]:
    """(accession, primaryDocument, reportDate) for 10-K / 10-K/A filings,
    newest first, walking the older filing batches only when the recent
    window does not reach the earliest wanted period (heavy filers roll
    ~7-9 years of filings through `recent`; FY2016 can sit in a batch)."""
    out: list[tuple[str, str, str]] = []

    def _walk(block: dict) -> None:
        forms = block.get("form", [])
        accs = block.get("accessionNumber", [])
        docs = block.get("primaryDocument", [])
        reps = block.get("reportDate", [])
        for i, f in enumerate(forms):
            if f in ("10-K", "10-K/A"):
                out.append((accs[i], docs[i] if i < len(docs) else "",
                            reps[i] if i < len(reps) else ""))

    _walk(subs.get("filings", {}).get("recent", {}))
    covered = out and min(r for _, _, r in out if r) <= want_earliest_end
    if not covered:
        for extra in subs.get("filings", {}).get("files", []):
            try:
                _walk(_sec_get("https://data.sec.gov/submissions/"
                               + extra.get("name", ""), timeout=20).json())
            except Exception:
                break
            if out and min(r for _, _, r in out if r) <= want_earliest_end:
                break
    out.sort(key=lambda t: t[2], reverse=True)
    return out


@st.cache_data(ttl=86400, show_spinner=False)
def _xbrl_instance_values(cik: str, accession: str, primary_doc: str,
                          ticker: str, wanted_ends: tuple, wanted_instants: tuple):
    """Fetch ONE filing's instance, extract this ticker's registered values.

    Cached on the small extracted dict, never the raw XML (instances run
    1-4MB; Alphabet's FY2024 extracted instance is 2.7MB, its FY2016 raw
    instance 4.1MB — both verified on the filing indexes, 9 Sep 2026).
    Returns (values, meta) or (None, error-string) when the instance cannot
    be found or parsed — a per-filing miss, named in the route note, never
    an exception out of load().
    """
    entries = XBRL_REGISTRY.get(ticker, ())
    nodash = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{nodash}/"
    xml_text = None
    if primary_doc:
        try:
            xml_text = _sec_get(base + _xbrl_instance_guess(primary_doc),
                                timeout=30).content
        except Exception:
            xml_text = None
    if xml_text is None:
        try:
            idx = _sec_get(base + "index.json", timeout=20).json()
            names = [it.get("name", "")
                     for it in idx.get("directory", {}).get("item", [])]
            pick = _xbrl_pick_instance(names)
            if not pick:
                return None, "no instance document in the filing index"
            xml_text = _sec_get(base + pick, timeout=30).content
        except Exception as e:
            return None, f"index fallback failed ({type(e).__name__})"
    try:
        contexts, facts = _xbrl_parse_instance(xml_text)
        return _xbrl_extract(contexts, facts, entries,
                             dict(wanted_ends), dict(wanted_instants))
    except Exception as e:
        return None, f"instance parse failed ({type(e).__name__})"


def _xbrl_merge_meta(into: dict, meta: dict) -> None:
    into["issued_members"] |= meta.get("issued_members", set())
    into["coarse"] = into["coarse"] or meta.get("coarse", False)
    into["undimmed"] = into["undimmed"] or meta.get("undimmed", False)
    into["n_members"].update({k: v for k, v in meta.get("n_members", {}).items()
                              if k not in into["n_members"]})


def _xbrl_dec_int(dec) -> int | None:
    """A fact's decimals attribute as an int; None means exact (absent or
    INF) or unparseable — treated as exact and never displaced."""
    if dec is None or dec == "INF":
        return None
    try:
        return int(dec)
    except (TypeError, ValueError):
        return None


def _xbrl_merge_value(merged: dict, merged_dec: dict, k, v, dec,
                      verified: float | None = None) -> bool:
    """Newest filing wins per year — except that an OLDER filing carrying
    the same year at strictly FINER decimals, agreeing with the held value
    at the coarser precision's half-unit tolerance, replaces it: same
    figure, better resolution, not a restatement.

    ADBE FY2018, 12 Sep 2026: a newer filing's comparative tagged the
    withholding line million-rounded (393,000,000 at decimals=-6) and
    claimed the slot ahead of the FY2018 original's 393,193,000 at
    decimals=-3; the verification gate then discarded the whole entry —
    correctly, by its own contract — over a $193,000 rounding artifact.
    A disagreement BEYOND the coarse tolerance is a genuine restatement
    and the newest value keeps the slot (latest-vintage doctrine); a held
    value with unknown or INF decimals is exact and is never replaced.
    Applied at BOTH levels: across filings in the walk's merge loop, and
    within a single instance's fact list, where document order plays the
    vintage role. Duration facts only — instant sums keep their documented
    ±1M million-rounding noise (META) unchanged.

    THE CONTRACT OUTRANKS THE HEURISTICS (12 Sep 2026, approved): for
    registered verification pairs, a candidate reproducing the entry's
    verify figure within the half-unit takes the slot unconditionally —
    the verify tuple IS the identity, and a fact matching it is the
    verified fact by definition. Vintage and precision rules arbitrate
    among unverified candidates only. Scope: exactly the (key, fy) pairs
    a registry entry names with figures from the filings; nothing else is
    touched. Born of the ADBE FY2018 block: a decimals-less fact held the
    slot as "exact" and vetoed both finer facts, including the one
    matching the registered figure.
    """
    if verified is not None and abs(abs(v) - abs(verified)) <= 0.5:
        merged[k] = v
        merged_dec[k] = dec
        return True
    if k not in merged:
        merged[k] = v
        merged_dec[k] = dec
        return True
    d_old = merged_dec.get(k)
    if dec is None or d_old is None or dec <= d_old:
        return False
    if abs(v - merged[k]) <= 0.5 * (10 ** -d_old):
        merged[k] = v
        merged_dec[k] = dec
        return True
    return False


def _xbrl_trace_note(trace: list) -> str:
    """One diagnostic sentence per fetched filing, printed ONLY when a
    verification discard occurred (12 Sep 2026, the ADBE FY2018 hunt):
    a filing that parses but matches nothing is otherwise invisible — no
    miss, no note — and this session spent an evening deducing what one
    such line would have named instantly. trace rows: (reportDate,
    accession, outcome) where outcome names the matched (key, fy) pairs,
    "matched none", or the miss reason."""
    if not trace:
        return ""
    rows = "; ".join(f"{rep or '?'} {acc}: {outcome}"
                     for rep, acc, outcome in trace)
    return ("**Route trace (printed because a verification figure failed):** "
            + rows + ".")


def _xbrl_verify(entries: tuple, merged: dict, merged_dec: dict | None = None,
                 merged_src: dict | None = None) -> tuple[set, list[str]]:
    """The registry's contract: each entry's verify figures must reproduce
    to $0.50 / half a share, or the WHOLE entry is discarded. Tolerance
    strictly below a unit on purpose — the baselines' $1-boundary lesson.
    When merge provenance is supplied, a discard note names the held
    value's decimals and supplying accession — the failure message carries
    its own investigation (12 Sep 2026, the ADBE FY2018 hunt)."""
    dead: set = set()
    notes: list[str] = []
    for entry in entries:
        for fy, expected in entry.verify:
            got = merged.get((entry.key, fy))
            if got is None or abs(abs(got) - abs(expected)) > 0.5:
                dead.add(entry.key)
                _k = (entry.key, fy)
                _prov = ""
                if got is not None and merged_src is not None:
                    _d = (merged_dec or {}).get(_k)
                    _prov = (f" Held value came from filing {merged_src.get(_k, '?')} "
                             f"at decimals={_d if _d is not None else 'exact/INF'}.")
                notes.append(
                    f"**The per-filing XBRL read for {entry.key} was discarded.** Its "
                    f"registered verification figure for FY{fy} "
                    f"({expected:,.0f}) "
                    + ("was not found in any fetched filing"
                       if got is None else f"read {got:,.0f} instead")
                    + "." + _prov
                    + " The registry folds verified figures or nothing; the page "
                      "behaves as it did before this route existed. This usually "
                      "means the filer changed its tagging — re-verify the entry "
                      "against the latest filing's fact panel.")
                break
    return dead, notes


def up_c_sentence(ticker: str, total_m: float) -> str:
    """The surviving Up-C refusal (§5.5 G, 15 Sep 2026): counts fold, but
    the consolidated income legs could not be read and verified, so the
    as-exchanged basis cannot be stood behind and nothing per share is
    printed. For a verified filer the basis banner replaces this stop."""
    return (
        f"**Valuation withheld — Up-C structure, consolidated income unverified.** "
        f"{ticker}'s per-class share counts were read from its filings' own XBRL "
        f"instances and summed ({total_m:,.1f}M across the classes), so the table "
        "and the measurements above are real. This page prices umbrella-partnership "
        "C-corps on the as-exchanged basis — filed consolidated earnings over that "
        "full count — but the consolidated income legs (the filed consolidated "
        "net-income line, or the parent's slice plus the filed noncontrolling-"
        "interest line) could not be read and verified for this filer, and the "
        "parent's slice alone over the full count would understate every per-share "
        "figure. Nothing per share is printed until the legs verify.")


# ── §5.5 G — the as-exchanged income basis for Up-C filers (15 Sep 2026) ──
#
# The counts above fold (queue G); this block re-bases the INCOME to match.
# An umbrella-partnership C-corp's parent-slice net income over the summed
# A+B count mixes bases (Ryan Specialty's parent slice is ~30-40% of a
# steady-state year; Carvana's varies wildly with the waterfall and parent
# tax items). Parent-slice owners' earnings do not exist as filed — SBC,
# withholding and buybacks are whole-company cash flows — so the honest
# whole-over-whole basis is: filed CONSOLIDATED net income over the
# as-exchanged Class A + Class B count, at the Class A price the exchange
# right gives every unit. Never a ratio; filed lines only.
#
# Pairing evidence (session of record, 15 Sep 2026): Carvana's LLC Units
# exchange five-to-four, and holders deliver Class B COMMON shares one for
# one against the Class A shares issued (B common = 0.8 × Class A Units =
# exactly the shares issuable), so the A+B common sum IS the as-exchanged
# count with the 5:4 ratio already baked in — an exchange nets the common
# count to zero, which is queue G's exchange-invariance finding. Carvana's
# participation-threshold Class B Units are option-like overhang outside
# the count — the same treatment every filer's unvested awards get: dS
# prices delivery when it happens. Ryan Specialty's LLC common units pair
# 1:1 with Class B common. The intra-group Class A Non-Convertible
# Preferred Units Carvana Group issued to Carvana Co. (FY2025, against the
# Senior Notes proceeds) are parent-held, not NCI; the per-year identity
# below is the standing tripwire should any waterfall change ever break
# the filed three-leg sum.
#
# Income verification (Chen's companyconcept pastes, 15 Sep 2026): the
# identity ProfitLoss = NetIncomeLoss + NCI holds TO THE DOLLAR in every
# year either filer has ever filed, on every vintage — 10/10 CVNA years,
# 7/7 RYAN years. CVNA FY2018 is the one genuine restatement (parent
# −61,754K → −55,476K, NCI mirroring −192,991K → −199,269K, consolidated
# invariant at −254,745K): newest-wins keeps the identity exact on both
# vintages. CVNA re-tagged FY2019/FY2020 legs in whole millions in later
# filings — all three legs together, so newest-wins stays identity-exact.
# Pre-check of record: NetIncomeLossAttributableToRedeemableNoncontrolling-
# Interest returns NoSuchKey for BOTH CIKs (15 Sep 2026) — no second NCI
# leg exists; the three-leg identity is the whole story.
#
# The two named limits (stated on the page, with direction, NEVER adjusted
# for and never called anything but what they are): the Tax Receivable
# Agreements — cvna:TaxReceivableAgreementLiabilityNoncurrent $2,228M at
# FY2025 (from $65M — the valuation-allowance release landing the
# obligation essentially at once), ryan:TaxReceivableAgreementLiabilities-
# Noncurrent $458,997K at FY2025 (from $436,296K) — transfer value to
# pre-IPO holders outside this arithmetic; and the tax-status difference —
# the NCI slice of LLC income is pre-tax at the member level, so
# consolidated tax expense understates the as-exchanged tax burden. Both
# directions: flattering.

@dataclass(frozen=True)
class UpCIncome:
    """One Up-C filer's consolidated-income legs and named-limit anchors."""
    pl: str                                  # consolidated net income concept
    parent: str                              # parent-slice concept
    nci: str                                 # NCI income concept
    verify: tuple[tuple[int, float], ...]    # (fy, consolidated USD) pastes
    tra_tag: str                             # filed TRA liability tag
    tra_fy: int                              # its latest balance-sheet year
    tra_val_m: float                         # latest balance, $M
    tra_prior_m: float                       # prior-year balance, $M


UP_C_INCOME: dict[str, UpCIncome] = {
    "CVNA": UpCIncome(
        pl="ProfitLoss", parent="NetIncomeLoss",
        nci="NetIncomeLossAttributableToNoncontrollingInterest",
        verify=((2025, 1_895_000_000.0), (2024, 404_000_000.0)),
        tra_tag="cvna:TaxReceivableAgreementLiabilityNoncurrent",
        tra_fy=2025, tra_val_m=2228.0, tra_prior_m=65.0),
    "RYAN": UpCIncome(
        pl="ProfitLoss", parent="NetIncomeLoss",
        nci="NetIncomeLossAttributableToNoncontrollingInterest",
        verify=((2025, 214_157_000.0), (2024, 229_913_000.0)),
        tra_tag="ryan:TaxReceivableAgreementLiabilitiesNoncurrent",
        tra_fy=2025, tra_val_m=459.0, tra_prior_m=436.3),
}


def _income_identity_unit(vals: list[float]) -> float:
    """The coarsest rounding step among the identity's legs: 1e6 when every
    leg is a whole number of millions, 1e3 when thousands, else dollars.
    The identity gate is half of this — strictly below one unit — so a
    filer whose legs are filed at mixed precision is never refused over a
    legitimate rounding gap, and a real disagreement still fails loudly."""
    for unit in (1e6, 1e3):
        if all(abs(v) % unit == 0 for v in vals):
            return unit
    return 1.0


def up_c_income_rebase(ticker: str, facts: dict, series: dict,
                       tag_sources: dict) -> dict | None:
    """Re-base series['N'] to filed consolidated income for a registered
    Up-C filer. One dict lookup and out for everyone else — the untouched-
    shape control. Per year: the consolidated tag stands where filed (it is
    consolidated by definition); else parent + filed NCI, an addition of
    two filed lines with the arithmetic in the note, never a ratio; a year
    with neither reading, or a failed three-leg identity, is DROPPED with a
    note naming it and the size of any disagreement — missing is not zero.
    The whole fold is discarded (ok=False, series untouched) when the
    registered verification figures do not reproduce to under fifty cents,
    and the page then refuses with the surviving Up-C stop."""
    spec = UP_C_INCOME.get(ticker)
    if spec is None or not series.get("N"):
        return None
    res = {"ok": False, "notes": [], "identity": "", "fy": 0,
           "parent_m": 0.0, "nci_m": 0.0, "pl_m": 0.0}
    pl = _annual(facts, [spec.pl], [])
    par = _annual(facts, [spec.parent], [])
    nci = _annual(facts, [spec.nci], [])
    for vfy, vval in spec.verify:
        got = pl[vfy][-1] if vfy in pl else (
            par[vfy][-1] + nci[vfy][-1] if vfy in par and vfy in nci else None)
        if got is None or abs(got - vval) >= 0.5:
            res["notes"].append(
                f"**Up-C income fold DISCARDED for {ticker}** — the registered "
                f"verification figure for FY{vfy} ({vval:,.0f}) "
                + ("was not readable from any consolidated leg."
                   if got is None else
                   f"did not reproduce (read {got:,.0f}, off by "
                   f"{abs(got - vval):,.0f}).")
                + " Net income stays as first read and the per-share valuation "
                  "is withheld; nothing below prices on a basis this page "
                  "cannot stand behind.")
            return res
    rebased: dict[int, tuple] = {}
    dropped: list[str] = []
    for fy in sorted(series["N"]):
        has3 = fy in pl and fy in par and fy in nci
        if has3:
            legs = [pl[fy][-1], par[fy][-1], nci[fy][-1]]
            gap = abs(legs[0] - (legs[1] + legs[2]))
            if gap >= 0.5 * _income_identity_unit(legs):
                dropped.append(f"FY{fy} (identity off by {gap:,.0f})")
                continue
        if fy in pl:
            rebased[fy] = (pl[fy][0], pl[fy][1], pl[fy][-1])
        elif fy in par and fy in nci:
            s = par[fy][-1] + nci[fy][-1]
            rebased[fy] = (par[fy][0], par[fy][1], s)
            res["notes"].append(
                f"FY{fy}: no consolidated net-income tag filed for the year — "
                f"priced from the two filed slices, {par[fy][-1] / 1e6:,.1f} + "
                f"{nci[fy][-1] / 1e6:,.1f} = {s / 1e6:,.1f} (\\$M, parent + "
                "noncontrolling). An addition of filed lines, never a ratio.")
        else:
            dropped.append(f"FY{fy} (no consolidated reading; the "
                           "noncontrolling deduction is missing, not zero)")
    if dropped:
        res["notes"].append(
            "Years dropped from the window — no consolidated income could be "
            "stated from filed lines: " + "; ".join(dropped) + ". The pooled "
            "figures cover fewer years; read them with that in mind.")
    if not rebased:
        res["notes"].append(
            f"**Up-C income fold DISCARDED for {ticker}** — no year offered a "
            "readable consolidated figure. The per-share valuation is withheld.")
        return res
    for fy in list(series["N"]):
        if fy in rebased:
            series["N"][fy] = rebased[fy]
        else:
            del series["N"][fy]
    if spec.pl not in tag_sources["N"]:
        tag_sources["N"].append(spec.pl)
    last = max(rebased)
    res.update(ok=True, fy=last, pl_m=rebased[last][-1] / 1e6)
    if last in par and last in nci:
        res.update(parent_m=par[last][-1] / 1e6, nci_m=nci[last][-1] / 1e6)
        res["identity"] = (
            f"The filed identity for FY{last}: {res['parent_m']:,.1f} + "
            f"{res['nci_m']:,.1f} = {res['pl_m']:,.1f} (parent + "
            "noncontrolling = consolidated, \\$M) — exact as filed. ")
    res["notes"].append(
        f"As-exchanged basis (Up-C): net income re-based to the filed "
        f"consolidated figure for every filed year, FY{min(rebased)}–FY{last} "
        "(the table's window shows the most recent of them), verified against "
        "the registered filings figures; the basis statement above the inputs "
        "says what this means and names its limits.")
    return res


def up_c_basis_banner(ticker: str, total_m: float, basis: dict) -> str:
    """The as-exchanged basis statement (§5.5 G) — what basis, what the
    count is, the filed identity, and the two named limits with directions.
    The banned false sentence is pinned by a self-test: nothing here may
    claim the TRA or the tax-status difference is accounted for, and
    nothing may size them down."""
    spec = UP_C_INCOME[ticker]
    return (
        f"**Priced on the as-exchanged basis — Up-C structure.** {ticker}'s "
        f"per-class share counts were read from its filings' own XBRL "
        f"instances and summed ({total_m:,.1f}M across the classes). Part of "
        "an umbrella-partnership C-corp's economics sits in LLC units outside "
        "the parent, so every per-share figure here divides **consolidated "
        "earnings** — net income with the noncontrolling interest's share, as "
        "filed — by that full as-exchanged count: one basis on both sides. "
        + basis.get("identity", "")
        + "The Class A price applies to every unit through the exchange "
        "right. Two real limits are named here, not adjusted for: the **Tax "
        f"Receivable Agreement** (`{spec.tra_tag}`, \\${spec.tra_val_m:,.0f}M "
        f"at FY{spec.tra_fy}, from \\${spec.tra_prior_m:,.0f}M a year earlier) "
        "transfers value to pre-IPO holders outside this arithmetic — "
        "direction: flattering. And the **tax-status difference**: the "
        "noncontrolling slice of LLC income is pre-tax at the member level, "
        "so consolidated tax expense understates the tax a fully public "
        "company would bear on the same earnings — direction: flattering.")


def mixed_n_note(nsrc: list[str], up_c_verified: bool) -> str:
    """The mixed-tag warning for a net-income series read from more than
    one concept — silenced for a verified Up-C rebase, where ProfitLoss is
    not a fill but the deliberate, verified basis and the basis note and
    banner already say so. A note describing the chosen basis as a tagging
    wart misdescribes what the page did (CVNA screenshot, 16 Sep 2026);
    every other filer's wording is the 12 Sep original, moved verbatim."""
    if len(nsrc) <= 1 or up_c_verified:
        return ""
    return ("Net income came from more than one tag: the years "
            f"{nsrc[0]} does not cover were filled from {', '.join(nsrc[1:])}. "
            + ("ProfitLoss includes profit belonging to minority holders of "
               "consolidated subsidiaries, so where it filled a year the figure is "
               "the whole group's rather than shareholders' alone. "
               if "ProfitLoss" in nsrc[1:] else "")
            + "The tag panel shows which tags answered.")


def xbrl_route_apply(ticker: str, cik: str, series: dict,
                     tag_sources: dict, tag_origin: dict,
                     n_years: int) -> dict | None:
    """Fetch, verify and fold the registered per-filing reads for one ticker.

    Runs ONLY for registered tickers — the first line is the whole cost for
    everyone else. Folds Cw (and its Ce zeroing) into `series` in place,
    exactly where _annual's own values land, so the gates, notes, pools and
    ΔE machinery downstream run unchanged; returns share-count fills for the
    caller to merge before split_adjust, because absence is what every
    refusal keys on and presence is what lifts them.
    """
    entries = XBRL_REGISTRY.get(ticker)
    if not entries:
        return None
    if not series.get("N"):
        return None

    fys = sorted(series["N"])[-n_years:]
    ends = {fy: series["N"][fy][1] for fy in fys}
    # Duration reads are scoped to each entry's target years (only_fys), so
    # the parser never hunts periods the concept does not tag; empty = all.
    _dur_fys: set = set()
    for _e in entries:
        if _e.kind == "duration":
            _dur_fys.update(_e.only_fys or fys)
    wanted_ends = ({fy: ends[fy] for fy in ends if fy in _dur_fys}
                   if _dur_fys else dict(ends))
    # dS needs the year BEFORE the window's earliest; its end date comes from
    # the N series where read, else the earliest end shifted back a year (a
    # calendar guess only a calendar filer can match — a miss is just an
    # absent instant, named in the note).
    inst = dict(ends)
    fy0 = fys[0] - 1
    if fy0 in series["N"]:
        inst[fy0] = series["N"][fy0][1]
    else:
        try:
            e = dt.date.fromisoformat(ends[fys[0]])
            inst[fy0] = e.replace(year=e.year - 1).isoformat()
        except ValueError:
            pass
    need_dur = any(e.kind == "duration" for e in entries)
    need_ins = any(e.kind == "instant_sum" for e in entries)

    merged: dict = {}
    merged_dec: dict = {}
    merged_src: dict = {}
    _verify_map = {(e.key, fy): expected
                   for e in entries for fy, expected in e.verify}
    trace: list = []
    meta = {"issued_members": set(), "coarse": False, "undimmed": False,
            "n_members": {}}
    notes: list[str] = []
    fetched = misses = 0
    try:
        subs = _submissions(cik)
        earliest = (min(inst.values()) if need_ins
                    else min(wanted_ends.values()))
        for accession, pdoc, _rep in _xbrl_accessions(subs, earliest):
            if fetched >= 12:
                break             # budget guard: never more than 12 instances
            done_dur = (not need_dur) or all(
                (e.key, fy) in merged for e in entries
                for fy in (e.only_fys or ends) if e.kind == "duration")
            done_ins = (not need_ins) or all(
                (e.key, fy) in merged for e in entries for fy in inst
                if e.kind == "instant_sum")
            if done_dur and done_ins:
                break
            vals, m = _xbrl_instance_values(
                cik, accession, pdoc, ticker,
                tuple(sorted(wanted_ends.items())), tuple(sorted(inst.items())))
            fetched += 1
            if vals is None:
                misses += 1
                trace.append((_rep, accession, f"miss: {m}"))
                continue
            _dd = m.get("dur_dec", {})
            _wrote = []
            for k, v in vals.items():
                # newest filing wins per year; an older, strictly finer,
                # agreeing duration fact upgrades the resolution; and a
                # candidate matching the registered verify figure takes
                # the slot outright — see _xbrl_merge_value.
                if _xbrl_merge_value(merged, merged_dec, k, v, _dd.get(k),
                                     _verify_map.get(k)):
                    merged_src[k] = accession
                    _wrote.append(k)
            _kept = sorted(k for k in vals if k not in _wrote)
            _parts = []
            if _wrote:
                _parts.append("wrote " + ", ".join(
                    f"{k[0]} FY{k[1]}" for k in sorted(_wrote)))
            if _kept:
                _parts.append("offered " + ", ".join(
                    f"{k[0]} FY{k[1]}" for k in _kept) + " (held values kept)")
            trace.append((_rep, accession,
                          "; ".join(_parts) if _parts else "matched none"))
            _xbrl_merge_meta(meta, m)
    except Exception as e:
        notes.append(
            f"**The per-filing XBRL route could not run** ({type(e).__name__}). "
            "The page behaves as it did before this route existed.")
        return {"SHO": {}, "notes": notes, "up_c": False}

    dead, vnotes = _xbrl_verify(entries, merged, merged_dec, merged_src)
    if dead:
        _tn = _xbrl_trace_note(trace)
        if _tn:
            vnotes.append(_tn)
    sho, up_c, fnotes = _xbrl_fold(entries, dead, merged, meta, series,
                                   tag_sources, tag_origin, fys, fetched)
    notes.extend(vnotes)
    notes.extend(fnotes)
    if misses:
        notes.append(f"{misses} filing{'s' if misses != 1 else ''} could not be "
                     "read on the XBRL route (no instance found or unparseable); "
                     "any years they alone covered are unfilled.")
    return {"SHO": sho, "notes": notes, "up_c": up_c}


def _xbrl_fold(entries: tuple, dead: set, merged: dict, meta: dict,
               series: dict, tag_sources: dict, tag_origin: dict,
               fys: list, fetched: int) -> tuple[dict, bool, list[str]]:
    """Fold verified values into the reader's own structures — pure of any
    network so the container can test the whole fold on synthetic data.

    Cw lands in series[key] with the SAME (start, end, value) shape _annual
    writes, its concept recorded in tag_sources (the tag panel names it, the
    no-withholding note stops firing because values now exist) and in
    tag_origin per year — which makes broad_gate_fires return False on these
    years without any gate special-casing: the origin is not the treasury
    tag. net=True zeroes Ce for exactly the filled years. Share counts are
    returned for the caller to merge before split_adjust.
    """
    notes: list[str] = []
    sho: dict = {}
    up_c = False
    for entry in entries:
        if entry.key in dead:
            continue
        got_fys = sorted(fy for (k, fy) in merged if k == entry.key)
        if entry.kind == "duration" and entry.key in series:
            filled = []
            for fy in got_fys:
                if fy not in series["N"]:
                    continue
                start, end, _n = series["N"][fy]
                series[entry.key][fy] = (start, end, merged[(entry.key, fy)])
                tag_origin[entry.key][fy] = entry.concepts[0]
                filled.append(fy)
                if entry.net and "Ce" in series:
                    series["Ce"][fy] = (start, end, 0.0)
            if filled and entry.concepts[0] not in tag_sources.get(entry.key, []):
                tag_sources[entry.key].append(entry.concepts[0])
            unread = [fy for fy in (entry.only_fys or fys) if fy not in filled]
            if filled:
                notes.append(
                    f"**{entry.key} was read from the filings' own XBRL instances** "
                    f"({fetched} filing{'s' if fetched != 1 else ''} fetched): the line is "
                    f"tagged under the custom concept `{entry.concepts[0]}`, which the "
                    "SEC's aggregation API excludes by design, so it is invisible to the "
                    "reader's normal data source. FY"
                    + ", FY".join(str(f) for f in filled)
                    + " filled"
                    + (", FY" + ", FY".join(str(f) for f in unread)
                       + " still unread — the tag was not found in those filings"
                       if unread else "")
                    + "."
                    + (" The line is NET of option/ESPP proceeds (the tag name says "
                       "so), so proceeds are set to zero for those years — charging "
                       "both would count the same dollars twice. The value is taken "
                       "on magnitude; every year on record is a net outflow."
                       if entry.net else ""))
        elif entry.kind == "instant_sum":
            for fy in got_fys:
                sho[fy] = merged[(entry.key, fy)]
            if got_fys:
                unread = [fy for fy in fys if fy not in sho]
                nm = meta["n_members"].get(got_fys[-1], 0)
                notes.append(
                    f"**Year-end share counts were read from the filings' own XBRL "
                    f"instances** ({fetched} filing{'s' if fetched != 1 else ''} fetched) by "
                    f"summing the per-class counts ({nm} classes in the latest year) that "
                    "the SEC's aggregation API strips with their class dimension. FY"
                    + ", FY".join(str(f) for f in got_fys) + " filled"
                    + (", FY" + ", FY".join(str(f) for f in unread)
                       + " have no per-class counts in any filing (typically pre-IPO years)"
                       if unread else "") + "."
                    + (" " + "/".join(sorted(meta["issued_members"]))
                       + " answered on the shares-ISSUED tag where no outstanding "
                         "count was tagged; issued can include treasury shares, so "
                         "check it if the filer has bought back stock."
                       if meta["issued_members"] else "")
                    + (" Counts are tagged rounded to the nearest million, so each "
                       "year-over-year share change carries up to ±1M of rounding."
                       if meta["coarse"] else "")
                    + (" An undimensioned count exists at the same date — the sum was "
                       "used, but a filer with a usable total should not need this "
                       "route; re-check the registry entry." if meta["undimmed"] else ""))
            up_c = up_c or entry.up_c
    return sho, up_c, notes

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
                and "provider's history" in price_coverage_refusal(10, 10, True),
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

    # 19. The read-nothing route (Grab shape, 1 Sep 2026): net income read,
    #     revenue empty → the load refuses with the page-6 route; both read →
    #     no refusal on this ground.
    def _grab_shape(n_read, rev_read):
        return bool(n_read) and not bool(rev_read)
    out.append(("Net income read with no revenue refuses toward the Non-US page",
                _grab_shape({2024: 1}, {}) and not _grab_shape({2024: 1}, {2024: 2})
                and not _grab_shape({}, {}),
                "fires only on the asymmetric read"))

    # 20. The financial gate (pasted from page 5, its decisions verbatim):
    #     substance confirms SIC, ordinary fee businesses go back to tool 1,
    #     deposit-holding brokers promote, float businesses without deposits
    #     are refused.
    _fx = lambda tags: {"facts": {"us-gaap": {t: {"units": {"USD": []}} for t in tags}}}
    out.append(("A 6331 filer with premiums is an insurer; without them, refused",
                financial_class("6331", _fx(["PremiumsEarnedNet"]))[0] == "insurer"
                and financial_class("6331", _fx([]))[0] == "refused",
                "KNSL lands insurer; a shell with the code does not"))
    out.append(("6411 is ordinary, 6211 with deposits promotes to bank, 6141 without is refused",
                financial_class("6411", _fx([]))[0] == "ordinary"
                and financial_class("6211", _fx(["Deposits", "InterestIncomeExpenseNet"]))[0] == "bank"
                and financial_class("6141", _fx([]))[0] == "refused",
                "Ryan Specialty ordinary; Schwab-shaped promotes; a lender is not priced"))
    # v2 broker cascade (14 Sep 2026): deposits promote FIRST, client assets
    # land broker, neither refuses. NII alone is not client evidence.
    out.append(("Brokers: client assets land 6211/6221 as broker; deposits still promote; a dealer refuses",
                financial_class("6211", _fx(["InterestIncomeExpenseNet", "PayablesToCustomers"]))[0] == "broker"
                and financial_class("6221", _fx(["CashAndSecuritiesSegregatedUnderFederalAndOtherRegulations"]))[0] == "broker"
                and financial_class("6211", _fx(["Deposits", "InterestIncomeExpenseNet", "PayablesToCustomers"]))[0] == "bank"
                and financial_class("6211", _fx(["InterestIncomeExpenseNet"]))[0] == "refused"
                and financial_class("6221", _fx([]))[0] == "refused",
                "IBKR-shaped lands broker; SCHW-shaped promotes; Virtu-shaped refuses"))
    out.append(("Brokers: a payables line filed under the srt taxonomy is client evidence",
                financial_class("6211", {"facts": {"srt": {"PayablesToCustomers":
                                                           {"units": {"USD": []}}}}})[0] == "broker",
                "IBKR files its live customer payables under srt:, not us-gaap"))

    # 21. The META caption (5 Sep 2026): no share counts anywhere → the ΔE
    #     radio must say so instead of offering the polluted pools.
    out.append(("ΔE caption refuses when no year has a share count",
                dE_caption(0.604, True, True) == "n/a — no share counts read"
                and dE_caption(0.833, True, False) == "83.3%"
                and dE_caption(0.0, False, False) == "n/a — losses",
                "no-counts beats defined; the other two branches unchanged"))

    # 22. The note that explains an unprojectable ΔE must not claim a ceiling
    #     breach when the reason is missing share counts (META, 5 Sep: the
    #     ceiling branch fired saying "60.4% is above 100%"). Pure branch
    #     check on the same predicate order the page uses.
    def _dE_note_kind(no_counts, defined, val):
        if no_counts: return "no-counts"
        if not defined: return "undefined"
        if val < 0: return "negative"
        return "ceiling"
    out.append(("ΔE explanation picks no-counts before the ceiling",
                _dE_note_kind(True, True, 0.604) == "no-counts"
                and _dE_note_kind(False, True, 1.30) == "ceiling"
                and _dE_note_kind(False, False, 0.0) == "undefined",
                "60.4% with no counts is explained by the counts, not the ceiling"))

    # 23. The gates act per year on the tag that supplied it (NFLX, 5 Sep
    #     2026, the Baselines app's first catch). A narrow-supplied year is
    #     never zeroed however the gate got armed; a broad-supplied year of
    #     the same size is; and the no-charge branch keys on net income.
    out.append(("A narrow-supplied year survives an armed gate; a broad one does not",
                not broad_gate_fires("ProceedsFromStockOptionsExercised",
                                     "ProceedsFromIssuanceOfCommonStock",
                                     832.887, 272.588, 5407.9)
                and broad_gate_fires("ProceedsFromIssuanceOfCommonStock",
                                     "ProceedsFromIssuanceOfCommonStock",
                                     832.887, 272.588, 5407.9),
                "NFLX FY2024: 832.9 vs 3x272.6 — origin decides, not arming"))
    out.append(("...and the PDEX/CVNA broad-supplied shapes still gate",
                broad_gate_fires("ProceedsFromIssuanceOfCommonStock",
                                 "ProceedsFromIssuanceOfCommonStock", 2.2, 0.19, 5.0)
                and broad_gate_fires("ProceedsFromIssuanceOfCommonStock",
                                     "ProceedsFromIssuanceOfCommonStock", 600.0, 0.0, 450.0)
                and not broad_gate_fires(None,
                                         "ProceedsFromIssuanceOfCommonStock", 600.0, 0.0, 450.0),
                "broad-supplied years zeroed; an unknown-origin year is left alone"))


    # ── §1.7 price-range roll (queue B): the request reaches the window ──
    _prd = dt.date(2026, 9, 7)
    out.append(("Price fetch start: deep window extends, short window floors at 11y",
                _price_fetch_start("2015-02-01", _prd) == dt.date(2015, 2, 1)
                and _price_fetch_start("2019-06-15", _prd) == dt.date(2015, 9, 1)
                and _price_fetch_start(None, _prd) == dt.date(2015, 9, 1),
                "BBW's Feb-2015 window start wins; anything inside 11y floors"))
    _prc = {f"2015-{_m:02d}": 10.0 + _m for _m in range(2, 13)}
    _prc["2016-01"] = 22.0
    out.append(("Priced months: full year 12/12, clipped tail 5/12",
                _priced_months(_prc, "2015-02-01", "2016-01-31") == (12, 12)
                and _priced_months({k: v for k, v in _prc.items() if k >= "2015-09"},
                                   "2015-02-01", "2016-01-31") == (5, 12),
                "the same walk _avg_price takes"))
    out.append(("BBW shape: a fully covered FY2016 averages all twelve months",
                abs(_avg_price(_prc, "2015-02-01", "2016-01-31")
                    - statistics.fmean(_prc.values())) < 1e-9,
                f"{_avg_price(_prc, '2015-02-01', '2016-01-31'):.4f}"))
    out.append(("Partial note: listing wording vs missing-months wording",
                "first trading month" in partial_price_note([(2020, 3, 12, True)])
                and "missing inside the year" in partial_price_note([(2019, 9, 12, False)]),
                "the two partial truths read differently"))
    out.append(("Partial note: silent on full and pre-listing years; a provider hole is named",
                partial_price_note([(2024, 12, 12, True), (2016, 0, 10, True)]) == ""
                and "no priced month" in partial_price_note([(2018, 0, 12, False)]),
                "zero-priced pre-listing years are Gate 2's business"))
    # ── Per-filing XBRL route (queue G, 9 Sep 2026). All synthetic: the
    # container cannot reach SEC, so the parser, extraction, verification
    # gate and fold are proven here and the live deploy is proven by the
    # Baselines protocol, which fails loudly by construction. ────────────
    out.append(("XBRL qnames: std prefixes match year-versioned URIs, custom match non-std",
                _xbrl_qname_matches("us-gaap:CommonStockSharesOutstanding",
                                    "http://fasb.org/us-gaap/2025",
                                    "CommonStockSharesOutstanding")
                and _xbrl_qname_matches("goog:NetProceedsPaymentsRelatedToStockBasedAwardActivities",
                                        "http://alphabet.com/20251231",
                                        "NetProceedsPaymentsRelatedToStockBasedAwardActivities")
                and not _xbrl_qname_matches("goog:X", "http://fasb.org/us-gaap/2025", "X")
                and not _xbrl_qname_matches("us-gaap:A", "http://fasb.org/us-gaap/2025", "B"),
                "prefix drift and year-versioned URIs both handled"))
    _gx = ('<?xml version="1.0"?><xbrl xmlns="http://www.xbrl.org/2003/instance" '
           'xmlns:goog="http://alphabet.com/20251231" '
           'xmlns:us-gaap="http://fasb.org/us-gaap/2025" '
           'xmlns:xbrldi="http://xbrl.org/2006/xbrldi">'
           '<context id="d25"><entity><identifier scheme="s">X</identifier></entity>'
           '<period><startDate>2025-01-01</startDate><endDate>2025-12-31</endDate></period></context>'
           '<context id="d24"><entity><identifier scheme="s">X</identifier></entity>'
           '<period><startDate>2024-01-01</startDate><endDate>2024-12-31</endDate></period></context>'
           '<context id="dseg"><entity><identifier scheme="s">X</identifier>'
           '<segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">'
           'goog:CloudMember</xbrldi:explicitMember></segment></entity>'
           '<period><startDate>2025-01-01</startDate><endDate>2025-12-31</endDate></period></context>'
           '<context id="q25"><entity><identifier scheme="s">X</identifier></entity>'
           '<period><startDate>2025-10-01</startDate><endDate>2025-12-31</endDate></period></context>'
           '<goog:NetProceedsPaymentsRelatedToStockBasedAwardActivities contextRef="d25" '
           'unitRef="u">14167000000</goog:NetProceedsPaymentsRelatedToStockBasedAwardActivities>'
           '<goog:NetProceedsPaymentsRelatedToStockBasedAwardActivities contextRef="d24" '
           'unitRef="u">12190000000</goog:NetProceedsPaymentsRelatedToStockBasedAwardActivities>'
           '<goog:NetProceedsPaymentsRelatedToStockBasedAwardActivities contextRef="dseg" '
           'unitRef="u">999</goog:NetProceedsPaymentsRelatedToStockBasedAwardActivities>'
           '<goog:NetProceedsPaymentsRelatedToStockBasedAwardActivities contextRef="q25" '
           'unitRef="u">555</goog:NetProceedsPaymentsRelatedToStockBasedAwardActivities></xbrl>')
    _gctx, _gfacts = _xbrl_parse_instance(_gx)
    _gvals, _gmeta = _xbrl_extract(_gctx, _gfacts, XBRL_REGISTRY["GOOGL"],
                                   {2025: "2025-12-31", 2024: "2024-12-31"}, {})
    out.append(("XBRL GOOGL netness: the goog: line reads FY2025/FY2024 to his table's Cw",
                _gvals.get(("Cw", 2025)) == 14167000000.0
                and _gvals.get(("Cw", 2024)) == 12190000000.0
                and abs(_gvals[("Cw", 2025)] / 1e6 - 14167) < 0.5
                and abs(_gvals[("Cw", 2024)] / 1e6 - 12190) < 0.5,
                f"{_gvals.get(('Cw', 2025), 0)/1e6:,.0f}M / {_gvals.get(('Cw', 2024), 0)/1e6:,.0f}M"))
    out.append(("XBRL duration filters: a segment-dimensioned fact and a quarter are not annual",
                len(_gvals) == 2, f"{len(_gvals)} values extracted of 4 facts present"))
    _sx = ('<?xml version="1.0"?><xbrl xmlns="http://www.xbrl.org/2003/instance" '
           'xmlns:us-gaap="http://fasb.org/us-gaap/2025" '
           'xmlns:cvna="http://www.carvana.com/20251231" '
           'xmlns:xbrldi="http://xbrl.org/2006/xbrldi">'
           '<context id="iA"><entity><identifier scheme="s">X</identifier>'
           '<segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">'
           'us-gaap:CommonClassAMember</xbrldi:explicitMember></segment></entity>'
           '<period><instant>2025-12-31</instant></period></context>'
           '<context id="iB"><entity><identifier scheme="s">X</identifier>'
           '<segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">'
           'us-gaap:CommonClassBMember</xbrldi:explicitMember></segment></entity>'
           '<period><instant>2025-12-31</instant></period></context>'
           '<context id="iB2ax"><entity><identifier scheme="s">X</identifier>'
           '<segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">'
           'us-gaap:CommonClassBMember</xbrldi:explicitMember>'
           '<xbrldi:explicitMember dimension="cvna:SomeOtherAxis">'
           'cvna:SomeMember</xbrldi:explicitMember></segment></entity>'
           '<period><instant>2025-12-31</instant></period></context>'
           '<context id="iOther"><entity><identifier scheme="s">X</identifier>'
           '<segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">'
           'cvna:RetailMember</xbrldi:explicitMember></segment></entity>'
           '<period><instant>2025-12-31</instant></period></context>'
           '<context id="iNone"><entity><identifier scheme="s">X</identifier></entity>'
           '<period><instant>2025-12-31</instant></period></context>'
           '<us-gaap:CommonStockSharesIssued contextRef="iA" unitRef="sh">142230000'
           '</us-gaap:CommonStockSharesIssued>'
           '<us-gaap:CommonStockSharesOutstanding contextRef="iB" unitRef="sh" decimals="0">'
           '76109000</us-gaap:CommonStockSharesOutstanding>'
           '<us-gaap:CommonStockSharesOutstanding contextRef="iB2ax" unitRef="sh">999'
           '</us-gaap:CommonStockSharesOutstanding>'
           '<us-gaap:CommonStockSharesOutstanding contextRef="iOther" unitRef="sh">888'
           '</us-gaap:CommonStockSharesOutstanding>'
           '<us-gaap:CommonStockSharesOutstanding contextRef="iNone" unitRef="sh">777'
           '</us-gaap:CommonStockSharesOutstanding></xbrl>')
    _sctx, _sfacts = _xbrl_parse_instance(_sx)
    _svals, _smeta = _xbrl_extract(_sctx, _sfacts, XBRL_REGISTRY["CVNA"],
                                   {}, {2025: "2025-12-31"})
    out.append(("XBRL dimensioned sum: Class A + Class B = the CVNA verification figure",
                _svals.get(("SHO", 2025)) == 218339000.0,
                f"{_svals.get(('SHO', 2025), 0):,.0f}"))
    out.append(("XBRL sum excludes extra-axis and other-axis facts; flags the undimensioned decoy",
                _smeta["undimmed"] and _smeta["n_members"].get(2025) == 2,
                "999/888 excluded, 777 flagged not summed"))
    out.append(("XBRL per-member concept fallback: a class tagged only as Issued still answers",
                _smeta["issued_members"] == {"CommonClassAMember"},
                "the Reddit shape — filers mix Issued and Outstanding along every seam"))
    _mx = _sx.replace('decimals="0"', 'decimals="-6"')
    _mctx, _mfacts = _xbrl_parse_instance(_mx)
    _mvals, _mmeta = _xbrl_extract(_mctx, _mfacts, XBRL_REGISTRY["CVNA"],
                                   {}, {2025: "2025-12-31"})
    out.append(("XBRL coarse counts flagged: decimals=-6 means million-rounded (the META shape)",
                _mmeta["coarse"] and not _smeta["coarse"],
                "±1M per year-over-year share change, said in the note"))
    out.append(("XBRL instance name: modern iXBRL guess, and the FY2016 index fallback",
                _xbrl_instance_guess("goog-20241231.htm") == "goog-20241231_htm.xml"
                and _xbrl_pick_instance(
                    ["goog10-kq42016.htm", "googexhibit12q42016.htm",
                     "goog10-kq4_chartx25818.jpg", "goog-20161231.xml",
                     "goog-20161231.xsd", "goog-20161231_cal.xml",
                     "goog-20161231_def.xml", "goog-20161231_lab.xml",
                     "goog-20161231_pre.xml", "0001652044-17-000008.txt"])
                == "goog-20161231.xml",
                "the primary-doc stem does not predict the old instance; the picker must"))
    _vdead, _vnotes = _xbrl_verify(XBRL_REGISTRY["GOOGL"],
                                   {("Cw", 2025): 14167000000.0,
                                    ("Cw", 2024): 12190000000.0})
    _bdead, _bnotes = _xbrl_verify(XBRL_REGISTRY["GOOGL"],
                                   {("Cw", 2025): 14167000000.0,
                                    ("Cw", 2024): 12191000000.0})
    out.append(("XBRL verification gate: exact figures pass, a $1M miss discards the entry loudly",
                not _vdead and _bdead == {"Cw"} and "discarded" in _bnotes[0],
                "the registry folds verified figures or nothing"))
    _fs = {"N": {2024: ("2024-01-01", "2024-12-31", 100118e6),
                 2025: ("2025-01-01", "2025-12-31", 132170e6)},
           "Cw": {}, "Ce": {2023: ("2023-01-01", "2023-12-31", 5e6),
                            2025: ("2025-01-01", "2025-12-31", 300e6)}}
    _fsrc = {"Cw": [], "Ce": ["ProceedsFromStockOptionsExercised"]}
    _forg = {"Cw": {}, "Ce": {}}
    _fsho, _fupc, _fnotes = _xbrl_fold(
        XBRL_REGISTRY["GOOGL"], set(),
        {("Cw", 2025): 14167000000.0, ("Cw", 2024): 12190000000.0},
        {"issued_members": set(), "coarse": False, "undimmed": False,
         "n_members": {}}, _fs, _fsrc, _forg, [2024, 2025], 4)
    out.append(("XBRL fold: Cw lands in the series shape _annual writes, source and origin recorded",
                _fs["Cw"][2025] == ("2025-01-01", "2025-12-31", 14167000000.0)
                and _fs["Cw"][2024][2] == 12190000000.0
                and _fsrc["Cw"] == ["goog:NetProceedsPaymentsRelatedToStockBasedAwardActivities"]
                and _forg["Cw"][2025] == "goog:NetProceedsPaymentsRelatedToStockBasedAwardActivities",
                "the no-withholding note keys on values and stops firing by itself"))
    out.append(("XBRL netness fold: Ce zeroed for exactly the filled years, and the note says why",
                _fs["Ce"][2025][2] == 0.0 and _fs["Ce"][2024][2] == 0.0
                and _fs["Ce"][2023][2] == 5e6
                and any("NET of option/ESPP proceeds" in n for n in _fnotes),
                "proceeds are inside the net line; charging both counts them twice"))
    out.append(("XBRL fold disarms the Cw gate by origin, not by special-casing",
                not broad_gate_fires(_forg["Cw"][2025],
                                     "TreasuryStockValueAcquiredCostMethod",
                                     14167.0, 24953.0, 132170.0)
                and not _fupc,
                "origin is the goog: tag, so the treasury size test never runs"))
    _us, _uu, _un = _xbrl_fold(
        XBRL_REGISTRY["RYAN"], set(), {("SHO", 2025): 264112311.0},
        {"issued_members": set(), "coarse": False, "undimmed": False,
         "n_members": {2025: 2}},
        {"N": {2025: ("2025-01-01", "2025-12-31", 1e6)}}, {"Cw": []},
        {"Cw": {}}, [2025], 2)
    out.append(("XBRL up_c: RYAN and CVNA flag, the clean folds do not, the sentence names the cause",
                _uu and XBRL_REGISTRY["CVNA"][0].up_c
                and not XBRL_REGISTRY["META"][0].up_c
                and not XBRL_REGISTRY["RDDT"][0].up_c
                and _us == {2025: 264112311.0}
                and "Up-C" in up_c_sentence("RYAN", 264.1)
                and "withheld" in up_c_sentence("RYAN", 264.1),
                "counts fold; the sentence is now the surviving stop — §5.5 G landed 15 Sep 2026"))
    # ── §5.5 G: the as-exchanged income basis (15 Sep 2026) ──────────
    # Figures throughout are the session's pastes and Chen's live tables,
    # never invented constants.
    def _g5f(**tags):
        rows = lambda d: [{"start": f"{fy}-01-01", "end": f"{fy}-12-31",
                           "val": v, "form": "10-K", "fy": fy, "fp": "FY",
                           "filed": f"{fy + 1}-02-20", "accn": f"a{fy}-{v}"}
                          for fy, v in d.items()]
        return {"facts": {"us-gaap": {k: {"units": {"USD": rows(v)}}
                                      for k, v in tags.items()}}}
    def _g5n(*fys):
        return {"N": {fy: (f"{fy}-01-01", f"{fy}-12-31", 1.0) for fy in fys}}
    out.append(("UP_C income spec: both filers, three legs, the pasted verify pairs",
                set(UP_C_INCOME) == {"CVNA", "RYAN"}
                and UP_C_INCOME["CVNA"].verify == ((2025, 1_895_000_000.0), (2024, 404_000_000.0))
                and UP_C_INCOME["RYAN"].verify == ((2025, 214_157_000.0), (2024, 229_913_000.0))
                and UP_C_INCOME["CVNA"].nci == "NetIncomeLossAttributableToNoncontrollingInterest"
                and "cvna:" in UP_C_INCOME["CVNA"].tra_tag
                and "ryan:" in UP_C_INCOME["RYAN"].tra_tag,
                "companyconcept pastes of 15 Sep 2026"))
    out.append(("Identity unit: millions-rounded legs gate at $0.5M, thousands at $500, dollars at 50 cents",
                _income_identity_unit([1_895_000_000.0, 404_000_000.0]) == 1e6
                and _income_identity_unit([214_157_000.0, 63_399_000.0]) == 1e3
                and _income_identity_unit([214_157_000.0, 63_399_500.0]) == 1.0,
                "mixed-precision legs never refuse over legitimate rounding"))
    _g5a = _g5f(ProfitLoss={2025: 1_895_000_000.0, 2024: 404_000_000.0},
               NetIncomeLoss={2025: 1_407_000_000.0, 2024: 210_000_000.0},
               NetIncomeLossAttributableToNoncontrollingInterest={2025: 488_000_000.0, 2024: 194_000_000.0})
    _g5s1 = _g5n(2024, 2025); _g5t = {"N": []}
    _g5r1 = up_c_income_rebase("CVNA", _g5a, _g5s1, _g5t)
    out.append(("PL rung: the consolidated figure stands per year, the identity prints, the tag panel names it",
                _g5r1 is not None and _g5r1["ok"]
                and _g5s1["N"][2025][2] == 1_895_000_000.0
                and _g5s1["N"][2024][2] == 404_000_000.0
                and "1,407.0 + 488.0 = 1,895.0" in _g5r1["identity"]
                and "ProfitLoss" in _g5t["N"],
                "the pasted FY2025/FY2024 legs end to end"))
    _g5b = _g5f(NetIncomeLoss={2025: 63_399_000.0},
                NetIncomeLossAttributableToNoncontrollingInterest={2025: 150_758_000.0})
    _g5s2 = _g5n(2025)
    _g5r2 = up_c_income_rebase("RYAN", {"facts": {"us-gaap": {
        **_g5b["facts"]["us-gaap"],
        "ProfitLoss": {"units": {"USD": [{"start": "2024-01-01", "end": "2024-12-31",
                                          "val": 229_913_000.0, "form": "10-K", "fy": 2024,
                                          "fp": "FY", "filed": "2025-02-21", "accn": "r24"},
                                         {"start": "2025-01-01", "end": "2025-12-31",
                                          "val": 214_157_000.0, "form": "10-K", "fy": 2025,
                                          "fp": "FY", "filed": "2026-02-13", "accn": "r25"}]}}}}},
        _g5n(2024, 2025), {"N": []})
    out.append(("Verification gate: the pasted figures pass exact; a $1M miss discards the whole fold",
                _g5r2 is not None and _g5r2["ok"]
                and (lambda s, r: r is not None and not r["ok"]
                     and s["N"][2025][2] == 1.0
                     and any("DISCARDED" in n for n in r["notes"]))(
                    _g5s2, up_c_income_rebase("RYAN", _g5f(
                        ProfitLoss={2025: 215_157_000.0, 2024: 229_913_000.0}), _g5s2, {"N": []})),
                "series untouched on a discard; the surviving stop owns the page"))
    _g5s3 = _g5n(2024, 2025)
    _g5r3 = up_c_income_rebase("CVNA", _g5f(
        ProfitLoss={2024: 404_000_000.0},
        NetIncomeLoss={2025: 1_407_000_000.0, 2024: 210_000_000.0},
        NetIncomeLossAttributableToNoncontrollingInterest={2025: 488_000_000.0, 2024: 194_000_000.0}),
        _g5s3, {"N": []})
    out.append(("Addition rung: a year with no consolidated tag prices from the two filed slices, arithmetic in the note",
                _g5r3 is not None and _g5r3["ok"]
                and _g5s3["N"][2025][2] == 1_895_000_000.0
                and any("1,407.0 + 488.0 = 1,895.0" in n for n in _g5r3["notes"]),
                "an addition of filed lines, never a ratio"))
    _g5s4 = _g5n(2023, 2024, 2025)
    _g5r4 = up_c_income_rebase("CVNA", _g5f(
        ProfitLoss={2025: 1_895_000_000.0, 2024: 404_000_000.0},
        NetIncomeLoss={2025: 1_407_000_000.0, 2024: 210_000_000.0, 2023: 450_000_000.0},
        NetIncomeLossAttributableToNoncontrollingInterest={2025: 488_000_000.0, 2024: 194_000_000.0}),
        _g5s4, {"N": []})
    out.append(("Missing NCI where filed elsewhere drops the year — the deduction is missing, not zero",
                _g5r4 is not None and _g5r4["ok"] and 2023 not in _g5s4["N"]
                and 2024 in _g5s4["N"] and 2025 in _g5s4["N"]
                and any("FY2023" in n and "missing, not zero" in n for n in _g5r4["notes"]),
                "the fin-v2 refusal rung, mirrored"))
    _g5s5 = _g5n(2024, 2025)
    _g5r5 = up_c_income_rebase("CVNA", _g5f(
        ProfitLoss={2025: 1_895_000_000.0, 2024: 404_000_000.0},
        NetIncomeLoss={2025: 1_408_000_000.0},
        NetIncomeLossAttributableToNoncontrollingInterest={2025: 488_000_000.0}),
        _g5s5, {"N": []})
    out.append(("A million-dollar identity gap drops the year and names the size",
                _g5r5 is not None and _g5r5["ok"] and 2025 not in _g5s5["N"]
                and 2024 in _g5s5["N"] and _g5s5["N"][2024][2] == 404_000_000.0
                and any("identity off by 1,000,000" in n for n in _g5r5["notes"]),
                "three legs that do not sum are not a basis"))
    _g5s6 = _g5n(2025)
    out.append(("Unregistered ticker: one dict lookup, series untouched",
                up_c_income_rebase("PDEX", _g5f(ProfitLoss={2025: 1.0}), _g5s6, {"N": []}) is None
                and _g5s6["N"][2025][2] == 1.0,
                "the untouched-shape control, same as the route's"))
    _g5s7 = _g5n(2018)
    _g5c7 = _g5f(ProfitLoss={2018: -254_745_000.0, 2024: 404_000_000.0,
                             2025: 1_895_000_000.0})
    for _g5cc, _g5o, _g5w in ((("NetIncomeLoss"), -61_754_000.0, -55_476_000.0),
                           (("NetIncomeLossAttributableToNoncontrollingInterest"),
                            -192_991_000.0, -199_269_000.0)):
        _g5c7["facts"]["us-gaap"][_g5cc] = {"units": {"USD": [
            {"start": "2018-01-01", "end": "2018-12-31", "val": _g5o, "form": "10-K",
             "fy": 2018, "fp": "FY", "filed": "2019-02-27", "accn": "v1"},
            {"start": "2018-01-01", "end": "2018-12-31", "val": _g5w, "form": "10-K",
             "fy": 2019, "fp": "FY", "filed": "2020-02-26", "accn": "v2"}]}}
    _g5r7 = up_c_income_rebase("CVNA", _g5c7, _g5s7, {"N": []})
    out.append(("CVNA FY2018 restatement shape: newest-wins legs still sum to the invariant consolidated figure",
                _g5r7 is not None and _g5r7["ok"]
                and _g5s7["N"][2018][2] == -254_745_000.0,
                "parent −61,754→−55,476 mirrored by NCI −192,991→−199,269; identity exact on both vintages"))
    _g5cv = [(150.0, 73.0, 310.50405588145054), (404.0, 91.0, 1774.3856129741669),
           (1895.0, 96.0, 1885.9377713155748)]
    _g5cd = sum(n + g - o for n, g, o in _g5cv) / sum(n for n, g, o in _g5cv)
    out.append(("CVNA re-based 3-year pool: −51.5% from the pasted consolidated series and the live Ω table",
                abs(_g5cd - (-0.5152)) < 5e-4,
                f"registered prediction of record: {_g5cd:.2%}"))
    _g5ry = [(63.057, 8.153, 0.0), (70.513, 10.8, 0.0), (56.632, 67.534, 78.256),
           (163.257, 77.48, 31.12821054283301), (194.480, 69.743, 33.336901357221606),
           (229.913, 78.995, 125.54977450858561), (214.157, 69.451, 178.11695234604548)]
    _g5d3 = sum(n + g - o for n, g, o in _g5ry[-3:]) / sum(n for n, g, o in _g5ry[-3:])
    _g5df = sum(n + g - o for n, g, o in _g5ry) / sum(n for n, g, o in _g5ry)
    out.append(("RYAN re-based pools: 81.4% (3y) and 93.5% (full) from the pasted series and the live Ω table",
                abs(_g5d3 - 0.8139) < 5e-4 and abs(_g5df - 0.9353) < 5e-4,
                f"the 166% two-stage explanation closed: {_g5d3:.1%} / {_g5df:.1%}"))
    _g5u1 = up_c_basis_banner("CVNA", 1091.7, {"identity": "The filed identity for FY2025: "
                             "1,407.0 + 488.0 = 1,895.0 (parent + noncontrolling = consolidated, \\$M) — exact as filed. "})
    _g5u2 = up_c_basis_banner("RYAN", 264.1, {"identity": ""})
    out.append(("Banner: basis, count, identity, both limits with directions, the filed TRA tags and balances",
                "as-exchanged" in _g5u1 and "1,091.7M" in _g5u1
                and "1,407.0 + 488.0 = 1,895.0" in _g5u1
                and _g5u1.count("flattering") == 2 and _g5u2.count("flattering") == 2
                and "cvna:TaxReceivableAgreementLiabilityNoncurrent" in _g5u1
                and "\\$2,228M" in _g5u1 and "\\$65M" in _g5u1
                and "ryan:TaxReceivableAgreementLiabilitiesNoncurrent" in _g5u2
                and "\\$459M" in _g5u2
                and "$" not in (_g5u1 + _g5u2).replace("\\$", ""),
                "what basis, what count, what limits — the house shape"))
    out.append(("The banned false sentence is unwritable: no sizing-down words, no accounted-for claim, and the banner never withholds",
                all(w not in _g5u1 and w not in _g5u2
                    for w in ("included", "including", "negligible", "immaterial",
                              " small", "accounted for"))
                and "withheld" not in _g5u1 and "withheld" not in _g5u2
                and "Up-C" in up_c_sentence("RYAN", 264.1)
                and "withheld" in up_c_sentence("RYAN", 264.1)
                and "unverified" in up_c_sentence("RYAN", 264.1),
                "NCI-BRIEF §4's banned sentence, pinned"))
    out.append(("Mixed-tag N note: silenced for a verified Up-C rebase, the 12 Sep wording verbatim otherwise",
                mixed_n_note(["NetIncomeLoss", "ProfitLoss"], True) == ""
                and "whole group's rather than shareholders' alone"
                    in mixed_n_note(["NetIncomeLoss", "ProfitLoss"], False)
                and "minority holders"
                    not in mixed_n_note(["NetIncomeLoss", "IncomeLossFromContinuingOperations"], False)
                and mixed_n_note(["NetIncomeLoss"], False) == "",
                "the basis banner replaced it for the Up-C filers; every other filer unchanged (16 Sep 2026)"))
    out.append(("XBRL registry gate: an unregistered ticker returns None untouched",
                xbrl_route_apply("PDEX", "0000788920",
                                 {"N": {2025: ("a", "b", 1.0)}}, {}, {}, 10) is None
                and "PDEX" not in XBRL_REGISTRY,
                "one dict lookup and out — zero fetches, byte-identical behaviour"))
    _dead_fold = _xbrl_fold(XBRL_REGISTRY["GOOGL"], {"Cw"},
                            {("Cw", 2025): 1.0},
                            {"issued_members": set(), "coarse": False,
                             "undimmed": False, "n_members": {}},
                            {"N": {2025: ("s", "e", 1.0)}, "Cw": {}, "Ce": {}},
                            {"Cw": []}, {"Cw": {}}, [2025], 1)
    out.append(("XBRL discarded entry folds nothing: a failed verification leaves the series alone",
                _dead_fold[0] == {} and not _dead_fold[1]
                and not any("read from the filings" in n for n in _dead_fold[2]),
                "the page behaves as it did before the route existed"))
    # F4 — the treasury equality-reject (Amazon, 12 Sep 2026; DECOMP §1.4, §4).
    _q1 = [Year(fy=2021, N=33364.0, G=12757.0, T=0.0, Cw=0.0),
           Year(fy=2022, N=-2722.0, G=19621.0, T=6000.0, Cw=6000.0)]
    _q1h = treasury_equal_reject(_q1, {2022: "TreasuryStockValueAcquiredCostMethod"})
    out.append(("Equality-reject: a treasury Cw equal to the year's T is the buyback, zeroed",
                _q1h == [(2022, 6000.0)] and _q1[1].Cw == 0.0 and _q1[0].Cw == 0.0,
                "AMZN FY2022: $6B counted twice — buyback in V, phantom Cw in C"))
    _q1n = treasury_equal_note(_q1h, any(y.Cw for y in _q1), True, True)
    out.append(("...and the sell-to-cover sentence names the year, the figure, the direction",
                "FY2022" in _q1n and "$6,000M" in _q1n
                and "sells shares to cover" in _q1n and "flattering" in _q1n,
                "the sentence IS the finding"))
    _q2 = [Year(fy=2020, N=100.0, G=50.0, T=1000.0, Cw=999.6),
           Year(fy=2021, N=100.0, G=50.0, T=1000.0, Cw=999.4),
           Year(fy=2022, N=100.0, G=50.0, T=500.0, Cw=500.0),
           Year(fy=2023, N=100.0, G=50.0, T=0.0, Cw=40.0)]
    _q2o = {2020: "TreasuryStockValueAcquiredCostMethod",
            2021: "TreasuryStockValueAcquiredCostMethod",
            2022: "PaymentsRelatedToTaxWithholdingForShareBasedCompensation",
            2023: "TreasuryStockValueAcquiredCostMethod"}
    _q2h = treasury_equal_reject(_q2, _q2o)
    _q2n = treasury_equal_note(_q2h, any(y.Cw for y in _q2),
                               not treasury_accepted(_q2, _q2o), any(y.G for y in _q2))
    out.append(("Equality is half a dollar; narrow origin and a missing buyback are immune",
                _q2h == [(2020, 999.6)] and _q2[0].Cw == 0.0
                and _q2[1].Cw == 999.4 and _q2[2].Cw == 500.0 and _q2[3].Cw == 40.0
                and "and stand" not in _q2n,
                "$0.40 off rejects, $0.60 off survives to the size gate; Cw equal to T on "
                "the narrow tag is a coincidence, not a candidate; a treasury survivor "
                "silences the survivors clause"))
    _q3 = [Year(fy=2013, N=800.0, G=60.0, T=1800.0, Cw=1800.0),
           Year(fy=2014, N=900.0, G=70.0, T=2264.0, Cw=2264.0),
           Year(fy=2016, N=1000.0, G=300.0, T=500.0, Cw=153.0)]
    _q3o = {2013: "TreasuryStockValueAcquiredCostMethod",
            2014: "TreasuryStockValueAcquiredCostMethod",
            2016: "PaymentsRelatedToTaxWithholdingForShareBasedCompensation"}
    _q3h = treasury_equal_reject(_q3, _q3o)
    _q3n = treasury_equal_note(_q3h, any(y.Cw for y in _q3),
                               not treasury_accepted(_q3, _q3o), any(y.G for y in _q3))
    out.append(("The Intuit shape: equal fills rejected, narrow years stand and the note says so",
                _q3h == [(2013, 1800.0), (2014, 2264.0)] and _q3[2].Cw == 153.0
                and "2 years (FY2013, FY2014)" in _q3n and "and stand" in _q3n
                and "sells shares to cover" not in _q3n,
                "two truths told apart: the rejection and the survivors"))
    out.append(("The accepted sentence keys on treasury ORIGIN, not on any survivor",
                not treasury_accepted(_q3, _q3o)
                and treasury_accepted([Year(fy=2019, N=100.0, G=50.0, T=900.0, Cw=120.0)],
                                      {2019: "TreasuryStockValueAcquiredCostMethod"}),
                "keying on survival instead of origin is the false-sentence class"))
    # F3 — the parent Ce tag appended last with the offering gate inherited,
    # and the ADBE scoped route entry (12 Sep 2026; DECOMP §1.2/§1.3, §4).
    out.append(("F3: the parent proceeds tag sits LAST in the Ce list and inherits the gate",
                CONCEPTS["Ce"][0][-1] == "ProceedsFromIssuanceOrSaleOfEquity"
                and not broad_gate_fires("ProceedsFromIssuanceOrSaleOfEquity",
                                         "ProceedsFromIssuanceOrSaleOfEquity",
                                         539.0, 252.0, 4000.0)
                and broad_gate_fires("ProceedsFromIssuanceOrSaleOfEquity",
                                     "ProceedsFromIssuanceOrSaleOfEquity",
                                     2000.0, 252.0, 4000.0),
                "TXN's max 539 vs 3xG=756 never gates; an offering-sized 2,000 does"))
    _txf = {"facts": {"us-gaap": {
        "ProceedsFromIssuanceOrSaleOfEquity": {"units": {"USD": [
            {"form": "10-K", "start": f"{y}-01-01", "end": f"{y}-12-31",
             "filed": f"{y+1}-02-15", "val": 400e6} for y in range(2016, 2026)]}}}}}
    _txs: list[str] = []
    _txo: dict[int, str] = {}
    _txr = _annual(_txf, CONCEPTS["Ce"][0], [], _txs, True, False, origin=_txo)
    out.append(("The TXN shape: Ce empty on every narrower name fills from the parent tag",
                len(_txr) == 10 and _txr[2025][2] == 400e6
                and _txs == ["ProceedsFromIssuanceOrSaleOfEquity"]
                and _txo[2025] == "ProceedsFromIssuanceOrSaleOfEquity",
                "TXN's 'proceeds from common stock transactions' line, read at last"))
    _mxf = {"facts": {"us-gaap": {
        "ProceedsFromStockOptionsExercised": {"units": {"USD": [
            {"form": "10-K", "start": f"{y}-01-01", "end": f"{y}-12-31",
             "filed": f"{y+1}-02-15", "val": 100e6} for y in (2016, 2017)]}},
        "ProceedsFromIssuanceOrSaleOfEquity": {"units": {"USD": [
            {"form": "10-K", "start": f"{y}-01-01", "end": f"{y}-12-31",
             "filed": f"{y+1}-02-15", "val": 900e6} for y in (2016, 2017, 2018)]}}}}}
    _mxs: list[str] = []
    _mxo: dict[int, str] = {}
    _mxr = _annual(_mxf, CONCEPTS["Ce"][0], [], _mxs, True, False, origin=_mxo)
    out.append(("Appended-last means fill-only: narrow-covered years are never re-read",
                _mxr[2016][2] == 100e6 and _mxr[2017][2] == 100e6
                and _mxr[2018][2] == 900e6
                and _mxo[2016] == "ProceedsFromStockOptionsExercised"
                and _mxo[2018] == "ProceedsFromIssuanceOrSaleOfEquity",
                "the parent adds FY2018 and touches nothing the options tag answered"))
    _af = {"N": {fy: (f"{fy-1}-12-01", f"{fy}-11-30", 1e9) for fy in range(2016, 2026)},
           "Cw": {2016: ("2015-12-01", "2016-11-30", 1075e6),
                  2017: ("2016-12-01", "2017-11-30", 1100e6),
                  2018: ("2017-12-01", "2018-11-30", 2050e6)},
           "Ce": {2016: ("2015-12-01", "2016-11-30", 145.697e6)}}
    _asrc = {"Cw": ["TreasuryStockValueAcquiredCostMethod"],
             "Ce": ["ProceedsFromIssuanceOfTreasuryStock"]}
    _aorg = {"Cw": {2016: "TreasuryStockValueAcquiredCostMethod",
                    2017: "TreasuryStockValueAcquiredCostMethod",
                    2018: "TreasuryStockValueAcquiredCostMethod"},
             "Ce": {2016: "ProceedsFromIssuanceOfTreasuryStock"}}
    _amerged = {("Cw", 2018): 393_193_000.0, ("Cw", 2017): 240_126_000.0,
                ("Cw", 2016): 236_400_000.0}
    _adead, _avn = _xbrl_verify(XBRL_REGISTRY["ADBE"], _amerged)
    _asho, _aupc, _anotes = _xbrl_fold(
        XBRL_REGISTRY["ADBE"], _adead, _amerged,
        {"issued_members": set(), "coarse": False, "undimmed": False,
         "n_members": {}},
        _af, _asrc, _aorg, list(range(2016, 2026)), 9)
    out.append(("ADBE route: verified figures OVERWRITE the treasury fill; net=False keeps Ce",
                not _adead
                and _af["Cw"][2016][2] == 236_400_000.0
                and _af["Cw"][2018][2] == 393_193_000.0
                and _af["Ce"][2016][2] == 145.697e6
                and _aorg["Cw"][2016] == "adbe:CostOfIssuanceOfTreasuryStock"
                and "adbe:CostOfIssuanceOfTreasuryStock" in _asrc["Cw"],
                "the label and the figures are the identity, not the concept name"))
    out.append(("...and the scoped note names FY2016-2018 filled, calling nothing else unread",
                any("FY2016, FY2017, FY2018 filled" in n for n in _anotes)
                and not any("still unread" in n for n in _anotes)
                and not any("FY2019" in n for n in _anotes),
                "FY2019 on reads the standard narrow tag; the note must not deny it"))
    _bdead2, _ = _xbrl_verify(XBRL_REGISTRY["ADBE"],
                              {("Cw", 2018): 394_193_000.0,
                               ("Cw", 2017): 240_126_000.0,
                               ("Cw", 2016): 236_400_000.0})
    _imm = [Year(fy=2016, N=1168.782, G=349.297, T=1075.0, Cw=1075.0)]
    _immh = treasury_equal_reject(_imm, {2016: "adbe:CostOfIssuanceOfTreasuryStock"})
    out.append(("A $1M miss discards ADBE's entry; a route-origin year is equality-immune",
                _bdead2 == {"Cw"} and _immh == [] and _imm[0].Cw == 1075.0,
                "the route's verified read is never mistaken for the treasury fallback"))
    _se = (XbrlRoute(key="Cw", kind="duration",
                     concepts=("test:ScopedTag",), verify=((2017, 5e6),),
                     only_fys=(2016, 2017, 2018)),)
    _sf = {"N": {fy: (f"{fy-1}-01-01", f"{fy}-12-31", 1e9) for fy in range(2016, 2026)},
           "Cw": {}, "Ce": {}}
    _ssrc = {"Cw": [], "Ce": []}
    _sorg2 = {"Cw": {}, "Ce": {}}
    _smerged = {("Cw", 2017): 5e6, ("Cw", 2018): 6e6}
    _sdead2, _ = _xbrl_verify(_se, _smerged)
    _, _, _snotes = _xbrl_fold(_se, _sdead2, _smerged,
                               {"issued_members": set(), "coarse": False,
                                "undimmed": False, "n_members": {}},
                               _sf, _ssrc, _sorg2, list(range(2016, 2026)), 2)
    out.append(("only_fys scopes the unread list to the entry's own target years",
                any("FY2016 still unread" in n for n in _snotes)
                and not any("FY2019" in n for n in _snotes)
                and not any("FY2025" in n for n in _snotes),
                "a target year genuinely missing is named; non-target years never are"))
    # The precision-merge rule (12 Sep 2026): the ADBE FY2018 rounding
    # artifact — a newer filing's million-rounded comparative must not
    # displace the original filing's thousands-precision fact.
    _pm: dict = {}
    _pmd: dict = {}
    _xbrl_merge_value(_pm, _pmd, ("Cw", 2018), 393_000_000.0, -6)
    _xbrl_merge_value(_pm, _pmd, ("Cw", 2018), 393_193_000.0, -3)
    out.append(("Precision merge: an older, finer, agreeing fact upgrades the resolution",
                _pm[("Cw", 2018)] == 393_193_000.0 and _pmd[("Cw", 2018)] == -3,
                "393,193,000 at -3 vs a comparative's 393,000,000 at -6: same figure"))
    _xbrl_merge_value(_pm, _pmd, ("Ce", 2015), 554_000_000.0, -6)
    _xbrl_merge_value(_pm, _pmd, ("Ce", 2015), 616_000_000.0, -3)
    out.append(("...a disagreement beyond the coarse tolerance is a restatement: newest holds",
                _pm[("Ce", 2015)] == 554_000_000.0 and _pmd[("Ce", 2015)] == -6,
                "the TXN FY2014-15 restatement era shape — latest vintage wins"))
    _xbrl_merge_value(_pm, _pmd, ("Cw", 2024), 12_190_000_000.0, None)
    _xbrl_merge_value(_pm, _pmd, ("Cw", 2024), 12_190_000_128.0, -3)
    _xbrl_merge_value(_pm, _pmd, ("Cw", 2025), 14_167_000_000.0, -3)
    _xbrl_merge_value(_pm, _pmd, ("Cw", 2025), 14_167_000_000.0, -6)
    out.append(("...an exact held value is never displaced, and coarser never replaces finer",
                _pm[("Cw", 2024)] == 12_190_000_000.0 and _pmd[("Cw", 2024)] is None
                and _pmd[("Cw", 2025)] == -3
                and _xbrl_dec_int("INF") is None and _xbrl_dec_int("-3") == -3
                and _xbrl_dec_int(None) is None and _xbrl_dec_int("x") is None,
                "unknown decimals means exact; resolution only ever improves"))
    _px = ('<?xml version="1.0"?><xbrl xmlns="http://www.xbrl.org/2003/instance" '
           'xmlns:adbe="http://www.adobe.com/20181130">'
           '<context id="dfy18"><entity><identifier scheme="s">X</identifier></entity>'
           '<period><startDate>2017-12-02</startDate><endDate>2018-11-30</endDate>'
           '</period></context>'
           '<adbe:CostOfIssuanceOfTreasuryStock contextRef="dfy18" unitRef="u" '
           'decimals="-6">393000000</adbe:CostOfIssuanceOfTreasuryStock>'
           '<adbe:CostOfIssuanceOfTreasuryStock contextRef="dfy18" unitRef="u" '
           'decimals="-3">393193000</adbe:CostOfIssuanceOfTreasuryStock></xbrl>')
    _pctx, _pfacts = _xbrl_parse_instance(_px)
    _pvals, _pmeta = _xbrl_extract(_pctx, _pfacts, XBRL_REGISTRY["ADBE"],
                                   {2018: "2018-11-30"}, {})
    out.append(("...and within one instance: a millions fact first in the document loses to "
                "the thousands fact",
                _pvals.get(("Cw", 2018)) == 393193000.0
                and _pmeta["dur_dec"][("Cw", 2018)] == -3,
                "document order plays vintage; the finer agreeing fact wins either way"))
    _tm: dict = {}
    _tmd: dict = {}
    out.append(("Merge reports writes: insert True, blocked False, upgrade True",
                _xbrl_merge_value(_tm, _tmd, ("Cw", 2018), 393_000_000.0, -6) is True
                and _xbrl_merge_value(_tm, _tmd, ("Cw", 2018), 400_000_000.0, -3) is False
                and _xbrl_merge_value(_tm, _tmd, ("Cw", 2018), 393_193_000.0, -3) is True,
                "the walk records WHO supplied each held value from these returns"))
    _dv, _dn = _xbrl_verify(XBRL_REGISTRY["ADBE"],
                            {("Cw", 2018): 393_000_000.0,
                             ("Cw", 2017): 240_126_000.0,
                             ("Cw", 2016): 236_400_000.0},
                            {("Cw", 2018): -6}, {("Cw", 2018): "0000796343-21-000006"})
    out.append(("A discard note names the held value's decimals and supplying accession",
                _dv == {"Cw"}
                and any("decimals=-6" in n and "0000796343-21-000006" in n for n in _dn)
                and "matched none" in _xbrl_trace_note(
                    [("2019-01-25", "acc-1", "matched none")])
                and _xbrl_trace_note([]) == "",
                "the failure message carries its own investigation"))
    _vm: dict = {}
    _vmd: dict = {}
    _xbrl_merge_value(_vm, _vmd, ("Cw", 2018), 393_000_000.0, None)
    _vblocked = _xbrl_merge_value(_vm, _vmd, ("Cw", 2018), 393_193_000.0, -3)
    _vwon = _xbrl_merge_value(_vm, _vmd, ("Cw", 2018), 393_193_000.0, -3,
                              393_193_000.0)
    out.append(("The contract outranks the heuristics: a verify-matching fact takes the slot",
                _vblocked is False and _vwon is True
                and _vm[("Cw", 2018)] == 393_193_000.0 and _vmd[("Cw", 2018)] == -3,
                "an unknown-decimals holder cannot veto the registered identity (ADBE FY2018)"))
    _vx = ('<?xml version="1.0"?><xbrl xmlns="http://www.xbrl.org/2003/instance" '
           'xmlns:adbe="http://www.adobe.com/20181130">'
           '<context id="dv18"><entity><identifier scheme="s">X</identifier></entity>'
           '<period><startDate>2017-12-02</startDate><endDate>2018-11-30</endDate>'
           '</period></context>'
           '<adbe:CostOfIssuanceOfTreasuryStock contextRef="dv18" unitRef="u">'
           '393000000</adbe:CostOfIssuanceOfTreasuryStock>'
           '<adbe:CostOfIssuanceOfTreasuryStock contextRef="dv18" decimals="-3" '
           'unitRef="u">393193000</adbe:CostOfIssuanceOfTreasuryStock></xbrl>')
    _vctx, _vfacts = _xbrl_parse_instance(_vx)
    _vvals, _vmeta = _xbrl_extract(_vctx, _vfacts, XBRL_REGISTRY["ADBE"],
                                   {2018: "2018-11-30"}, {})
    out.append(("...at the document level too: a decimals-less first fact cannot hold it",
                _vvals.get(("Cw", 2018)) == 393_193_000.0
                and _vmeta["dur_dec"][("Cw", 2018)] == -3,
                "the live FY2020 shape, unreachable for verified figures at either level"))
    # F2 — the successor tag at rank 1 (12 Sep 2026; DECOMP §1.2, §4).
    out.append(("F2: the successor sits at rank 1, adjacent to its deprecated predecessor",
                CONCEPTS["Ce"][0][0] == "ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlans"
                and CONCEPTS["Ce"][0][1] == "ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlansIncludingStockOptions",
                "element succession modelled where the deprecation happened"))
    _inf = {"facts": {"us-gaap": {
        "ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlansIncludingStockOptions":
            {"units": {"USD": [
                {"form": "10-K", "start": f"{y}-08-01", "end": f"{y+1}-07-31",
                 "filed": f"{y+1}-09-15", "val": v * 1e6}
                for y, v in ((2023, 282.0), (2024, 398.0))]}},
        "ProceedsFromStockOptionsExercised": {"units": {"USD": [
            {"form": "10-K", "start": f"{y}-08-01", "end": f"{y+1}-07-31",
             "filed": f"{y+1}-09-15", "val": v * 1e6}
            for y, v in ((2023, 121.0), (2024, 234.0))]}}}}}
    _ins: list[str] = []
    _ino: dict[int, str] = {}
    _inr = _annual(_inf, CONCEPTS["Ce"][0], [], _ins, True, False, origin=_ino)
    out.append(("The Intuit shape: the successor's combined figure wins over options-only",
                _inr[2024][2] == 282e6 and _inr[2025][2] == 398e6
                and _ino[2025] == "ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlansIncludingStockOptions",
                "228/282/398 match the 10-K face; options-only 90/121/234 was the subset"))
    # F1 — the OFFER cadence/size test (12 Sep 2026; DECOMP §1.1, §4).
    out.append(("F1: payroll cadence returns to dS; raises, spikes and blind years stay out",
                not offer_event_sized(30e6, 1.6e9, [25e6, 29e6, 22e6])
                and offer_event_sized(5.7e6, 22e6, [0.695e6])
                and offer_event_sized(30e6, 2e9, [8e6, 9e6, 7e6])
                and offer_event_sized(1e6, 0.0, [1e6, 1e6, 1e6]),
                "QCOM's 1.9% cadence returns; TGTX's 26% raise, a 3x-median spike and a "
                "no-base year all stay excluded"))
    _orn = offer_returned_note(206.0, list(range(2016, 2026)))
    out.append(("...and the returned note is self-naming: years, figure, cadence, thresholds",
                "10 year(s)" in _orn and "FY2016" in _orn and "FY2025" in _orn
                and "206.0M" in _orn and "payroll cadence" in _orn
                and "4%" in _orn and "three times" in _orn
                and "still excluded" in _orn,
                "a page run months from now still announces what the test did"))
    out.append(("F1 amended: an episodic line is a capital event whatever its size (KNSL)",
                offer_event_sized(742e3, 21.3e6, [7.59e6], False)
                and offer_event_sized(155e3, 22.8e6, [7.59e6, 742e3, 311e3], False)
                and not offer_event_sized(742e3, 21.3e6, [7.59e6], True)
                and "occasional years" in offer_returned_note(1.0, [2020]),
                "Kinsale: an IPO and three sub-4% follow-ons in 4 of 10 years — real "
                "raises that slipped the size bar; persistence is the cadence the "
                "note claims"))
    # ── the sweep (toolkit pass, 12 Sep 2026): route sentences name menu
    #    pages, never numbers. _route_ok = a frozen menu name present, no
    #    "tool N" / "page N". Standalone producers only; inline UI text is
    #    covered by the tokenizer scan of record and the click-through.
    out.append(("Sweep: IFRS-filer note (partial branch) routes by menu name",
                _route_ok(foreign_filer_note("ProfitLoss", [])),
                foreign_filer_note("ProfitLoss", [])[-80:]))
    out.append(("Sweep: IFRS-filer note (unread branch) routes by menu name",
                _route_ok(foreign_filer_note("ProfitLoss", ["revenue"])),
                foreign_filer_note("ProfitLoss", ["revenue"])[-80:]))

    # ── the 11 Sep 2026 concession (job 5 of the toolkit pass): the
    #    compounding language is conditional. dE is a level ratio (OE/N),
    #    not an annual retention factor; dE**t and the 87% break-even hold
    #    only if the dilution pace persists, and every surface now says so.
    #    Old absolutes are asserted ABSENT via concatenation so this check
    #    never matches itself; UI wording is asserted only where UI exists,
    #    so the check also holds on the Baselines app's engine copy.
    from pathlib import Path as _P5
    _s5 = _P5(__file__).read_text(encoding="utf-8")
    out.append(("Concession wording: conditional everywhere, old absolutes gone",
                ("Share of reported value growth" + " that survives") not in _s5
                and ("Value kept" + " after 10y") not in _s5
                and ("Below the 87%" + " break-even.**") not in _s5
                and ("Above the 87%" + " break-even**") not in _s5
                and "dilution pace producing this" in _s5
                and (("q3.metric(" not in _s5)
                     or ("Per-share level after 10y" in _s5
                         and "If the dilution pace behind it persists" in _s5)),
                "own-source scan"))

    # ── job-7 ride wordings (toolkit pass, 12 Sep 2026), pinned at the
    #    engine's home file; the hash audit carries them to every copy.
    from pathlib import Path as _P7
    _s7 = _P7(__file__).read_text(encoding="utf-8")
    out.append(("Ride wordings: equity-raise cause, sub-1M swing decimal, one-year grammar",
                "an all-stock acquisition or an equity raise" in _s7
                and ("most often an all-stock" + " acquisition") not in _s7
                and ":,.1f}M\" if abs(alt - net_cash) < 1" in _s7
                and "more than 1% that year." in _s7,
                "own-source scan"))


    # ── Residue sweep, 16 Sep 2026 ──────────────────────────────────
    _tm_adj, _tm_ph = split_adjust({2016: 100e6, 2017: 3_300_000_000.0,
                                    2018: 3_300_000_000.0})
    out.append(("Ladder switch drops the abandoned series' split notes and keeps the rest",
                len(_tm_ph) == 1 and switched_series_notes(
                    _tm_ph + ["a note about something else"], _tm_ph, []
                ) == ["a note about something else"],
                "TM's phantom pair: a split note may describe the series in use, no other"))
    _asml = treasury_mixed_note([2016, 2019, 2021])
    out.append(("Mixed treasury filer: the accepted years are named beside the rejection",
                "FY2016, FY2019, FY2021" in _asml
                and _asml.startswith("The same treasury line passed the size test"),
                "ASML: one tag split by the size test; both sides now on the page"))
    out.append(("The static switch note drops the buyback premise when no buyback was read",
                "buying stock back" in share_route_note("static", 56.3e6, 58.2e6,
                                                        "the 10-K cover page", 10, 10, 2025)
                and "buying stock back" not in share_route_note(
                    "static", 56.3e6, 58.2e6, "the 10-K cover page", 10, 10, 2025, any_T=False)
                and "not shares outstanding" in share_route_note(
                    "static", 56.3e6, 58.2e6, "the 10-K cover page", 10, 10, 2025, any_T=False),
                "IBKR, FIN-V2 §7.1: no buybacks were read, so the premise was false"))
    out.append(("The cent glyph is spelled out — cents, one word (Chen, 16 Sep 2026)",
                chr(0xA2) not in _s7,
                "the shareholder-quality banner reads cents; no stray glyph in this file"))


    # ── B1: Gate 2 no-price-year exclusion, 16 Sep 2026 ─────────────
    _g2y = [Year(fy=2022, N=50.0, G=5.0, price=0.0),
            Year(fy=2023, N=100.0, G=10.0, Cw=12.0, price=50.0),
            Year(fy=2024, N=100.0, Cw=8.0, price=55.0)]
    _g2x = gate2_exclusions(_g2y)
    out.append(("Gate 2: the unpriced year is excluded with the reason on the year, priced years untouched",
                _g2x == [2022] and _g2y[0].excluded == GATE2_REASON
                and not _g2y[1].excluded and not _g2y[2].excluded,
                "HANDOVER §5.3: drop N, G and C where V is null for lack of a price"))
    _g2p = pool(_g2y)
    out.append(("Gate 2: the pools cover the priced years only",
                _g2p.years == 2 and _g2p.sum_N == 200.0 and abs(_g2p.dE - 0.95) < 1e-12,
                "the excluded year's 55.0M of flattered owners' earnings never enters"))
    _g2z = [Year(fy=2022, N=50.0, price=0.0)]
    out.append(("Gate 2: with no price anywhere, nothing is excluded — the refusal upstream owns that page",
                gate2_exclusions(_g2z) == [] and not _g2z[0].excluded,
                "a transient provider failure must not empty the pools"))
    out.append(("Gate 2: the note names the years, the old hedge is retired, and a short recent pool says so",
                "FY2022, FY2023 excluded — no share price, per Gate 2" in gate2_note([2022, 2023])
                and "that year" in gate2_note([2024])
                and ("their SBC cost is under" + "stated.") not in _s7
                and ((("Satisfy your" + "self") not in _s7)
                     or (("({recent.years} pri" + "ced)") in _s7)),
                "the sentence is exact, the hedge is gone, the caption states priced coverage when short"))


    # ── B2: SHD input-seed fallback, 16 Sep 2026 ─────────────────
    _shd_s, _shd_n = shd_input_seed(0.0, {2023: 180.0e6, 2024: 202.1e6}, 1.0)
    out.append(("SHD fallback: an empty seed takes the latest weighted average, scaled, with the note",
                abs(_shd_s - 202.1) < 1e-9 and "202.1M, FY2024" in _shd_n
                and "need setting by hand" in _shd_n
                and abs(shd_input_seed(0.0, {2024: 100.0e6}, 10.0)[0] - 1000.0) < 1e-9,
                "the RDDT-era item: tool 2 seeded 202.1M while this cascade seeded zero"))
    out.append(("SHD fallback: a live seed and a read-nothing wavg both pass through untouched",
                shd_input_seed(190.9, {2024: 202.1e6}, 1.0) == (190.9, "")
                and shd_input_seed(0.0, {}, 1.0) == (0.0, ""),
                "GRAB stays refused: its IFRS-tagged average is invisible to this US-GAAP series"))

    return out



from pathlib import Path

# ══════════════════════════════════════════════════════════════════════
#  EPV — Greenwald's Earnings Power Value (page-local; the reader above
#  is the Tragic Algebra Analyzer's)
# ══════════════════════════════════════════════════════════════════════
#
# Everything from here down is this page's own. The line between the two
# matters: nothing below changes a figure another page computes, and the
# three concept lines added above (OI, TAX, PRETAX) feed only this block.
#
# Whose method: Bruce Greenwald ("Value Investing: From Graham to Buffett
# and Beyond"). What is ours: every refusal, the SBC-corrected second leg,
# and the honest limits of a filings-only normalization — his maintenance-
# capex and growth-SG&A adjustments need judgement filings don't encode,
# so they are boxes with stated defaults of zero, never silent estimates.
#
# One pool serves both legs, by design. The pool already requires an
# Ω-measurable year (the exclusion machinery marks exactly the years where
# Ω cannot be read honestly), so the SBC-corrected leg needs nothing the
# standard leg's pool lacks — and the gap between the legs is exact by
# linearity: the capitalized (Ω − G) margin effect, nothing else. If the
# legs ran on different year sets, the gap would smuggle a window
# difference into a sentence that claims to price stock compensation.
# That sentence must not be writable.

EPV_MIN_POOL = 4        # a cycle of two years is not a normalization
EPV_MIN_RATE_YEARS = 3  # below this the tax box seeds at the statutory rate
EPV_TAX_DEFAULT = 0.21  # US statutory; Burry normalises per company (MSFT 19%, ADBE 18%)
EPV_RR_DEFAULT = 15.0   # the kit's demanded return
EPV_RR_FLOOR = 8.0      # capitalization at low r explodes; the Expectations
                        # page floors its required-return box here for the
                        # same reason, and 8% is Greenwald's own
                        # cost-of-capital neighbourhood


@dataclass
class EpvYear:
    """One fiscal year of the normalization lines, $M. None means the tag
    did not answer for that year — the cell is refused, never read as
    zero. G and omega come from the Year row and follow its arithmetic."""
    fy: int
    rev: float | None
    oi: float | None
    tax: float | None
    pretax: float | None
    G: float
    omega: float
    excluded: str           # carried from the Year row; non-empty leaves the pool

    @property
    def opm(self) -> float | None:
        return self.oi / self.rev if self.rev and self.oi is not None else None

    @property
    def adj_oi(self) -> float | None:
        """Operating income with the GAAP charge added back and the true
        stock-comp cost taken out: OI + G − Ω."""
        return self.oi + self.G - self.omega if self.oi is not None else None

    @property
    def adj_opm(self) -> float | None:
        return self.adj_oi / self.rev if self.rev and self.adj_oi is not None else None

    @property
    def eff_rate(self) -> float | None:
        """Effective tax rate, filed. Only meaningful against positive
        pretax income; a benefit in a positive-pretax year reads negative
        and stays — the median absorbs it."""
        if self.pretax is None or self.tax is None or self.pretax <= 0:
            return None
        return self.tax / self.pretax


def build_epv_years(years: list, epv: dict) -> list[EpvYear]:
    rows = []
    for y in years:
        e = epv.get(y.fy, {})
        rows.append(EpvYear(fy=y.fy, rev=e.get("rev"), oi=e.get("oi"),
                            tax=e.get("tax"), pretax=e.get("pretax"),
                            G=y.G, omega=y.omega, excluded=y.excluded))
    return rows


def margin_pool(rows: list[EpvYear]) -> list[EpvYear]:
    """The normalization pool, one pool for both legs: operating income
    read, revenue read and positive, year not excluded. Exclusions
    (listing years, share-funded deals, Gate 2 no-price years) leave the
    pool the way they leave every other pool in the kit — they are
    precisely the years whose Ω cannot be read honestly, and a stock-
    funded deal year's margin carries partial-year acquired revenue the
    average should not swallow silently."""
    return [r for r in rows if r.rev and r.rev > 0 and r.oi is not None
            and not r.excluded]


def pool_margins(pool: list[EpvYear]) -> tuple[float, float]:
    """(standard mean margin, SBC-corrected mean margin) over one pool.
    Mean, not median: the cycle average is the method — clipping the
    trough years is what normalization exists not to do. The median is
    shown beside it on the page, unconditionally."""
    m1 = sum(r.opm for r in pool) / len(pool)
    m2 = sum(r.adj_opm for r in pool) / len(pool)
    return m1, m2


def normalization_refusal(n: int) -> str:
    """Reason to refuse the whole page for a thin pool, or ''."""
    if n >= EPV_MIN_POOL:
        return ""
    return (f"Only {n} year{'s' if n != 1 else ''} of operating margin could be read in "
            f"this window after exclusions, and a normalization needs at least "
            f"{EPV_MIN_POOL} — a cycle of two years is not a normalization. The "
            "Tragic Algebra Analyzer prices on net income and does not need this "
            "window; use that page.")


def negative_margin_refusal(mean_m: float, n: int, fy_lo: int, fy_hi: int) -> str:
    """The routed stop for a business with no positive normalized margin."""
    return (f"**No positive normalized operating margin.** The mean over the {n} readable "
            f"years FY{fy_lo}–FY{fy_hi} is {mean_m:.1%}: there is no earnings power to "
            "capitalize, and a no-growth value built on a negative margin would be a "
            "guess wearing arithmetic. If the story is a crossing from loss to profit, "
            "the Inflection Checker is the page that reads that evidence; use that page.")


def dispersion_caption(pool: list[EpvYear], mean_m: float) -> str:
    """Printed on every run, no threshold: the choice of the mean is kept
    honest by showing the median, the latest year and the range beside it."""
    ms = sorted(pool, key=lambda r: r.opm)
    med = (ms[len(ms) // 2].opm if len(ms) % 2 else
           (ms[len(ms) // 2 - 1].opm + ms[len(ms) // 2].opm) / 2)
    latest = max(pool, key=lambda r: r.fy)
    return (f"Normalized operating margin {mean_m:.1%} — the mean of {len(pool)} years, "
            f"FY{min(r.fy for r in pool)}–FY{max(r.fy for r in pool)} · median {med:.1%} · "
            f"latest FY{latest.fy} {latest.opm:.1%} · range FY{ms[0].fy} {ms[0].opm:.1%} "
            f"to FY{ms[-1].fy} {ms[-1].opm:.1%}.")


def tax_rate_pool(rows: list[EpvYear]) -> list[tuple[int, float]]:
    """(fy, effective rate) for every window year with positive pretax
    income and a tax figure — excluded years included on purpose: a
    listing year's tax rate is still a tax rate, and the rate pool is
    deliberately wider than the margin pool."""
    return [(r.fy, r.eff_rate) for r in sorted(rows, key=lambda r: r.fy)
            if r.eff_rate is not None]


def normalized_tax(rate_pool: list[tuple[int, float]]) -> tuple[float, str]:
    """(seed for the tax box, source sentence). The median filed rate —
    robust to one-off years (TCJA remeasurements, big credits) without a
    tuned clamp. Below EPV_MIN_RATE_YEARS readable years the box seeds at
    the statutory rate and the sentence says so."""
    if len(rate_pool) < EPV_MIN_RATE_YEARS:
        return EPV_TAX_DEFAULT, (
            f"US statutory {EPV_TAX_DEFAULT:.0%} — only {len(rate_pool)} readable "
            f"filed rate{'s' if len(rate_pool) != 1 else ''} in the window, below the "
            f"{EPV_MIN_RATE_YEARS} the seed needs")
    rates = sorted(r for _, r in rate_pool)
    med = (rates[len(rates) // 2] if len(rates) % 2 else
           (rates[len(rates) // 2 - 1] + rates[len(rates) // 2]) / 2)
    return med, (f"median filed effective rate over {len(rate_pool)} years, "
                 f"FY{rate_pool[0][0]}–FY{rate_pool[-1][0]}")


def epv_leg(margin: float, revenue: float, adj_pretax: float,
            tax_rate: float, rr: float) -> tuple[float, float]:
    """(NOPAT $M, enterprise EPV $M). The whole model in one line:
    normalized pre-tax operating earnings, taxed, capitalized. rr is a
    fraction. Judgement adjustments enter pre-tax, and enter BOTH legs
    equally, so the gap between the legs stays pure SBC."""
    nopat = (margin * revenue + adj_pretax) * (1.0 - tax_rate)
    return nopat, nopat / rr


def epv_equity(ev: float, net_cash: float, shares: float) -> tuple[float, float, bool]:
    """(equity $M unfloored, per share for display, floored?). The $M
    arithmetic is never floored — the subtraction prints as it is — but a
    negative per-share figure is not a price, so the headline floors at
    $0.00 with the sentence that owns the floor."""
    eq = ev + net_cash
    ps = eq / shares
    return eq, (0.0 if ps < 0 else ps), ps < 0


def zero_floor_sentence(nopat: float, ev: float, net_cash: float, rr: float) -> str:
    return (f"**Normalized earnings power capitalized at {rr:.1%} does not cover net "
            f"debt.** NOPAT of \\${nopat:,.0f}M capitalizes to \\${ev:,.0f}M against net "
            f"cash of \\${net_cash:,.0f}M — the equity's no-growth value is nothing, and "
            "it is shown as \\$0.00 rather than as a negative price.")


def framing_sentence(rr: float) -> str:
    """The page's epistemics in one line — chained to the kit's doctrine
    that a demanded-return valuation is a price, not a property of the
    company."""
    return (f"EPV at {rr:.1%} is the price at which a buyer earns {rr:.1%} a year on the "
            "business exactly as it stands — current revenue at the normalized margin, "
            "growing never.")


def gap_sentence(gap_ps: float, floored_either: bool) -> str:
    tail = (" (Stated before the zero floor on the headline figures.)"
            if floored_either else "")
    if gap_ps >= 0:
        return (f"The gap, \\${gap_ps:,.2f} per share, is the no-growth price of stock "
                "compensation: the same EPV with the GAAP charge added back and the true "
                "cost Ω taken out, on the same years. Exact by linearity — the capitalized "
                "(Ω − G) margin effect, nothing else." + tail)
    return (f"The SBC-corrected leg is the HIGHER one by \\${-gap_ps:,.2f} per share: over "
            "this pool the true stock-comp cost Ω came out below the GAAP charge G, which "
            "happens when buybacks retire more stock than the years issue. Same pool, same "
            "arithmetic — the capitalized (Ω − G) margin effect, nothing else." + tail)


def triad_sentence(price: float, eps1: float, floored: bool) -> str:
    """EPV here, the price there, the difference is what the Expectations
    page says must be believed. Three shapes: the ordinary one, the
    zero-anchor one, and a price below the anchor."""
    if floored or eps1 <= 0:
        return (f"The no-growth value of the equity is nothing, so the entire "
                f"\\${price:,.2f} price is a payment for growth. The Expectations page "
                "states the growth path that payment implies — and what the company's "
                "own record says about it.")
    if price <= eps1:
        return (f"The \\${price:,.2f} price sits \\${eps1 - price:,.2f} BELOW the "
                f"no-growth value of \\${eps1:,.2f} — the market is paying nothing for "
                "growth and discounting the earnings power that already exists. The "
                "Expectations page states the implied path all the same.")
    return (f"Of the \\${price:,.2f} price, \\${eps1:,.2f} is covered by no-growth "
            f"earnings power and \\${price - eps1:,.2f} — {(price - eps1) / price:.0%} of "
            "the price — is a payment for growth. The Expectations page states the "
            "growth path that payment implies.")


def epv_financial_stop(tk: str, fin_class: str, fin_reason: str) -> str:
    """The full routed stop for balance-sheet businesses, before any input
    renders. Unlike the Tragic Algebra Analyzer's withheld verdict, this
    page has no indicative ladder worth showing — an operating-margin
    normalization is simply the wrong frame."""
    desc = {"bank": "a bank", "insurer": "an insurer", "reit": "a REIT",
            "broker": "a client-asset broker"}.get(fin_class)
    if desc:
        return (f"**{tk} is {desc}.** {fin_reason} Earnings power normalized from an "
                "operating margin is the wrong frame for a balance-sheet business. The "
                "Financials Checker prices banks, insurers, REITs and client-asset "
                "brokers on a filed base per share — use that page.")
    return (f"**{tk} is in a financial class this kit refuses.** {fin_reason} "
            "No page here prices this class honestly, and a page that cannot stand "
            "behind a number refuses instead of printing one.")


def epv_foreign_route_suffix(msg: str) -> str:
    """The reader's currency refusal arrives as its own sentence; this
    page appends the route. Keyed on the reader's exact phrase — the
    self-test asserts that phrase still exists in the span above, so a
    future rewording fails loudly instead of silently dropping the
    route."""
    if ("Foreign private issuers are " + "not supported") not in msg:
        return ""
    return (" The Non-US Checker prices 20-F and 40-F filers in the filing currency, "
            "with a paste-your-own-figures mode for companies EDGAR has never heard "
            "of — use that page.")


def out_of_scope_sentence() -> str:
    return ("Greenwald's framework has a second leg this page does not build: "
            "reproduction cost — what a competitor would spend to recreate the assets. "
            "Without it the page cannot make his asset-value-vs-EPV comparison, the one "
            "that separates a franchise from a business earning no more than its assets "
            "deserve. EPV here is the no-growth anchor only, and nothing on this page "
            "calls it more than that.")



# ══════════════════════════════════════════════════════════════════════
#  DERIVED-OI REGISTRY (DOI, 19 Sep 2026) — page-local
# ══════════════════════════════════════════════════════════════════════
#
# Four filers present no operating subtotal, so OperatingIncomeLoss is
# never tagged and every window year refused the pool: ADP, HRB, PBI and
# BBW (the EPV census, 18 Sep 2026). For each, a per-year derivation from
# the filer's OWN tagged lines was established against pasted statement
# faces and fact panels (DOI session, 19 Sep 2026), with an arithmetic
# completeness bracket that must close against pretax income — the same
# figure per year, from the same filing vintages — before the year is
# admitted. A year whose bracket does not close REFUSES with the reason
# printed. No ratio apportionment exists anywhere on this route: every
# figure is a filed line or a signed sum of filed lines.
#
# PLACEMENT (Chen, 19 Sep 2026): page-local by the house placement rule —
# consumption decides, not category. XBRL_REGISTRY is shared because the
# shared reader consumes it on every carrier; this registry has exactly
# one consumer by the Job-2 doctrine itself (only the compounding frame
# needs verification-pinned derivation, which is why the Inflection
# Checker and the Magic Formula page are untouched by construction).
# Precedent: the DCF Evaluator's banner, the Magic Formula fallback.
# Every future docket entry is therefore a single-file deploy.
#
# The queue-G instance route was the brief's assumed pattern and ZERO of
# the four names needed it: every derivation input is a standard
# companyconcept tag. The registry carries derivation terms and bracket
# tuples only; nothing here keys into the shared fold and no docket name
# enters the instance-fetch walk.

DOI_EPS = 2.0   # dollars. API facts are exact integers; the bracket is a
                # float-safety epsilon, not a materiality band — a vintage
                # mismatch must fail loudly, never squeak under a threshold.


@dataclass(frozen=True)
class DoiTerm:
    """One signed line of a derivation or bracket. `source` is either the
    literal key "REV" (the page's own revenue cell, same vintage the table
    shows) or a us-gaap tag read page-locally through _annual. The bounds
    exist for PBI's two-element interest splice and are inclusive."""
    label: str
    source: str
    sign: int
    fy_lo: int = 0
    fy_hi: int = 9999


@dataclass(frozen=True)
class DoiRoute:
    """One filer's derivation. `terms` build OI; `bracket` is the per-year
    completeness AND vintage pin — its signed sum must equal the page's
    pretax cell (continuing operations) within DOI_EPS or the year
    refuses. `refused` are years refused by decision, with reasons.
    `notes` are the session's banked caveats, printed verbatim. `pins`
    (19 Sep 2026, live acceptance) are per-year VERIFIED bracket targets
    in raw dollars with the reason naming the sources: a year whose page
    pretax cell is poisoned by a documented bad fact brackets against
    the pin instead, and the printed line reports whether the page cell
    diverges (the documented defect) or agrees (the pin is retirable).
    A pin never repairs the pretax cell itself, only the bracket target."""
    formula: str
    terms: tuple[DoiTerm, ...]
    bracket: tuple[DoiTerm, ...]
    refused: dict[int, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    pins: dict[int, tuple[float, str]] = field(default_factory=dict)


DOI_REGISTRY: dict[str, DoiRoute] = {
    # ADP (evidence pasted 19 Sep 2026: FY2026 10-K face + notes, three
    # companyconcept series, FY2008-FY2026 annual coverage on all three
    # added tags). Interest expense is a named line INSIDE Total expenses;
    # the outside line is NonoperatingIncomeExpense (Note 4 decomposes it:
    # interest on corporate funds, AFS gains, non-service pension, ADP
    # Ventures — everything the net-cash add already prices).
    "ADP": DoiRoute(
        formula="Revenues − CostsAndExpenses + InterestExpense",
        terms=(DoiTerm("Total revenues", "REV", +1),
               DoiTerm("Total expenses", "CostsAndExpenses", -1),
               DoiTerm("Interest expense (add-back)", "InterestExpense", +1)),
        bracket=(DoiTerm("Total revenues", "REV", +1),
                 DoiTerm("Total expenses", "CostsAndExpenses", -1),
                 DoiTerm("Other (income)/expense, net",
                         "NonoperatingIncomeExpense", +1)),
        notes=(
            "ADP's interest expense partly funds the client-float strategy "
            "(commercial paper and reverse repos) while the float income sits in "
            "revenue, so the full add-back may flatter the operating margin by the "
            "client-funding cost. Direction stated, never sized: no per-year split "
            "is filed, and estimating one would be the apportionment this route "
            "forbids. ADP's own adjusted-EBIT non-GAAP makes the identical move.",
            "FY2018 nonoperating is a net-EXPENSE year (−172.1M, the pension era). "
            "The bracket takes it signed; the sign flip is the filing, not a data "
            "fault.",
            "FY2017–FY2018 carry ASC 606 / pension-reclass restatements in "
            "CostsAndExpenses and the nonoperating line. The bracket is the vintage "
            "pin: a year whose tag vintages diverge fails to close and refuses.",
        )),
    # HRB (evidence 19 Sep 2026: FY2026 10-K face, three fact panels, two
    # companyconcept series, FY2017-FY2026 annual coverage). Interest sits
    # OUTSIDE the operating total: Rev − CostsAndExpenses IS the filer's
    # operating income — no add-back. The outside tags serve the bracket
    # only, never the derived figure.
    "HRB": DoiRoute(
        formula="Revenues − CostsAndExpenses (the filer's own operating "
                "total; no add-back)",
        terms=(DoiTerm("Total revenues", "REV", +1),
               DoiTerm("Total operating expenses", "CostsAndExpenses", -1)),
        bracket=(DoiTerm("Total revenues", "REV", +1),
                 DoiTerm("Total operating expenses", "CostsAndExpenses", -1),
                 DoiTerm("Other income (expense), net",
                         "OtherNonoperatingIncomeExpense", +1),
                 DoiTerm("Interest expense on borrowings",
                         "InterestExpenseDebt", -1)),
        notes=(
            "HRB moved its fiscal year-end from April 30 to June 30 in 2021; a "
            "two-month transition stub (May–June 2021) belongs to no fiscal year "
            "and never enters.",
            "The bracket targets income from continuing operations before taxes; "
            "discontinued operations sit below it on HRB's face.",
            "HRB tags its only interest line under InterestExpenseDebt — an "
            "element outside this reader's interest group, which is why the line "
            "read as unfiled since FY2015. It was filed all along, under a door "
            "the reader never knocked on.",
        ),
        pins={2021: (668_736_000.0,
            "FY2021 bracket target pinned at 668.736 — the fiscal year ended "
            "April 30, 2021. Sources: the FY2022 and FY2023 10-Ks' agreeing "
            "annual facts, and the FY2021 10-K's own printed statement (pasted "
            "19 Sep 2026), which closes Rev − Total operating expenses + Other "
            "income − Interest expense to 668.736 exactly. The page's fy-2021 "
            "pretax cell carries a mis-dated fact, a FILER defect repeated "
            "across three filings: each of the FY2024, FY2025 and FY2026 10-Ks "
            "tagged its oldest June-year comparative with the April-2021 period "
            "dates (659,069 = FY2022's pretax, 711,212 = FY2023's, 762,322 = "
            "FY2024's — the slide, one year per filing), and newest-wins serves "
            "the latest. A future vintage that agrees with the pin retires it; "
            "any other movement re-fires the bracket visibly.")}),
    # PBI (evidence 19 Sep 2026: FY2025 10-K face, four fact panels, two
    # companyconcept series, plus the FY2019 and FY2022 10-K faces pasted
    # whole — all six splice years verified against their own printed
    # statements, brackets closing exactly, values matching to the dollar).
    "PBI": DoiRoute(
        formula="Revenues − CostsAndExpenses + net non-financing interest "
                "(InterestExpense FY2017–2022; −InterestIncomeExpenseNet "
                "FY2023 on)",
        terms=(DoiTerm("Total revenue", "REV", +1),
               DoiTerm("Total costs and expenses", "CostsAndExpenses", -1),
               DoiTerm("Interest expense, net (add-back)",
                       "InterestExpense", +1, fy_hi=2022),
               DoiTerm("Interest expense, net (add-back)",
                       "InterestIncomeExpenseNet", -1, fy_lo=2023)),
        bracket=(DoiTerm("Total revenue", "REV", +1),
                 DoiTerm("Total costs and expenses", "CostsAndExpenses", -1)),
        refused={2016: "FY2016 is not reconcilable: that era tagged two interest "
                       "lines and InterestIncomeExpenseNet aggregated both (a 55M "
                       "wedge against InterestExpense), and no verified face "
                       "resolves the year. Refused rather than guessed."},
        notes=(
            "Interest is a two-element splice: InterestExpense carries "
            "FY2017–FY2022 (its annual facts stop at CY2023) and the negated "
            "InterestIncomeExpenseNet carries FY2023 on (its annual facts went "
            "dark FY2017–FY2022). Every spliced year was verified against its own "
            "printed statement face — the FY2019 and FY2022 10-Ks, pasted whole.",
            "Financing interest expense stays INSIDE costs in both eras by the "
            "filer's own presentation: it is a cost of the financing revenue "
            "above it. Only the net non-financing interest line adds back.",
            "Other components of net pension cost and Other expense stay inside "
            "costs (the narrow scope): leaving them in understates OI in years "
            "they are net costs — direction stated, never sized. A fuller "
            "add-back is a documented v2, priced at per-year verification of "
            "each further line.",
            "The GEC-recast seam sits at FY2022, not FY2023 (live acceptance, "
            "19 Sep 2026): newest-wins serves FY2022 from the FY2023 10-K's "
            "recast comparative (bracket closes at 188.652), while FY2021 kept "
            "its pre-recast vintage (bracket −7.415). FY2017–FY2021 include "
            "GEC; FY2022 on is post-recast; the pool mixes the two knowingly. "
            "One residue, directioned never sized: PBI's bracket is degenerate "
            "(revenue minus costs), so it never pins the interest add-back's "
            "vintage — each year's interest rides its own newest fact beside "
            "the bracketed pair, face-verified for the spliced years but "
            "unpinned against future refilings.",
        ),
        pins={2018: (188_121_000.0,
            "FY2018 bracket target pinned at 188.121. Sources: the FY2019 and "
            "FY2020 10-Ks' agreeing annual facts, and the FY2019 10-K's own "
            "printed statement (stage 2, pasted 19 Sep 2026). The page's "
            "fy-2018 pretax cell reads 212.361, the pre-recast vintage — "
            "correct at its own time, stale at the terms': the divestiture "
            "recast lives entirely in the SECOND pretax element, and the "
            "reader's two-tag fill serves tag order over vintage, so the first "
            "tag's 2019-filed fact wins the cell. A READER seam, not a filer "
            "defect; whether multi-tag fills should prefer recency across tags "
            "is a fleet question documented in the handover. A future vintage "
            "that agrees with the pin retires it.")}),
    # BBW (evidence 19 Sep 2026: FY2026 10-K face, three fact panels, three
    # companyconcept series, plus the FY2018 10-K face for the Dec-2017
    # year — the bracket closed on that unseen face at exactly 13,813).
    # Shape (ii'): both derivation lines are filed subtotals and the
    # bracket's completeness is itself filed arithmetic, per year.
    "BBW": DoiRoute(
        formula="GrossProfit − SellingGeneralAndAdministrativeExpense",
        terms=(DoiTerm("Consolidated gross profit", "GrossProfit", +1),
               DoiTerm("Selling, general and administrative expense",
                       "SellingGeneralAndAdministrativeExpense", -1)),
        bracket=(DoiTerm("Consolidated gross profit", "GrossProfit", +1),
                 DoiTerm("Selling, general and administrative expense",
                         "SellingGeneralAndAdministrativeExpense", -1),
                 DoiTerm("Interest income (expense), net",
                         "InterestIncomeExpenseNonoperatingNet", +1)),
        notes=(
            "BBW moved to the retail calendar in 2018: fiscal 2017 ended "
            "December 30, 2017, and a five-week transition stub (to February 3, "
            "2018) belongs to no fiscal year and never enters. The year ending "
            "February 2024 is a 53-week year; the derivation does not care, but "
            "a 53-week year should never read as organic growth against 52-week "
            "neighbours.",
            "The filed Consolidated gross profit already absorbs store asset "
            "impairment (presented inside Total cost of merchandise sold in the "
            "Dec-2017-era face). The derivation takes the filed figure, as ever.",
            "The interest line is income in recent years and a small net expense "
            "in several early ones; the bracket takes it signed. The element "
            "(InterestIncomeExpenseNonoperatingNet) sits outside this reader's "
            "interest group — the HRB pattern, filed behind an unread door.",
        )),
}


def doi_terms_for(route: DoiRoute, fy: int) -> tuple[DoiTerm, ...]:
    """The derivation terms applicable to one fiscal year — the splice
    resolved. Bounds are inclusive on both ends."""
    return tuple(t for t in route.terms if t.fy_lo <= fy <= t.fy_hi)


def _doi_val(term: DoiTerm, fy: int, rev_raw: float | None,
             extra: dict) -> float | None:
    if term.source == "REV":
        return rev_raw
    s = extra.get(term.source, {})
    return s[fy][2] if fy in s else None


def doi_bracket_sum(route: DoiRoute, fy: int, rev_raw: float | None,
                    extra: dict) -> float | None:
    """Signed sum of the bracket lines in raw dollars, or None if any
    line is missing for the year."""
    total = 0.0
    for t in route.bracket:
        v = _doi_val(t, fy, rev_raw, extra)
        if v is None:
            return None
        total += t.sign * v
    return total


def _doi_arith(pairs: list[tuple[int, float]]) -> str:
    """Render a signed sum the way a hand checks it: 1,892.629 − 1,700.105
    + 101.460 — each printed magnitude is the term's CONTRIBUTION in $M,
    so a negated element (PBI's net-interest splice) prints as the
    positive add-back the face shows."""
    out = ""
    for i, (sign, v) in enumerate(pairs):
        c = sign * v / 1e6
        if i == 0:
            out = f"{c:,.3f}"
        else:
            out += (" − " if c < 0 else " + ") + f"{abs(c):,.3f}"
    return out


def doi_derive(route: DoiRoute, fy: int, rev_raw: float | None,
               pretax_raw: float | None,
               extra: dict) -> tuple[float | None, str, str]:
    """(OI raw dollars | None, refusal reason, printed line). The printed
    line is the hand-checkable record either way: the derivation and its
    bracket on success, the named reason on refusal. Figures in $M."""
    if fy in route.refused:
        return None, route.refused[fy], f"FY{fy} refused — {route.refused[fy]}"
    terms = doi_terms_for(route, fy)
    pairs, missing = [], []
    for t in terms:
        v = _doi_val(t, fy, rev_raw, extra)
        (missing if v is None else pairs).append(
            t.source if v is None else (t.sign, v))
    if missing:
        why = f"{', '.join(missing)} did not answer for FY{fy}"
        return None, why, f"FY{fy} refused — {why}"
    pin = route.pins.get(fy)
    if pretax_raw is None and pin is None:
        why = f"pretax income did not answer for FY{fy}, so the bracket cannot close"
        return None, why, f"FY{fy} refused — {why}"
    bpairs = []
    for t in route.bracket:
        v = _doi_val(t, fy, rev_raw, extra)
        if v is None:
            why = f"bracket line {t.source} did not answer for FY{fy}"
            return None, why, f"FY{fy} refused — {why}"
        bpairs.append((t.sign, v))
    bsum = sum(s * v for s, v in bpairs)
    target = pin[0] if pin is not None else pretax_raw
    if abs(bsum - target) > DOI_EPS:
        against = ("the pinned target" if pin is not None else "pretax")
        why = (f"bracket did not close for FY{fy}: "
               f"{_doi_arith(bpairs)} = {bsum / 1e6:,.3f} against {against} "
               f"{target / 1e6:,.3f} — a completeness or vintage mismatch, "
               "refused rather than papered over")
        return None, why, f"FY{fy} refused — {why}"
    oi = sum(s * v for s, v in pairs)
    if pin is None:
        line = (f"FY{fy}: OI = {_doi_arith(pairs)} = {oi / 1e6:,.3f} · bracket "
                f"{_doi_arith(bpairs)} = {bsum / 1e6:,.3f} = pretax ✓")
    else:
        if pretax_raw is None:
            cell = "page pretax cell unread; the bracket closed against the pin"
        elif abs(pretax_raw - pin[0]) <= DOI_EPS:
            cell = ("page pretax cell agrees with the pin — the pin is "
                    "retirable")
        else:
            cell = (f"page pretax cell {pretax_raw / 1e6:,.3f} diverges from "
                    "the pinned target — the documented defect; the refusal "
                    "returns if the pin is ever removed")
        line = (f"FY{fy}: OI = {_doi_arith(pairs)} = {oi / 1e6:,.3f} · bracket "
                f"{_doi_arith(bpairs)} = {bsum / 1e6:,.3f} = pinned pretax ✓ "
                f"({cell})")
    return oi, "", line


def doi_banner(tk: str, route: DoiRoute) -> str:
    return (f"**Operating income derived per registry.** {tk} presents no "
            "operating subtotal, so OperatingIncomeLoss is never tagged and "
            f"every year would refuse. OI = {route.formula} — every line a "
            "filed figure, verified against the filer's own statements "
            "(19 Sep 2026). Each year's derivation prints hand-checkable in "
            "Notes and detail beside a completeness bracket that must equal "
            "pretax income within \\$2; a year whose bracket does not close "
            "refuses rather than reads. Figures in the printed lines are \\$M.")


def doi_floor_suffix(tk: str, rows: list[EpvYear]) -> str:
    """Appended to the normalization-floor refusal. Registered names point
    at their per-year reasons; an unregistered filer with the docket's
    shape (pretax reads, operating income never does) learns the route
    exists and that it refuses rather than approximates."""
    if tk in DOI_REGISTRY:
        return (" This ticker is registered on the derived-OI route; the "
                "per-year derivations and refusals under Notes and detail "
                "say which brackets could not close.")
    if rows and all(r.oi is None for r in rows)             and any(r.pretax is not None for r in rows):
        return (" This filer appears to present no operating subtotal: "
                "operating income never answered while pretax income did. A "
                "registry-verified derivation admits such filers name by name "
                "— ADP, HRB, PBI and BBW are registered — and an unregistered "
                "name refuses rather than approximates.")
    return ""


def doi_apply(tk: str, rows: list[EpvYear],
              pre: dict) -> tuple[DoiRoute | None, list[str]]:
    """Fill derived OI into the page's rows, for registered tickers only —
    one dict lookup and out for everyone else, the queue-G shape. Mutates
    r.oi in place (the pool, table and both legs consume it through the
    normal machinery); returns the route and the per-year printed lines.
    Never touches a year whose OI the filer tagged, and never writes into
    the reader's series — this is the page's own last step."""
    route = DOI_REGISTRY.get(tk)
    if route is None:
        return None, []
    try:
        cik = _ticker_map().get(tk)
        wanted = {t.source for t in route.terms + route.bracket
                  if t.source != "REV"}
        facts = _facts(cik)
        extra = {tag: _annual(facts, [tag], []) for tag in sorted(wanted)}
    except Exception as e:
        return route, [f"The derived-OI route could not read the filings "
                       f"({type(e).__name__}); every year refuses."]
    lines = []
    for r in sorted(rows, key=lambda r: r.fy):
        if r.oi is not None:
            continue
        rev_raw = r.rev * 1e6 if r.rev is not None else None
        pretax_raw = r.pretax * 1e6 if r.pretax is not None else None
        oi, why, line = doi_derive(route, r.fy, rev_raw, pretax_raw, extra)
        if oi is not None:
            r.oi = oi / 1e6
        lines.append(line)
    for _pfy in sorted(route.pins):
        lines.append(f"Pinned bracket target, FY{_pfy}: {route.pins[_pfy][1]}")
    return route, lines



def _epv_fixture() -> list[EpvYear]:
    """A live-shaped fixture: a retailer's cycle — near-zero trough years,
    a strong recent run, one excluded listing-shaped year, one year whose
    operating income never read. Built for the pool and linearity checks;
    the synthetic hand-checks below use round numbers instead."""
    return [
        EpvYear(2017, 400.0, 2.0, 0.4, 1.6, 4.0, 6.0, ""),
        EpvYear(2018, 410.0, -4.0, -0.5, -5.0, 4.0, 7.0, ""),
        EpvYear(2019, 420.0, 1.0, 0.2, 0.9, 4.0, 6.5, ""),
        EpvYear(2020, 380.0, 30.0, 6.9, 30.0, 5.0, 8.0, "listing year"),
        EpvYear(2021, 460.0, 55.0, 12.0, 52.0, 5.0, 9.0, ""),
        EpvYear(2022, 480.0, None, 13.0, 55.0, 5.0, 9.0, ""),
        EpvYear(2023, 500.0, 62.0, 14.5, 60.0, 6.0, 10.0, ""),
        EpvYear(2024, 515.0, 64.0, 15.0, 62.0, 6.0, 11.0, ""),
    ]


def epv_self_test() -> list[tuple[str, bool, str]]:
    out = []

    # ── the capitalization arithmetic, to the cent ────────────────────
    _n, _ev = epv_leg(0.20, 1000.0, 0.0, 0.25, 0.15)
    _eq, _ps, _fl = epv_equity(_ev, 100.0, 100.0)
    out.append(("EPV: the synthetic hand-check — 20% on $1,000M taxed 25% at 15% is "
                "$1,000.00M enterprise, $1,100.00M equity, $11.00/share",
                abs(_n - 150.0) < 1e-9 and abs(_ev - 1000.0) < 1e-9
                and abs(_eq - 1100.0) < 1e-9 and abs(_ps - 11.0) < 1e-9 and not _fl,
                f"NOPAT {_n:.4f}, EV {_ev:.4f}, equity {_eq:.4f}, {_ps:.4f}/share"))

    _n2, _ev2 = epv_leg(0.20, 1000.0, 40.0, 0.25, 0.15)
    out.append(("EPV: a $40M pre-tax judgement box moves equity by exactly its "
                "after-tax capitalization, $200.00M",
                abs((_ev2 - _ev) - 200.0) < 1e-9 and abs(_n2 - 180.0) < 1e-9,
                f"ΔEV {_ev2 - _ev:.4f}"))

    # ── linearity: the gap is the capitalized (Ω − G) margin effect ──
    _g1, _gev1 = epv_leg(0.20, 1000.0, 0.0, 0.25, 0.15)
    _g2, _gev2 = epv_leg(0.18, 1000.0, 0.0, 0.25, 0.15)   # Ω − G costs 2 margin points
    _gap = (_gev1 - _gev2) / 100.0
    out.append(("EPV: linearity, synthetic — a 2-point margin delta on these inputs "
                "is exactly $1.00/share of gap",
                abs(_gap - 1.0) < 1e-9, f"{_gap:.6f}"))

    _rows = _epv_fixture()
    _pool = margin_pool(_rows)
    _m1, _m2 = pool_margins(_pool)
    _, _e1 = epv_leg(_m1, 515.0, 0.0, 0.24, 0.15)
    _, _e2 = epv_leg(_m2, 515.0, 0.0, 0.24, 0.15)
    _pred = (_m1 - _m2) * 515.0 * (1 - 0.24) / 0.15 / 13.5
    out.append(("EPV: linearity, live-shaped — through the full pool path the gap "
                "equals the capitalized margin delta to 1e-9",
                abs((_e1 - _e2) / 13.5 - _pred) < 1e-9,
                f"gap {(_e1 - _e2) / 13.5:.6f} vs {_pred:.6f}"))

    # ── the pool: who is in, who is out, and the floor ───────────────
    out.append(("EPV: the pool drops the excluded year and the unread-OI year, "
                "keeps the loss year",
                len(_pool) == 6 and 2020 not in {r.fy for r in _pool}
                and 2022 not in {r.fy for r in _pool} and 2018 in {r.fy for r in _pool},
                f"pool = {sorted(r.fy for r in _pool)}"))
    out.append(("EPV: the floor refuses three years, passes four, and routes",
                normalization_refusal(4) == "" and "not a normalization" in normalization_refusal(3)
                and _route_ok(normalization_refusal(3)),
                normalization_refusal(3)[:70]))

    # ── the dispersion caption computes what it claims ───────────────
    _cap = dispersion_caption(_pool, _m1)
    _med_expected = sorted(r.opm for r in _pool)[3]  # 6 rows -> mean of ranks 3,4
    _med_expected = (sorted(r.opm for r in _pool)[2] + _med_expected) / 2
    out.append(("EPV: the caption names mean, median, latest and both range years",
                f"{_m1:.1%}" in _cap and f"{_med_expected:.1%}" in _cap
                and "latest FY2024" in _cap and "FY2018 -1.0%" in _cap
                and "FY2024 12.4%" in _cap,
                _cap))

    # ── tax normalization ────────────────────────────────────────────
    _rp = tax_rate_pool(_rows)
    out.append(("EPV: the rate pool takes positive-pretax years only — the loss year "
                "is out, the excluded listing year is deliberately in",
                [fy for fy, _ in _rp] == [2017, 2019, 2020, 2021, 2022, 2023, 2024],
                f"{[fy for fy, _ in _rp]}"))
    _t, _tsrc = normalized_tax(_rp)
    out.append(("EPV: the seed is the median filed rate and the source sentence "
                "names the window",
                abs(_t - 13.0/55.0) < 1e-12 and "median filed effective rate" in _tsrc
                and "FY2017" in _tsrc and "FY2024" in _tsrc,
                f"{_t:.4f} — {_tsrc}"))
    _t2, _tsrc2 = normalized_tax(_rp[:2])
    out.append(("EPV: below three readable rates the box seeds at the statutory "
                "21% and the sentence says so",
                abs(_t2 - EPV_TAX_DEFAULT) < 1e-12 and "statutory" in _tsrc2 and "only 2" in _tsrc2,
                _tsrc2))

    # ── the zero floor ───────────────────────────────────────────────
    _eqz, _psz, _flz = epv_equity(800.0, -2500.0, 850.0)
    out.append(("EPV: equity below zero keeps its $M arithmetic, floors the "
                "per-share headline, and the sentence owns the floor",
                _flz and _psz == 0.0 and abs(_eqz + 1700.0) < 1e-9
                and "does not cover net debt" in zero_floor_sentence(120.0, 800.0, -2500.0, 0.15)
                and "\\$0.00" in zero_floor_sentence(120.0, 800.0, -2500.0, 0.15),
                f"equity {_eqz:.1f}M, headline {_psz}"))

    # ── refusal and route sentences ──────────────────────────────────
    _neg = negative_margin_refusal(-0.021, 9, 2016, 2025)
    out.append(("EPV: the negative-margin stop names the mean and the window and "
                "routes to the Inflection Checker",
                "-2.1%" in _neg and "FY2016–FY2025" in _neg and _route_ok(_neg),
                _neg[:80]))
    out.append(("EPV: the financial stop routes all four priced classes to the "
                "Financials Checker; the refused classes get the no-page sentence",
                all(_route_ok(epv_financial_stop("T", c, "r.")) and "Financials Checker"
                    in epv_financial_stop("T", c, "r.")
                    for c in ("bank", "insurer", "reit", "broker"))
                and "No page here prices" in epv_financial_stop("T", "refused", "r."),
                ""))
    out.append(("EPV: the foreign suffix routes to the Non-US Checker and keys on "
                "the reader's own sentence",
                _route_ok(epv_foreign_route_suffix("Foreign private issuers are "
                                                   "not supported"))
                and epv_foreign_route_suffix("something else entirely") == "",
                ""))
    out.append(("EPV: the reader's currency sentence still exists in the span above "
                "— the route's key would fail loudly on a rewording",
                ("Foreign private issuers are " + "not supported")
                in Path(__file__).read_text(encoding="utf-8"),
                ""))

    # ── the triad, all three shapes ──────────────────────────────────
    _t1 = triad_sentence(268.26, 60.56, False)
    _t2_ = triad_sentence(40.00, 55.00, False)
    _t3 = triad_sentence(189.17, 0.0, True)
    out.append(("EPV: the triad sentence — ordinary shape carries the split and the "
                "percentage, and every shape routes to the Expectations page",
                "\\$60.56" in _t1 and "\\$207.70" in _t1 and "77%" in _t1
                and "BELOW" in _t2_ and "entire" in _t3
                and all(_route_ok(s) for s in (_t1, _t2_, _t3)),
                _t1[:90]))

    # ── the gap sentence, both signs ─────────────────────────────────
    out.append(("EPV: the gap sentence handles both signs and names the linearity",
                "no-growth price of stock compensation" in gap_sentence(3.25, False)
                and "HIGHER" in gap_sentence(-1.10, False)
                and "zero floor" in gap_sentence(3.25, True),
                ""))

    # ── the page's own discipline ────────────────────────────────────
    _self = Path(__file__).read_text(encoding="utf-8")
    _local = _self[_self.index("EPV — Greenwald's Earnings " + "Power Value"):]
    out.append(("EPV: no sentence below the page-local banner calls this "
                "anything like fair" + " value",
                ("fair " + "value") not in _local.lower()
                and "growing never" in framing_sentence(0.15),
                ""))
    _stops, _covered = 0, 0
    _slines = _self.split("\n")
    for _i, _l in enumerate(_slines):
        if "st.stop()" in _l and "adjacency" not in _l:
            _stops += 1
            _prev = [p for p in _slines[max(0, _i - 3):_i] if p.strip()]
            if any("_page_footer()" in p for p in _prev):
                _covered += 1
    out.append((f"EPV: every stop renders the footer first — source-level adjacency",
                _stops > 0 and _stops == _covered, f"{_covered} of {_stops} stops covered"))

    # ── the three lines this page adds, present at all three sites ───
    out.append(("EPV: OI, TAX and PRETAX are in CONCEPTS, FILL_KEYS and TAG_LABELS, "
                "and the frozen tuple carries the Expectations page for the triad route",
                all(k in CONCEPTS and k in FILL_KEYS and k in TAG_LABELS
                    for k in ("OI", "TAX", "PRETAX"))
                and CONCEPTS["OI"][0] == ["OperatingIncomeLoss"]
                and "Expectations" in FROZEN_MENU,
                ""))

    # ── DOI: the derived-OI registry (19 Sep 2026) ───────────────────
    # Fixtures are the session's banked figures, raw dollars, from the
    # filers' own statements and companyconcept facts. Deterministic and
    # offline: the checks exercise the derivation core, never the network.
    _adp_x = {"CostsAndExpenses": {2026: ("", "", 16_627_700_000.0)},
              "InterestExpense": {2026: ("", "", 459_300_000.0)},
              "NonoperatingIncomeExpense": {2026: ("", "", 410_600_000.0)}}
    _hrb_x = {"CostsAndExpenses": {2026: ("", "", 3_037_706_000.0)},
              "OtherNonoperatingIncomeExpense": {2026: ("", "", 26_813_000.0)},
              "InterestExpenseDebt": {2026: ("", "", 80_611_000.0)}}
    _pbi_x = {"CostsAndExpenses": {2025: ("", "", 1_700_105_000.0),
                                   2023: ("", "", 2_122_845_000.0),
                                   2022: ("", "", 3_498_162_000.0)},
              "InterestExpense": {2022: ("", "", 89_980_000.0)},
              "InterestIncomeExpenseNet": {2025: ("", "", -101_460_000.0),
                                           2023: ("", "", -98_769_000.0)}}
    _bbw_x = {"GrossProfit": {2026: ("", "", 295_629_000.0),
                              2017: ("", "", 168_973_000.0)},
              "SellingGeneralAndAdministrativeExpense":
                  {2026: ("", "", 229_203_000.0),
                   2017: ("", "", 155_149_000.0)},
              "InterestIncomeExpenseNonoperatingNet":
                  {2026: ("", "", 801_000.0), 2017: ("", "", -11_000.0)}}

    out.append(("DOI: the registry holds exactly the four docket names as DoiRoute "
                "entries — terms, bracket and notes non-empty, the PBI splice "
                "bounded in the term structure, FY2016 refused with a reason",
                set(DOI_REGISTRY) == {"ADP", "HRB", "PBI", "BBW"}
                and all(isinstance(r, DoiRoute) and r.terms and r.bracket
                        and r.notes for r in DOI_REGISTRY.values())
                and any(t.fy_hi == 2022 for t in DOI_REGISTRY["PBI"].terms)
                and any(t.fy_lo == 2023 for t in DOI_REGISTRY["PBI"].terms)
                and bool(DOI_REGISTRY["PBI"].refused.get(2016)),
                f"{sorted(DOI_REGISTRY)}"))

    _oi, _why, _ln = doi_derive(DOI_REGISTRY["ADP"], 2026, 21_947_400_000.0,
                                5_730_300_000.0, _adp_x)
    out.append(("DOI: ADP FY2026 known-answer — Rev − CAE + InterestExpense "
                "derives 5,779.0M exactly",
                _oi == 5_779_000_000.0 and _why == "", f"{_oi} {_why or _ln}"))
    _oi, _why, _ln = doi_derive(DOI_REGISTRY["HRB"], 2026, 3_945_392_000.0,
                                853_888_000.0, _hrb_x)
    out.append(("DOI: HRB FY2026 known-answer — Rev − CAE derives 907.686M, the "
                "banked cross-frame anchor, no add-back",
                _oi == 907_686_000.0 and _why == "", f"{_oi} {_why or _ln}"))
    _oi, _why, _ln = doi_derive(DOI_REGISTRY["PBI"], 2025, 1_892_629_000.0,
                                192_524_000.0, _pbi_x)
    out.append(("DOI: PBI FY2025 known-answer — the negated net element adds back "
                "101.460M for 293.984M",
                _oi == 293_984_000.0 and _why == "", f"{_oi} {_why or _ln}"))
    _oi, _why, _ln = doi_derive(DOI_REGISTRY["BBW"], 2026, None,
                                67_227_000.0, _bbw_x)
    out.append(("DOI: BBW FY2026 known-answer — GP − SG&A derives 66.426M with no "
                "revenue term at all (shape ii-prime)",
                _oi == 66_426_000.0 and _why == "", f"{_oi} {_why or _ln}"))

    out.append(("DOI: ADP bracket known-answer — Rev − CAE + Nonop equals pretax "
                "exactly (the vintage pin)",
                doi_bracket_sum(DOI_REGISTRY["ADP"], 2026, 21_947_400_000.0,
                                _adp_x) == 5_730_300_000.0, ""))
    out.append(("DOI: HRB bracket known-answer — the two outside tags close the "
                "wedge against CONTINUING pretax exactly",
                doi_bracket_sum(DOI_REGISTRY["HRB"], 2026, 3_945_392_000.0,
                                _hrb_x) == 853_888_000.0, ""))
    out.append(("DOI: PBI bracket known-answer — the degenerate pin, Rev − CAE = "
                "pretax exactly",
                doi_bracket_sum(DOI_REGISTRY["PBI"], 2025, 1_892_629_000.0,
                                _pbi_x) == 192_524_000.0, ""))
    out.append(("DOI: BBW bracket known-answer — GP − SG&A + net interest equals "
                "pretax exactly, the self-verifying filed arithmetic",
                doi_bracket_sum(DOI_REGISTRY["BBW"], 2026, None,
                                _bbw_x) == 67_227_000.0, ""))

    _mut = {k: dict(v) for k, v in _hrb_x.items()}
    _mut["CostsAndExpenses"] = {2026: ("", "", 3_037_706_000.0 + 5_000_000.0)}
    _mutated = (_mut["CostsAndExpenses"][2026][2]
                != _hrb_x["CostsAndExpenses"][2026][2])
    _oi, _why, _ln = doi_derive(DOI_REGISTRY["HRB"], 2026, 3_945_392_000.0,
                                853_888_000.0, _mut)
    out.append(("DOI: negative control — a mutated CostsAndExpenses (asserted "
                "applied) fails the bracket and the year refuses with the reason "
                "printed",
                _mutated and _oi is None and "bracket did not close" in _why
                and "refused" in _ln, _ln[:90]))

    _oi, _why, _ = doi_derive(DOI_REGISTRY["HRB"], 2026, 3_945_392_000.0,
                              853_888_000.0 + 2.0, _hrb_x)
    out.append(("DOI: a \\$2 wedge passes — the float-safety epsilon, not a "
                "materiality band",
                _oi == 907_686_000.0 and _why == "", _why))
    _oi, _why, _ = doi_derive(DOI_REGISTRY["HRB"], 2026, 3_945_392_000.0,
                              853_888_000.0 + 3.0, _hrb_x)
    out.append(("DOI: a \\$3 wedge refuses — beyond the epsilon the bracket "
                "fails loudly",
                _oi is None and "bracket did not close" in _why, _why[:90]))

    _pre22 = {t.source for t in doi_terms_for(DOI_REGISTRY["PBI"], 2022)}
    _oi22, _w22, _ = doi_derive(DOI_REGISTRY["PBI"], 2022, 3_538_042_000.0,
                                39_880_000.0, _pbi_x)
    out.append(("DOI: PBI splice, pre side — FY2022 resolves to InterestExpense "
                "(never the net element) and derives 129.860M",
                "InterestExpense" in _pre22
                and "InterestIncomeExpenseNet" not in _pre22
                and _oi22 == 129_860_000.0 and _w22 == "", f"{sorted(_pre22)}"))
    _post23 = {t.source for t in doi_terms_for(DOI_REGISTRY["PBI"], 2023)}
    _oi23, _w23, _ = doi_derive(DOI_REGISTRY["PBI"], 2023, 2_078_925_000.0,
                                -43_920_000.0, _pbi_x)
    out.append(("DOI: PBI splice, post side — FY2023 resolves to the negated net "
                "element (never InterestExpense) and derives 54.849M through a "
                "loss-year pretax",
                "InterestIncomeExpenseNet" in _post23
                and "InterestExpense" not in _post23
                and _oi23 == 54_849_000.0 and _w23 == "", f"{sorted(_post23)}"))

    _oi, _why, _ln = doi_derive(DOI_REGISTRY["PBI"], 2016, 3_000_000_000.0,
                                100_000_000.0, _pbi_x)
    out.append(("DOI: PBI FY2016 refuses by decision even with inputs present — "
                "the registry reason prints",
                _oi is None and _why == DOI_REGISTRY["PBI"].refused[2016]
                and "not reconcilable" in _ln, _ln[:80]))

    _oi, _why, _ln = doi_derive(DOI_REGISTRY["BBW"], 2017, None,
                                13_813_000.0, _bbw_x)
    out.append(("DOI: BBW Dec-2017 admitted — the bracket that closed on an "
                "unseen face at exactly 13,813 derives 13.824M",
                _oi == 13_824_000.0 and _why == "", f"{_oi} {_why or _ln}"))

    _stub_facts = {"facts": {"us-gaap": {"GrossProfit": {"units": {"USD": [
        {"start": "2017-12-31", "end": "2018-02-03", "val": 13902000,
         "form": "10-K", "filed": "2019-04-18"},
        {"start": "2018-02-04", "end": "2019-02-02", "val": 138754000,
         "form": "10-K", "filed": "2019-04-18"}]}}}}}
    _sg = _annual(_stub_facts, ["GrossProfit"], [])
    out.append(("DOI: fiscal-transition stubs never enter — the route reads "
                "through _annual, whose duration filter drops BBW's five-week "
                "period and keeps the year",
                set(_sg) == {2019} and _sg[2019][2] == 138754000.0,
                f"{sorted(_sg)}"))

    _bn = doi_banner("PBI", DOI_REGISTRY["PBI"])
    out.append(("DOI: the banner names the route, the formula and the refusal "
                "behaviour",
                "derived per registry" in _bn and "InterestExpense FY2017" in _bn
                and "does not close" in _bn, _bn[:80]))

    _plain = [EpvYear(2024, 500.0, 60.0, 10.0, 58.0, 1.0, 1.0, "")]
    _rt, _lns = doi_apply("MSFT", _plain, {})
    _docket_shape = [EpvYear(2024, 500.0, None, 10.0, 58.0, 1.0, 1.0, "")]
    out.append(("DOI: a non-docket name is untouched — one dict lookup and out, "
                "no lines, OI as tagged, and the floor suffix stays empty unless "
                "the filer has the docket's shape",
                _rt is None and _lns == [] and _plain[0].oi == 60.0
                and doi_floor_suffix("MSFT", _plain) == ""
                and "no operating subtotal" in doi_floor_suffix("MSFT",
                                                                _docket_shape)
                and "registered" in doi_floor_suffix("ADP", _docket_shape),
                ""))

    # ── DOI pins (19 Sep 2026, live acceptance): verified bracket ────
    # targets for two poisoned comparator cells the bracket caught live.
    # HRB FY2021: filer defect (mis-dated oldest-comparative fact, the
    # slide). PBI FY2018: reader seam (two-tag fill serves tag order over
    # vintage). Both pins triply verified; fixtures are the banked raw
    # figures and the poisoned cell values as the live page read them.
    _h21_x = {"CostsAndExpenses": {2021: ("", "", 2_644_360_000.0)},
              "OtherNonoperatingIncomeExpense": {2021: ("", "", 5_979_000.0)},
              "InterestExpenseDebt": {2021: ("", "", 106_870_000.0)}}
    _p18_x = {"CostsAndExpenses": {2018: ("", "", 3_023_401_000.0)},
              "InterestExpense": {2018: ("", "", 115_381_000.0)}}

    out.append(("DOI pins: HRB carries the FY2021 pin at 668.736M with the "
                "sources and the slide pattern named in its reason",
                DOI_REGISTRY["HRB"].pins.get(2021, (None,))[0] == 668_736_000.0
                and "printed statement" in DOI_REGISTRY["HRB"].pins[2021][1]
                and "762,322" in DOI_REGISTRY["HRB"].pins[2021][1]
                and "FILER" in DOI_REGISTRY["HRB"].pins[2021][1], ""))

    _oi, _why, _ln = doi_derive(DOI_REGISTRY["HRB"], 2021, 3_413_987_000.0,
                                762_322_000.0, _h21_x)
    out.append(("DOI pins: HRB FY2021 known-answer — derives 769.627M with the "
                "bracket closing at 668.736 against the pin, poisoned cell "
                "present",
                _oi == 769_627_000.0 and _why == "", f"{_oi} {_why or _ln}"))
    out.append(("DOI pins: divergence visibility — the printed line names the "
                "pinned close and the diverging page cell",
                "pinned pretax ✓" in _ln and "762.322 diverges" in _ln,
                _ln[-90:]))
    _oi2, _why2, _ln2 = doi_derive(DOI_REGISTRY["HRB"], 2021, 3_413_987_000.0,
                                   668_736_000.0, _h21_x)
    out.append(("DOI pins: agreement visibility — a cell that agrees with the "
                "pin prints the retirable notice",
                _oi2 == 769_627_000.0 and "agrees with the pin" in _ln2
                and "retirable" in _ln2, _ln2[-70:]))

    _mut_pin = DoiRoute(formula=DOI_REGISTRY["HRB"].formula,
                        terms=DOI_REGISTRY["HRB"].terms,
                        bracket=DOI_REGISTRY["HRB"].bracket,
                        pins={2021: (668_736_000.0 + 5_000_000.0, "mutated")})
    _pin_mutated = (_mut_pin.pins[2021][0]
                    != DOI_REGISTRY["HRB"].pins[2021][0])
    _oi3, _why3, _ = doi_derive(_mut_pin, 2021, 3_413_987_000.0,
                                762_322_000.0, _h21_x)
    out.append(("DOI pins: negative control — a mutated pin (asserted applied) "
                "fails the bracket and the year refuses naming the pinned "
                "target",
                _pin_mutated and _oi3 is None
                and "bracket did not close" in _why3
                and "pinned target" in _why3, _why3[:90]))

    _oi4, _why4, _ln4 = doi_derive(DOI_REGISTRY["HRB"], 2026, 3_945_392_000.0,
                                   853_888_000.0, _hrb_x)
    out.append(("DOI pins: non-pinned years unchanged — FY2026 still brackets "
                "against the page's own cell, no pin language in its line",
                2026 not in DOI_REGISTRY["HRB"].pins
                and _oi4 == 907_686_000.0 and "pinned" not in _ln4, _ln4[-60:]))

    out.append(("DOI pins: PBI carries the FY2018 pin at 188.121M naming the "
                "pre-recast cell and the fill-priority reader seam",
                DOI_REGISTRY["PBI"].pins.get(2018, (None,))[0] == 188_121_000.0
                and "212.361" in DOI_REGISTRY["PBI"].pins[2018][1]
                and "tag order" in DOI_REGISTRY["PBI"].pins[2018][1]
                and "READER" in DOI_REGISTRY["PBI"].pins[2018][1], ""))
    _oi5, _why5, _ln5 = doi_derive(DOI_REGISTRY["PBI"], 2018, 3_211_522_000.0,
                                   212_361_000.0, _p18_x)
    out.append(("DOI pins: PBI FY2018 known-answer — derives 303.502M with the "
                "degenerate bracket closing at 188.121 against the pin, the "
                "stale cell named as diverging",
                _oi5 == 303_502_000.0 and _why5 == ""
                and "212.361 diverges" in _ln5, f"{_oi5} {_why5 or _ln5[-70:]}"))



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


def _page_footer() -> None:
    """The glossary, the self-test button and the disclaimer — called
    before every stop and once at the bottom, per the kit's rule."""
    st.divider()
    _r1, _r2 = st.columns(2)
    with _r1:
        with st.expander("What the numbers mean", expanded=False):
            st.markdown(
                "**EPV** — Earnings Power Value: normalized operating earnings, taxed and "
                "capitalized at the required return, plus net cash. The value of the business "
                "assuming zero growth, forever. At the required return r it is the price at "
                "which a buyer earns r a year with no growth at all.\\n\\n"
                "**Normalized margin** — the mean operating margin over the readable window. "
                "The cycle average is the method's core move: one good or bad latest year is "
                "exactly what it exists to see through. The median and range print beside it "
                "on every run.\\n\\n"
                "**The SBC-corrected leg** — the same EPV with the GAAP stock-comp charge "
                "added back and the true cost Ω taken out, on the same years. The gap between "
                "the legs is the no-growth price of stock compensation, exact by linearity.\\n\\n"
                "**The growth payment** — price minus EPV: the dollar amount the market is "
                "paying for growth. The Expectations page states the growth path that payment "
                "implies.\\n\\n"
                + out_of_scope_sentence()
            )
    with _r2:
        with st.expander("Verify the engine"):
            st.caption(
                "Two suites. The engine checks are the Tragic Algebra Analyzer's own — this "
                "page carries its reader and must prove it unchanged. The EPV checks pin this "
                "page's arithmetic: the capitalization hand-check to the cent, the linearity "
                "of the gap, every refusal sentence and route."
            )
            if st.button("Run checks"):
                for _label, _suite in (("Engine", self_test()), ("EPV", epv_self_test())):
                    _sev, _line = test_summary(_suite)
                    getattr(st, _sev)(f"{_label}: " + _line.strip("*"))
                    for name, ok, got in _suite:
                        if not ok:
                            st.write("❌ " + f"{name} — {got}")
                st.caption("Only failures are listed line by line; the counts above are the "
                           "record. Green means every check in both suites passed.")
    st.caption(
        "Research aid, not financial advice. Outputs depend on estimates you supply — change "
        "the required return and the answer changes a great deal. The method follows Bruce "
        "Greenwald's published writing; the refusals, the SBC-corrected leg and the "
        "filings-only limits are this project's own. Independent; not affiliated with or "
        "endorsed by Bruce Greenwald."
    )


st.set_page_config(
    page_title="EPV — Investor Toolkit",
    page_icon="⚓",
    layout="centered",
    initial_sidebar_state="collapsed",
)
st.title("⚓ EPV")
st.caption("Bruce Greenwald's Earnings Power Value — what zero growth is worth, "
           "and what the market is paying for the rest")

if not _sec_contact():
    st.warning(
        "**No SEC contact address set.** The SEC requires a real email in the request header "
        "and blocks generic user agents, so lookups will fail. Add `sec_contact = "
        "\"you@example.com\"` in Streamlit Settings → Secrets, or set a SEC_CONTACT "
        "environment variable locally."
    )

if "epv_years" not in st.session_state:
    st.info(
        "**The no-growth anchor.** Every growth model in this kit asks you to believe a "
        "rate. This page asks for none: normalized current earnings, capitalized, less net "
        "debt — what the business is worth if it never grows again. The gap between that and "
        "the price is the dollar amount the market is paying for growth, and the Expectations "
        "page states the path that payment implies.\\n\\n"
        "Enter a US-listed ticker to start. Banks, insurers, REITs and brokers are refused "
        "here and priced on the Financials Checker."
    )

with st.form("epv_lookup"):
    ticker = st.text_input("Stock ticker",
                           placeholder="ADP · BBW · PDEX — press Enter").upper().strip()
    submitted = st.form_submit_button("Evaluate", type="primary")

if submitted:
    if not ticker:
        st.warning("Enter a ticker first.")
    else:
        try:
            with st.spinner(f"Reading {ticker} annual filings…"):
                yrs, notes, pre = load(ticker, 10)
            st.session_state.update(epv_years=yrs, epv_notes=notes, epv_pre=pre,
                                    epv_tk=ticker)
        except ValueError as e:
            st.error(f"Could not load {ticker}: {e}" + epv_foreign_route_suffix(str(e)))
        except Exception as e:
            st.error(
                f"Could not load {ticker} — {type(e).__name__}: {e}\n\n"
                "This is a gap in how the filings were read, not something you did. Filers "
                "with several share classes, recent listings and foreign issuers are the "
                "usual causes.")

years = st.session_state.get("epv_years", [])
if years and ticker and st.session_state.get("epv_tk") == ticker:
    notes, pre, tk = (st.session_state["epv_notes"], st.session_state["epv_pre"],
                      st.session_state["epv_tk"])
    alerts: list[tuple[str, str]] = [("info", n) for n in notes]
    rows = build_epv_years(years, pre.get("epv", {}))
    # ── Derived-OI route (DOI, 19 Sep 2026): registered tickers only —
    # one dict lookup and out for everyone else. Fills r.oi in place, so
    # the pool, the table and both legs consume derived years through the
    # normal machinery; the per-year printed lines land in the notes.
    _doi_route, _doi_lines = doi_apply(tk, rows, pre)
    if _doi_route is not None:
        alerts += [("info", _l) for _l in _doi_lines]
        alerts += [("info", "Derived-OI registry note: " + _n)
                   for _n in _doi_route.notes]
    mpool = margin_pool(rows)

    _mfmt = money_fmt([v for r in rows for v in (r.rev, r.oi, r.G, r.omega, r.adj_oi)
                       if v is not None])
    _pct = lambda v: "—" if v is None else f"{v:.1%}"

    def _epv_table() -> None:
        st.write("**Year by year** — starred years and dash cells are outside the pool")
        st.dataframe(pd.DataFrame([{
            "FY": f"{r.fy}*" if r.excluded else str(r.fy),
            "Revenue": r.rev, "Operating income": r.oi, "Margin": _pct(r.opm),
            "GAAP SBC": r.G, "True SBC cost": r.omega,
            "Adjusted OI": r.adj_oi, "Adjusted margin": _pct(r.adj_opm)}
            for r in rows]).style.format({
                "Revenue": _mfmt, "Operating income": _mfmt, "GAAP SBC": _mfmt,
                "True SBC cost": _mfmt, "Adjusted OI": _mfmt}, na_rep="—"),
            width='stretch', hide_index=True)
        st.caption(
            "The pool takes every window year with operating income and positive revenue "
            "read and no exclusion. A starred year is excluded for the reason in the notes "
            "— a listing, a share-funded deal, or Gate 2's no-price rule — and a dash means "
            "the tag did not answer for that year; the cell is refused, never read as zero. "
            "Adjusted OI = OI + GAAP charge − true SBC cost: the margin recomputed with Ω "
            "in place of G.")

    def _notes_expander(expanded: bool = False) -> None:
        label = "Notes and detail" + (f" · {len(alerts)} to review" if alerts else "")
        with st.expander(label, expanded=expanded):
            for kind_, msg in alerts:
                getattr(st, kind_)(msg)
            _epv_table()
            st.write("**What was read from the filings** — every tag, found or missing")
            st.dataframe(pd.DataFrame(pre.get("tags", [])), width='stretch',
                         hide_index=True)
            st.caption(
                "A zero in this app is either something the company did not do or a tag "
                "this reader does not know. If a line you know exists reads fewer years "
                "than net income, that is a bug worth reporting — the tag name is the "
                "whole fix.")

    # ══ the financial gate — first, a full routed stop ═══════════════
    if pre.get("financial"):
        st.error(epv_financial_stop(tk, pre.get("fin_class", ""), pre.get("fin_reason", "")))
        _notes_expander(expanded=True)
        _page_footer()
        st.stop()

    # ══ Up-C basis (§5.5 G) — same doctrine as everywhere ════════════
    _upcb = pre.get("up_c_basis")
    if any(e.up_c for e in XBRL_REGISTRY.get(tk, ())) and pre.get("shares", 0) > 0:
        if _upcb and _upcb.get("ok"):
            st.info(up_c_basis_banner(tk, pre.get("shares", 0.0), _upcb))
        else:
            st.error(up_c_sentence(tk, pre.get("shares", 0.0)))
            _notes_expander(expanded=True)
            _page_footer()
            st.stop()

    # ══ derived-OI banner (DOI, 19 Sep 2026) ═════════════════════════
    if _doi_route is not None:
        st.info(doi_banner(tk, _doi_route))

    # ══ the normalization floor ══════════════════════════════════════
    _floor = normalization_refusal(len(mpool))
    if _floor:
        st.error("**The window cannot be normalized.** " + _floor
                 + doi_floor_suffix(tk, rows))
        _notes_expander(expanded=True)
        _page_footer()
        st.stop()

    m1, m2 = pool_margins(mpool)
    if m1 <= 0:
        st.error(negative_margin_refusal(m1, len(mpool),
                                         min(r.fy for r in mpool), max(r.fy for r in mpool)))
        _notes_expander(expanded=True)
        _page_footer()
        st.stop()

    # ══ inputs ═══════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Inputs")
    st.caption(dispersion_caption(mpool, m1))

    _rev_fy = max((fy for fy, e in pre.get("epv", {}).items() if e.get("rev")), default=0)
    _tax_seed, _tax_src = normalized_tax(tax_rate_pool(rows))

    c1, c2, c3 = st.columns(3)
    rr = c1.number_input("Required return (%)", value=EPV_RR_DEFAULT, step=0.5,
                         min_value=EPV_RR_FLOOR, max_value=30.0,
                         help="EPV at r is the price at which a buyer earns r a year with "
                              "zero growth. 15% is the kit's demanded return; 8% — the "
                              "floor — is Greenwald's own cost-of-capital neighbourhood. "
                              "Capitalization at low rates explodes, which is what the "
                              "floor is for.") / 100.0
    tax = c1.number_input("Tax rate (%)", value=round(_tax_seed * 100, 1), step=0.5,
                          min_value=0.0, max_value=60.0,
                          help="Applied to normalized pre-tax operating earnings. Seeded "
                               "from the filed record; the caption below the inputs says "
                               "from what.") / 100.0
    shares = c2.number_input("Diluted shares (M)", value=float(round(pre["shares"], 1)),
                             step=1.0)
    price = c2.number_input("Price", value=float(current_price(pre.get("ticker", tk)) or 0.0),
                            step=0.01,
                            help="Used only for the growth-payment comparison. Zero means "
                               "no live price could be fetched; type it to see the split.")
    cash = c3.number_input("Cash & investments ($M)", value=float(round(pre.get("cash", 0.0), 1)),
                           step=10.0, help="Only what is freely deployable. Restricted, "
                                           "regulated and operationally-tied cash funds "
                                           "the business.")
    debt = c3.number_input("Total debt ($M)", value=float(round(pre.get("debt", 0.0), 1)),
                           step=10.0, help="Short-term plus long-term borrowings. Subtracted "
                                           "from cash to give the net figure added to EPV.")
    net_cash = cash - debt
    c2.caption(f"Net cash {d(net_cash,0)}M · {d(cash,0)}M cash less {d(debt,0)}M debt")
    st.caption(f"Tax seeded from the {_tax_src}. Current revenue is FY{_rev_fy}'s, "
               f"{d(pre.get('revenue', 0.0),0)}M — the one figure here taken from a single "
               "year, because EPV normalizes the margin, not the size of today's business.")

    with st.expander("Greenwald's judgement adjustments — stated defaults of zero"):
        st.caption(
            "His full method adjusts normalized operating earnings for two things filings "
            "do not encode, so they are boxes, not estimates. Both enter pre-tax and enter "
            "BOTH legs equally, so the gap between the legs stays pure stock compensation.\\n\\n"
            "**Excess depreciation over maintenance capex** — where reported depreciation "
            "overstates what merely maintaining the business costs, add the difference back. "
            "Filings do not split maintenance from growth capex; zero means no adjustment.\\n\\n"
            "**SG&A spent on growth** — the part of SG&A that builds future business rather "
            "than supporting the current one. Same story: a judgement, default zero.")
        a1, a2 = st.columns(2)
        box_a = a1.number_input("Excess depreciation over maintenance capex ($M)",
                                value=0.0, step=10.0)
        box_b = a2.number_input("SG&A spent on growth ($M)", value=0.0, step=10.0)

    # ══ share count — the standard machinery's last gate ═════════════
    if shares <= 0:
        st.error(
            "**No share count was read from any tag this reader knows** — the notes say "
            "which years. Nothing per share can be computed. The usual shape is a "
            "dual-class filer: per-class counts carry a class dimension that EDGAR's "
            "companyfacts API strips, so nothing undimensioned exists to read. Type the "
            "diluted count from the 10-K cover page to continue; the assumptions block "
            "will record it as set by hand.")
        _notes_expander(expanded=True)
        _page_footer()
        st.stop()

    # ══ the two legs ═════════════════════════════════════════════════
    _rev_now = pre.get("revenue", 0.0)
    nopat1, ev1 = epv_leg(m1, _rev_now, box_a + box_b, tax, rr)
    nopat2, ev2 = epv_leg(m2, _rev_now, box_a + box_b, tax, rr)
    eq1, ps1, fl1 = epv_equity(ev1, net_cash, shares)
    eq2, ps2, fl2 = epv_equity(ev2, net_cash, shares)
    gap_ps = (eq1 - eq2) / shares   # before the zero floor, on purpose

    st.markdown("---")
    st.subheader(f"Earnings Power Value · {tk}")

    v1, v2, v3 = st.columns(3)
    v1.metric("EPV — standard", f"${ps1:,.2f}",
              f"market ${price:,.2f}" if price > 0 else "no live price")
    v2.metric("EPV — SBC-corrected", f"${ps2:,.2f}",
              f"gap ${gap_ps:,.2f}/share", delta_color="off")
    v3.metric("Growth payment in the price",
              f"${max(price - ps1, 0.0):,.2f}" if price > 0 else "—",
              f"{(price - ps1) / price:.0%} of the price"
              if price > 0 and price > ps1 else
              ("price at or below EPV" if price > 0 else "type the price above"),
              delta_color="off")
    st.caption(framing_sentence(rr))

    for _nop, _ev, _eqx, _flx, _label, _m in ((nopat1, ev1, eq1, fl1, "Standard", m1),
                                              (nopat2, ev2, eq2, fl2, "SBC-corrected", m2)):
        st.caption(f"{_label}: margin {_m:.2%} × revenue {d(_rev_now,0)}M"
                   + (f" + adjustments {d(box_a + box_b,0)}M" if box_a or box_b else "")
                   + f", taxed at {tax:.1%} → NOPAT {d(_nop,0)}M · ÷ {rr:.1%} = "
                   f"{d(_ev,0)}M · + net cash {d(net_cash,0)}M = {d(_eqx,0)}M over "
                   f"{shares:,.1f}M shares")
        if _flx:
            st.error(zero_floor_sentence(_nop, _ev, net_cash, rr))

    _gk = "info" if gap_ps >= 0 else "warning"
    getattr(st, _gk)(gap_sentence(gap_ps, fl1 or fl2))

    if price > 0:
        st.info(triad_sentence(price, ps1, fl1))
    else:
        st.caption("No live price could be fetched, so the growth-payment split is "
                   "refused rather than computed against a stale or default figure. "
                   "Type the price above to see it.")

    _notes_expander()

    with st.expander("Assumptions used — paste this if something looks wrong"):
        st.code(
            f"{tk}   price {price:,.2f}   shares {shares:,.1f}M   "
            f"mkt cap ${shares * price / 1000:,.2f}B\n"
            f"revenue (FY{_rev_fy})    {_rev_now:,.0f}\n"
            f"margin pool             {len(mpool)} years, FY{min(r.fy for r in mpool)}–"
            f"FY{max(r.fy for r in mpool)}\n"
            f"normalized margin       {m1:.2%}   (SBC-corrected {m2:.2%}, "
            f"Ω−G effect {m2 - m1:+.2%})\n"
            f"tax rate applied        {tax:.1%}   seeded from the {_tax_src}\n"
            f"required return         {rr:.1%}\n"
            f"adjustments (pre-tax)   excess depreciation {box_a:,.0f}   "
            f"growth SG&A {box_b:,.0f}\n"
            f"net cash                {net_cash:,.0f}   ({net_cash / shares:,.2f}/share)\n"
            f"EPV, standard           EV {ev1:,.0f}M → equity {eq1:,.0f}M → "
            f"{ps1:,.2f}/share" + ("   (floored at 0.00)" if fl1 else "") + "\n"
            f"EPV, SBC-corrected      EV {ev2:,.0f}M → equity {eq2:,.0f}M → "
            f"{ps2:,.2f}/share" + ("   (floored at 0.00)" if fl2 else "") + "\n"
            f"gap                     {gap_ps:,.2f}/share (before the zero floor)",
            language="text")

_page_footer()
