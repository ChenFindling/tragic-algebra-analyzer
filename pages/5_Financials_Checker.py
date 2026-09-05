"""
Financials Checker
==================
A page for the three structures the other pages refuse: insurers, banks
and REITs. It answers one question — what is this balance sheet worth at
15% — from the numbers the filing states: tangible common equity, the
return on it, what is paid out, and for an insurer the combined ratio and
float, for a bank net interest income and the efficiency ratio, for a
REIT Nareit FFO and the dividend.

THREE PARTS, AND WHICH IS WHOSE
-------------------------------
1. The stock-comp adjustment is Burry's Tragic Algebra on tool 1's engine,
   unchanged. Ω does not care what the company sells.
2. Everything after it is this app's: the class gate (which SIC ranges are
   this page's, which go back to tool 1, which are refused — every other
   page's banner follows it), the class tables, the return on tangible
   common equity, growth derived from what is kept, a fade to the lower of
   the company's own two medians, and an exit at the price that hands the
   next buyer the same 15%. Burry publishes no method for float businesses
   beyond naming float as productive capital; nothing here is his.
3. The refusals are this app's, and they fire often. The rule that does
   not move: the page never prints a number it cannot stand behind.

Net cash, ROIC and the AI-moat tiers do not appear on this page. A bank's
cash is its inventory and an insurer's investments back its policies.

The reader below is tool 1's (the 115-check version of 31 Aug 2026),
copied verbatim (a Streamlit page cannot be imported without executing its
UI) with the lines this page needs added and marked: dividends, preferred
dividends, the insurer, bank and REIT income lines, a per-share dividend
reader, and the balance-sheet lines in FIN_BALANCE. Pages 1 and 2 are not
touched. The self-test pins the ported engine against tool 1's Alphabet
figures.

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
    # ── Added for this page only (1 Sep 2026). Read SIGNED, not abs() —
    # dividends come off the cash-flow statement negative and are made
    # positive where they are used; a provision release is negative and
    # stays so; a loss on sale is a negative gain and is added back to FFO.
    # Every line is optional for the reader and refused, cell by cell,
    # where it did not answer.
    "DIV":   (["PaymentsOfDividendsCommonStock", "PaymentsOfDividends",
               "PaymentsOfOrdinaryDividends"], []),
    "PDIV":  (["PreferredStockDividendsIncomeStatementImpact", "DividendsPreferredStock",
               "PreferredStockDividendsAndOtherAdjustments"], []),
    # insurers
    "NEP":   (["PremiumsEarnedNet", "PremiumsEarnedNetPropertyAndCasualty",
               "PremiumsEarnedNetLife"], []),
    "LOSS":  (["PolicyholderBenefitsAndClaimsIncurredNet",
               "IncurredClaimsPropertyCasualtyAndLiability"], []),
    "DACX":  (["DeferredPolicyAcquisitionCostAmortizationExpense",
               "AmortizationOfDeferredPolicyAcquisitionCosts"], []),
    "UWO":   (["OtherUnderwritingExpense"], []),
    "UWT":   (["PolicyAcquisitionCostsAndOtherUnderwritingExpense",
               "UnderwritingAcquisitionAndInsuranceExpenses"], []),
    "NII":   (["NetInvestmentIncome", "InvestmentIncomeNet",
               "InvestmentIncomeInterestAndDividend"], []),
    "PTX":   (["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"], []),
    "TAX":   (["IncomeTaxExpenseBenefit"], []),
    # banks
    "BNII":  (["InterestIncomeExpenseNet"], []),
    "PROV":  (["ProvisionForLoanLeaseAndOtherLosses", "ProvisionForLoanLossesExpensed",
               "ProvisionForLoanAndLeaseLosses", "ProvisionForCreditLosses"], []),
    "NONII": (["NoninterestIncome"], []),
    "NONIX": (["NoninterestExpense"], []),
    # REITs
    "DA":    (["DepreciationAndAmortization", "DepreciationDepletionAndAmortization",
               "DepreciationAmortizationAndAccretionNet", "Depreciation"], []),
    "RGAIN": (["GainsLossesOnSalesOfInvestmentRealEstate",
               "GainLossOnSaleOfPropertiesNetOfApplicableIncomeTaxes", "GainLossOnSaleOfProperties",
               "GainLossOnDispositionOfAssets1", "GainLossOnDispositionOfAssets"], []),
    "RIMP":  (["ImpairmentOfRealEstate", "ImpairmentOfLongLivedAssetsHeldForUse",
               "AssetImpairmentCharges"], []),
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
             "PDIV", "NEP", "LOSS", "DACX", "UWO", "UWT", "NII", "PTX", "TAX", "BNII", "PROV",
             "NONII", "NONIX", "DA", "RGAIN", "RIMP"}   # the lines this page adds


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
    # Without an entry here the panel printed the raw dictionary key "SHD"
    # beside a "— Shares: diluted average" row and looked like a duplicate.
    # They are two different reads of the same idea: this one is unfilled and
    # feeds the dual-class check, the row below is filled across three tags
    # and feeds the share ladder. Named for what it is used for.
    "SHD": "Diluted shares — dual-class check",
    # this page's lines
    "PDIV": "Preferred dividends", "NEP": "Net premiums earned", "LOSS": "Losses & LAE",
    "DACX": "Acquisition-cost amortisation", "UWO": "Other underwriting expense",
    "UWT": "Underwriting expense (total)", "NII": "Net investment income",
    "PTX": "Pre-tax income", "TAX": "Income tax", "BNII": "Net interest income",
    "PROV": "Provision for credit losses", "NONII": "Noninterest income",
    "NONIX": "Noninterest expense", "DA": "Depreciation & amortisation",
    "RGAIN": "Gains on sale of real estate", "RIMP": "Real-estate impairments",
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
    # ── Added for this page: the class lines by fiscal year, in $M, for the
    # same window as `years`, signed; absent means the tag did not answer for
    # that year and the page refuses the cell rather than reading zero. The
    # balance lines in FIN_BALANCE are read the way BALANCE is above — one
    # tag answers for a line, prefer_recent on — and kept per year, with the
    # coverage captured from the same read that the panel reports.
    _signed = lambda k, fy: (series[k][fy][2] / 1e6) if fy in series.get(k, {}) else None
    _fin = {fy: {k.lower(): _signed(k, fy) for k in ("DIV", "PDIV", "NEP", "LOSS", "DACX", "UWO", "UWT",
                                                      "NII", "PTX", "TAX", "BNII", "PROV", "NONII",
                                                      "NONIX", "DA", "RGAIN", "RIMP")}
            for fy in fys}
    _fin_bal: dict[str, dict[int, float]] = {}
    _fin_src: dict[str, list[str]] = {}
    _fin_n: dict[str, int] = {}
    _fin_fy: dict[str, str] = {}
    for _k, _ks in FIN_BALANCE.items():
        _s: list[str] = []
        _d = _instant(facts, _ks, "USD", _s, None, prefer_recent=True)
        _fin_bal[_k] = {fy: v / 1e6 for fy, v in _d.items()}
        _fin_src[_k], _fin_n[_k], _fin_fy[_k] = _s, len(_d), _latest_fy(_d)
    if _fin_src.get("eq") and _fin_src["eq"][0] != "StockholdersEquity":
        notes.append("Shareholders' equity was read from the tag that includes non-controlling "
                     "interests, because the parent-only tag stops earlier or is absent. Tangible "
                     "common equity here is the consolidated figure, so a filer with real minority "
                     "holders reads a little high.")
    _dps_src: list[str] = []
    _dps = _per_share(facts, ["CommonStockDividendsPerShareDeclared",
                              "CommonStockDividendsPerShareCashPaid"], _dps_src)
    _shares_by_fy = {fy: shares_out[fy] / 1e6 for fy in fys if fy in shares_out}
    # Weighted-average diluted shares per year, scaled for a post-filing
    # split like `diluted` above; split_adjust does not restate this series,
    # so a filer that split inside the window has old years on the old basis.
    _wavg_by_fy = {fy: v / 1e6 * _split_factor for fy, v in _wv.items() if fy in fys}
    _cls, _cls_reason = financial_class(sic, facts)
    tags = tags + [
        {"Line": f"— {name}", "Years read": _fin_n.get(k, 0), "Latest year": _fin_fy.get(k, "—"),
         "XBRL tag": " + ".join(_fin_src.get(k, [])) or "—",
         "Status": "read" if _fin_src.get(k) else "none of the tags this reader knows are in the filing"}
        for name, k in FIN_ROWS
    ] + [{"Line": "— Dividend per share", "Years read": len(_dps), "Latest year": _latest_fy(_dps),
          "XBRL tag": " + ".join(_dps_src) or "—",
          "Status": "read" if _dps else "none of the tags this reader knows are in the filing"}]
    return years, notes, {"tags": tags, "net_cash": net_cash, "cash": cash_total, "debt": debt_total,
                          "fin": _fin, "fin_bal": _fin_bal, "fin_bal_fy": _fin_fy,
                          "dps": _dps, "shares_by_fy": _shares_by_fy, "wavg_by_fy": _wavg_by_fy,
                          "n_source": (tag_sources.get("N") or [""])[0],
                          "cls": _cls, "cls_reason": _cls_reason,
                          "median_OE": _med, "revenue": latest_rev, "cagr3": cagr3,
                          "leases": lease_total,
                          # The form that resolved against the SEC list. Yahoo uses the
                          # same hyphenated spelling, so pricing BRK.B as typed returned
                          # nothing and the page fell back to its $100.00 default beside
                          # a real market cap.
                          "ticker": ticker,
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


# ══════════════════════════════════════════════════════════════════════
#  FINANCIALS — banks, insurers, REITs (this app's design, not Burry's)
# ══════════════════════════════════════════════════════════════════════
#
# Everything from here to the end is new. The reader above is tool 1's,
# with the lines this page adds marked in CONCEPTS, FILL_KEYS, TAG_LABELS
# and at the end of load(). Nothing below changes a figure another page
# computes; the Tragic Algebra table this page prints is tool 1's for the
# same ticker.
#
# One idea for all three classes: a financial's earnings are a return on
# its equity and its growth is what it keeps. Tool 1 projected Kinsale's
# net income at 18% because premiums grew 18%; an insurer can only write
# what its surplus backs. So the page prices every class the same way and
# the class only changes the base and the evidence:
#
#   base      tangible common equity per share (insurers, banks), or
#             Nareit FFO per share (REITs) — filed
#   return    net income to common / average tangible common equity — filed
#   payout    dividends + buybacks over net income to common, five years
#             pooled — filed, and buybacks are the FILED figure only
#   growth    DERIVED for insurers and banks: return × (1 − payout). A
#             judgement for REITs, seeded from their own FFO record.
#   fade      a terminal return, seeded at the lower of the 5- and 10-year
#             medians — judgement
#   exit      sell in year N at the price that hands the next buyer the
#             same 15% on a level stream: P/TBV = terminal return ÷ 15%,
#             or FFO ÷ 15% — the only exit the page can derive rather
#             than borrow. Judgement box, seeded by that rule, with the
#             P/E equivalent printed beside it.
#
# The AICT tiers are not on this page. They are AI-moat tiers and mean
# nothing for a bank.


def d(x, dp=2):
    """Escaped dollar amount, safe inside markdown."""
    return f"\\${x:,.{dp}f}"


REQUIRED_RETURN = 0.15
HORIZON_DEFAULT = 10
LADDER_RATES = (8, 10, 12, 15, 18, 20)
TAX_STATUTORY = 0.21      # used only when the filing's own rate cannot be read


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
# Lenders, finance companies, functions related to deposit banking, and
# brokers: a bank when the filing carries deposits and net interest
# income, otherwise a float business this page does not price (v2).
PROMOTABLE_SIC = {6099, 6211} | set(range(6111, 6200))
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
    "interest income; insurers (6311-6399) whose filings carry premiums earned; and "
    "equity REITs (6798) whose filings carry real estate. Lenders, finance companies and "
    "brokers (6099, 6111-6199, 6211) are priced as banks when they hold deposits and "
    "refused when they do not. Insurance agents (6411), asset managers (6282), real-estate "
    "services and operators (6500-6553) and royalty owners (6792-6795) are ordinary "
    "businesses and belong to the Tragic Algebra Analyzer with net cash read. Exchanges "
    "and dealers (6200, 6221), blank-check companies (6770), investors n.e.c. (6799), "
    "mortgage REITs and anything else in 6000-6799 are refused.")


def _tags_present(facts: dict, concepts: list[str]) -> bool:
    """Does the filing tag any of these concepts at all? Presence, not a
    read: the gate asks what kind of balance sheet this is, and a line that
    was tagged in any annual filing answers that even if it later stopped."""
    tax = facts.get("facts", {}).get("us-gaap", {})
    return any(c in tax and tax[c].get("units") for c in concepts)


def financial_class(sic: str, facts: dict) -> tuple[str, str]:
    """(class, reason). class is one of bank, insurer, reit, ordinary,
    refused. `ordinary` means tool 1 prices it as a normal business; this
    page does not."""
    if not (sic and sic.isdigit()):
        return "ordinary", "No SIC code on file; not treated as a financial."
    code = int(sic)
    if not 6000 <= code <= 6799:
        return "ordinary", f"SIC {sic} is outside 6000-6799; not a financial."
    has_bank = _tags_present(facts, DEPOSIT_TAGS) and _tags_present(facts, NII_TAGS)
    has_prem = _tags_present(facts, PREMIUM_TAGS)
    has_re = _tags_present(facts, REIT_PROPERTY_TAGS)
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
    if code in PROMOTABLE_SIC:
        if has_bank:
            return "bank", (f"SIC {sic} is a lender or broker code, but the filing carries "
                            "deposits and net interest income — a bank in substance.")
        return "refused", (f"SIC {sic} — a lender, finance company or broker funded without "
                           "deposits. Its float is the product, and this page does not price "
                           "float businesses (Tool A v2).")
    if code in ORDINARY_SIC:
        return "ordinary", (f"SIC {sic} — a fee business, not a balance-sheet one. The Tragic "
                            "Algebra Analyzer prices it as an ordinary company with net cash read.")
    return "refused", (f"SIC {sic} — an exchange, dealer, blank-check company or holding "
                       "structure this page does not price.")


# ── The class lines the reader adds (see CONCEPTS, marked) ─────────────
#
# Instant (balance-sheet) groups this page reads on top of BALANCE. Each
# inner list is alternate names for ONE line; prefer_recent applies, as it
# does to every balance-sheet group in tool 1.
FIN_BALANCE = {
    "eq":    ["StockholdersEquity",
              "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "pref":  ["PreferredStockValue", "PreferredStockValueOutstanding",
              # JPMorgan, 1 Sep 2026: ~$27B of preferred under neither of the
              # first two; dividends read, the stock line did not, and TBVPS
              # printed $10/share high. Candidates for the big banks:
              "PreferredStockLiquidationPreferenceValue",
              "PreferredStockIncludingAdditionalPaidInCapital"],
    "gw":    ["Goodwill"],
    "intan": ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"],
    "aoci":  ["AccumulatedOtherComprehensiveIncomeLossNetOfTax"],
    # insurers
    "resv":  ["LiabilityForClaimsAndClaimsAdjustmentExpense",
              "LiabilityForFuturePolicyBenefitsAndUnpaidClaimsAndClaimsAdjustmentExpense"],
    "upr":   ["UnearnedPremiums"],
    "prec":  ["PremiumsReceivableAtCarryingValue"],
    "reins": ["ReinsuranceRecoverablesOnPaidAndUnpaidLosses", "ReinsuranceRecoverables",
              "ReinsuranceRecoverablesOnUnpaidLosses"],
    "dac":   ["DeferredPolicyAcquisitionCosts"],
    "inv":   ["Investments", "AvailableForSaleSecuritiesDebtSecurities",
              "DebtSecuritiesAvailableForSaleExcludingAccruedInterest"],
    # banks
    "dep":   ["Deposits"],
    "loans": ["FinancingReceivableExceptAccruedInterestAfterAllowanceForCreditLoss",
              "LoansAndLeasesReceivableNetReportedAmount",
              "LoansAndLeasesReceivableNetOfDeferredIncome", "NotesReceivableNet"],
    # REITs
    "re":    ["RealEstateInvestmentPropertyNet"],
}

FIN_ROWS = (
    ("Shareholders' equity", "eq"), ("Preferred stock", "pref"), ("Goodwill", "gw"),
    ("Intangibles", "intan"), ("AOCI", "aoci"),
    ("Loss reserves", "resv"), ("Unearned premiums", "upr"), ("Premiums receivable", "prec"),
    ("Reinsurance recoverables", "reins"), ("Deferred acquisition costs", "dac"),
    ("Invested assets", "inv"), ("Deposits", "dep"), ("Loans", "loans"), ("Real estate", "re"),
)

# Which balance lines each class shows in its table (all classes show the
# equity block).
CLASS_ROWS = {"insurer": ("resv", "upr", "prec", "reins", "dac", "inv"),
              "bank": ("dep", "loans"), "reit": ("re",)}

CLASS_NAME = {"bank": "Bank", "insurer": "Insurer", "reit": "REIT"}


def _per_share(facts: dict, concepts: list[str], sources: list[str] | None = None
               ) -> dict[int, float]:
    """Dividends declared per share. _annual reads USD and shares; a
    per-share figure is a third unit, so it needs its own read. Same three
    filters: full-year period, annual forms, latest filing wins."""
    out: dict[int, tuple[str, float]] = {}
    tax = facts.get("facts", {}).get("us-gaap", {})
    for concept in concepts:
        if concept not in tax:
            continue
        got: dict[int, tuple[str, float]] = {}
        for row in tax[concept].get("units", {}).get("USD/shares", []):
            if row.get("form") not in ANNUAL_FORMS:
                continue
            start, end = row.get("start"), row.get("end")
            if not (start and end):
                continue
            if not 330 <= (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days <= 400:
                continue
            fy, filed = int(end[:4]), row.get("filed", "")
            if fy not in got or filed > got[fy][0]:
                got[fy] = (filed, float(row.get("val", 0.0)))
        fresh = {fy: v for fy, v in got.items() if fy not in out}
        if fresh:
            out.update(fresh)
            if sources is not None:
                sources.append(concept)
    return {fy: v[1] for fy, v in out.items()}


# ── Net income to common, tangible common equity, ROTE ────────────────

N_TO_COMMON = "NetIncomeLossAvailableToCommonStockholdersBasic"


def net_to_common(N: float, n_tag: str, pdiv: float | None, pref: float | None
                  ) -> tuple[float | None, str]:
    """Net income after preferred dividends, or a refusal.

    JPMorgan carries about $27B of preferred stock and pays over a billion
    a year on it. A return on COMMON equity that keeps the preferred
    dividend in the numerator reads high on every bank with preferred
    outstanding. Four cases, in order: the tag is already to common; the
    preferred dividend was read, subtract it; preferred stock is on the
    balance sheet and the dividend was not read — refuse, do not guess; no
    preferred at all — net income is the figure."""
    if n_tag == N_TO_COMMON:
        return N, "net income available to common, as tagged"
    if pdiv is not None:
        return N - abs(pdiv), "net income less preferred dividends read"
    if pref is not None and pref > 0:
        return None, ("preferred stock is on the balance sheet but no preferred-dividend line "
                      "was read, so net income to common cannot be stated")
    return N, "no preferred stock"


def tangible_common(eq: float | None, pref: float | None, gw: float | None,
                    intan: float | None, last: dict[str, tuple[int, float]] | None = None
                    ) -> tuple[float | None, str, list[tuple[str, int, float]]]:
    """Equity − preferred − goodwill − intangibles, the sentence that says
    how, and the deductions that had to be carried.

    A deduction the filer never tagged is genuinely zero. A deduction last
    filed at ZERO has ended, and its absence afterwards is zero too —
    Progressive redeemed its preferred in 2024, and filers drop the element
    after one zero year. A deduction last filed at a non-zero value and not
    tagged this year is neither: it is a stopped line, and BRIEF §3's rule
    for stopped lines is to report the disagreement, never carry silently
    or zero silently. Carrying it forward is the conservative side — a
    lower book — so it is deducted as if unchanged and named in `carried`,
    with the year and amount, so the page can print the size of what it
    cannot place. Progressive, 1 Sep 2026: goodwill 228M last read FY2024
    and intangibles 86M last read FY2022, 1% of a 30,000M base, refused
    the whole page before this."""
    if eq is None:
        return None, "shareholders' equity not read", []
    ded, carried = 0.0, []
    for name, v in (("preferred stock", pref), ("goodwill", gw), ("intangibles", intan)):
        if v is not None:
            ded += v
        elif last and name in last and last[name][1] != 0:
            fy, val = last[name]
            ded += val
            carried.append((name, fy, val))
    reason = "equity less preferred, goodwill and intangibles"
    if carried:
        reason += "; " + ", ".join(f"{n} carried from FY{fy} at {v:,.0f}M" for n, fy, v in carried) \
                  + " (line stopped — deducted as if unchanged, the conservative side)"
    return eq - ded, reason, carried


def carry_sentence(rows: list) -> str:
    """One warning for every year that carries a stopped deduction, with the
    size of the disagreement per share in the latest year: if the line was
    in fact written off, tangible book per share is that much higher."""
    hit = [r for r in rows if r.carried]
    if not hit:
        return ""
    yrs = "; ".join(f"FY{r.fy}: " + ", ".join(f"{n} {v:,.0f}M from FY{fy}" for n, fy, v in r.carried) for r in hit)
    last = hit[-1]
    size = sum(v for _, _, v in last.carried)
    per = f" In FY{last.fy} that is {size:,.0f}M, {size / last.shares:.2f} per share: tangible book per share is at most that much higher than shown if the lines were written off." if last.shares else ""
    return ("**A stopped balance-sheet line is carried forward here, deducted as if unchanged** — "
            + yrs + "." + per + " The tag panel names the tag; the missing name is usually the whole fix.")


def rote(n_common: float | None, tbv_now: float | None, tbv_prev: float | None) -> float | None:
    """Return on AVERAGE tangible common equity. Needs the prior year-end;
    the first year of a window has none and is refused, not computed on a
    single balance."""
    if n_common is None or tbv_now is None or tbv_prev is None:
        return None
    avg = (tbv_now + tbv_prev) / 2.0
    if avg <= 0:
        return None
    return n_common / avg


@dataclass
class FinYear:
    fy: int
    N: float                       # tool 1's net income, $M
    N_common: float | None = None
    n_reason: str = ""
    eq: float | None = None
    pref: float | None = None
    gw: float | None = None
    intan: float | None = None
    aoci: float | None = None
    tbv: float | None = None       # tangible common equity, $M
    tbv_reason: str = ""
    carried: list = field(default_factory=list)   # stopped deductions carried forward
    shares: float | None = None    # year-end, M
    wavg: float | None = None      # weighted-average diluted, M
    rote: float | None = None
    div: float = 0.0               # dividends paid, $M (common)
    T: float = 0.0                 # buybacks as filed, $M
    excluded: bool = False
    lines: dict = field(default_factory=dict)   # the class lines, $M or None
    bal: dict = field(default_factory=dict)     # the class balance lines, $M or None

    @property
    def tbvps(self) -> float | None:
        if self.tbv is None or not self.shares:
            return None
        return self.tbv / self.shares

    @property
    def bvps(self) -> float | None:
        if self.eq is None or not self.shares:
            return None
        return (self.eq - (self.pref or 0.0)) / self.shares


def build_fin_years(years: list, fin: dict, bal: dict, shares_by_fy: dict,
                    wavg_by_fy: dict, n_tag: str) -> list[FinYear]:
    """One FinYear per tool-1 Year, in order. `fin` is {fy: {line: $M|None}},
    `bal` is {line: {fy: $M}}."""
    out: list[FinYear] = []
    seen: dict[str, tuple[int, float]] = {}
    prev_tbv: float | None = None
    for y in years:
        fy = y.fy
        b = {k: bal.get(k, {}).get(fy) for k in FIN_BALANCE}
        for name, key in (("preferred stock", "pref"), ("goodwill", "gw"), ("intangibles", "intan")):
            if b[key] is not None:
                seen[name] = (fy, b[key])
        f = fin.get(fy, {})
        nc, nr = net_to_common(y.N, n_tag, f.get("pdiv"), b["pref"])
        tbv, tr, carried = tangible_common(b["eq"], b["pref"], b["gw"], b["intan"],
                                           {k: v for k, v in seen.items() if v[0] < fy})
        # The mirror of net_to_common's third case. JPMorgan, 1 Sep 2026:
        # preferred DIVIDENDS read 17 years, the preferred STOCK line zero,
        # and tangible book printed $10/share high with nothing firing. If a
        # preferred dividend is paid this year and no preferred-stock line
        # was ever read (an ended-at-zero line is read), the deduction is
        # missing, not zero, and tangible COMMON equity cannot be stated.
        pdiv_v = f.get("pdiv")
        if tbv is not None and b["pref"] is None and "preferred stock" not in seen and pdiv_v:
            # money_fmt picks the precision: Universal's preferred dividend is a
            # few thousand dollars, and "{:,.0f}M" printed it as "preferred
            # dividends of 0M are paid", which describes a zero that would not
            # have refused (1 Sep 2026).
            _pd = money_fmt([abs(pdiv_v)]).format(abs(pdiv_v))
            tbv, tr = None, (f"preferred dividends of {_pd}M are paid this year but no "
                             "preferred-stock line was read, so the deduction is missing, not zero, "
                             "and tangible common equity cannot be stated — the tag panel's Preferred "
                             "stock row names the tags tried")
        r = FinYear(fy=fy, N=y.N, N_common=nc, n_reason=nr, eq=b["eq"], pref=b["pref"],
                    gw=b["gw"], intan=b["intan"], aoci=b["aoci"], tbv=tbv, tbv_reason=tr, carried=carried,
                    shares=shares_by_fy.get(fy), wavg=wavg_by_fy.get(fy),
                    div=abs(f.get("div") or 0.0), T=y.T, excluded=bool(y.excluded),
                    lines=f, bal=b)
        r.rote = rote(nc, tbv, prev_tbv)
        prev_tbv = tbv
        out.append(r)
    return out


def median_of(vals: list[float]) -> float | None:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    return statistics.median(v)


def rote_medians(rows: list[FinYear]) -> tuple[float | None, float | None, int]:
    """(5-year median, whole-window median, years readable). Excluded years
    (tool 1's capital events) are left out — a share-funded deal makes the
    year's book unreadable for the same reason it makes its ΔE unreadable."""
    ok = [r for r in rows if r.rote is not None and not r.excluded]
    return median_of([r.rote for r in ok[-5:]]), median_of([r.rote for r in ok]), len(ok)


def terminal_seed(m5: float | None, m10: float | None) -> float | None:
    """The lower of the two medians. A company whose recent years are its
    best — Kinsale's FY2021-25 against FY2016-20 — fades toward its own
    longer record rather than projecting its peak."""
    if m5 is None or m10 is None:
        return None
    return min(m5, m10)


@dataclass
class FinPayout:
    """Cash returned, kept in pieces, over the last n clean years. Buybacks
    are the FILED figure only. Tool 2 falls back to an implied figure from
    the share count; this page does not — Progressive stopped buying back
    in 2016 and its count drifts by a few hundred thousand shares a year,
    and a page that invented a buyback from that would misstate the one
    thing Burry used it as a control for."""
    dividends: float = 0.0
    buybacks: float = 0.0
    n_common: float = 0.0
    years: int = 0

    @property
    def returned(self) -> float:
        return self.dividends + self.buybacks

    @property
    def ratio(self) -> float | None:
        return self.returned / self.n_common if self.n_common > 0 else None


def fin_payout(rows: list[FinYear], n: int = 5) -> FinPayout:
    clean = [r for r in rows if not r.excluded and r.N_common is not None][-n:]
    if not clean:
        return FinPayout()
    return FinPayout(dividends=sum(r.div for r in clean), buybacks=sum(r.T for r in clean),
                     n_common=sum(r.N_common for r in clean), years=len(clean))


def tbvps_cagr(rows: list[FinYear], n: int = 5) -> float | None:
    """Filed compounding of tangible book per share over the last n steps,
    printed beside the derived growth so the identity can be checked
    against the record."""
    ok = [r for r in rows if r.tbvps is not None and r.tbvps > 0]
    if len(ok) < 2:
        return None
    a, b = ok[max(0, len(ok) - 1 - n)], ok[-1]
    steps = b.fy - a.fy
    if steps <= 0:
        return None
    return (b.tbvps / a.tbvps) ** (1 / steps) - 1


# ── Class evidence ─────────────────────────────────────────────────────

def ratio_or_none(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return num / den


def underwriting(lines: dict) -> dict:
    """Loss ratio, expense ratio, combined ratio and the underwriting
    result, each refused (None) when a component is unread. The expense
    ratio needs BOTH acquisition-cost amortisation and other underwriting
    expense, or a single total: half an expense ratio is a wrong number,
    not a partial one."""
    nep, loss = lines.get("nep"), lines.get("loss")
    dacx, uwo, uwt = lines.get("dacx"), lines.get("uwo"), lines.get("uwt")
    if uwt is not None:
        uwx = uwt
    elif dacx is not None and uwo is not None:
        uwx = dacx + uwo
    else:
        uwx = None
    lr = ratio_or_none(loss, nep)
    er = ratio_or_none(uwx, nep)
    cr = lr + er if lr is not None and er is not None else None
    uw = nep - loss - uwx if (nep is not None and loss is not None and uwx is not None) else None
    return {"uwx": uwx, "lr": lr, "er": er, "cr": cr, "uw": uw}


def insurance_float(bal: dict) -> tuple[float | None, str]:
    """Buffett's definition: loss reserves + unearned premiums − premiums
    receivable − reinsurance recoverables − deferred acquisition costs. The
    two liabilities are required; each deduction is netted only where it
    was read, and the sentence says which were not."""
    resv, upr = bal.get("resv"), bal.get("upr")
    if resv is None or upr is None:
        return None, "loss reserves or unearned premiums not read"
    total = resv + upr
    gross = []
    for name, key in (("premiums receivable", "prec"), ("reinsurance recoverables", "reins"),
                      ("deferred acquisition costs", "dac")):
        if bal.get(key) is None:
            gross.append(name)
        else:
            total -= bal[key]
    return total, ("net of every deduction read" if not gross
                   else "gross of " + ", ".join(gross) + " (not read)")


def cost_of_float(uw: float | None, flt: float | None) -> float | None:
    """Negative is a profit: the insurer is paid to hold the money."""
    if uw is None or flt is None or flt <= 0:
        return None
    return -uw / flt


def effective_tax(lines: dict) -> tuple[float, str]:
    ptx, tax = lines.get("ptx"), lines.get("tax")
    if ptx and ptx > 0 and tax is not None and 0 <= tax / ptx < 0.6:
        return tax / ptx, "the filing's own effective rate"
    return TAX_STATUTORY, "US statutory, the filing's rate could not be read"


def cr_lever(nep: float | None, avg_tbv: float | None, tax: float) -> float | None:
    """Points of ROTE per point of combined ratio: premiums-to-tangible-
    equity × (1 − tax). An identity on filed numbers, printed so the
    terminal box can be moved from the company's own record."""
    if nep is None or avg_tbv is None or avg_tbv <= 0:
        return None
    return nep / avg_tbv * (1 - tax)


def lever_sentence(lev: float, cr_now: float, cr_median: float, fy: int, tax: float, tax_reason: str) -> str:
    """The combined-ratio lever, worded. `lev` is points of ROTE per point of
    combined ratio — a dimensionless ratio, so it is printed as it is.
    Kinsale, 1 Sep 2026: the page printed '72.68 points of ROTE' for a
    lever of 0.727, having multiplied a ratio of points by 100 as though
    it were a percentage. The second figure was right and read '+1.4
    points off' — a rise in the combined ratio lowers ROTE, so it is a
    reduction and is worded as one."""
    delta = (cr_median - cr_now) * lev * 100
    if abs(delta) < 0.05:
        effect = "the window median is where it already is"
    elif delta > 0:
        effect = f"the window median would take about {delta:.1f} points off the normal-year box"
    else:
        effect = f"the window median would add about {-delta:.1f} points to the normal-year box"
    return (f"Lever: at FY{fy}'s premiums-to-tangible-equity and a tax rate of {tax:.1%} ({tax_reason}), one "
            f"point of combined ratio is **{lev:.2f} points of ROTE**. Combined ratio FY{fy} {cr_now:.1%}, "
            f"window median {cr_median:.1%} — so {effect}.")


def float_sentence(bal: dict, total: float | None, fy: int) -> str:
    """The float arithmetic, line by line, so it can be checked against the
    10-K balance sheet — Kinsale publishes no float figure, only the parts."""
    if total is None:
        return ""
    parts = [f"loss reserves {bal['resv']:,.0f} + unearned premiums {bal['upr']:,.0f}"]
    for name, key in (("premiums receivable", "prec"), ("reinsurance recoverables", "reins"),
                      ("deferred acquisition costs", "dac")):
        if bal.get(key) is not None:
            parts.append(f"− {name} {bal[key]:,.0f}")
    return f"Float FY{fy}: " + " ".join(parts) + f" = {total:,.0f}."


def bank_lines(lines: dict) -> dict:
    nii, noni, nonx, prov = (lines.get(k) for k in ("bnii", "nonii", "nonix", "prov"))
    rev = nii + noni if nii is not None and noni is not None else None
    return {"rev": rev, "eff": ratio_or_none(nonx, rev),
            "ppnr": rev - nonx if rev is not None and nonx is not None else None,
            "prov": prov}


def ffo(n_common: float | None, da: float | None, gain: float | None, imp: float | None
        ) -> tuple[float | None, str]:
    """Nareit FFO: net income to common + real-estate D&A − gains on sale +
    impairments. D&A is required; the other two adjust only where read, and
    the sentence says which did not."""
    if n_common is None:
        return None, "net income to common unread"
    if da is None:
        return None, "depreciation and amortisation not read"
    v, notes = n_common + da, []
    if gain is None:
        notes.append("gains on sale not read")
    else:
        v -= gain
    if imp is None:
        notes.append("impairments not read")
    else:
        v += imp
    return v, ("all four lines read" if not notes else "; ".join(notes))


def per_share_cagr(series: list[tuple[int, float]], n: int) -> float | None:
    ok = [(fy, v) for fy, v in series if v is not None and v > 0]
    if len(ok) < 2:
        return None
    a, b = ok[max(0, len(ok) - 1 - n)], ok[-1]
    if b[0] <= a[0]:
        return None
    return (b[1] / a[1]) ** (1 / (b[0] - a[0])) - 1


def ffo_growth_seed(cagr5: float | None, cagr10: float | None) -> float | None:
    """The lower of the two, like the terminal ROTE: a REIT's growth comes
    from external capital and its own record is the only anchor."""
    if cagr5 is None and cagr10 is None:
        return None
    return min(v for v in (cagr5, cagr10) if v is not None)


# ── The engine ─────────────────────────────────────────────────────────

@dataclass
class FinParams:
    kind: str            # "book" or "ffo"
    base: float          # per share: tangible book, or FFO
    ret0: float          # book: normal-year return on the base (decimal)
    retT: float          # book: terminal return
    payout: float        # decimal, of the year's earnings
    years: int
    exit_mult: float     # price / base at exit
    growth: float = 0.0  # ffo: FFO per-share growth


@dataclass
class Step:
    year: int
    base_start: float
    ret: float
    earnings: float
    paid: float
    base_end: float


def fade(ret0: float, retT: float, t: int, n: int) -> float:
    """Linear from ret0 in year 1 to retT in year n."""
    if n <= 1:
        return ret0
    return ret0 + (retT - ret0) * (t - 1) / (n - 1)


def fin_stream(p: FinParams) -> list[Step]:
    out, b = [], p.base
    for t in range(1, p.years + 1):
        if p.kind == "book":
            r = fade(p.ret0, p.retT, t, p.years)
            e = r * b
            paid = p.payout * e
            b_end = b + e - paid
        else:
            e = b * (1 + p.growth)
            r = p.growth
            paid = p.payout * e
            b_end = e
        out.append(Step(t, b, r, e, paid, b_end))
        b = b_end
    return out


def fin_value(p: FinParams, r_pct: float) -> tuple[float, float]:
    """(discounted owner's stream, discounted exit) per share. Their sum is
    the intrinsic value at that required return; printed apart so a reader
    sees how much of the answer is the year-N sale."""
    r = r_pct / 100.0
    s = fin_stream(p)
    stream = sum(st.paid / (1 + r) ** st.year for st in s)
    exit_ = s[-1].base_end * p.exit_mult / (1 + r) ** p.years if s else 0.0
    return stream, exit_


def fin_iv(p: FinParams, r_pct: float) -> float:
    a, b = fin_value(p, r_pct)
    return a + b


def fin_ladder(p: FinParams) -> dict[int, float]:
    return {n: fin_iv(p, n) for n in LADDER_RATES}


def fin_implied_return(price: float, p: FinParams) -> float | None:
    """The required return at which the value equals today's price, by
    bisection. Value falls as the rate rises, so the root is unique."""
    if price <= 0:
        return None
    lo, hi = -0.5, 3.0
    if fin_iv(p, lo * 100) < price:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if fin_iv(p, mid * 100) > price else (lo, mid)
    return (lo + hi) / 2


def exit_seed(kind: str, retT: float | None) -> float | None:
    """P/TBV = terminal return ÷ 15% (book); P/FFO = 1 ÷ 15% (REIT). The
    price at which the next buyer earns the same 15% on a level stream."""
    if kind == "ffo":
        return 1.0 / REQUIRED_RETURN
    if retT is None:
        return None
    return retT / REQUIRED_RETURN


def exit_pe(kind: str, exit_mult: float, retT: float | None) -> float | None:
    """The P/E the exit multiple implies, printed beside it so a book
    multiple is never mistaken for a traded one."""
    if kind == "ffo":
        return exit_mult
    if not retT or retT <= 0:
        return None
    return exit_mult / retT


def derived_growth(ret: float, payout: float) -> float:
    return ret * max(0.0, 1.0 - payout)


def value_split(stream: float, exit_: float) -> tuple[float, float]:
    tot = stream + exit_
    if tot <= 0:
        return float("nan"), float("nan")
    return stream / tot, exit_ / tot


# ── Refusals, in gate order ────────────────────────────────────────────

def gate_shares(shares: float | None) -> str:
    if not shares or shares <= 0:
        return ("**No year-end share count was read for the latest year.** Tangible book per "
                "share needs one; the page refuses rather than dividing by a weighted average "
                "that lags every buyback.")
    return ""


def gate_tbv(tbv: float | None, reason: str, cls: str) -> str:
    if tbv is None:
        return f"**Tangible common equity could not be stated for the latest year:** {reason}."
    if tbv <= 0:
        return ("**Tangible common equity is negative** — this filer's equity is goodwill and "
                "intangibles. A return on tangible book is not a return measure here, and there "
                "is no book to price. The Tragic Algebra Analyzer's projection with net cash "
                "set to zero is the better indicative reading for a company like this.")
    return ""


def gate_equity_stale(eq_fy: str, ni_fy: int) -> str:
    """The base IS the equity line, so a stale one is refused, not carried."""
    if not eq_fy or eq_fy == "—":
        return "**Shareholders' equity was not read at all.**"
    if int(eq_fy) < ni_fy:
        return (f"**Shareholders' equity stops at FY{eq_fy} while net income reaches FY{ni_fy}.** "
                "Every figure on this page is built on that line; a stale one is refused rather "
                "than carried forward. The tag panel names the tag.")
    return ""


def gate_n_common(n_common: float | None, reason: str) -> str:
    """A bank with preferred stock and no preferred-dividend line: say so,
    rather than let the history gate report zero readable years."""
    if n_common is None:
        return f"**Net income to common could not be stated for the latest year:** {reason}."
    return ""


def gate_history(n_ok: int, need: int, what: str) -> str:
    if n_ok < need:
        return (f"**Only {n_ok} year{'s' if n_ok != 1 else ''} of {what} could be read; "
                f"{need} is the least this page will seed from.**")
    return ""


def gate_dE(pooled) -> tuple[str, float | None]:
    """Tool 1's rule, inherited: above 125% refuse; 100-125% cap; not
    projectable → 100% with the note. Returns (refusal, applied ΔE)."""
    if pooled is None:
        return "", 1.0
    if pooled.dE_defined and pooled.dE > DE_UNUSABLE_ABOVE:
        return (f"**ΔE reads {pooled.dE:.1%}, above the 125% ceiling.** Tool 1's rule: that is "
                "issuance the reader failed to capture, not a heavy buyback, and it cannot be "
                "projected."), None
    if dE_projectable(pooled):
        return "", seed_dE(pooled.dE)
    return "", 1.0


def gate_reit(ffo_ps: float | None, dps: float | None, ffo_reason: str) -> str:
    if ffo_ps is None:
        return f"**FFO could not be computed for the latest year:** {ffo_reason}."
    if ffo_ps <= 0:
        return "**FFO per share is not positive in the latest year.** Nothing to grow."
    if dps is None:
        return "**No dividend per share was read for the latest year.** The owner's stream is the dividend."
    if dps > ffo_ps:
        return (f"**The dividend ({dps:.2f}) exceeds FFO per share ({ffo_ps:.2f}).** An "
                "uncovered dividend is being paid from capital, and the page will not project it.")
    return ""


def page_notes(notes: list[str]) -> list[str]:
    """Tool 1's notes minus the ones written for its own pricing: the
    financial banner (net cash set to zero — this page never reads it) and
    the revenue-growth seeding sentences (this page derives growth)."""
    drop = ("net cash has been set to zero", "The seed uses the recent rate",
            "which is a launch rate",
            # Kinsale, 1 Sep 2026: tool 1's stale-line note ends "net cash above
            # carries the last figure found forward", and its fallback-tag note
            # is about the cash and debt groups. Neither line is on this page.
            "Net cash above carries", "read from a fallback tag because the preferred one had stopped")
    return [n for n in notes if not any(x in n for x in drop)]


def cell(v, fmt: str, blank: str = "—") -> str:
    """Text for one table cell — page 4's convention: Streamlit's grid ignores
    the Styler's na_rep and prints None into a refused cell, so every cell
    is formatted here and the table is handed over as text. `fmt` is a
    str.format template, the form money_fmt returns."""
    if v is None or (isinstance(v, float) and v != v):
        return blank
    return fmt.format(v)


def pct(v, dp: int = 1) -> str:
    return cell(v, f"{{:.{dp}%}}")


def dE_row(y, median_N: float) -> float | None:
    return dE_cell(y.N, y.dE, median_N)


# ══════════════════════════════════════════════════════════════════════
#  SELF-TEST
# ══════════════════════════════════════════════════════════════════════

def _facts_with(*concepts: str) -> dict:
    return {"facts": {"us-gaap": {c: {"units": {"USD": [{"val": 1}]}} for c in concepts}}}


def self_test() -> list[tuple[str, bool, str]]:
    out = []
    ok = lambda name, cond, got="": out.append((name, bool(cond), str(got)))

    # 1. The ported engine still agrees with tool 1, to the dollar.
    goog = [(2016, 19478, 6900, 3693, 3304, 97, 47), (2017, 12662, 7900, 4846, 4166, 78, 55),
            (2018, 30736, 10000, 9075, 4993, -2, 61), (2019, 34343, 11700, 18396, 4765, -158, 70),
            (2020, 40269, 12991, 31149, 5720, -263, 73), (2021, 76033, 15376, 50274, 10162, -264, 125),
            (2022, 59972, 19362, 59296, 9300, -412, 117), (2023, 73795, 22460, 61504, 9837, -374, 115),
            (2024, 100118, 22785, 62222, 12190, -243, 164), (2025, 132170, 24953, 45709, 14167, -93, 206)]
    ys = [Year(fy=f, N=n, G=g, T=t, Cw=c, dS=d, price=p) for f, n, g, t, c, d, p in goog]
    ok("Ported engine: Alphabet FY2016 V = $8,252M", abs(ys[0].V - 8252) < 1, f"${ys[0].V:,.0f}M")
    ok("Ported engine: Alphabet pooled ΔE = 88.7%", abs(pool(ys).dE - 0.887) < 0.002, f"{pool(ys).dE:.2%}")
    crm = IVParams(OE=7300, shares=1073.3, tier="Chapel", growth=0.069, exit_multiple=21.8, blend=1.0)
    ok("Ported engine: Salesforce IV15, his inputs → $69.81",
       abs(intrinsic_value(crm, 15) - 69.81) < 1.0, f"${intrinsic_value(crm, 15):,.2f}")

    # 2. The gate. SIC names the expectation; the filing confirms it.
    bank = _facts_with("Deposits", "InterestIncomeExpenseNet")
    ins = _facts_with("PremiumsEarnedNet")
    re_ = _facts_with("RealEstateInvestmentPropertyNet")
    none = _facts_with("Revenues")
    ok("Gate: 6021 with deposits and NII → bank (JPM)", financial_class("6021", bank)[0] == "bank")
    ok("Gate: 6021 without deposits → refused, names the mismatch",
       financial_class("6021", none)[0] == "refused" and "says bank" in financial_class("6021", none)[1])
    ok("Gate: 6331 with premiums → insurer (KNSL, PGR)", financial_class("6331", ins)[0] == "insurer")
    ok("Gate: 6324 with premiums → insurer (health insurers sorted by tangible equity, not SIC)",
       financial_class("6324", ins)[0] == "insurer")
    ok("Gate: 6311 with premiums → insurer (life)", financial_class("6311", ins)[0] == "insurer")
    ok("Gate: 6798 with real estate → reit (O)", financial_class("6798", re_)[0] == "reit")
    ok("Gate: 6798 without real estate → refused as a mortgage REIT (AGNC)",
       financial_class("6798", bank)[0] == "refused" and "mortgage" in financial_class("6798", bank)[1])
    ok("Gate: 6141 with deposits → bank (Discover)", financial_class("6141", bank)[0] == "bank")
    ok("Gate: 6211 with deposits → bank (Schwab)", financial_class("6211", bank)[0] == "bank")
    ok("Gate: 6141 without deposits → refused as a lender (Affirm)",
       financial_class("6141", none)[0] == "refused" and "without deposits" in financial_class("6141", none)[1])
    ok("Gate: 6099 without deposits → refused (Western Union)", financial_class("6099", none)[0] == "refused")
    ok("Gate: 6411 → ordinary, tool 1 (MMC, AJG, Ryan Specialty)", financial_class("6411", none)[0] == "ordinary")
    ok("Gate: 6531 → ordinary, tool 1 (Compass)", financial_class("6531", none)[0] == "ordinary")
    ok("Gate: 6282 → ordinary, tool 1 (asset managers)", financial_class("6282", none)[0] == "ordinary")
    ok("Gate: 6792 → ordinary, tool 1 (royalty)", financial_class("6792", none)[0] == "ordinary")
    ok("Gate: 6200 → refused (exchanges)", financial_class("6200", bank)[0] == "refused")
    ok("Gate: 6770 → refused (SPAC)", financial_class("6770", none)[0] == "refused")
    ok("Gate: 6799 → refused (investors n.e.c.)", financial_class("6799", none)[0] == "refused")
    ok("Gate: 7372 → ordinary, not a financial", financial_class("7372", none)[0] == "ordinary")
    ok("Gate: empty SIC → ordinary, says no SIC",
       financial_class("", none)[0] == "ordinary" and "No SIC" in financial_class("", none)[1])
    ok("Gate: every bank SIC needs deposits, no exception",
       all(financial_class(str(s), none)[0] == "refused" for s in BANK_SIC))
    ok("Gate: a tag present but stopped still counts as presence (the gate asks what kind of filer)",
       _tags_present({"facts": {"us-gaap": {"Deposits": {"units": {"USD": [{"val": 5, "end": "2015-12-31"}]}}}}},
                     DEPOSIT_TAGS))

    # 3. Net income to common.
    ok("N to common: available-to-common tag is used as is", net_to_common(100, N_TO_COMMON, 7, 500)[0] == 100)
    ok("N to common: NetIncomeLoss less preferred dividends read", net_to_common(100, "NetIncomeLoss", -7, 500)[0] == 93)
    ok("N to common: preferred on the balance sheet, no dividend line → refused",
       net_to_common(100, "NetIncomeLoss", None, 500)[0] is None)
    ok("N to common: no preferred at all → net income", net_to_common(100, "NetIncomeLoss", None, None)[0] == 100)
    ok("N to common: preferred read as zero → net income", net_to_common(100, "NetIncomeLoss", None, 0.0)[0] == 100)

    # 4. Tangible common equity.
    ok("TBV: equity − preferred − goodwill − intangibles", tangible_common(1000, 100, 200, 50)[0] == 650)
    ok("TBV: deductions never tagged are zero", tangible_common(1000, None, None, None)[0] == 1000)
    _tc = tangible_common(1000, None, None, None, {"goodwill": (2022, 200.0)})
    ok("TBV: goodwill tagged earlier and not this year → carried forward, deducted, named with year and size",
       _tc[0] == 800 and "goodwill carried from FY2022 at 200M" in _tc[1] and _tc[2] == [("goodwill", 2022, 200.0)])
    ok("TBV: a deduction read this year is never carried", tangible_common(1000, None, 150, None, {"goodwill": (2022, 200.0)})[2] == [])
    ok("TBV: no equity → refused", tangible_common(None, 0, 0, 0)[0] is None)
    ok("TBV: a deduction last filed at ZERO has ended — absence is zero, not staleness (PGR's redeemed preferred)",
       tangible_common(1000, None, None, None, {"preferred stock": (2023, 0.0)})[0] == 1000)
    _pg = build_fin_years([Year(fy=2022, N=10), Year(fy=2023, N=10), Year(fy=2024, N=10)], {},
                          {"eq": {2022: 900, 2023: 950, 2024: 1000}, "pref": {2022: 494, 2023: 0}}, {}, {}, N_TO_COMMON)
    _jp = build_fin_years([Year(fy=2024, N=58471), Year(fy=2025, N=57048)],
                          {2024: {"pdiv": -1259}, 2025: {"pdiv": -1099}},
                          {"eq": {2024: 344758, 2025: 362438}, "gw": {2024: 52565, 2025: 52731},
                           "intan": {2024: 1700, 2025: 1300}}, {2025: 2696.2}, {}, "NetIncomeLoss")
    ok("JPM shape: preferred dividends read, preferred stock never tagged → TBV refused, not printed high",
       all(r.tbv is None for r in _jp) and "preferred dividends of 1,099" in _jp[-1].tbv_reason
       and "cannot be stated" in _jp[-1].tbv_reason and all(r.rote is None for r in _jp))
    ok("JPM shape: N to common still stated (the dividend IS read)", _jp[-1].N_common == 57048 - 1099)
    ok("JPM shape: the gate carries the preferred sentence",
       "preferred dividends" in gate_tbv(_jp[-1].tbv, _jp[-1].tbv_reason, "bank"))
    _ok0 = build_fin_years([Year(fy=2024, N=100), Year(fy=2025, N=100)], {2025: {"pdiv": -5}},
                           {"eq": {2024: 1000, 2025: 1100}, "pref": {2024: 0}}, {}, {}, "NetIncomeLoss")
    ok("Mirror rule: preferred ended at zero earlier → a later dividend does not refuse (redemption-year trickle)",
       _ok0[-1].tbv == 1100)
    _tiny = build_fin_years([Year(fy=2016, N=99)], {2016: {"pdiv": -0.013}}, {"eq": {2016: 371}}, {}, {}, "NetIncomeLoss")
    ok("Mirror rule: a thousands-sized preferred dividend refuses with its size, never '0M' (UVE FY2016)",
       _tiny[0].tbv is None and "0.01" in _tiny[0].tbv_reason and "of 0M" not in _tiny[0].tbv_reason,
       _tiny[0].tbv_reason[:60])
    ok("Mirror rule: no dividend, no preferred line → nothing fires (Kinsale)",
       build_fin_years([Year(fy=2025, N=100)], {2025: {}}, {"eq": {2025: 500}}, {}, {}, "NetIncomeLoss")[0].tbv == 500)
    ok("Reader lists: the CECL loans tag and the two bank preferred tags are in FIN_BALANCE",
       "FinancingReceivableExceptAccruedInterestAfterAllowanceForCreditLoss" in FIN_BALANCE["loans"]
       and "PreferredStockLiquidationPreferenceValue" in FIN_BALANCE["pref"]
       and "PreferredStockIncludingAdditionalPaidInCapital" in FIN_BALANCE["pref"])
    ok("Rows: preferred 494 → 0 → dropped reads TBV 406, 950, 1000, nothing carried",
       [r.tbv for r in _pg] == [406, 950, 1000] and not any(r.carried for r in _pg))

    # 5. ROTE on average tangible equity.
    ok("ROTE: net to common over the average of two year-ends", abs(rote(30, 120, 80) - 0.30) < 1e-12)
    ok("ROTE: first year of a window has no prior balance → refused", rote(30, 120, None) is None)
    ok("ROTE: negative average book → refused", rote(30, -10, -20) is None)
    ok("ROTE: unreadable net to common → refused", rote(None, 120, 80) is None)

    # 6. Building the rows from the reader's dicts.
    _ys = [Year(fy=2022, N=100), Year(fy=2023, N=120), Year(fy=2024, N=150), Year(fy=2025, N=180)]
    _fin = {2023: {"pdiv": -5, "div": -20}, 2024: {"pdiv": -5, "div": -25}, 2025: {"pdiv": -5, "div": -30}, 2022: {"pdiv": -5}}
    _bal = {"eq": {2022: 1000, 2023: 1100, 2024: 1250, 2025: 1450}, "pref": {2022: 100, 2023: 100, 2024: 100, 2025: 100},
            "gw": {2022: 50, 2023: 50, 2024: 50}}
    _sh = {2022: 10, 2023: 10, 2024: 10, 2025: 10}
    rows = build_fin_years(_ys, _fin, _bal, _sh, {}, "NetIncomeLoss")
    ok("Rows: FY2023 ROTE = (120−5)/avg(1100−100−50, 1000−100−50)", abs(rows[1].rote - 115 / 900) < 1e-12, f"{rows[1].rote:.4%}")
    ok("Rows: FY2025 goodwill unread after FY2024 → carried at 50, TBV 1,300, ROTE stated",
       rows[3].tbv == 1300 and rows[3].carried == [("goodwill", 2024, 50)] and abs(rows[3].rote - 175 / 1200) < 1e-12)
    _cs = carry_sentence(rows)
    ok("Carry sentence: names the year, the line, the amount and the per-share size",
       "FY2025: goodwill 50M from FY2024" in _cs and "5.00 per share" in _cs and carry_sentence(rows[:3]) == "")
    # PGR's shape: goodwill 228 to FY2024, intangibles 86 to FY2022, equity 30,323 in FY2025
    _pgr = build_fin_years([Year(fy=f, N=1) for f in (2022, 2023, 2024, 2025)], {},
                           {"eq": {2022: 15891, 2023: 20277, 2024: 25591, 2025: 30323}, "pref": {2022: 494, 2023: 494, 2024: 0},
                            "gw": {2022: 228, 2023: 228, 2024: 228}, "intan": {2022: 86}}, {2025: 585.9}, {}, N_TO_COMMON)
    ok("PGR shape: FY2025 TBV = 30,323 − 228 − 86 = 30,009, both lines carried, preferred ended at zero",
       _pgr[-1].tbv == 30009 and [n for n, _, _ in _pgr[-1].carried] == ["goodwill", "intangibles"], f"{_pgr[-1].tbv}")
    ok("PGR shape: 314M is 0.54 per share of disagreement", "0.54 per share" in carry_sentence(_pgr))
    ok("Rows: FY2022 ROTE refused (no prior year), TBV still stated", rows[0].rote is None and rows[0].tbv == 850)
    ok("Rows: TBVPS = TBV / year-end shares", rows[2].tbvps == 110.0)
    ok("Rows: dividends read positive from a negative cash-flow tag", rows[2].div == 25.0)
    m5, m10, n_ok = rote_medians(rows)
    ok("Medians: three readable years (FY2025 carried, not refused), both medians on them", n_ok == 3 and m5 == m10)
    ok("Terminal seed: the lower of the two medians", terminal_seed(0.28, 0.19) == 0.19 and terminal_seed(0.12, 0.15) == 0.12)
    ok("Terminal seed: refused when a median is missing", terminal_seed(0.28, None) is None)

    # 7. Payout: filed buybacks only, never implied. Progressive's shape.
    _pg = [FinYear(fy=f, N=n, N_common=n, div=dv, T=0.0, shares=s)
           for f, n, dv, s in ((2021, 3350, 2800, 585.9), (2022, 720, 260, 585.5), (2023, 3900, 235, 585.2),
                               (2024, 8480, 4450, 585.0), (2025, 9000, 2500, 584.8))]
    _pp = fin_payout(_pg)
    ok("Payout: PGR shape — count drifting down, T zero → buybacks 0, dividends only",
       _pp.buybacks == 0.0 and abs(_pp.dividends - 10245) < 1e-9 and abs(_pp.ratio - 10245 / 25450) < 1e-12,
       f"{_pp.ratio:.1%}")
    ok("Payout: pooled over the last five clean years only",
       fin_payout([FinYear(fy=2019, N=1, N_common=1, div=1000)] + _pg).dividends == 10245)
    ok("Payout: excluded year left out", fin_payout(_pg[:-1] + [FinYear(fy=2025, N=9000, N_common=9000, div=99999, excluded=True)]).dividends == 7745)
    ok("Payout: no readable net to common → no ratio", fin_payout([FinYear(fy=2025, N=1, N_common=None)]).ratio is None)
    _tb = [FinYear(fy=2020, N=1, tbv=100, shares=1), FinYear(fy=2025, N=1, tbv=200, shares=1)]
    ok("TBVPS CAGR: 100 → 200 over five years = 14.87%", abs(tbvps_cagr(_tb) - (2 ** 0.2 - 1)) < 1e-12)
    ok("TBVPS CAGR: one year read → refused", tbvps_cagr(_tb[:1]) is None)

    # 8. Insurer evidence.
    _u = underwriting({"nep": 1000, "loss": 600, "dacx": 120, "uwo": 80})
    ok("Underwriting: loss 60% + expense 20% = combined 80%, result 200",
       abs(_u["lr"] - 0.60) < 1e-12 and abs(_u["er"] - 0.20) < 1e-12 and abs(_u["cr"] - 0.80) < 1e-12 and _u["uw"] == 200)
    _u2 = underwriting({"nep": 1000, "loss": 600, "dacx": 120})
    ok("Underwriting: one expense component missing → expense and combined refused, loss ratio kept",
       _u2["lr"] == 0.6 and _u2["er"] is None and _u2["cr"] is None and _u2["uw"] is None)
    ok("Underwriting: a single total-expense tag suffices", underwriting({"nep": 1000, "loss": 600, "uwt": 200})["cr"] == 0.8)
    ok("Underwriting: no premiums → everything refused", underwriting({"loss": 600, "uwt": 200})["lr"] is None)
    _f, _fr = insurance_float({"resv": 2000, "upr": 800, "prec": 150, "reins": 250, "dac": 100})
    ok("Float: reserves + unearned − receivable − recoverables − DAC = 2,300", _f == 2300 and _fr.startswith("net of every"))
    _f2, _fr2 = insurance_float({"resv": 2000, "upr": 800, "prec": 150})
    ok("Float: deductions not read are named, not zeroed silently", _f2 == 2650 and "reinsurance recoverables" in _fr2 and "deferred" in _fr2)
    ok("Float: a required liability missing → refused", insurance_float({"resv": 2000})[0] is None)
    ok("Cost of float: an underwriting profit is a negative cost", cost_of_float(200, 2300) == -200 / 2300)
    ok("Cost of float: refused without float", cost_of_float(200, None) is None)
    ok("Tax: the filing's rate when readable", effective_tax({"ptx": 500, "tax": 100}) == (0.2, "the filing's own effective rate"))
    ok("Tax: statutory when pre-tax is a loss", effective_tax({"ptx": -500, "tax": 10})[0] == TAX_STATUTORY)
    ok("CR lever: 1 point of combined ratio = NEP/avg TBV × (1−tax) points of ROTE", abs(cr_lever(1100, 1000, 0.21) - 0.869) < 1e-12)
    _ls = lever_sentence(0.7267, 0.789, 0.808, 2025, 0.206, "the filing's own effective rate")
    ok("CR lever sentence: KNSL's 0.73 points, not 72.68, and a higher median takes points OFF",
       "0.73 points of ROTE" in _ls and "take about 1.4 points off" in _ls and "+" not in _ls.split("so")[1], _ls[-70:])
    ok("CR lever sentence: a lower median adds points", "add about" in lever_sentence(0.7267, 0.85, 0.80, 2025, 0.21, "x"))
    _fs = float_sentence({"resv": 2000, "upr": 800, "prec": 150, "reins": None, "dac": 100}, 2550, 2025)
    ok("Float sentence: every line read is shown with its figure; an unread one is absent",
       "2,000 + unearned premiums 800" in _fs and "premiums receivable 150" in _fs and "deferred acquisition costs 100" in _fs
       and "reinsurance" not in _fs and "= 2,550" in _fs)
    ok("Float sentence: nothing when float is refused", float_sentence({}, None, 2025) == "")

    # 9. Bank evidence.
    _b = bank_lines({"bnii": 900, "nonii": 400, "nonix": 700, "prov": 50})
    ok("Bank: efficiency = expense / (NII + fees) = 53.8%; pre-provision 600",
       abs(_b["eff"] - 700 / 1300) < 1e-12 and _b["ppnr"] == 600)
    ok("Bank: fees unread → efficiency and pre-provision refused", bank_lines({"bnii": 900, "nonix": 700})["eff"] is None)

    # 10. REIT evidence.
    _ffo, _fr3 = ffo(500, 700, 40, 10)
    ok("FFO: net income + D&A − gains + impairments = 1,170", _ffo == 1170 and _fr3 == "all four lines read")
    _ffo2, _fr4 = ffo(500, 700, None, None)
    ok("FFO: gains and impairments unread → computed, and both named", _ffo2 == 1200 and "gains" in _fr4 and "impairments" in _fr4)
    ok("FFO: D&A unread → refused", ffo(500, None, 40, 10)[0] is None)
    ok("FFO: a loss on sale (negative gain tag) adds back", ffo(500, 700, -30, 0)[0] == 1230)
    ok("Per-share CAGR: 4.00 → 4.86 over five years = 4.0%", abs(per_share_cagr([(2020, 4.0), (2025, 4.0 * 1.04 ** 5)], 5) - 0.04) < 1e-9)
    ok("FFO growth seed: the lower of the two CAGRs, or the one that exists",
       ffo_growth_seed(0.05, 0.03) == 0.03 and ffo_growth_seed(None, 0.03) == 0.03 and ffo_growth_seed(None, None) is None)
    ok("REIT gate: dividend above FFO → refused", "exceeds" in gate_reit(4.0, 4.5, ""))
    ok("REIT gate: covered dividend passes", gate_reit(4.0, 3.0, "") == "")
    ok("REIT gate: no FFO → refused with the reason", "D&A" in gate_reit(None, 3.0, "D&A not read"))

    # 11. The engine. A hand-built stream, then the identities.
    p = FinParams(kind="book", base=100.0, ret0=0.20, retT=0.10, payout=0.5, years=3, exit_mult=1.0)
    s = fin_stream(p)
    # year 1: r=.20 e=20 paid=10 b=110 | year 2: r=.15 e=16.5 paid=8.25 b=118.25 | year 3: r=.10 e=11.825 paid=5.9125 b=124.1625
    ok("Engine: fade is linear, year 1 = normal, year N = terminal", s[0].ret == 0.20 and abs(s[1].ret - 0.15) < 1e-12 and s[2].ret == 0.10)
    ok("Engine: book compounds by retained earnings", abs(s[2].base_end - 124.1625) < 1e-9, f"{s[2].base_end:.4f}")
    _hand = 10 / 1.15 + 8.25 / 1.15 ** 2 + 5.9125 / 1.15 ** 3
    _st, _ex = fin_value(p, 15)
    ok("Engine: discounted stream equals the hand sum", abs(_st - _hand) < 1e-9, f"{_st:.4f} vs {_hand:.4f}")
    ok("Engine: exit = year-N book × multiple, discounted N years", abs(_ex - 124.1625 / 1.15 ** 3) < 1e-9)
    ok("Engine: IV is the sum of the two legs", abs(fin_iv(p, 15) - (_st + _ex)) < 1e-12)
    ok("Engine: one-year horizon uses the normal return", fin_stream(FinParams("book", 100, 0.2, 0.1, 0, 1, 1))[0].ret == 0.2)
    _p0 = FinParams("book", 100.0, 0.20, 0.20, 0.0, 10, 1.0)
    ok("Engine: zero payout → stream is zero and the exit is everything", fin_value(_p0, 15)[0] == 0.0 and value_split(*fin_value(_p0, 15))[1] == 1.0)
    ok("Engine: zero payout → book = base × (1+r)^N", abs(fin_stream(_p0)[-1].base_end - 100 * 1.2 ** 10) < 1e-9)
    _p1 = FinParams("book", 100.0, 0.20, 0.20, 1.0, 10, 1.0)
    ok("Engine: full payout → book never moves", abs(fin_stream(_p1)[-1].base_end - 100) < 1e-12)
    ok("Engine: retained growth identity, year 1 = r × (1 − payout)", derived_growth(0.28, 0.05) == 0.28 * 0.95)
    _lad = fin_ladder(p)
    ok("Ladder: value falls as the required return rises", all(_lad[a] > _lad[b] for a, b in zip(LADDER_RATES, LADDER_RATES[1:])))
    _pr = fin_iv(p, 12)
    _ir = fin_implied_return(_pr, p)
    ok("Implied return: solves back to the rate that produced the price", abs(_ir - 0.12) < 1e-6, f"{_ir:.4%}")
    ok("Implied return: refused on a non-positive price", fin_implied_return(0, p) is None)
    ok("Value split: the two shares sum to one", abs(sum(value_split(30.0, 70.0)) - 1.0) < 1e-12 and value_split(30.0, 70.0)[1] == 0.7)
    ok("Value split: nothing to split → nan, not a division error", value_split(0.0, 0.0)[0] != value_split(0.0, 0.0)[0])
    ok("Exit seed: terminal 19% ÷ 15% = 1.267× book, P/E 6.67", abs(exit_seed("book", 0.19) - 0.19 / 0.15) < 1e-12
       and abs(exit_pe("book", exit_seed("book", 0.19), 0.19) - 1 / 0.15) < 1e-12)
    ok("Exit seed: REIT 1 ÷ 15% = 6.67× FFO, and that IS its P/E", exit_seed("ffo", None) == 1 / 0.15 and exit_pe("ffo", 10.0, None) == 10.0)
    ok("Exit seed: no terminal return → no seed", exit_seed("book", None) is None and exit_pe("book", 1.3, 0.0) is None)

    # 12. A KNSL-shaped worked example, computed independently of the engine.
    #     Filed-shaped inputs only: base 77, normal 28% fading to 19%, payout 5%,
    #     ten years, exit 19%/15%. Every figure below is a seed or a hand loop.
    b, tot, hand_ret = 77.0, 0.0, []
    for t in range(1, 11):
        r = 0.28 + (0.19 - 0.28) * (t - 1) / 9
        e = r * b
        tot += 0.05 * e / 1.15 ** t
        b += e * 0.95
    hand = tot + b * (0.19 / 0.15) / 1.15 ** 10
    kp = FinParams("book", 77.0, 0.28, 0.19, 0.05, 10, exit_seed("book", 0.19))
    ok("KNSL shape: engine equals the hand loop to the cent", abs(fin_iv(kp, 15) - hand) < 0.005, f"{fin_iv(kp, 15):.2f} vs {hand:.2f}")
    _ks, _ke = fin_value(kp, 15)
    ok("KNSL shape: the exit is over 90% of the value (94.2%) — the split has to be printed",
       value_split(_ks, _ke)[1] > 0.90, f"{value_split(_ks, _ke)[1]:.1%}")
    ok("KNSL shape: at $372 the zone is Out Field", zone(372 / fin_iv(kp, 15))[0] == "Out Field", f"{372 / fin_iv(kp, 15):.2f}x")
    kp2 = FinParams("book", 77.0, 0.28, 0.28, 0.05, 10, 3.0)
    ok("KNSL shape: 28% held and a 3× exit is what a Fat Pitch needs", zone(372 / fin_iv(kp2, 15))[0] == "Fat Pitch", f"{372 / fin_iv(kp2, 15):.2f}x")
    # a REIT shape: base 4.20 FFO, 4% growth, 75% payout, exit 6.67x
    rp = FinParams("ffo", 4.20, 0, 0, 0.75, 10, exit_seed("ffo", None), growth=0.04)
    _rs = fin_stream(rp)
    ok("REIT shape: FFO grows at the seed, dividend is the payout of it",
       abs(_rs[0].earnings - 4.20 * 1.04) < 1e-12 and abs(_rs[0].paid - 0.75 * 4.20 * 1.04) < 1e-12 and abs(_rs[-1].base_end - 4.20 * 1.04 ** 10) < 1e-9)
    _rh = sum(0.75 * 4.20 * 1.04 ** t / 1.15 ** t for t in range(1, 11)) + 4.20 * 1.04 ** 10 / 0.15 / 1.15 ** 10
    ok("REIT shape: engine equals the hand loop", abs(fin_iv(rp, 15) - _rh) < 1e-9, f"{fin_iv(rp, 15):.2f}")
    # a bank at book: 12% ROTE, 50% payout, 1x TBV, exit 0.8x
    bp = FinParams("book", 100.0, 0.12, 0.12, 0.5, 10, exit_seed("book", 0.12))
    ok("Bank shape: 12% ROTE at 1× book, half paid out → Just Outside at 15%", zone(100 / fin_iv(bp, 15))[0] == "Just Outside", f"{100 / fin_iv(bp, 15):.2f}x")
    ok("Bank shape: the same bank at 0.6× book is a Fat Pitch", zone(60 / fin_iv(bp, 15))[0] == "Fat Pitch")

    # 13. Gates.
    ok("Gate: no share count → refused", "share count" in gate_shares(None) and gate_shares(23.3) == "")
    ok("Gate: negative tangible equity → refused, points to tool 1", "Tragic Algebra" in gate_tbv(-5.0, "", "insurer"))
    ok("Gate: unread tangible equity → refused with the reason", "not read" in gate_tbv(None, "shareholders' equity not read", "bank"))
    ok("Gate: positive tangible equity passes", gate_tbv(650.0, "", "bank") == "")
    ok("Gate: equity stale against net income → refused, names both years",
       "FY2023" in gate_equity_stale("2023", 2025) and "FY2025" in gate_equity_stale("2023", 2025) and gate_equity_stale("2025", 2025) == "")
    ok("Gate: equity never read → refused", "not read" in gate_equity_stale("—", 2025))
    ok("Gate: net to common unreadable → refused with the preferred reason",
       "preferred" in gate_n_common(None, "preferred stock is on the balance sheet but no preferred-dividend line was read")
       and gate_n_common(50.0, "") == "")
    ok("Gate: history — two readable ROTE years against three needed", "Only 2 years" in gate_history(2, 3, "ROTE") and gate_history(3, 3, "ROTE") == "")
    _hi = Pooled(dE=1.30, sum_N=100, sum_OE=130, sum_omega=0, sum_G=0, years=3)
    ok("Gate: ΔE above 125% → refused, tool 1's rule", gate_dE(_hi)[1] is None and "125%" in gate_dE(_hi)[0])
    _cap = Pooled(dE=1.10, sum_N=100, sum_OE=110, sum_omega=0, sum_G=0, years=3)
    ok("Gate: ΔE 100-125% → applied at 100%", gate_dE(_cap) == ("", 1.0))
    _neg = Pooled(dE=1.07, sum_N=-100, sum_OE=-107, sum_omega=0, sum_G=0, years=3)
    ok("Gate: ΔE on a negative denominator → not projectable, applied at 100% with no refusal", gate_dE(_neg) == ("", 1.0))
    ok("Gate: ordinary ΔE applied as measured", gate_dE(Pooled(dE=0.927, sum_N=100, sum_OE=92.7, sum_omega=0, sum_G=0, years=3))[1] == 0.927)
    ok("Notes: tool 1's net-cash banner, growth-seed, stale-line and fallback-tag sentences dropped, others kept",
       page_notes(["x net cash has been set to zero y", "Revenue is slowing — The seed uses the recent rate.",
                   "Latest revenue growth is 40%, which is a launch rate",
                   "A balance-sheet line here stops ... Net cash above carries the last figure found forward",
                   "Some balance-sheet lines were read from a fallback tag because the preferred one had stopped: x",
                   "Excluded 8.8M shares issued for acquisitions", "keep me"])
       == ["Excluded 8.8M shares issued for acquisitions", "keep me"])
    ok("Cells: None and nan print as a dash", cell(None, "{:.1%}") == "—" and cell(float("nan"), "{:.1f}") == "—" and pct(0.1234) == "12.3%")
    ok("Sanity: FIN_ROWS covers every FIN_BALANCE key once", sorted(k for _, k in FIN_ROWS) == sorted(FIN_BALANCE))
    ok("Sanity: the standalone SIC table names all three classes and the promotion rule",
       all(w in FINANCIAL_SIC_TABLE for w in ("banks", "insurers", "REITs", "deposits", "refused", "ordinary")))
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


# ══════════════════════════════════════════════════════════════════════
#  UI
# ══════════════════════════════════════════════════════════════════════

st.set_page_config(page_title="Financials Checker — banks, insurers, REITs at 15%",
                   page_icon="🏦", layout="centered", initial_sidebar_state="collapsed")
st.title("🏦 Financials Checker")
st.caption("Tangible book, the return on it, what is kept — and the price that returns 15%. "
           "For the three structures the other pages refuse: insurers, banks, REITs.")

if not _sec_contact():
    st.warning(
        "**No SEC contact address set.** The SEC requires a real email in the request header "
        "and blocks generic user agents, so lookups will fail. Add `sec_contact = "
        "\"you@example.com\"` in Streamlit Settings → Secrets, or set a SEC_CONTACT "
        "environment variable locally.")

if "fin_years" not in st.session_state:
    st.info(
        "**Three parts, and which is whose.** The stock-comp adjustment is Burry's Tragic "
        "Algebra, tool 1's engine unchanged — Ω does not care what the company sells. Everything "
        "after it is this app's: the class gate, the class tables, the return on tangible "
        "common equity, growth derived from what is kept, and an exit that hands the next buyer "
        "the same 15%. Net cash, ROIC and the AI-moat tiers do not appear here — a bank's cash is "
        "its inventory and an insurer's investments back its policies. The refusals are this "
        "app's, and they fire often.\n\n"
        "Enter a US-listed ticker. Fee businesses in the 6000s — agents, asset managers, "
        "real-estate services — are sent to the Tragic Algebra Analyzer.")

with st.form("fin_lookup"):
    ticker = st.text_input("Stock ticker", placeholder="KNSL · JPM · O — press Enter").upper().strip()
    submitted = st.form_submit_button("Evaluate", type="primary")

if submitted:
    if not ticker:
        st.warning("Enter a ticker first.")
    else:
        try:
            with st.spinner(f"Reading {ticker} annual filings…"):
                yrs, notes, pre = load(ticker, 10)
            st.session_state.update(fin_years=yrs, fin_notes=notes, fin_pre=pre, fin_tk=ticker)
        except ValueError as e:
            st.error(f"Could not load {ticker}: {e}")
        except Exception as e:
            st.error(
                f"Could not load {ticker} — {type(e).__name__}: {e}\n\n"
                "This is a gap in how the filings were read, not something you did. Filers with "
                "several share classes, recent listings and foreign issuers are the usual causes.")

years = st.session_state.get("fin_years", [])
if years and ticker and st.session_state.get("fin_tk") == ticker:
    notes, pre, tk = st.session_state["fin_notes"], st.session_state["fin_pre"], st.session_state["fin_tk"]
    cls, cls_reason = pre["cls"], pre["cls_reason"]
    alerts = [("info", n) for n in page_notes(notes)]

    def _notes_and_tags():
        with st.expander("Notes and detail", expanded=False):
            for kind_, msg in alerts:
                getattr(st, kind_)(msg)
            st.write("**What was read from the filings** — every tag, found or missing")
            st.dataframe(pd.DataFrame(pre.get("tags", [])), width='stretch', hide_index=True)

    # ══ 1. what kind of company ═══════════════════════════════════════
    st.markdown("---")
    st.subheader(f"What kind of company is this? · {tk}")
    _desc = f"{pre.get('sic_desc') or 'No SIC description'} — "
    if cls in CLASS_NAME:
        st.success(f"**{CLASS_NAME[cls]}.** {_desc}{cls_reason}")
    elif cls == "ordinary":
        st.info(f"**Not this page — an ordinary business.** {_desc}{cls_reason}")
        _notes_and_tags()
        st.stop()
    else:
        st.error(f"**Refused.** {_desc}{cls_reason}")
        _notes_and_tags()
        st.stop()

    rows = build_fin_years(years, pre["fin"], pre["fin_bal"], pre["shares_by_fy"],
                           pre["wavg_by_fy"], pre["n_source"])
    latest = rows[-1]
    fys = [r.fy for r in rows]

    # ══ 2. class evidence ═════════════════════════════════════════════
    st.markdown("---")
    st.subheader(f"{CLASS_NAME[cls]} evidence")
    st.caption("Every cell is a filed number or a named identity on filed numbers. A dash is a "
               "refused cell, not zero. Years marked * are tool 1's excluded capital-event years.")
    _fyl = lambda r: f"{r.fy}*" if r.excluded else str(r.fy)
    if cls == "insurer":
        _uw = {r.fy: underwriting(r.lines) for r in rows}
        _flt = {r.fy: insurance_float(r.bal) for r in rows}
        _mf = money_fmt([v for r in rows for v in (r.lines.get("nep"), r.lines.get("loss"),
                                                    _uw[r.fy]["uwx"], r.lines.get("nii"),
                                                    r.bal.get("inv"), _flt[r.fy][0]) if v is not None])
        _inv_prev = {r.fy: (rows[i - 1].bal.get("inv") if i else None) for i, r in enumerate(rows)}
        _yield = {r.fy: ratio_or_none(r.lines.get("nii"),
                                      (r.bal["inv"] + _inv_prev[r.fy]) / 2 if r.bal.get("inv") is not None and _inv_prev[r.fy] is not None else None)
                  for r in rows}
        st.dataframe(pd.DataFrame([{
            "FY": _fyl(r), "Net premiums earned": cell(r.lines.get("nep"), _mf),
            "Losses & LAE": cell(r.lines.get("loss"), _mf), "Underwriting exp.": cell(_uw[r.fy]["uwx"], _mf),
            "Loss ratio": pct(_uw[r.fy]["lr"]), "Expense ratio": pct(_uw[r.fy]["er"]),
            "Combined": pct(_uw[r.fy]["cr"]), "Underwriting result": cell(_uw[r.fy]["uw"], _mf),
            "Net inv. income": cell(r.lines.get("nii"), _mf), "Invested assets": cell(r.bal.get("inv"), _mf),
            "Yield": pct(_yield[r.fy]), "Float": cell(_flt[r.fy][0], _mf),
            "Cost of float": pct(cost_of_float(_uw[r.fy]["uw"], _flt[r.fy][0])),
        } for r in rows]), width='stretch', hide_index=True)
        _lf = _flt[latest.fy]
        if _lf[0] is not None:
            st.caption(float_sentence(latest.bal, _lf[0], latest.fy)
                       + f" On Buffett's definition, {_lf[1]}. Burry's ROIC slide "
                       "calls customer float productive capital; here it earns through the "
                       "investment yield already in the earnings, and is shown, not priced.")
        else:
            st.caption(f"Float not stated: {_lf[1]}.")
        st.caption("Ratios here are over net earned premiums alone, with the expense numerator "
                   "summed from the acquisition-cost and other-underwriting tags. Kinsale and "
                   "Progressive compute ALL their ratios over net earned premiums PLUS fee income, "
                   "so every published ratio — loss included — sits below its cell here; and the "
                   "two-tag expense sum can run above the filed expense line when ceding-commission "
                   "offsets net into the line but not the tags (Kinsale FY2025: tags 352 against a "
                   "filed 337). For filers whose expense line maps cleanly (Kinsale, Progressive) "
                   "the published combined ratio lands one to three points under this column; a "
                   "filer whose other-underwriting tag carries more than underwriting expense "
                   "(Mercury, about fifteen points) sits much further below. The levels move "
                   "together either way; compare trends here and levels in the 10-K.")
        if _uw[latest.fy]["cr"] is None:
            st.warning("**Combined ratio refused in the latest year.** " +
                       ("Premiums earned were not read." if latest.lines.get("nep") is None else
                        "Losses were not read." if latest.lines.get("loss") is None else
                        "Underwriting expense needs both acquisition-cost amortisation and other "
                        "underwriting expense, or a single total; the tag panel names which is missing.")
                       + " Pricing does not depend on it — ROTE does — but the lever below cannot be stated.")
    elif cls == "bank":
        _bk = {r.fy: bank_lines(r.lines) for r in rows}
        _mf = money_fmt([v for r in rows for v in (r.lines.get("bnii"), r.lines.get("prov"), r.lines.get("nonii"),
                                                    r.lines.get("nonix"), _bk[r.fy]["ppnr"], r.bal.get("dep"), r.bal.get("loans")) if v is not None])
        st.dataframe(pd.DataFrame([{
            "FY": _fyl(r), "Net interest income": cell(r.lines.get("bnii"), _mf), "Provisions": cell(r.lines.get("prov"), _mf),
            "Fee income": cell(r.lines.get("nonii"), _mf), "Noninterest exp.": cell(r.lines.get("nonix"), _mf),
            "Efficiency": pct(_bk[r.fy]["eff"]), "Pre-provision profit": cell(_bk[r.fy]["ppnr"], _mf),
            "Provisions / loans": pct(ratio_or_none(r.lines.get("prov"), r.bal.get("loans")), 2),
            "Deposits": cell(r.bal.get("dep"), _mf), "Loans": cell(r.bal.get("loans"), _mf),
            "Loans / deposits": pct(ratio_or_none(r.bal.get("loans"), r.bal.get("dep"))),
        } for r in rows]), width='stretch', hide_index=True)
        st.caption("Net interest margin is refused: average earning assets are not in XBRL. "
                   "Efficiency = noninterest expense over net interest income plus fees. "
                   "Regulatory capital ratios are out of scope.")
    else:
        _ffo = {r.fy: ffo(r.N_common, r.lines.get("da"), r.lines.get("rgain"), r.lines.get("rimp")) for r in rows}
        _ffops = {r.fy: (_ffo[r.fy][0] / r.wavg if _ffo[r.fy][0] is not None and r.wavg else None) for r in rows}
        _dps = pre.get("dps", {})
        _mf = money_fmt([v for r in rows for v in (r.N_common, r.lines.get("da"), r.lines.get("rgain"),
                                                    r.lines.get("rimp"), _ffo[r.fy][0], r.bal.get("re")) if v is not None])
        st.dataframe(pd.DataFrame([{
            "FY": _fyl(r), "Net income to common": cell(r.N_common, _mf), "D&A": cell(r.lines.get("da"), _mf),
            "Gains on sale": cell(r.lines.get("rgain"), _mf), "Impairments": cell(r.lines.get("rimp"), _mf),
            "FFO": cell(_ffo[r.fy][0], _mf), "Diluted shares (M)": cell(r.wavg, "{:,.1f}"),
            "FFO / share": cell(_ffops[r.fy], "{:.2f}"), "Dividend / share": cell(_dps.get(r.fy), "{:.2f}"),
            "Payout": pct(ratio_or_none(_dps.get(r.fy), _ffops[r.fy])), "Real estate": cell(r.bal.get("re"), _mf),
        } for r in rows]), width='stretch', hide_index=True)
        st.caption(f"Nareit FFO from us-gaap tags; FY{latest.fy}: {_ffo[latest.fy][1]}. D&A and "
                   "impairments are the filer's TOTAL lines — non-real-estate depreciation and "
                   "non-real-estate impairments included where the split is not tagged — and joint-"
                   "venture and minority-interest adjustments are not read, so this column sits a "
                   "cent or two from the published figure (Realty Income FY2025: 4.27 here against "
                   "4.25 published, the gap being total impairments 471 against real-estate-only "
                   "434, FF&E, JV and minority pieces). FFO per share is on weighted-average "
                   "diluted shares, as the companies report it. AFFO is company-defined and not in "
                   "us-gaap; the box below takes it from the 10-K.")

    # ══ 3. tangible book and the return on it ═════════════════════════
    st.markdown("---")
    st.subheader("Tangible common equity and the return on it")
    _mf2 = money_fmt([v for r in rows for v in (r.eq, r.pref, r.gw, r.intan, r.aoci, r.tbv, r.N_common, r.div, r.T) if v is not None])
    st.dataframe(pd.DataFrame([{
        "FY": _fyl(r), "Equity": cell(r.eq, _mf2), "Preferred": cell(r.pref, _mf2), "Goodwill": cell(r.gw, _mf2),
        "Intangibles": cell(r.intan, _mf2), "AOCI": cell(r.aoci, _mf2), "Tangible common": cell(r.tbv, _mf2),
        "Shares (M)": cell(r.shares, "{:,.1f}"), "TBV / share": cell(r.tbvps, "{:.2f}"), "BV / share": cell(r.bvps, "{:.2f}"),
        "Net income to common": cell(r.N_common, _mf2), "ROTE": pct(r.rote),
        "Dividends": cell(r.div, _mf2), "Buybacks": cell(r.T, _mf2),
    } for r in rows]), width='stretch', hide_index=True)
    if cls == "reit":
        st.caption("Shown for the record; a REIT's book is depreciated cost and drives nothing "
                   "below — the pricing is on FFO.")
    st.caption(f"ROTE = net income to common over the average of two year-end tangible common "
               f"equities, so the first year is refused. Net income to common, FY{latest.fy}: "
               f"{latest.n_reason}. Tangible common: {latest.tbv_reason}. AOCI is shown so a rate "
               "swing through book is visible; it is inside the equity figure as filed.")
    _unstated = [f"FY{r.fy}: {r.tbv_reason}" for r in rows if r.tbv is None]
    if _unstated:
        st.warning("**Tangible equity unread in some years** — " + "; ".join(_unstated) + ".")
    _carry = carry_sentence(rows)
    if _carry:
        st.warning(_carry)

    # ══ 4. Tragic Algebra — tool 1's table, identical ═════════════════
    st.markdown("---")
    st.subheader("Tragic Algebra — Burry's stock-comp adjustment, tool 1's engine")
    _medN = median_positive_N([y.N for y in years])
    _mf3 = money_fmt([v for y in years for v in (y.N, y.G, y.omega, y.OE)])
    st.dataframe(pd.DataFrame([{
        "FY": f"{y.fy}*" if y.excluded else str(y.fy), "Net income": cell(y.N, _mf3), "GAAP SBC": cell(y.G, _mf3),
        "Ω true cost": cell(y.omega, _mf3), "Owners' earnings": cell(y.OE, _mf3), "ΔE": pct(dE_row(y, _medN)),
    } for y in years]), width='stretch', hide_index=True)
    try:
        _pooled = pool(years)
    except ValueError:
        _pooled = None
    try:
        _pooled3 = pool_recent(years, 3)
    except ValueError:
        _pooled3 = None
    _dE_ref, _dE_applied = gate_dE(_pooled3)
    if _pooled is not None and _pooled3 is not None:
        st.caption(f"Pooled ΔE {_pooled.dE:.1%} over {_pooled.years} years, {_pooled3.dE:.1%} over the "
                   f"last three. The three-year figure is applied once to the return seeds"
                   + (f" at {_dE_applied:.1%}" if _dE_applied is not None else "")
                   + (", capped at 100% — tool 1's rule" if _dE_applied is not None and dE_was_capped(_pooled3.dE) else "")
                   + (", or at 100% because it is not projectable" if _dE_applied == 1.0 and not dE_projectable(_pooled3) else "")
                   + ". Identical to tool 1 for the same ticker.")

    # ══ 5. gates ══════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Can it be priced?")
    m5, m10, n_ok = rote_medians(rows)
    # A REIT is priced on FFO, not book — its depreciated equity says nothing
    # about the dividend it covers, so the two book gates apply to banks and
    # insurers only.
    refusals = [gate_shares(latest.shares), gate_n_common(latest.N_common, latest.n_reason), _dE_ref]
    if cls != "reit":
        refusals += [gate_equity_stale(pre["fin_bal_fy"].get("eq", "—"), latest.fy),
                     gate_tbv(latest.tbv, latest.tbv_reason, cls)]
    if cls == "reit":
        _ffops_latest = _ffops.get(latest.fy)
        _dps_latest = pre.get("dps", {}).get(latest.fy)
        _fseries = [(r.fy, _ffops.get(r.fy)) for r in rows]
        refusals.append(gate_reit(_ffops_latest, _dps_latest, _ffo[latest.fy][1]))
        refusals.append(gate_history(sum(1 for _, v in _fseries if v is not None), 2, "FFO per share"))
    else:
        refusals.append(gate_history(n_ok, 3, "ROTE"))
    refusals = [r for r in refusals if r]
    if refusals:
        for r in refusals:
            st.error(r)
        st.caption("The tables above stand; nothing below is printed.")
        _notes_and_tags()
        st.stop()
    st.success("**Priced.** " + ("Share count, FFO, the dividend and the history it needs were all read."
                                if cls == "reit" else
                                "Share count, tangible common equity and the history it needs were all read."))

    # ══ 6. judgement ══════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Judgement — every box is a seed you can move")
    _pay = fin_payout(rows)
    _pay_seed = min(max(_pay.ratio if _pay.ratio is not None else 0.0, 0.0), 1.5)
    _key = f"fin_{tk}_"
    if cls == "reit":
        _c5 = per_share_cagr(_fseries, 5)
        _c10 = per_share_cagr(_fseries, len(_fseries))
        _g_seed = ffo_growth_seed(_c5, _c10) or 0.0
        _pay_reit = ratio_or_none(_dps_latest, _ffops_latest) or 0.0
        c1, c2, c3 = st.columns(3)
        _affo = c1.number_input("AFFO per share (from the 10-K, optional)", min_value=0.0, value=0.0, step=0.01,
                                key=_key + "affo", help="Company-defined; not in us-gaap. Zero prices FFO.")
        _base_used = _affo if _affo > 0 else _ffops_latest
        _growth = c2.number_input("FFO growth (%)", value=round(_g_seed * 100, 1), step=0.5, key=_key + "g",
                                  help=f"Seed: the lower of the 5-year ({pct(_c5)}) and full-window ({pct(_c10)}) FFO-per-share CAGRs.") / 100
        _payout = c3.number_input("Payout of FFO (%)", value=round(_pay_reit * 100, 1), step=1.0, key=_key + "p",
                                  help=f"Seed: dividend per share over FFO per share, FY{latest.fy}.") / 100
        c4, c5 = st.columns(2)
        _years = int(c4.number_input("Years", min_value=1, max_value=30, value=HORIZON_DEFAULT, step=1, key=_key + "n"))
        _exit = c5.number_input("Exit multiple of FFO", value=round(exit_seed("ffo", None), 2), step=0.25, key=_key + "x",
                                help="Seed: 1 ÷ 15% — the next buyer earns 15% on a level FFO stream.")
        params = FinParams("ffo", _base_used, 0.0, 0.0, _payout, _years, _exit, growth=_growth)
        _ret0 = _retT = None
    else:
        _ret0_seed = (m5 or 0.0) * (_dE_applied or 1.0)
        _retT_seed = (terminal_seed(m5, m10) or 0.0) * (_dE_applied or 1.0)
        c1, c2, c3 = st.columns(3)
        _ret0 = c1.number_input("Normal-year ROTE (%)", value=round(_ret0_seed * 100, 1), step=0.5, key=_key + "r0",
                                help=f"Seed: 5-year median of filed ROTE ({pct(m5)}) × applied ΔE.") / 100
        _retT = c2.number_input("Terminal ROTE (%)", value=round(_retT_seed * 100, 1), step=0.5, key=_key + "rT",
                                help=f"Seed: the lower of the 5-year ({pct(m5)}) and full-window ({pct(m10)}) medians × applied ΔE. Faded to linearly.") / 100
        _payout = c3.number_input("Payout (%)", value=round(_pay_seed * 100, 1), step=1.0, key=_key + "p",
                                  help=f"Seed: dividends + filed buybacks over net income to common, FY{fys[-_pay.years]}–{fys[-1]} pooled.") / 100
        c4, c5 = st.columns(2)
        _years = int(c4.number_input("Years", min_value=1, max_value=30, value=HORIZON_DEFAULT, step=1, key=_key + "n"))
        _xs = exit_seed("book", _retT) or 0.0
        _exit = c5.number_input("Exit multiple of tangible book", value=round(_xs, 2), step=0.05, key=_key + "x",
                                help="Seed: terminal ROTE ÷ 15% — the next buyer earns 15% on a level stream, no growth.")
        params = FinParams("book", latest.tbvps, _ret0, _retT, _payout, _years, _exit)
        _g1 = derived_growth(_ret0, _payout)
        _gc = tbvps_cagr(rows)
        st.caption(f"Growth is derived, not a box: {pct(_ret0)} × (1 − {pct(_payout)}) = **{pct(_g1)}** in "
                   f"year 1, fading with the return. Filed tangible book per share compounded at "
                   f"{pct(_gc)} over the last five steps" + (" — consistent." if _gc is not None and abs(_g1 - _gc) < 0.05 else
                   " — a gap worth explaining before trusting the seed." if _gc is not None else ".")
                   + " Buybacks are treated as cash returned; a buyback above book lowers the filed "
                   "per-share figure, which is one reason the two can differ.")
        _pe = exit_pe("book", _exit, _retT)
        st.caption(f"Exit {_exit:.2f}× tangible book at a terminal ROTE of {pct(_retT)} is a P/E of "
                   f"**{cell(_pe, '{:.1f}')}×** in year {_years}. Traded multiples for the class sit two to "
                   "three times higher; the grid shows them.")
        if cls == "insurer":
            _tx, _txr = effective_tax(latest.lines)
            _avg_tbv = (latest.tbv + rows[-2].tbv) / 2 if len(rows) > 1 and rows[-2].tbv is not None else None
            _lev = cr_lever(latest.lines.get("nep"), _avg_tbv, _tx)
            _crs = [_uw[r.fy]["cr"] for r in rows if _uw[r.fy]["cr"] is not None]
            if _lev is not None and _uw[latest.fy]["cr"] is not None:
                st.caption(lever_sentence(_lev, _uw[latest.fy]["cr"], median_of(_crs), latest.fy, _tx, _txr))

    # ══ 7. verdict ════════════════════════════════════════════════════
    st.markdown("---")
    price = current_price(tk) or 0.0
    _iv15 = fin_iv(params, 15)
    _streamv, _exitv = fin_value(params, 15)
    _sh_s, _sh_e = value_split(_streamv, _exitv)
    _ratio = price / _iv15 if _iv15 > 0 else -1.0
    _zone, _sev = zone(_ratio) if price > 0 else ("No price", "warning")
    st.subheader(f"Verdict · {tk}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("IV15 / share", f"${_iv15:,.2f}")
    m2.metric("Price", f"${price:,.2f}" if price > 0 else "n/a")
    m3.metric("Price / IV15", f"{_ratio:.2f}x" if price > 0 and _iv15 > 0 else "n/a")
    _ir = fin_implied_return(price, params) if price > 0 else None
    m4.metric("Implied return", f"{_ir:.1%}" if _ir is not None else "n/a",
              help="The required return at which the value equals today's price.")
    getattr(st, _sev)(f"**{_zone}.** " + (
        f"At {d(price)} against an IV15 of {d(_iv15)} the price is {_ratio:.2f}× the value at 15%."
        if price > 0 else "No current price could be fetched, so no zone."))
    st.markdown(f"**Where the value comes from.** Owner's stream {d(_streamv)} per share "
                f"(**{pct(_sh_s, 0)}**) and the year-{params.years} sale {d(_exitv)} per share "
                f"(**{pct(_sh_e, 0)}**). " + (
                "Nearly all of this IV15 is the exit — a Fat Pitch on this page would be a bet on the "
                "exit multiple, not on the cash the business hands you." if _sh_e == _sh_e and _sh_e > 0.8 else
                "The stream carries most of the value, so the exit multiple is doing less work here."))
    _lad = fin_ladder(params)
    st.dataframe(pd.DataFrame([{"Required return": f"{n}%", "Value / share": f"${_lad[n]:,.2f}",
                                "Price / value": (f"{price / _lad[n]:.2f}x" if price > 0 and _lad[n] > 0 else "—")}
                               for n in LADDER_RATES]), width='stretch', hide_index=True)

    # the two grids
    st.write("**Sensitivity — terminal return × exit multiple** (price / IV15)" if cls != "reit"
             else "**Sensitivity — FFO growth × exit multiple** (price / IV15)")
    if cls != "reit":
        _rT_grid = [max(0.01, _retT + dlt) for dlt in (-0.06, -0.03, 0.0, 0.03, 0.06)]
        _x_grid = sorted({1.0, round(_exit, 2), 2.0, 3.0})
        _gr = []
        for rT in _rT_grid:
            _row = {"Terminal ROTE": pct(rT)}
            for x in _x_grid:
                _v = fin_iv(FinParams("book", params.base, params.ret0, rT, params.payout, params.years, x), 15)
                _row[f"{x:.2f}× book"] = f"{price / _v:.2f}x" if price > 0 and _v > 0 else "—"
            _gr.append(_row)
    else:
        _g_grid = [_growth + dlt for dlt in (-0.04, -0.02, 0.0, 0.02, 0.04)]
        _x_grid = sorted({round(_exit, 2), 10.0, 14.0, 18.0})
        _gr = []
        for g_ in _g_grid:
            _row = {"FFO growth": pct(g_)}
            for x in _x_grid:
                _v = fin_iv(FinParams("ffo", params.base, 0, 0, params.payout, params.years, x, growth=g_), 15)
                _row[f"{x:.1f}× FFO"] = f"{price / _v:.2f}x" if price > 0 and _v > 0 else "—"
            _gr.append(_row)
    st.dataframe(pd.DataFrame(_gr), width='stretch', hide_index=True)
    st.write("**Sensitivity — payout × years** (price / IV15)")
    _gr2 = []
    for py in (0.0, 0.25, 0.5, 0.75, 1.0):
        _row = {"Payout": pct(py, 0)}
        for ny in (5, 10, 15):
            _pp = FinParams(params.kind, params.base, params.ret0, params.retT, py, ny, params.exit_mult, growth=params.growth)
            _v = fin_iv(_pp, 15)
            _row[f"{ny} years"] = f"{price / _v:.2f}x" if price > 0 and _v > 0 else "—"
        _gr2.append(_row)
    st.dataframe(pd.DataFrame(_gr2), width='stretch', hide_index=True)

    # ══ 8. assumptions ════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Assumptions used")
    _a = [f"class            {CLASS_NAME[cls]} — {cls_reason}",
          f"shares           {latest.shares:,.1f}M year-end FY{latest.fy} (tool 1's route)",
          f"price            ${price:,.2f}" if price > 0 else "price            not fetched"]
    if cls == "reit":
        _a += [f"base             FFO/share {_ffops_latest:.2f} FY{latest.fy} (Nareit, us-gaap tags: {_ffo[latest.fy][1]})"
               + (f"; priced on AFFO {_affo:.2f} entered from the 10-K" if _affo > 0 else ""),
               f"dividend/share   {_dps_latest:.2f} FY{latest.fy} filed; payout {pct(_payout)}",
               f"growth           {pct(_growth)} — seed lower of 5y {pct(_c5)} / window {pct(_c10)} FFO/share CAGR",
               f"years            {_years}",
               f"exit             {_exit:.2f}× FFO — seed 1 ÷ 15% (P/E {cell(exit_pe('ffo', _exit, None), '{:.1f}')}×)"]
    else:
        _a += [f"base             TBV/share ${latest.tbvps:,.2f} FY{latest.fy} — {latest.tbv_reason}",
               f"net to common    FY{latest.fy}: {latest.n_reason}",
               f"ROTE filed       5y median {pct(m5)} · window median {pct(m10)} ({n_ok} years readable)",
               f"ΔE applied       {pct(_dE_applied)} (three-year pooled, tool 1's rule)",
               f"normal ROTE      {pct(_ret0)} — seed 5y median × ΔE",
               f"terminal ROTE    {pct(_retT)} — seed lower median × ΔE, faded to linearly",
               f"payout           {pct(_payout)} — seed dividends {_pay.dividends:,.0f} + buybacks {_pay.buybacks:,.0f} "
               f"over net to common {_pay.n_common:,.0f}, FY{fys[-_pay.years]}–{fys[-1]}",
               f"growth           derived {pct(derived_growth(_ret0, _payout))} year 1 — filed TBV/share CAGR {pct(tbvps_cagr(rows))}",
               f"years            {_years}",
               f"exit             {_exit:.2f}× tangible book — seed terminal ROTE ÷ 15% (P/E {cell(exit_pe('book', _exit, _retT), '{:.1f}')}×)"]
    _a += [f"IV15             ${_iv15:,.2f} = stream ${_streamv:,.2f} ({pct(_sh_s, 0)}) + exit ${_exitv:,.2f} ({pct(_sh_e, 0)})",
           "not read         net cash, ROIC, NIM, NAV, regulatory capital — refused on this page by design"]
    st.code("\n".join(_a))

    _notes_and_tags()

with st.expander("Verify the engine", expanded=False):
    _res = self_test()
    _sev, _txt = test_summary(_res)
    getattr(st, _sev)(_txt)
    for _name, _ok, _got in _res:
        st.write(("✅ " if _ok else "❌ ") + _name + (f" — {_got}" if _got else ""))

st.caption("Research aid, not financial advice. Outputs depend on estimates you supply. The "
           "stock-comp adjustment is Burry's Tragic Algebra; the class gate, tangible-book pricing "
           "and every refusal are this app's own design.")
