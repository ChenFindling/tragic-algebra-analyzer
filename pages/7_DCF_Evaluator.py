"""
DCF Evaluator
=============
The standard two-stage free-cash-flow DCF — the model every valuation site
prints — with its assumptions in boxes and its blind spots displayed, so
nobody needs a paid tool for it. And one thing no other site prints: the
same DCF run a second time on SBC-corrected cash flow.

THREE PARTS, AND WHICH IS WHOSE
-------------------------------
1. The model is Aswath Damodaran's two-stage FCFF DCF, implemented from his
   published framework: free cash flow to the firm, a growth stage, a
   terminal value at a rate below the discount rate, less net debt, per
   share. He is named on the page the way Burry and Mayer are on theirs.
2. The signature is this kit's: two legs side by side. Leg 1 is the DCF on
   the standard definition — cash from operations less capex, which leaves
   stock comp costing nothing, because the GAAP charge expensed in net
   income is added straight back inside CFO. Leg 2 is the same DCF with the
   true SBC cost Omega from the shared Year machinery subtracted. The
   income-side correction is -(Omega - G) because net income charges G; the
   cash-side correction is the full -Omega because CFO's add-back already
   neutralised G. All of Omega's cash components (withholding, option
   proceeds, buybacks) sit in the financing section, so nothing is counted
   twice. The engine is linear in the base cash flow, so with every other
   input shared the gap between the legs IS the discounted Omega stream —
   the self-test pins that identity to the cent.
3. The refusals, the grid, and the defaults are this app's. The defaults
   are conventions in boxes, stated as such. The rule that does not move:
   the page never prints a number it cannot stand behind.

The reader below is page 4's, copied verbatim (a Streamlit page cannot be
imported without executing its UI) minus the three operating lines only
page 4 projects (OI, GP, COGS). CFO and capex stay — page 4 first added
them on 31 Aug 2026 and this page is why they are shared. The financial
gate is tool 1's paste of page 5's standalone block, decisions unchanged,
and load()'s financial branch is tool 1's post-gate-fix block rather than
page 4's older is_financial note, so ordinary fee businesses in the 6000s
are valued with net cash read.

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


# (The IV15 ladder that sits here on pages 1 and 4 is not carried: this page
# discounts free cash flow on its own two-stage engine below, and nothing in
# the shared reader references the ladder.)



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
    # ── CFO and capex, copied from page 4, which first added them on
    # 31 Aug 2026 (its OI / GP / COGS lines are not carried — this page
    # discounts cash flow, not margins). CFO keeps its sign — a cash-burning
    # year is a finding, not a hole — and capex reads as an outflow.
    "CFO":  (["NetCashProvidedByUsedInOperatingActivities",
              "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
             ["CashFlowsFromUsedInOperatingActivities"]),
    "CAPEX": (["PaymentsToAcquirePropertyPlantAndEquipment",
               "PaymentsToAcquireProductiveAssets"],
              ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"]),
    # IFRS fallbacks kept verbatim from page 4 (Grab, 1 Sep 2026), though a
    # pure-IFRS filer is refused toward the Non-US page before they matter.
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
             "CFO"}   # the cash-flow line this page adds beyond tool 1's list


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
    if _xr:
        notes.extend(_xr["notes"])
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
                   "capital event, most often an all-stock acquisition.")
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
    fin_class, fin_reason = financial_class(sic, facts)
    if fin_class in ("bank", "insurer", "reit"):
        # KNSL, 1 Sep 2026: the old banner disclaimed the number and the
        # verdict still printed a fat pitch on 18% premium growth at a
        # software exit. The Financials Checker exists now; route, withhold.
        notes.append(f"{sic_desc or 'Financial company'} (SIC {sic}). {fin_reason} Investments "
                     "here back policyholder or depositor liabilities rather than belonging to "
                     "shareholders, so net cash has been set to zero. The Tragic Algebra below "
                     "is real; the valuation frame is not — "
                     f"{ {'bank': 'a bank', 'insurer': 'an insurer', 'reit': 'a REIT'}[fin_class] } "
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
    # ── Added for this page: CFO and capex by fiscal year, in $M, for the
    # same window as `years`. Absent means the tag did not answer for that
    # year; the page refuses the cell rather than reading zero. CFO keeps its
    # sign; capex is an outflow and reads positive. `shares_by_fy` is the
    # split-restated year-end count, the same series every dS above was built on.
    _signed = lambda k, fy: (series[k][fy][2] / 1e6) if fy in series.get(k, {}) else None
    _outflow = lambda k, fy: (abs(series[k][fy][2]) / 1e6) if fy in series.get(k, {}) else None
    _trend = {fy: {"rev": _signed("REV", fy),
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
                          "sic_desc": sic_desc,
                          "financial": fin_class in ("bank", "insurer", "reit", "refused"),
                          "fin_class": fin_class, "fin_reason": fin_reason}


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
#  DCF — the two-stage engine (Damodaran's model; the legs are this kit's)
# ══════════════════════════════════════════════════════════════════════
#
# Everything from here to the end is new. The reader above is page 4's,
# which is tool 1's plus CFO and capex. The line between the two matters:
# nothing below changes a figure the other pages compute.
#
# The model: Aswath Damodaran's two-stage FCFF DCF, from his published
# framework. Free cash flow to the firm grows at a stage-1 rate for a
# stated number of years, a Gordon terminal value at a rate below the
# discount rate prices everything after, the whole stream is discounted at
# a required return, net debt comes off, and the remainder is divided by
# the share count. Stage 1 is a CONSTANT rate (settled 8 Sep 2026): that
# is what the public sites leg 1 must be comparable to run, and it is what
# a synthetic case checks by hand to the cent. A fade is a v2 toggle.
#
# THE TWO LEGS, AND WHY THE CORRECTION IS THE FULL OMEGA (settled 8 Sep
# 2026 — the brief's first draft said Omega - G and was wrong). Net income
# charges the GAAP stock-comp expense G, so the income-side correction is
# -(Omega - G): that is tool 1's OE = N + G - Omega. But CFO adds G
# straight back, so leg 1's cash flow (CFO - capex) charges NOTHING for
# stock comp — that is the standard convention, and the reason the public
# sites' FCF figures treat share issuance to employees as free. The
# correction to a figure that charges nothing is the whole cost:
#
#     leg 1:  FCF  = CFO - capex
#     leg 2:  FCF' = CFO - capex - Omega
#
# Subtracting Omega - G instead undercharges by exactly G, and on a
# buyback-heavy year it inverts the sign: Adobe FY2025 has Omega 370
# against G 1,942, so an Omega - G leg would print stock comp ADDING
# $1.57B a year to Adobe's cash flow. No double-count in the full
# subtraction either: withholding, option proceeds and buybacks — all of
# Omega's cash components — sit in the financing section, not in CFO.
#
# The engine is linear in the base cash flow. Both legs share the growth
# rate (seeded once, from leg 1's history), the years, the terminal rate
# and the discount rate, so the gap between them IS the engine run on the
# Omega base alone — pinned to the cent by the self-test. Seeding each leg
# separately would mix a growth disagreement into a page whose whole point
# is that only the SBC definition moves between the columns.

from dataclasses import replace


def d(x, dp=2):
    """Escaped dollar amount, safe inside markdown."""
    return f"\\${x:,.{dp}f}"


DCF_YEARS_DEFAULT = 10        # a convention in a judgement box, not evidence
DISCOUNT_DEFAULT = 0.10       # plain required return; WACC is display-only
TERMINAL_DEFAULT = 0.025      # capped below the discount rate, refused at it
FCF_GROWTH_FLOOR = -0.10      # tool 1's revenue-seed band, unchanged
FCF_GROWTH_CAP = 0.25
OMEGA_MEDIAN_YEARS = 5        # the base correction pools this many years
OMEGA_MIN_USABLE = 3          # fewer usable years than this refuses leg 2
FCF_MIN_YEARS = 4             # same minimum every page applies to its window


@dataclass
class DCFParams:
    """Everything the engine needs. Dollars in $M, shares in millions."""
    base_fcf: float           # year-0 free cash flow
    growth: float             # stage-1 rate, decimal
    years: int                # stage-1 length
    discount: float           # required return, decimal
    terminal: float           # terminal growth, decimal
    net_debt: float           # debt less deployable cash; negative = net cash
    shares: float             # diluted, millions


def dcf_stream(p: DCFParams) -> list[float]:
    """Stage-1 cash flows, year 1 through year `years`."""
    return [p.base_fcf * (1 + p.growth) ** t for t in range(1, p.years + 1)]


def dcf_enterprise(p: DCFParams) -> tuple[float, float, float]:
    """(PV of stage 1, PV of terminal value, enterprise value), $M.

    Refuses — raises, never clamps — when the Gordon denominator is not
    positive. A terminal rate at or above the discount rate prices the tail
    at infinity, and quietly capping it would print a number built on an
    input the model cannot hold.
    """
    if p.terminal >= p.discount:
        raise ValueError("terminal growth at or above the discount rate")
    if p.discount <= 0 or p.years < 1:
        raise ValueError("discount rate must be positive and years at least 1")
    s = dcf_stream(p)
    pv1 = sum(cf / (1 + p.discount) ** t for t, cf in enumerate(s, 1))
    tv = s[-1] * (1 + p.terminal) / (p.discount - p.terminal)
    pv_tv = tv / (1 + p.discount) ** p.years
    return pv1, pv_tv, pv1 + pv_tv


def dcf_per_share(p: DCFParams) -> float:
    """Equity value per share, dollars."""
    if p.shares <= 0:
        raise ValueError("no share count")
    return (dcf_enterprise(p)[2] - p.net_debt) / p.shares


def rate_grid(p: DCFParams, rates: list[float], terms: list[float]) -> list[list[float | None]]:
    """Value per share across discount rate x terminal growth. Every cell is
    the same engine call with one input moved (pages 4 and 5's pattern). A
    cell whose terminal rate reaches its discount rate is refused, not
    computed — None renders as the em dash with the reason in the caption."""
    return [[dcf_per_share(replace(p, discount=r, terminal=t)) if t < r else None
             for t in terms] for r in rates]


def growth_years_grid(p: DCFParams, growths: list[float], yearss: list[int]) -> list[list[float]]:
    """Value per share across stage-1 growth x stage-1 years."""
    return [[dcf_per_share(replace(p, growth=g, years=n)) for n in yearss]
            for g in growths]


# ── the base, and where it comes from ─────────────────────────────────

BASE_FROM_LATEST = "the latest fiscal year's free cash flow"
BASE_FROM_MEDIAN = ("the 5-year median — the latest year is a loss on a record whose "
                    "median is positive, the same rule tool 1's owners'-earnings seed uses")


def median5_fcf(fcf: list[tuple[int, float]]) -> float:
    """Median of the last five FCF years read (fewer if fewer exist)."""
    vals = [v for _, v in fcf[-5:]]
    return statistics.median(vals) if vals else 0.0


def seed_base_fcf(latest: float, median5: float) -> tuple[float | None, str]:
    """The base free cash flow and the sentence that says where it came from.
    None means there is nothing to discount — the page routes to page 4."""
    if latest > 0:
        return latest, BASE_FROM_LATEST
    if median5 > 0:
        return median5, BASE_FROM_MEDIAN
    return None, ""


def nothing_to_discount(latest: float, median5: float) -> str:
    """The refusal for a company with no positive base. Fires on the same
    shape page 4 refuses from the margin side, and says so: the two pages
    must visibly agree on a RIVN. Worded to stay true when years alternate
    in sign — 'no positive base' is the fact; 'every year negative' would
    sometimes be false (the misdescribing-note rule)."""
    return (f"**Nothing to discount.** The latest year's free cash flow is {latest:,.0f}M and "
            f"the 5-year median is {median5:,.0f}M — no positive base exists to project, and a "
            "DCF run on a negative stream prices the company below zero and says nothing. "
            "Whether the burn inflects is not a DCF question — it is the Inflection Checker's, "
            "which projects the operating-margin trend instead of the cash flow, and refuses "
            "on its own terms when even that trend gives nothing to project. Use page 4.")


def few_fcf_years_refusal(n: int) -> str:
    return (f"**Only {n} year(s) of free cash flow were read** — both cash from operations and "
            f"the window have to hold at least {FCF_MIN_YEARS} years before a growth rate seeded "
            "from this history means anything. The tag panel below shows which lines answered "
            "and through which year.")


def stale_fcf_refusal(latest_fy: int, today_year: int) -> str:
    """Same disease, same threshold as the shared window guard: BKNG printed
    a full verdict on eight years ending FY2015. Two years is ordinary
    reporting lag for a December filer read in January; three is a hole."""
    if today_year - latest_fy <= STALE_VS_TODAY:
        return ""
    return (f"**The cash-flow window is stale.** The newest free cash flow read is FY{latest_fy}, "
            f"{today_year - latest_fy} years behind today. Valuing it would price a company that "
            "may no longer exist in that shape — the Booking Holdings failure, refused here "
            "rather than repeated.")


# ── the Omega correction ──────────────────────────────────────────────

def omega_correction(years: list["Year"]) -> tuple[float | None, float | None, int]:
    """(median-5 Omega, latest usable Omega, usable-year count), $M.

    Usable = non-excluded AND priced. An excluded year's Omega is capital
    formation, not pay; an unpriced year floors V at zero and understates
    the cost (the BKNG price-coverage failure). Per-year Omega is lumpy —
    Adobe's FY2025 is 370 against a GAAP charge of 1,942 purely on buyback
    timing — so the base correction is the MEDIAN of the last five usable
    years, a convention named in the assumptions block, with every year's
    Omega printed in the history table so the lumpiness is on the page.
    Fewer than OMEGA_MIN_USABLE usable years returns (None, None, n): leg 2
    refuses while leg 1 stands.
    """
    usable = [y for y in years if not y.excluded and y.price > 0]
    if len(usable) < OMEGA_MIN_USABLE:
        return None, None, len(usable)
    pool = usable[-OMEGA_MEDIAN_YEARS:]
    return statistics.median(y.omega for y in pool), pool[-1].omega, len(usable)


def omega_swallows(base_fcf: float, om: float | None) -> bool:
    """Leg 2's base would be non-positive: refuse leg 2 out loud instead of
    burying the finding in a negative DCF (settled 8 Sep 2026)."""
    return om is not None and om >= base_fcf


def leg2_swallowed(base_fcf: float, om: float) -> str:
    return (f"**Leg 2 refused — the measured cost of stock comp swallows the cash flow.** "
            f"The Omega correction ({om:,.0f}M, median of the last five usable years) is at "
            f"least the base free cash flow ({base_fcf:,.0f}M): there is nothing left to "
            "discount. That IS the finding for this company, not a formatting gap — the "
            "standard leg beside this prints a value, and the whole of it rests on pricing "
            "stock comp at zero.")


def leg2_typed_shares(n_usable: int = 0) -> str:
    return ("**Leg 2 refused — the share count was typed.** Ω prices the shares delivered "
            "from the filed year-end counts, and none were read: a typed cover-page count "
            "feeds the per-share division, not the year-by-year share change the Ω "
            "measurement is built on. The standard leg runs on the typed count; the "
            "correction would be a number invented to fill a column.")


def leg2_unmeasured(n_usable: int) -> str:
    return (f"**Leg 2 refused — Omega is unmeasured across most of the window.** Only "
            f"{n_usable} year(s) are both priced and free of capital events, and the true "
            f"SBC cost needs the year's average price to value the shares delivered. Fewer "
            f"than {OMEGA_MIN_USABLE} such years is a correction built on air; the standard "
            "leg stands on its own definition.")


# ── the growth seed (tool 1's revenue-seed shape, on FCF history) ─────

def fcf_growth_seed(fcf: list[tuple[int, float]]) -> tuple[float, float | None, float | None]:
    """(capped seed, raw latest YoY, 5-year CAGR).

    Seed from the LATEST year-over-year rate, not a trailing CAGR — tool
    1's Paycom reasoning, unchanged. Both ends of a rate must be positive
    for the rate to mean anything; with no usable pair the seed stays at
    the 8% default and the assumptions block says so. FCF is lumpier than
    revenue (working-capital swings), which is exactly what the judgement
    box is for — the 5-year CAGR prints beside the seed so the trend is
    visible (settled 8 Sep 2026).
    """
    growth, raw, cagr5 = 0.08, None, None
    vals = dict(fcf)
    fys = sorted(vals)
    if len(fys) >= 2 and vals[fys[-2]] > 0 and vals[fys[-1]] > 0:
        raw = vals[fys[-1]] / vals[fys[-2]] - 1
    if len(fys) >= 6 and vals[fys[-6]] > 0 and vals[fys[-1]] > 0:
        cagr5 = (vals[fys[-1]] / vals[fys[-6]]) ** (1 / 5) - 1
    if raw is not None:
        growth = max(FCF_GROWTH_FLOOR, min(raw, FCF_GROWTH_CAP))
    return growth, raw, cagr5


def fcf_trend_note(raw: float | None, cagr5: float | None) -> str:
    """Tool 1's accel/decel note, in pattern, on the FCF series."""
    if raw is None or cagr5 is None:
        return ""
    if cagr5 - raw > 0.05:
        return (f"Free cash flow is {growth_trend_phrase(cagr5, raw)} — {cagr5:.1%} a year "
                f"over five years but {raw:.1%} in the latest. The seed uses the recent rate; "
                "FCF also swings with working capital, so satisfy yourself the latest year is "
                "a rate and not an event before trusting either figure.")
    if raw - cagr5 > 0.05:
        return (f"Free cash flow is {growth_trend_phrase(cagr5, raw)} — {cagr5:.1%} a year "
                f"over five years, {raw:.1%} in the latest. The seed uses the recent rate; "
                "satisfy yourself it is durable and not a working-capital swing.")
    return ""


def fcf_cap_note(raw: float | None, growth: float) -> str:
    if raw is None or abs(raw - growth) <= 1e-9:
        return ""
    return (f"Latest FCF growth is {raw:.0%}, outside the [{FCF_GROWTH_FLOOR:.0%}, "
            f"{FCF_GROWTH_CAP:.0%}] band tool 1 applies to its revenue seed — capped at "
            f"{growth:.0%} for the seed. Nothing compounds outside that band for a decade; "
            "if you believe otherwise, that belief belongs in the growth box, stated as yours.")


# ── the WACC estimate (display-only, by decision of 8 Sep 2026) ───────

def wacc_estimate(mcap: float, debt: float, rf: float, erp: float, beta: float,
                  kd: float, tax: float) -> tuple[float, float, float, float] | None:
    """(WACC, cost of equity, equity weight, debt weight), all decimals.

    The weights are read from filings and the price source (market cap,
    funded debt). The components are TYPED, with dated defaults: a live
    risk-free rate or a regression beta would need data from outside EDGAR
    and the price source, which is out of scope by design. The estimate is
    display-only — the engine always runs on the plain required-return box,
    and if you want the WACC there, you type it there yourself. Burry calls
    WACC false precision; the rate x terminal grid below shows what the
    rate alone does to the answer, which is the honest version of the same
    warning."""
    e, dd = max(mcap, 0.0), max(debt, 0.0)
    if e + dd <= 0:
        return None
    ke = rf + beta * erp
    we, wd = e / (e + dd), dd / (e + dd)
    return we * ke + wd * kd * (1 - tax), ke, we, wd


# ── the financial gate's banner (decisions are page 5's, unchanged) ───

def dcf_financial_banner(fin_class: str, fin_reason: str, sic_desc, sic) -> str:
    """Refused BEFORE the table, page 4's Oscar Health reasoning: a bank's
    CFO - capex is a filed number but not an operating company's free cash
    flow, and printing ten years of it invites exactly the misuse the
    refusal exists to stop."""
    kind = {"bank": "a bank", "insurer": "an insurer", "reit": "a REIT"}.get(fin_class)
    head = f"**Not this page — {kind}.** " if kind else "**Not this page.** "
    body = f"{sic_desc or 'Financial company'} (SIC {sic}). {fin_reason} "
    tail = ("A DCF discounts free cash flow — cash from operations less capex — and a "
            "financial's cash from operations moves with deposits, premiums or loan books, "
            "not with anything that line measures for an operating company. Damodaran's own "
            "framework prices financials on dividends or free cash flow to equity, never "
            "FCFF. ")
    tail += ("The Financials Checker page prices banks, insurers and equity REITs on "
             "tangible book, returns and payout — use it for the verdict." if kind else
             "No page in this kit prices this class yet.")
    return head + body + tail


# ── carried verbatim from page 4 (its lines around 3444-3466): the seed-note
# filter — this page also replaces tool 1's revenue seed with its own — and
# the refused-cell formatter with its Uber lesson.
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


# ── rows for the history table ────────────────────────────────────────

def fcf_rows(years: list["Year"], trend: dict) -> list[dict]:
    """One row per fiscal year in the shared window: CFO, capex, leg-1 FCF,
    Omega, leg-2 FCF. A missing CFO refuses the row's FCF cells rather than
    reading zero; a missing capex follows page 4's runway convention and
    reads zero WITH the years named in the caption. Omega is blank for
    excluded or unpriced years — the correction never uses them either."""
    rows = []
    for y in years:
        t = trend.get(y.fy, {})
        cfo, capex = t.get("cfo"), t.get("capex")
        fcf1 = None if cfo is None else cfo - (capex or 0.0)
        om = y.omega if (not y.excluded and y.price > 0) else None
        rows.append({"fy": y.fy, "cfo": cfo, "capex": capex, "fcf1": fcf1,
                     "omega": om, "fcf2": None if (fcf1 is None or om is None) else fcf1 - om,
                     "excluded": bool(y.excluded)})
    return rows


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
    """The right-reason refusal for umbrella-partnership C-corps."""
    return (
        f"**Valuation withheld — Up-C structure.** {ticker}'s per-class share counts "
        f"were read from its filings' own XBRL instances and summed "
        f"({total_m:,.1f}M across the classes), so the table, the true SBC cost and "
        "the ΔE pools above are real measurements. But this is an "
        "umbrella-partnership C-corp: a large share of the economics sits in LLC "
        "units outside the parent company — for Ryan Specialty over half the summed "
        "count, for Carvana roughly a third — while the net income read here is the "
        "parent's slice only. Dividing the parent's slice by the full count would "
        "understate every per-share figure by about that fraction, so no per-share "
        "value or verdict is printed. This lifts when the NCI fix lands (price the "
        "parent slice over the parent-only count, or the whole company over the "
        "whole count); the share counts read here are that job's test data.")


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


def up_c_sentence_dcf(ticker: str, total_m: float) -> str:
    """The right-reason refusal for umbrella-partnership C-corps, in this
    page's own terms. The shared up_c_sentence (ported verbatim inside the
    XBRL-route block above, where a self-check calls it) says "the ΔE pools
    above are real measurements" — true on tool 1, false here: this page has
    no ΔE pools, and a sentence pointing at evidence the page does not show
    is exactly the defect class this project hunts. Same cause, this page's
    evidence, both legs named (Chen, 10 Sep 2026: one extra check is the
    cheapest price ever paid for a true sentence)."""
    return (
        f"**Valuation withheld — Up-C structure.** {ticker}'s per-class share counts "
        f"were read from its filings' own XBRL instances and summed "
        f"({total_m:,.1f}M across the classes), so the free cash flow history and the "
        "per-year Ω column above are real measurements. But this is an "
        "umbrella-partnership C-corp: a large share of the economics sits in LLC "
        "units outside the parent company — for Ryan Specialty over half the "
        "summed count, for Carvana roughly a third — and until the NCI fix "
        "settles the basis (the parent's slice over a parent-only count, or the "
        "whole company over the whole count), any per-share division mixes the two. "
        "So neither leg is priced: no standard-FCF value, no SBC-corrected value, "
        "no gap and no verdict. The share counts read on this page are that fix's "
        "test data; this stop lifts when it lands.")


# ══════════════════════════════════════════════════════════════════════
#  SELF-TESTS
# ══════════════════════════════════════════════════════════════════════
#
# Arithmetic and wiring on synthetic figures, plus the shared-reader
# checks carried verbatim from page 4's harness and the financial-gate
# checks carried verbatim from tool 1's. They do not use live filings.


def _raises(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


def self_test() -> list[tuple[str, bool, str]]:
    out = []

    # D1. The synthetic case, checked by hand to the cent (8 Sep 2026).
    #     Base 1,000, growth 8%, 10 years, discount 10%, terminal 2.5%,
    #     net debt -5,000, 100M shares. By hand, in exact fractions:
    #     stage-1 PV = 1000·x(1-x^10)/(1-x) with x = 1.08/1.10 = 54/55
    #                = 9,052.611566
    #     TV = 1000·1.08^10·1.025/0.075 = 29,505.30729; PV = /1.1^10
    #                = 11,375.573616
    #     EV = 20,428.185182;  per share = (EV + 5,000)/100 = 254.281852.
    #     The closed form and the year-by-year sum were computed separately
    #     and agree exactly.
    _syn = DCFParams(1000.0, 0.08, 10, 0.10, 0.025, -5000.0, 100.0)
    _pv1, _pvtv, _ev = dcf_enterprise(_syn)
    out.append(("Synthetic DCF: stage-1 PV and PV(TV) match the hand arithmetic",
                abs(_pv1 - 9052.611566) < 0.01 and abs(_pvtv - 11375.573616) < 0.01,
                f"{_pv1:,.4f} + {_pvtv:,.4f}"))
    out.append(("...and the per-share value lands on 254.281852 to the cent",
                abs(dcf_per_share(_syn) - 254.281852) < 0.005,
                f"{dcf_per_share(_syn):,.6f}"))

    # D2. The terminal value alone: base 100, growth 0, 1 year, discount
    #     10%, terminal 0 gives CF1 = 100 (PV 90.909091) and TV = 100/0.10
    #     = 1,000 (PV 909.090909) — EV exactly 1,000.
    out.append(("Terminal-value component: the degenerate case sums to exactly 1,000",
                abs(dcf_enterprise(DCFParams(100.0, 0.0, 1, 0.10, 0.0, 0.0, 1.0))[2]
                    - 1000.0) < 1e-9, ""))

    # D3. THE IDENTITY THE PAGE STANDS ON. The engine is linear in the base,
    #     so with every other input shared, leg 1 minus leg 2 equals the
    #     engine run on the Omega base alone — the gap between the columns
    #     IS the discounted Omega stream. Checked at CRM-like magnitudes.
    _p1 = DCFParams(13000.0, 0.15, 10, 0.10, 0.025, 3500.0, 929.0)
    _om = 3900.0
    _p2 = replace(_p1, base_fcf=_p1.base_fcf - _om)
    _gap = dcf_per_share(_p1) - dcf_per_share(_p2)
    _omv = dcf_enterprise(replace(_p1, base_fcf=_om))[2] / _p1.shares
    out.append(("Linearity identity: the per-share gap between the legs is the discounted Omega stream",
                abs(_gap - _omv) < 1e-9, f"gap {_gap:,.4f}/share on Omega {_om:,.0f}M"))

    # D4. Terminal growth at or above the discount rate refuses, never clamps.
    out.append(("Terminal at the discount rate refuses; above it refuses; below it runs",
                _raises(lambda: dcf_enterprise(replace(_syn, terminal=0.10)))
                and _raises(lambda: dcf_enterprise(replace(_syn, terminal=0.12)))
                and dcf_enterprise(replace(_syn, terminal=0.0999))[2] > 0, ""))

    # D5. The rate grid refuses the cells the engine refuses, and the cell
    #     built from the page's own inputs is the page's own answer.
    _rg = rate_grid(_syn, [0.08, 0.10, 0.12], [0.025, 0.08, 0.10])
    out.append(("Rate grid: a cell whose terminal reaches its rate is refused, not computed",
                _rg[0][1] is None and _rg[0][2] is None and _rg[1][2] is None
                and _rg[1][0] is not None and abs(_rg[1][0] - dcf_per_share(_syn)) < 1e-9,
                "refused cells blank; the (10%, 2.5%) cell is the page's own figure"))
    out.append(("...and value falls as the discount rate rises, in every computable column",
                _rg[0][0] > _rg[1][0] > _rg[2][0], f"{_rg[0][0]:.2f} > {_rg[1][0]:.2f} > {_rg[2][0]:.2f}"))

    # D6. The growth x years grid: the page's own cell reproduces, and value
    #     rises with stage-1 growth in every column.
    _gg = growth_years_grid(_syn, [0.04, 0.08, 0.12], [5, 10, 15])
    out.append(("Growth grid: IV rises with growth in every column and the centre cell is the page's own",
                all(_gg[0][j] < _gg[1][j] < _gg[2][j] for j in range(3))
                and abs(_gg[1][1] - dcf_per_share(_syn)) < 1e-9,
                f"{_gg[0][1]:.2f} < {_gg[1][1]:.2f} < {_gg[2][1]:.2f}"))

    # D7. The growth seed: latest YoY, capped into tool 1's band both ways,
    #     8% default when no positive-to-positive pair exists, and the cap
    #     note fires only when the cap bit.
    _up = [(2020, 100.0), (2021, 110.0), (2022, 120.0), (2023, 130.0), (2024, 140.0), (2025, 200.0)]
    _g, _raw, _c5 = fcf_growth_seed(_up)
    out.append(("Growth seed: a 42.9% launch year is capped at 25% and the note names both figures",
                abs(_raw - 200.0 / 140.0 + 1) < 1e-9 and _g == 0.25
                and "capped at 25%" in fcf_cap_note(_raw, _g) and fcf_cap_note(0.10, 0.10) == "",
                f"raw {_raw:.1%} -> {_g:.0%}"))
    out.append(("...a collapse is floored at -10%, and a loss year leaves the 8% default",
                fcf_growth_seed([(2024, 100.0), (2025, 40.0)])[0] == -0.10
                and fcf_growth_seed([(2024, -5.0), (2025, 40.0)])[0] == 0.08, ""))
    out.append(("...the 5-year CAGR reads (200/100)^(1/5)-1 beside the seed",
                abs(_c5 - (2.0 ** 0.2 - 1)) < 1e-9, f"{_c5:.2%}"))
    out.append(("...and the trend note fires on a 5-point gap in either direction, silent inside it",
                "recent rate" in fcf_trend_note(0.05, 0.15) and "recent rate" in fcf_trend_note(0.20, 0.08)
                and fcf_trend_note(0.10, 0.12) == "", ""))

    # D8. The base seed: profit seeds from the latest year; a loss on a
    #     positive record falls to the median with the sentence that says
    #     so; no positive base anywhere returns None and the page routes to
    #     page 4 (the Crocs rule, applied to cash flow).
    out.append(("Base seed: latest year when positive, median on a loss-on-record, None when neither",
                seed_base_fcf(900.0, 600.0) == (900.0, BASE_FROM_LATEST)
                and seed_base_fcf(-81.0, 600.0) == (600.0, BASE_FROM_MEDIAN)
                and seed_base_fcf(-81.0, -40.0)[0] is None, ""))
    out.append(("...and the nothing-to-discount sentence routes to page 4 without misdescribing the years",
                "Use page 4" in nothing_to_discount(-81.0, -40.0)
                and "every" not in nothing_to_discount(-81.0, -40.0), ""))

    # D9. The Omega correction: excluded and unpriced years never enter the
    #     pool, the median is the median, the latest usable year rides
    #     along for the assumptions block, and a thin window refuses.
    _ys = [Year(fy=2019, N=100, G=50, Cw=10, price=20.0, dS=2.0),
           Year(fy=2020, N=100, G=50, Cw=10, price=20.0, dS=3.0, excluded="capital event"),
           Year(fy=2021, N=100, G=50, Cw=10, price=0.0, dS=4.0),
           Year(fy=2022, N=100, G=50, Cw=12, price=20.0, dS=1.0),
           Year(fy=2023, N=100, G=50, Cw=30, price=20.0, dS=1.5),
           Year(fy=2024, N=100, G=50, Cw=20, price=20.0, dS=1.0)]
    _med, _lat, _n = omega_correction(_ys)
    # usable omegas: 2019: 10+40=50; 2022: 12+20=32; 2023: 30+30=60; 2024: 20+20=40
    out.append(("Omega correction: excluded and unpriced years are out, median and latest are right",
                _n == 4 and abs(_med - 45.0) < 1e-9 and abs(_lat - 40.0) < 1e-9,
                f"median {_med:.0f} of [50, 32, 60, 40], latest {_lat:.0f}"))
    out.append(("...and fewer than three usable years refuses leg 2, leg 1 untouched",
                omega_correction(_ys[:3])[0] is None
                and "unmeasured" in leg2_unmeasured(2), ""))

    # D10. The swallow rule (settled 8 Sep 2026): a median Omega at or above
    #      the base refuses leg 2 with the finding said out loud.
    out.append(("Omega at or above the base refuses leg 2 and the sentence carries the finding",
                omega_swallows(1000.0, 1000.0) and omega_swallows(1000.0, 1500.0)
                and not omega_swallows(1000.0, 999.0)
                and "swallows the cash flow" in leg2_swallowed(1000.0, 1500.0), ""))

    # D10b. A typed share count refuses leg 2 outright — the gate's own
    #       sentence promises it, and with no filed counts every dS is zero,
    #       so an Omega computed anyway would be corrupt, not conservative.
    out.append(("A typed share count refuses leg 2 with the promise kept",
                "share count was typed" in leg2_typed_shares()
                and "standard leg runs on the typed count" in leg2_typed_shares(), ""))

    # D11. The WACC estimate: hand arithmetic, weights from the inputs, and
    #      the degenerate no-capital case refuses. 80/20 at ke = 4% + 1.2 x
    #      5% = 10%, kd 5% at 21% tax -> 0.8 x 10% + 0.2 x 3.95% = 8.79%.
    _w = wacc_estimate(8000.0, 2000.0, 0.04, 0.05, 1.2, 0.05, 0.21)
    out.append(("WACC estimate: 8.79% by hand, weights 0.8/0.2, and zero capital refuses",
                _w is not None and abs(_w[0] - 0.0879) < 1e-9 and abs(_w[1] - 0.10) < 1e-9
                and abs(_w[2] - 0.8) < 1e-9 and wacc_estimate(0, 0, .04, .05, 1, .05, .21) is None,
                f"{_w[0]:.4%}"))

    # D12. The stale-window guard, same threshold as the shared one: two
    #      years is reporting lag, three passes, four refuses.
    out.append(("Stale FCF: FY2023 in 2026 passes, FY2022 refuses with both years named",
                stale_fcf_refusal(2023, 2026) == "" and "FY2022" in stale_fcf_refusal(2022, 2026)
                and "4 years behind" in stale_fcf_refusal(2022, 2026), ""))

    # D13. The few-years floor names its number.
    out.append(("Fewer than four FCF years refuses and says how many were read",
                "Only 3 year(s)" in few_fcf_years_refusal(3), ""))

    # D14. The financial banner: a bank routes to the Financials Checker, a
    #      refused float business is told no page prices it, both carry the
    #      gate's own reason verbatim.
    _bank = dcf_financial_banner("bank", "SIC 6021 and the filing carries deposits and net "
                                 "interest income.", "National Commercial Banks", "6021")
    _ref = dcf_financial_banner("refused", "SIC 6141 says lender with no deposits.", None, "6141")
    out.append(("Financial banner: bank routes to the Financials Checker; refused says no page prices it",
                "Not this page — a bank." in _bank and "Financials Checker" in _bank
                and "deposits and net interest income" in _bank
                and "No page in this kit prices this class yet." in _ref, ""))

    # D15. History rows: a missing CFO refuses the FCF cells, a missing
    #      capex reads zero (page 4's runway convention, named in the
    #      caption), and an excluded or unpriced year blanks Omega and leg 2
    #      while leg 1 stands.
    _yr = [Year(fy=2024, N=10, G=5, Cw=2, price=10.0, dS=1.0),
           Year(fy=2025, N=10, G=5, Cw=2, price=10.0, dS=1.0, excluded="x")]
    _rows = fcf_rows(_yr, {2024: {"cfo": 100.0, "capex": None}, 2025: {"cfo": None, "capex": 5.0}})
    out.append(("History rows: missing capex reads zero, missing CFO refuses, excluded years blank Omega",
                _rows[0]["fcf1"] == 100.0 and _rows[0]["omega"] == 12.0 and _rows[0]["fcf2"] == 88.0
                and _rows[1]["fcf1"] is None and _rows[1]["omega"] is None, ""))

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

    # 19. The read-nothing route (Grab shape, 1 Sep 2026): net income read,
    #     revenue empty → the load refuses with the page-6 route; both read →
    #     no refusal on this ground.
    def _grab_shape(n_read, rev_read):
        return bool(n_read) and not bool(rev_read)
    out.append(("Net income read with no revenue refuses toward the Non-US page",
                _grab_shape({2024: 1}, {}) and not _grab_shape({2024: 1}, {2024: 2})
                and not _grab_shape({}, {}),
                "fires only on the asymmetric read"))

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
                "counts fold, valuation refuses for the true reason until §5.5 G"))
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
    out.append(("Page-local Up-C sentence: both legs named, no ΔE-pool claim",
                "Up-C" in up_c_sentence_dcf("CVNA", 1091.7)
                and "withheld" in up_c_sentence_dcf("CVNA", 1091.7)
                and "SBC-corrected" in up_c_sentence_dcf("CVNA", 1091.7)
                and "1,091.7M" in up_c_sentence_dcf("CVNA", 1091.7)
                and "ΔE" not in up_c_sentence_dcf("CVNA", 1091.7),
                "the shared sentence's ΔE-pools clause is tool 1's evidence, not this page's"))
    return out


# ══════════════════════════════════════════════════════════════════════
#  REFERENCE
# ══════════════════════════════════════════════════════════════════════
#
# Defined above the UI so the refusal paths can render it before st.stop()
# (the GRAB lesson: never point a reader at content a stopped page will
# not show). Called once, unconditionally, at the end of the module for
# every path that does not stop.


def _page_footer():
    st.divider()
    _r1, _r2 = st.columns(2)
    with _r1:
        with st.expander("What the numbers mean", expanded=False):
            st.markdown(
                "**Free cash flow (standard)** — cash from operations less capex, the "
                "definition the public valuation sites print. The GAAP stock-comp charge is "
                "expensed in net income and added straight back inside CFO, so this figure "
                "prices stock comp at zero.\n\n"
                "**Ω (true SBC cost)** — the shared Year machinery's measure: tax withheld on "
                "vesting, less option and ESPP proceeds, plus the market value of shares "
                "delivered to employees (buybacks plus the share-count change at the year's "
                "average price, net of tagged acquisition consideration).\n\n"
                "**FCF (SBC-corrected)** — standard FCF less Ω. The income-side correction is "
                "−(Ω − G) because net income already charges G; the cash-side correction is "
                "the full −Ω because CFO's add-back neutralised G. All of Ω's cash legs sit "
                "in financing, so nothing is double-counted.\n\n"
                "**Two-stage DCF** — Damodaran's: the base grown at a stage-1 rate for stated "
                "years, then a Gordon terminal value at a rate below the discount rate, all "
                "discounted, less net debt, per share.\n\n"
                "**The refusals** — this app's. Financials, a stale or thin window, no "
                "positive base to project, terminal growth at the discount rate, an Ω that "
                "swallows the cash flow or cannot be measured.")
    with _r2:
        with st.expander("Verify the engine"):
            st.caption("The synthetic case checked by hand to the cent, the linearity identity "
                       "that pins the gap between the legs to the discounted Ω stream, every "
                       "refusal branch, and the shared-reader checks carried verbatim from "
                       "page 4 and tool 1. They do not use live filings.")
            if st.button("Run checks"):
                _results = self_test()
                _sev, _line = test_summary(_results)
                getattr(st, _sev)(_line)
                for name, ok, got in _results:
                    st.write(("✅ " if ok else "❌ ") + f"{name} — {got}")

    st.caption(
        "Research aid, not financial advice. Outputs depend on estimates you supply. The model "
        "follows Aswath Damodaran's published two-stage FCFF framework; the SBC leg, the "
        "refusals and the defaults are this project's own. Independent, not affiliated with or "
        "endorsed by him or NYU Stern.")


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
    page_title="DCF Evaluator — the standard two-stage DCF, with its blind spot beside it",
    page_icon="🧮",
    layout="centered",
    initial_sidebar_state="collapsed",
)
st.title("🧮 DCF Evaluator")
st.caption("Aswath Damodaran's two-stage free-cash-flow DCF — the model every valuation site "
           "prints — with its assumptions in boxes, and beside it the same DCF on SBC-corrected "
           "cash flow, so the cost the standard definition prices at zero is visible in dollars "
           "per share.")

if not _sec_contact():
    st.warning(
        "**No SEC contact address set.** The SEC requires a real email in the request header "
        "and blocks generic user agents, so lookups will fail. Add `sec_contact = "
        "\"you@example.com\"` in Streamlit Settings → Secrets, or set a SEC_CONTACT "
        "environment variable locally.")

if "dcf_years" not in st.session_state:
    st.info(
        "**Three parts, and which is whose.** The model is Damodaran's published two-stage "
        "FCFF DCF: free cash flow grown at a stage-1 rate, a terminal value at a rate below "
        "the discount rate, less net debt, per share. The SBC leg is this kit's: the same DCF "
        "with the true cost of stock comp (Ω, from the shared Year machinery) subtracted, "
        "because the standard definition adds the GAAP charge straight back inside CFO and so "
        "prices stock comp at zero. The refusals and the defaults are this app's; the defaults "
        "are conventions in boxes, stated as such.\n\n"
        "Enter a US-listed ticker. Financials are routed to the Financials Checker; companies "
        "with no positive free cash flow to project are routed to the Inflection Checker; "
        "foreign private issuers belong on the Non-US Checker.")

with st.form("dcf_lookup"):
    ticker = st.text_input("Stock ticker", placeholder="MSFT · ADBE · CRM — press Enter").upper().strip()
    submitted = st.form_submit_button("Evaluate", type="primary")

if submitted:
    if not ticker:
        st.warning("Enter a ticker first.")
    else:
        try:
            with st.spinner(f"Reading {ticker} annual filings…"):
                yrs, notes, pre = load(ticker, 10)
            st.session_state.update(dcf_years=yrs, dcf_notes=notes, dcf_pre=pre, dcf_tk=ticker)
        except ValueError as e:
            st.error(f"Could not load {ticker}: {e}")
        except Exception as e:
            st.error(
                f"Could not load {ticker} — {type(e).__name__}: {e}\n\n"
                "This is a gap in how the filings were read, not something you did. Filers with "
                "several share classes, recent listings and foreign issuers are the usual causes.")

years = st.session_state.get("dcf_years", [])
if years and ticker and st.session_state.get("dcf_tk") == ticker:
    notes, pre, tk = st.session_state["dcf_notes"], st.session_state["dcf_pre"], st.session_state["dcf_tk"]
    alerts: list[tuple[str, str]] = [("info", n) for n in page_notes(notes)]

    # A financial filer is refused BEFORE the table — page 4's Oscar Health
    # reasoning: a bank's CFO − capex is a filed number but not an operating
    # company's free cash flow, and ten printed years of it invite exactly
    # the misuse the refusal exists to stop.
    if pre.get("financial"):
        st.markdown("---")
        st.subheader("Free cash flow")
        st.error(dcf_financial_banner(pre.get("fin_class", ""), pre.get("fin_reason", ""),
                                      pre.get("sic_desc"), pre.get("sic")))
        with st.expander("Notes and detail", expanded=False):
            for kind_, msg in alerts:
                getattr(st, kind_)(msg)
            st.write("**What was read from the filings** — every tag, found or missing")
            st.dataframe(pd.DataFrame(pre.get("tags", [])), width='stretch', hide_index=True)
        st.stop()

    # ══ the history — always printed before any further refusal ══════
    rows = fcf_rows(years, pre.get("trend", {}))
    st.markdown("---")
    st.subheader(f"Free cash flow history · {tk}")
    st.caption("Every column is a filed number or a named identity on two filed numbers. "
               "A blank cell is a refused cell, not zero. Years marked * are excluded from the "
               "Ω column as capital events; their cash flows are still read.")

    _mfmt = money_fmt([v for r in rows for v in (r["cfo"], r["capex"], r["fcf1"], r["omega"], r["fcf2"])
                       if v is not None])
    st.dataframe(pd.DataFrame([{
        "FY": f"{r['fy']}*" if r["excluded"] else str(r["fy"]),
        "Cash from operations": cell(r["cfo"], _mfmt),
        "Capex": cell(r["capex"], _mfmt),
        "FCF — standard": cell(r["fcf1"], _mfmt),
        "Ω (true SBC cost)": cell(r["omega"], _mfmt),
        "FCF — SBC-corrected": cell(r["fcf2"], _mfmt),
    } for r in rows]), width='stretch', hide_index=True)

    _no_capex = [r["fy"] for r in rows if r["cfo"] is not None and r["capex"] is None]
    _cap = ("FCF — standard is cash from operations less capex, the definition the public sites "
            "print; the GAAP stock-comp charge is added back inside CFO, so this column prices "
            "stock comp at zero. FCF — SBC-corrected subtracts the year's Ω instead: the full "
            "cost, because the add-back already neutralised the charge. ")
    if _no_capex:
        _cap += (f"Capex read nothing for FY{', FY'.join(str(f) for f in _no_capex)} and was "
                 "taken as zero in those years — page 4's convention, named here rather than "
                 "applied silently. ")
    _cap += "Ω is blank where a year is excluded or carries no average price; the correction below never uses those years either."
    st.caption(_cap)

    # ══ refusals that keep the table ═════════════════════════════════
    _fcf_pairs = [(r["fy"], r["fcf1"]) for r in rows if r["fcf1"] is not None]
    if len(_fcf_pairs) < FCF_MIN_YEARS:
        st.error(few_fcf_years_refusal(len(_fcf_pairs)))
        with st.expander("Notes and detail", expanded=True):
            for kind_, msg in alerts:
                getattr(st, kind_)(msg)
            st.write("**What was read from the filings** — every tag, found or missing")
            st.dataframe(pd.DataFrame(pre.get("tags", [])), width='stretch', hide_index=True)
        _page_footer()
        st.stop()

    _stale = stale_fcf_refusal(_fcf_pairs[-1][0], dt.date.today().year)
    if _stale:
        st.error(_stale)
        with st.expander("Notes and detail", expanded=True):
            for kind_, msg in alerts:
                getattr(st, kind_)(msg)
            st.write("**What was read from the filings** — every tag, found or missing")
            st.dataframe(pd.DataFrame(pre.get("tags", [])), width='stretch', hide_index=True)
        _page_footer()
        st.stop()

    _latest_fcf = _fcf_pairs[-1][1]
    _med5 = median5_fcf(_fcf_pairs)
    base_fcf, base_src = seed_base_fcf(_latest_fcf, _med5)
    if base_fcf is None:
        st.error(nothing_to_discount(_latest_fcf, _med5))
        with st.expander("Notes and detail", expanded=True):
            for kind_, msg in alerts:
                getattr(st, kind_)(msg)
            st.write("**What was read from the filings** — every tag, found or missing")
            st.dataframe(pd.DataFrame(pre.get("tags", [])), width='stretch', hide_index=True)
        _page_footer()
        st.stop()


    # ══ Up-C stop (queue H, 10 Sep 2026) ══════════════════════════════════
    # CVNA and RYAN: the XBRL route summed real per-class counts, so the
    # table and every per-year Ω above are genuine measurements — and the
    # per-share valuation still cannot be stood behind until HANDOVER §5.5 G's
    # NCI fix settles the basis (parent slice over parent-only count, or the
    # whole over the whole). Placed AFTER nothing-to-discount (a burner still
    # refuses for the burner reason — the RIVN ordering) and BEFORE the
    # share-count gate, guarded on shares > 0: a verification-discarded
    # registry entry leaves counts unread and the ordinary no-share-count
    # stop below handles it exactly as pre-route. Notes rendered inside the
    # stop (the GRAB lesson), footer before st.stop (the Job 5b lesson). The
    # sentence is the page-local one — the shared up_c_sentence names ΔE
    # pools this page does not have.
    _upc_shares = pre.get("shares", 0.0) or 0.0
    if any(e.up_c for e in XBRL_REGISTRY.get(tk, ())) and _upc_shares > 0:
        st.error(up_c_sentence_dcf(tk, _upc_shares))
        with st.expander("Notes and detail", expanded=True):
            for kind_, msg in alerts:
                getattr(st, kind_)(msg)
            st.write("**What was read from the filings** — every tag, found or missing")
            st.dataframe(pd.DataFrame(pre.get("tags", [])), width='stretch', hide_index=True)
        _page_footer()
        st.stop()

    shares = pre.get("shares", 0.0) or 0.0
    if shares <= 0:
        st.error(
            "**No share count was read from any tag this reader knows** — the notes below say "
            "which years. Nothing per share can be computed, and Ω, which prices the shares "
            "delivered at the year's average, cannot be measured without the year-end count. "
            "The usual shape is a dual-class filer (Carvana, Ryan Specialty, Reddit): per-class "
            "counts carry a class dimension that EDGAR's companyfacts API strips, so nothing "
            "undimensioned exists to read. Type the diluted count from the 10-K cover page to "
            "continue; the standard leg will run on it, the SBC leg stays refused, and the "
            "assumptions block will record the count as set by hand.")
        with st.expander("Notes and detail — why nothing was read", expanded=True):
            for kind_, msg in alerts:
                getattr(st, kind_)(msg)
        shares = st.number_input("Diluted shares from the 10-K cover page (millions)",
                                 min_value=0.0, value=0.0, step=1.0)
        if shares <= 0:
            _page_footer()
            st.stop()
        _typed_shares = True
    else:
        _typed_shares = False

    # ══ judgement boxes ══════════════════════════════════════════════
    _seed_g, _raw_g, _cagr5 = fcf_growth_seed(_fcf_pairs)
    for _n in (fcf_trend_note(_raw_g, _cagr5), fcf_cap_note(_raw_g, _seed_g)):
        if _n:
            alerts.append(("info", _n))

    st.markdown("---")
    st.subheader("Judgement — every figure here is yours to change")
    j1, j2 = st.columns(2)
    with j1:
        growth = st.number_input("Stage-1 growth, % a year", min_value=-50.0, max_value=100.0,
                                 value=round(_seed_g * 100, 1), step=0.5,
                                 help="Seeded from the latest year-over-year move in FCF, capped "
                                      f"into [{FCF_GROWTH_FLOOR:.0%}, {FCF_GROWTH_CAP:.0%}] — tool 1's "
                                      "band for its revenue seed. The 5-year CAGR is in the "
                                      "assumptions block for the trend.") / 100.0
        n_years = int(st.number_input("Stage-1 years", min_value=1, max_value=30,
                                      value=DCF_YEARS_DEFAULT, step=1,
                                      help="10 is a convention, not evidence."))
    with j2:
        discount = st.number_input("Required return (discount rate), %", min_value=1.0, max_value=50.0,
                                   value=DISCOUNT_DEFAULT * 100, step=0.5,
                                   help="A plain required return, default 10% — a convention in a "
                                        "box. The optional WACC estimate below is display-only.") / 100.0
        terminal = st.number_input("Terminal growth, %", min_value=-5.0, max_value=10.0,
                                   value=TERMINAL_DEFAULT * 100, step=0.25,
                                   help="Growth forever after stage 1. 2.5% is a convention near "
                                        "long-run nominal GDP; at or above the discount rate the "
                                        "page refuses.") / 100.0

    if terminal >= discount:
        st.error(f"**Refused: terminal growth ({terminal:.2%}) at or above the discount rate "
                 f"({discount:.2%}).** The Gordon terminal value divides by the difference — at "
                 "or past that line it prices the tail at infinity. Lower the terminal rate or "
                 "raise the required return; the page will not clamp either for you.")
        _page_footer()
        st.stop()

    net_debt = -pre.get("net_cash", 0.0)
    om_med, om_latest, om_usable = omega_correction(years)
    price = current_price(pre.get("ticker", tk)) or 0.0

    par = DCFParams(base_fcf, growth, n_years, discount, terminal, net_debt, shares)
    pv1, pvtv, ev = dcf_enterprise(par)
    leg1 = dcf_per_share(par)

    leg2 = None
    leg2_refusal = ""
    if _typed_shares:
        # The gate's sentence promises this: a typed count runs leg 1 only.
        leg2_refusal = leg2_typed_shares()
    elif om_med is None:
        leg2_refusal = leg2_unmeasured(om_usable)
    elif omega_swallows(base_fcf, om_med):
        leg2_refusal = leg2_swallowed(base_fcf, om_med)
    else:
        leg2 = dcf_per_share(replace(par, base_fcf=base_fcf - om_med))

    # ══ assumptions used ═════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Assumptions used")
    st.write(f"- **Base free cash flow** {base_fcf:,.0f}M — {base_src}.")
    _gline = f"- **Stage-1 growth** {growth:.1%} for {n_years} years"
    if _raw_g is not None:
        _gline += f" (seeded from the latest year-over-year move of {_raw_g:.1%}"
        _gline += f"; 5-year CAGR {_cagr5:.1%})" if _cagr5 is not None else ")"
    else:
        _gline += " (no positive-to-positive pair of years to seed from — 8% default)"
    st.write(_gline + ".")
    st.write(f"- **Required return** {discount:.1%}; **terminal growth** {terminal:.2%} after "
             f"stage 1. Both conventions in boxes; the grids below show what each does alone.")
    if om_med is not None:
        st.write(f"- **Ω correction (leg 2)**: median-5 = {om_med:,.0f}M; latest year "
                 f"{om_latest:,.0f}M. The median is the convention — per-year Ω moves with "
                 f"buyback timing, and the table above shows every year.")
    else:
        st.write(f"- **Ω correction (leg 2)**: unmeasured — {om_usable} usable year(s).")
    st.write(f"- **Net debt** {net_debt:,.0f}M (negative means net cash, deployable-cash rules "
             f"as on every page); **shares** {shares:,.1f}M diluted"
             + (", **typed from the cover page**" if _typed_shares else "") + ".")

    # ══ the two legs ═════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("The two legs")
    c1, c2 = st.columns(2)
    with c1:
        st.metric("Leg 1 — standard FCF definition", d(leg1),
                  f"{price / leg1:.2f}x price/value at {d(price)}" if price and leg1 > 0 else None,
                  delta_color="off")
        st.caption("CFO − capex. The GAAP stock-comp charge is added back inside CFO, so this "
                   "leg prices stock comp at zero — the convention, comparable to what the "
                   "public valuation sites print.")
    with c2:
        if leg2 is not None:
            st.metric("Leg 2 — SBC-corrected", d(leg2),
                      f"{price / leg2:.2f}x price/value at {d(price)}" if price and leg2 > 0 else None,
                      delta_color="off")
            st.caption("The same DCF with the median-5 Ω subtracted from the base: the measured "
                       "cost of stock comp, in place of a charge of zero.")
        else:
            st.error(leg2_refusal)

    if leg2 is not None:
        st.info(f"**The legs differ by {d(leg1 - leg2)} per share — the discounted Ω stream.** "
                f"Leg 1 prices stock comp at zero because the standard FCF definition adds the "
                f"GAAP charge back inside CFO; leg 2 charges the measured cost, {om_med:,.0f}M a "
                f"year (the median-5 convention), grown and discounted like every other dollar. "
                f"The engine is linear in the base, so the gap is exactly the engine run on Ω "
                f"alone — the self-test pins it.")

    with st.expander("Where the value sits", expanded=False):
        st.write(f"- Stage 1 ({n_years} years): {pv1:,.0f}M present value.")
        st.write(f"- Terminal value: {pvtv:,.0f}M present value — "
                 f"{pvtv / ev:.0%} of enterprise value. The larger this share, the more the "
                 f"answer is the terminal assumption wearing a model.")
        st.write(f"- Enterprise value {ev:,.0f}M, less net debt {net_debt:,.0f}M, over "
                 f"{shares:,.1f}M shares.")

    # ══ grids — the same engine call with one input moved ════════════
    st.markdown("---")
    st.subheader("What the judgement inputs do alone")
    _rates = [max(0.02, discount - 0.02), discount, discount + 0.02]
    _terms = [terminal - 0.01, terminal, terminal + 0.01]
    _rg = rate_grid(par, _rates, _terms)
    st.write("**Value per share across required return × terminal growth** (leg 1)")
    st.dataframe(pd.DataFrame([{"Required return": f"{r:.1%}",
                                **{f"terminal {t:.2%}": cell(v, "${:,.2f}", "refused")
                                   for t, v in zip(_terms, _rg[i])}}
                               for i, r in enumerate(_rates)]), width='stretch', hide_index=True)
    st.caption("A refused cell is a terminal rate at or above its required return — the page "
               "refuses there instead of computing an infinity.")
    _gs = [max(-0.10, growth - 0.05), growth, min(0.40, growth + 0.05)]
    _yrs = sorted({max(1, n_years - 5), n_years, n_years + 5})
    _gg = growth_years_grid(par, _gs, _yrs)
    st.write("**Value per share across stage-1 growth × stage-1 years** (leg 1)")
    st.dataframe(pd.DataFrame([{"Stage-1 growth": f"{g:.1%}",
                                **{f"{n}y": cell(v, "${:,.2f}") for n, v in zip(_yrs, _gg[i])}}
                               for i, g in enumerate(_gs)]), width='stretch', hide_index=True)

    # ══ WACC — display-only, by decision ═════════════════════════════
    with st.expander("A WACC estimate, if you want one (display-only)", expanded=False):
        st.caption("Burry calls WACC false precision, and this page half agrees: the estimate "
                   "is shown with its components, and the engine keeps running on the plain "
                   "required-return box above. If you want the WACC there, type it there. "
                   "Weights come from the market cap and funded debt already read; the "
                   "components are typed, because a live risk-free rate or a regression beta "
                   "would need data from outside EDGAR and the price source.")
        w1, w2 = st.columns(2)
        with w1:
            rf = st.number_input("Risk-free rate, %", 0.0, 15.0, 4.0, 0.1,
                                 help="Type today's 10-year Treasury yield.") / 100.0
            erp = st.number_input("Equity risk premium, %", 0.0, 15.0, 4.5, 0.1,
                                  help="Damodaran publishes a monthly implied ERP; 4.5% is a "
                                       "dated default, not a reading.") / 100.0
            beta = st.number_input("Beta", 0.0, 4.0, 1.0, 0.05)
        with w2:
            kd = st.number_input("Pre-tax cost of debt, %", 0.0, 20.0, 5.0, 0.1) / 100.0
            taxr = st.number_input("Tax rate, %", 0.0, 50.0, 21.0, 0.5) / 100.0
        _mcap = shares * price
        _w = wacc_estimate(_mcap, pre.get("debt", 0.0), rf, erp, beta, kd, taxr)
        if _w is None:
            st.write("No market cap or debt to weight — nothing to estimate.")
        else:
            _wacc, _ke, _we, _wd = _w
            st.write(f"Cost of equity {_ke:.2%} (= {rf:.2%} + {beta:.2f} × {erp:.2%}); weights "
                     f"{_we:.0%} equity / {_wd:.0%} debt from market cap {_mcap:,.0f}M and funded "
                     f"debt {pre.get('debt', 0.0):,.0f}M; after-tax cost of debt "
                     f"{kd * (1 - taxr):.2%}.")
            st.write(f"**WACC estimate: {_wacc:.2%}.** The rate × terminal grid above shows what "
                     f"using it instead of {discount:.1%} would do.")

    # ══ notes and the tag panel ══════════════════════════════════════
    with st.expander("Notes and detail", expanded=False):
        for kind_, msg in alerts:
            getattr(st, kind_)(msg)
        st.write("**What was read from the filings** — every tag, found or missing")
        st.dataframe(pd.DataFrame(pre.get("tags", [])), width='stretch', hide_index=True)

_page_footer()
