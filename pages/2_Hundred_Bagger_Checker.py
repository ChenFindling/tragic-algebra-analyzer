"""
100-Bagger Checker
==================
Christopher Mayer's criteria, checked against the filings, with Michael Burry's
fully-adjusted return on invested capital as the centrepiece.

WHAT THIS IS FOR
----------------
Not a screener. It takes one ticker you already like and tells you which of
Mayer's conditions the filings support, which they contradict, and which cannot
be answered from EDGAR at all.

THE ARITHMETIC THAT DOES MOST OF THE WORK
-----------------------------------------
A 100-bagger is two engines multiplied: earnings growth and multiple change.

    100  =  (1+g)^N  x  (M_exit / M_now)  /  (1+dilution)^N

Solve for g and you get the growth rate the business must actually deliver.
Then compare it against what its own return on capital allows:

    g_max  =  ROIC  x  reinvestment rate

Nothing outgrows its return on capital for long, because growth has to be
funded and reinvested profit is the only self-funded source. When the required
rate sits above the ceiling, the case is not optimistic — it is arithmetically
closed, and no amount of narrative reopens it.

That comparison is the whole tool. Everything else is the work of computing the
two numbers honestly.

BURRY'S ROIC
------------
    ROIC = (Owners' earnings - interest income - capital lease payments
            - other expense)
           / (total capital - LT operating leases - net cash + other capital)

Owners' earnings come from the Tragic Algebra engine, ported unchanged from
tool 1 so both pages agree to the dollar. The rest is assembled from the
balance sheet, with two rules that matter:

  * Only genuinely deployable cash leaves the capital base. Restricted,
    regulated and operationally-tied cash funds the business and stays in.
  * Anything not obtainable from XBRL is exposed as an input seeded at zero
    and labelled as judgement, never guessed and quietly folded in.

Run:  streamlit run Home.py   (this file lives in pages/)
"""

from __future__ import annotations

import datetime as dt
import math
import os
import statistics
import threading
import time
from dataclasses import dataclass

import pandas as pd
import requests
import streamlit as st

# ══════════════════════════════════════════════════════════════════════
#  SEC PLUMBING
#
#  Duplicated from tool 1 rather than imported. A Streamlit page module
#  cannot be imported without executing its UI, and a page filename
#  starting with a digit is not a legal module name either. The copy is
#  deliberate; the interval below is the one thing that had to change.
# ══════════════════════════════════════════════════════════════════════


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

# Tool 1 spaces its requests at 0.15s. This module keeps its own counter — two
# copies of the same limiter in one process can interleave, and 0.15 each would
# put the app at ~13 req/s against a 10 req/s limit with two people on it. 0.30
# here holds the combined worst case under the ceiling. Nothing on this page is
# latency-sensitive: it is one filing fetch per ticker.
_SEC_MIN_INTERVAL = 0.30
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
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
    raise RuntimeError(
        "SEC is throttling this app. Wait a minute and try again. If it keeps happening, "
        "check that a real contact address is set in Streamlit secrets — the SEC blocks "
        "generic user agents outright.")


ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "40-F")


@st.cache_data(ttl=86400, show_spinner=False)
def _ticker_map() -> dict[str, str]:
    r = _sec_get("https://www.sec.gov/files/company_tickers.json", timeout=15)
    return {e["ticker"].upper(): str(e["cik_str"]).zfill(10) for e in r.json().values()}


@st.cache_data(ttl=86400, show_spinner=False)
def _submissions(cik: str) -> dict:
    """Company metadata plus the recent filing index.

    Two things come from here that companyfacts cannot give: the SIC code, and
    a link to the latest proxy statement. Insider ownership is never tagged in
    XBRL — it lives in a beneficial ownership table inside the DEF 14A — so the
    most this tool can honestly do is take you straight to it.
    """
    try:
        return _sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json", timeout=20).json()
    except Exception:
        return {}


def _latest_filing(subs: dict, forms: tuple[str, ...]) -> tuple[str, str] | None:
    """(url, filing date) of the most recent filing of one of these forms."""
    rec = subs.get("filings", {}).get("recent", {})
    cik_int = str(int(subs.get("cik", 0) or 0))
    for form, acc, doc, date in zip(rec.get("form", []), rec.get("accessionNumber", []),
                                    rec.get("primaryDocument", []), rec.get("filingDate", [])):
        if form in forms and acc:
            return (f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
                    f"{acc.replace('-', '')}/{doc}", date)
    return None


def _form4_count(subs: dict, days: int = 365) -> int:
    rec = subs.get("filings", {}).get("recent", {})
    cutoff = dt.date.today() - dt.timedelta(days=days)
    n = 0
    for form, date in zip(rec.get("form", []), rec.get("filingDate", [])):
        if form == "4" and date:
            try:
                if dt.date.fromisoformat(date) >= cutoff:
                    n += 1
            except ValueError:
                continue
    return n


def is_financial(sic: str) -> bool:
    """SIC 6000-6799: banks, insurers, brokers, REITs. Leverage is the product
    for these, not a financing choice, so an invested-capital denominator built
    from equity plus borrowings describes nothing real."""
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


def dE_projectable(p: "Pooled") -> bool:
    """Can this ΔE be applied to next year's profit? Ported from tool 1.

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
    wording of the refusals, but not to the gate that decides whether the
    number gets projected.
    """
    return p.dE_defined and 0.0 < p.dE <= DE_UNUSABLE_ABOVE


# Each capital line, and which way a hole in it pushes ROIC. The direction is
# the point: a missing borrowings component understates the capital base and
# ROIC reads HIGH, which is the error class that costs money. A missing cash
# component understates the deduction and ROIC reads LOW, which is merely
# annoying. The same staleness, opposite consequences.
CAPITAL_LINE_EFFECT = (
    ("Shareholders' equity", "raises"),
    ("Borrowings", "raises"),
    ("Leases", "leases"),
    ("Cash", "lowers"),
    ("Investments", "lowers"),
    ("Goodwill & intangibles", "exgoodwill"),
)


def stale_capital_lines(latest: dict[str, str], ni_fy: int,
                        rows=CAPITAL_LINE_EFFECT) -> list[tuple[str, int, int, str]]:
    """Capital lines whose total stops before net income does. ITEM 9, tool 2.

    Tool 1's twin carries a stale balance FORWARD, because `g()` takes
    max(d.items()) per line. This page does the opposite: `_instant_sum` adds
    a missing component as ZERO for that year, so the line does not go stale,
    it goes absent. AutoZone's current debt stops at FY2014 and simply drops
    out of every year after it; invested capital of 5,785M is missing it, and
    the 55.51% ROIC reads high as a result.

    Same disease, opposite arithmetic, opposite wording — which is why this is
    a separate function from tool 1's rather than a shared one.

    INSTANTS ONLY, for the reason set out in tool 1: a flow line can
    legitimately stop, a balance sheet cannot. `_sum_latest` already reports
    the earliest year through which each total is COMPLETE rather than the
    union's newest year, so a partly-current line is caught here even when one
    of its components runs to the present.
    """
    out = []
    for name, effect in rows:
        fy = latest.get(name, "—")
        if not fy.isdigit():
            continue
        if int(fy) < ni_fy:
            out.append((name, int(fy), ni_fy - int(fy), effect))
    return out


def missing_component_total(series_list: list[dict[int, float]], cur_fy: int) -> float:
    """Value of the components that stopped, at the last year each was tagged.

    The first version of this compared a GROUP's total at its last complete
    year against its total today, and AutoZone showed why that is wrong: its
    `LongTermDebtCurrent` stops at FY2014 at 181M, while the long-term
    component beside it grew to 8,800M. Today's total is far larger than the
    total back then, so the difference came out negative and the note
    suppressed itself on exactly the ticker it was written for.

    A component that stops is missing its own last figure — nothing to do with
    what its neighbours did in the meantime. Each series here is one component
    of one capital line; a series that reaches `cur_fy` contributes nothing.
    """
    total = 0.0
    for d in series_list:
        if d and cur_fy not in d:
            total += max(d.items())[1]
    return total


def growth_leg_reason(name: str, points: int, span: int, first: float, last: float,
                      min_span: int = 5) -> str:
    """Why a growth leg was dropped — the real reason, not a default one.

    `cagr` returns None for four different reasons and the page reported all
    of them as "covered too few years to be a growth rate", printing the SPAN
    beside it. Salesforce, 26 Aug 2026: nine clean years spanning nine years,
    labelled "owners' earnings (9y) covered too few years". The actual cause
    was FY2017 owners' earnings of -27 — a compound rate cannot start from a
    negative base. The note contradicted its own figure and sent the reader
    looking for missing years that were all present.

    Returns "" when the leg is usable.
    """
    if points < 3:
        return f"{name} has only {points} readable year" + ("" if points == 1 else "s")
    if span < min_span:
        return f"{name} spans only {span} years"
    if first <= 0:
        return f"{name} starts from a loss, so a compound rate has no base to grow from"
    if last <= 0:
        return f"{name} ends in a loss, which no growth rate can describe"
    return ""


def carried_forward_capital(invested: float, numerator: float,
                            missing: list[tuple[str, float, str]]) -> tuple[float, float | None]:
    """Invested capital and ROIC if a stale line were carried forward, not zeroed.

    ITEM 4, this page's half. Tool 1 carries the last figure found forward;
    `_instant_sum` here adds the missing component as zero. Same filings,
    opposite arithmetic, and neither is conservative in general — see
    stale_swing_note in tool 1 for why unifying the two would be a mistake.
    So nothing is unified: this states the size of the disagreement.

    Only lines whose effect is unambiguous are counted. Leases are excluded
    because an operating lease enters the base only when the checkbox is on
    and a finance lease always does, and the panel row sums both — any claim
    about a lease line has to say which half, so it says nothing here.
    Goodwill is excluded because it moves the ex-goodwill figure alone.
    """
    adj = invested
    for _name, amount, effect in missing:
        if amount <= 0:
            continue
        if effect == "raises":
            adj += amount
        elif effect == "lowers":
            adj -= amount
    return adj, (numerator / adj if adj > 0 else None)


def stale_capital_swing_note(invested: float, numerator: float, roic: float | None,
                             missing: list[tuple[str, float, str]]) -> str:
    """One sentence putting a number on what the missing component is worth."""
    live = [m for m in missing if m[1] > 0.05 and m[2] in ("raises", "lowers")]
    if not live or roic is None:
        return ""
    adj, alt = carried_forward_capital(invested, numerator, live)
    if alt is None or abs(adj - invested) < 0.5:
        return ""
    return (" Carried forward at the last complete figure instead of added as zero — the way "
            "tool 1 treats the same line — invested capital would be about "
            f"{adj:,.0f}M against {invested:,.0f}M, and this return about {alt:.1%} against "
            f"{roic:.1%}. Neither is a correction of the other: one guesses the balance "
            "persisted, the other that it ended. The tag name settles it.")


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


def _instant_first(facts: dict, groups: list[list[str]],
                   unit: str = "USD",
                   skipped: list | None = None,
                   prefer_recent: bool = False) -> tuple[dict[int, float], int]:
    """Like _instant across several concept groups, returning which group won.

    Needed for cash: CashAndCashEquivalentsAtCarryingValue excludes restricted
    balances, the combined tag does not, and the difference is not shareholder
    money. Knowing which one answered is what lets the restricted amount be
    taken back out only when it was actually included.
    """
    for i, g in enumerate(groups):
        s = _instant(facts, g, unit, None, skipped, prefer_recent)
        if s:
            return s, i
    return {}, -1


def _instant_sum(facts: dict, groups: list[list[str]],
                 sources: list[str] | None = None,
                 skipped: list | None = None,
                 prefer_recent: bool = False) -> dict[int, float]:
    """Sum of several independent balance-sheet lines, per year.

    A missing component is treated as zero, which is right far more often than
    not: a company with no commercial paper simply does not tag it. It is wrong
    when a filer uses a tag this reader does not know, which is why every
    capital figure is shown line by line rather than only as a total.
    """
    out: dict[int, float] = {}
    for g in groups:
        for fy, v in _instant(facts, g, "USD", sources, skipped, prefer_recent).items():
            out[fy] = out.get(fy, 0.0) + v
    return out


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
            d_ = dt.datetime.utcfromtimestamp(ts)
            out[f"{d_.year:04d}-{d_.month:02d}"] = float(c)

    # Yahoo has used both {"numerator": 2, "denominator": 1} and
    # {"splitRatio": "2:1"} over the years, and returns the block under
    # different keys depending on the endpoint version. Parse defensively:
    # a split this reader cannot read must leave the factor at 1.0 rather
    # than throw, because the whole price series is riding on this call.
    splits: dict[str, float] = {}
    for s_ in ((res.get("events") or {}).get("splits") or {}).values():
        try:
            day = dt.datetime.utcfromtimestamp(int(s_["date"])).date().isoformat()
        except (KeyError, TypeError, ValueError, OSError):
            continue
        num, den = s_.get("numerator"), s_.get("denominator")
        if not (num and den):
            try:
                num, den = str(s_.get("splitRatio", "")).split(":")
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
    vals, day = [], s
    while day <= e:
        v = closes.get(f"{day.year:04d}-{day.month:02d}")
        if v:
            vals.append(v)
        day = (day.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
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
    """Restate historical share counts onto the current basis. Ported from
    tool 1: XBRL reports shares as filed, market prices arrive already
    split-adjusted, and mixing the two makes one year's dilution equal the
    whole split."""
    shares = {k: v for k, v in shares.items() if v and v > 0}
    fys, notes = sorted(shares), []
    if len(fys) < 2:
        return dict(shares), notes
    adjusted, factor = {}, 1.0
    for i in range(len(fys) - 1, -1, -1):
        fy = fys[i]
        adjusted[fy] = shares[fy] * factor
        if i > 0 and shares[fys[i - 1]] > 0:
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
            if ratio > 2.85 and shares[fys[i - 1]] < 25e6:
                continue
            if ratio > 0 and (ratio > 2.85 or ratio < 0.35):
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
                                 "restated onto the current basis; without this both the SBC "
                                 "cost and the dilution rate would be wildly overstated. A "
                                 "first listing or a recapitalisation produces the same jump "
                                 "and this reader cannot tell them apart, so if the company "
                                 "did not split, the restated years are wrong.")
    return adjusted, notes


# ══════════════════════════════════════════════════════════════════════
#  TRAGIC ALGEBRA  — ported unchanged from tool 1
#
#  Owners' earnings are the numerator of Burry's ROIC, so this page needs
#  the same engine. The self-test at the foot re-runs tool 1's Alphabet
#  checks against this copy: if the two ever drift apart, that is where it
#  will show.
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
    excluded: str = ""        # non-empty means capital formation, not pay

    @property
    def C(self) -> float:
        return self.Cw - self.Ce

    @property
    def V(self) -> float:
        """Market value of shares delivered to EMPLOYEES.

        `A` is stock issued to buy a company, netted out here rather than
        through dS. The protocol has always excluded M&A issuance — it is a
        corporate transaction, not pay — but the exclusion was routed through
        the share count, which meant finding a tagged number of SHARES. Filers
        mostly do not publish one. Salesforce publishes the dollar
        consideration instead, and a tagged value is better than a count in any
        case, because the count has to be priced at the year's average while
        the value is what the deal actually cost.

        Untreated, Slack put $11.3B and Tableau $15.6B into Salesforce's
        stock-comp column, and four separate years printed dE below -200%.
        """
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
    def dE_defined(self) -> bool:
        return self.sum_N > 0


def pool_safe(years: list[Year], fallback: "Pooled") -> "Pooled":
    """pool() over a slice that may be empty or sum to zero net income."""
    try:
        return pool(years)
    except (ValueError, ZeroDivisionError):
        return fallback


def pool(years: list[Year]) -> Pooled:
    years = [y for y in years if not y.excluded]
    sN = sum(y.N for y in years)
    if not years or sN == 0:
        raise ValueError("Not enough data to pool.")
    return Pooled(dE=sum(y.OE for y in years) / sN, sum_N=sN,
                  sum_OE=sum(y.OE for y in years), sum_omega=sum(y.omega for y in years),
                  sum_G=sum(y.G for y in years), years=len(years))


# ══════════════════════════════════════════════════════════════════════
#  RETURN ON INVESTED CAPITAL
# ══════════════════════════════════════════════════════════════════════


@dataclass
class Capital:
    """One year's capital base, kept as separate lines so the total can be
    audited. Everything in $M."""
    fy: int
    equity: float = 0.0
    minority: float = 0.0
    debt: float = 0.0              # borrowings, short and long
    finance_leases: float = 0.0    # capitalised leases: debt in all but name
    operating_leases: float = 0.0  # shown, not applied — see note below
    cash: float = 0.0              # cash and investments, restricted removed
    restricted: float = 0.0
    goodwill: float = 0.0
    intangibles: float = 0.0
    revenue: float = 0.0
    op_cash_pct: float = 0.02
    other_capital: float = 0.0     # judgement, seeded at zero
    equity_found: bool = False
    # Two switches, both off by default, which together are the difference
    # between this number and the one on a data provider's website. Neither
    # is a correction: they are different questions about the same filings.
    include_leases: bool = False   # capitalised office and store networks
    cash_in_base: bool = False     # leave cash in, as most providers do

    @property
    def op_cash_need(self) -> float:
        """Cash the business cannot actually hand out. Burry's rule is that
        only genuinely deployable cash is subtracted from the capital base;
        working balances fund the business and belong in it. No published
        figure exists for the split, so this is a stated convention — a
        percentage of revenue, adjustable, and visible in the waterfall."""
        return max(0.0, self.revenue * self.op_cash_pct)

    @property
    def deployable_cash(self) -> float:
        if self.cash_in_base:
            return 0.0
        return max(0.0, self.cash - self.op_cash_need)

    @property
    def total_capital(self) -> float:
        base = self.equity + self.debt + self.finance_leases
        # A leased office network is capital at work whatever the accounting
        # calls it. Burry's formula removes operating leases from a
        # total-capital figure that already contained them; this base never
        # did, so the switch adds them rather than subtracting. For a filer
        # with thousands of leased locations this is the difference between
        # a return on almost nothing and a return on the actual footprint.
        return base + (self.operating_leases if self.include_leases else 0.0)

    @property
    def invested(self) -> float:
        return self.total_capital - self.deployable_cash + self.other_capital

    @property
    def tangible_invested(self) -> float:
        return self.invested - self.goodwill - self.intangibles


@dataclass
class RoicYear:
    fy: int
    OE: float
    interest_income: float
    lease_payments: float
    other_expense: float
    cap: Capital
    excluded: str = ""

    @property
    def numerator(self) -> float:
        return self.OE - self.interest_income - self.lease_payments - self.other_expense

    @property
    def roic(self) -> float | None:
        c = self.cap.invested
        return self.numerator / c if c > 0 else None

    @property
    def tangible_roic(self) -> float | None:
        c = self.cap.tangible_invested
        return self.numerator / c if c > 0 else None

    @property
    def reason(self) -> str:
        """Empty when the year's ROIC can be trusted; otherwise why it cannot.

        Every one of these produced a confident wrong number before it produced
        a refusal. A negative capital base is the worst of them: buybacks that
        push equity below zero flip the sign, and a superb business prints as
        a catastrophic one.
        """
        if self.excluded:
            return f"{self.excluded} — owners' earnings distorted"
        # BellRing FY2018-19, 28 Aug 2026: the pre-listing holdco files net
        # income of exactly zero, so the year sits in the window with owners'
        # earnings 0 over a capital base of 452 and printed ROIC 0.0% — which
        # reads as "earned nothing on its capital" when nothing was earned or
        # lost, only absent. A zero numerator is a missing year, not a return.
        if self.OE == 0.0 and self.numerator == 0.0:
            return "no owners' earnings read for this year"
        if not self.cap.equity_found:
            return "no equity figure in this year's filing"
        if self.cap.invested <= 0:
            return "invested capital is zero or negative"
        if self.cap.revenue > 0 and self.cap.invested / self.cap.revenue < 0.05:
            return "capital base under 5% of revenue — ratio not informative"
        return ""


def median_roic(rows: list[RoicYear], n: int = 5) -> float | None:
    vals = [r.roic for r in rows[-n:] if not r.reason and r.roic is not None]
    return statistics.median(vals) if vals else None


# ══════════════════════════════════════════════════════════════════════
#  THE 100-BAGGER ARITHMETIC
# ══════════════════════════════════════════════════════════════════════


def required_growth(multiple_now: float, multiple_exit: float, years: int,
                    target: float = 100.0, dilution: float = 0.0) -> float | None:
    """Annual growth in owners' earnings needed for a target total return.

        target = (1+g)^N x (M_exit/M_now) / (1+dilution)^N

    Dilution enters as a straight drag on the per-share result, which is the
    only result that matters. A business can multiply its earnings a hundred
    times and still hand you far less if it pays for the growth in stock.
    """
    if multiple_now <= 0 or multiple_exit <= 0 or years <= 0 or target <= 0:
        return None
    return (target * multiple_now / multiple_exit) ** (1.0 / years) * (1.0 + dilution) - 1.0


def sustainable_growth(roic: float, payout_ratio: float) -> float:
    """Growth a business can fund from its own profits: ROIC x reinvestment.

    Above this it must raise capital — debt, which is finite, or stock, which
    is the dilution term above. This is the ceiling Burry means when he says
    ROIC bounds growth.
    """
    return roic * max(0.0, 1.0 - payout_ratio)


def per_share_ceiling(roic: float, payout_ratio: float, buyback_yield: float) -> float:
    """Total-earnings growth plus the lift from a shrinking share count.

    Buybacks do not grow the business, but a hundredfold on fewer shares is
    still a hundredfold to whoever stayed. Retiring 3% a year adds roughly
    3 points to per-share compounding.
    """
    g = sustainable_growth(roic, payout_ratio)
    b = min(max(buyback_yield, -0.20), 0.20)
    return (1.0 + g) / (1.0 - b) - 1.0


def cagr(first: float, last: float, years: int) -> float | None:
    if years <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1.0


def trend_growth(values: list[float]) -> float | None:
    """Growth rate from a least-squares fit through log(values), not endpoints.

    `cagr` above reads two numbers and ignores everything between them, so the
    figure it returns is a slope between whichever years happen to sit at the
    ends of the window. Progressive, 25 Aug 2026: owners' earnings ran
    954 (FY2016, a weak underwriting year) to 11,440 (FY2025, its best ever),
    and the two-point rate came out at 31.8% a year against a required 26.0%.
    The same ten numbers fitted log-linearly give 20.6%. The verdict was
    decided by the choice of measure, not by the company.

    The generous reading is the right one for a REFUSAL — a refusal that
    survives the kindest reading of the history is worth trusting. It is
    backwards for a PASS, and a false green is the only error on this page
    that costs money. So both figures are computed and both are shown.

    Returns None on fewer than three points or any non-positive value; a
    company that lost money cannot have a log fitted through it.
    """
    if len(values) < 3 or any(v <= 0 for v in values):
        return None
    n = len(values)
    xs = list(range(n))
    ys = [math.log(v) for v in values]
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    return math.exp(slope) - 1.0


# Bands for the starting size, stated as this tool's own convention rather than
# attributed to a precise figure in Mayer. What is not a convention is the
# arithmetic in the second column: it is simply what a hundredfold means.
SIZE_BANDS = [
    (500, "genuinely small base — the size range most 100-baggers started from"),
    (2_000, "small, not micro — 100x still lands inside what has been done before"),
    (10_000, "mid cap — 100x means a business worth several hundred billion"),
    (100_000, "large cap — 100x lands beyond anything that has ever traded"),
]

WORLD_GDP_M = 110_000_000.0   # world GDP, roughly $110T, expressed in $M


def size_band(mcap_m: float) -> str:
    for ceiling, label in SIZE_BANDS:
        if mcap_m <= ceiling:
            return label
    return "the arithmetic refuses this one on size alone"


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
    # Interest earned on the cash pile. It comes OUT of the numerator because
    # the cash came out of the denominator — leave it in and a company with a
    # large treasury books its deposit income as an operating return.
    "INT": (["InvestmentIncomeInterest", "InvestmentIncomeInterestAndDividend",
             "InterestIncomeOther"], []),
    # Finance lease principal. A financing outflow that never touches the
    # income statement, so earnings do not yet reflect it.
    "LEASEPAY": (["FinanceLeasePrincipalPayments",
                  "RepaymentsOfLongTermCapitalLeaseObligations"], []),
    "DIV": (["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"], []),
    "CAPEX": (["PaymentsToAcquirePropertyPlantAndEquipment",
               "PaymentsToAcquireProductiveAssets"], []),
}

# Balance-sheet groups. Each inner list is an ordered fallback where the first
# tag with data wins; the outer list is summed.
EQUITY = [["StockholdersEquity"],
          ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]]
MINORITY = [["MinorityInterest"]]
# DebtLongtermAndShorttermCombinedAmount is LAST because it is broader,
# not a synonym: it is the whole debt balance, long-term and current
# together. Progressive, 25 Aug 2026: it does not tag
# LongTermDebtNoncurrent at all and stopped tagging LongTermDebt after
# 2015, so invested capital of 21,933M carried no borrowings — $6,897M
# missing at FY2025. Its LongTermDebtCurrent is tagged and reads exactly
# zero every year, so the current group below double-counts nothing.
# Keeping the two narrower tags preferred confines this to filers where
# they have gone stale, and makes the fallback note name the swap.
DEBT = [["LongTermDebtNoncurrent", "LongTermDebt",
         "DebtLongtermAndShorttermCombinedAmount"],
        ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"],
        ["CommercialPaper"]]
FIN_LEASE = [["FinanceLeaseLiabilityNoncurrent", "CapitalLeaseObligationsNoncurrent"],
             ["FinanceLeaseLiabilityCurrent", "CapitalLeaseObligationsCurrent"]]
OP_LEASE = [["OperatingLeaseLiabilityNoncurrent", "OperatingLeaseLiability"]]
CASH_PLAIN = ["CashAndCashEquivalentsAtCarryingValue"]
CASH_WITH_RESTRICTED = ["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
INVESTMENTS = [["ShortTermInvestments", "MarketableSecuritiesCurrent",
                "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
               # LongTermInvestments is LAST because it is broader, not a synonym.
               # The two ahead of it are debt securities — cash-like, and safe to
               # subtract from the capital base. LongTermInvestments is total
               # long-term investments and can hold strategic equity stakes in
               # other companies, which are not deployable cash.
               # Booking Holdings, 24 Aug 2026: it tagged AvailableForSale...
               # Noncurrent for the last time in 2010 and has used
               # LongTermInvestments since 2017, so the capital base ignored the
               # line entirely from 2011 on. Keeping the narrower tag preferred
               # is what makes the fallback note fire and name the swap.
               ["MarketableSecuritiesNoncurrent",
                "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
                "LongTermInvestments"]]
RESTRICTED = [["RestrictedCashAndCashEquivalentsNoncurrent", "RestrictedCashNoncurrent"],
              ["RestrictedCashAndCashEquivalentsCurrent", "RestrictedCashCurrent"]]
GOODWILL = [["Goodwill"]]
INTANGIBLES = [["FiniteLivedIntangibleAssetsNet", "IntangibleAssetsNetExcludingGoodwill"]]


TAG_LABELS = {
    "N": "Net income", "G": "GAAP stock comp", "T": "Buybacks",
    "Cw": "Tax withheld on vesting", "Ce": "Option / ESPP proceeds",
    "REV": "Revenue", "INT": "Interest income", "LEASEPAY": "Finance lease payments",
    "DIV": "Dividends paid", "CAPEX": "Capital expenditure",
    "MA": "Shares issued for acquisitions", "OFFER": "Shares issued in offerings",
    "CONV": "Shares from conversions",
    "MAV": "Value of stock issued for acquisitions",
}


def _sum_latest(facts: dict, groups: list[list[str]]) -> str:
    """Latest year through which a SUMMED total is complete.

    A summed row reports the union of its components, so it shows the newest
    year any component reached — which is not the newest year the total is
    whole. Paychex tags MarketableSecuritiesCurrent to 2026 and
    MarketableSecuritiesNoncurrent to 2011; the union says 2026 while every
    year after 2011 is current investments alone. The earliest component is
    the honest answer, and the line-by-line rows in tool 1 show the rest.
    """
    yrs = [max(s) for s in (_instant(facts, g, prefer_recent=True) for g in groups) if s]
    return str(min(yrs)) if yrs else "—"


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
    """Everything this page needs, in one pass over the filings."""
    cmap = _ticker_map()
    resolved = resolve_ticker(ticker, cmap)
    if resolved is None:
        raise ValueError(
            f"'{ticker}' is not in the SEC company list. Class shares are listed with a "
            "hyphen rather than a dot — BRK-B, BF-B, HEI-A — and both spellings are "
            "accepted here, so this is more likely a delisted, foreign or private company.")
    ticker = resolved
    cik = cmap[ticker]
    facts = _facts(cik)
    subs = _submissions(cik)
    sic, sic_desc = str(subs.get("sic", "")), str(subs.get("sicDescription", ""))

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
                "currency throughout, and a euro capital base against a dollar share price "
                "would look fine and be wrong. Foreign private issuers filing in their home "
                "currency are not supported.")
        raise ValueError(
            f"No annual net income found for {ticker}. The filer uses tags this reader does not "
            "recognise. Owners' earnings are the numerator of everything here, so nothing can "
            "be computed without it.")

    shares_out = _instant(facts, ["CommonStockSharesOutstanding", "CommonStockSharesIssued",
                                  "EntityCommonStockSharesOutstanding"], unit="shares")
    shares_out = {k: v for k, v in shares_out.items() if v and v > 0}
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
    _c_out = _instant(facts, ["CommonStockSharesOutstanding"], unit="shares")
    _c_iss = _instant(facts, ["CommonStockSharesIssued"], unit="shares")
    _treas = _instant(facts, ["TreasuryStockCommonShares", "TreasuryStockShares",
                              "TreasuryStockNumberOfSharesHeld",
                              "TreasuryStockCommonSharesHeld"], unit="shares")
    _share_route = "as tagged"
    if _wv and shares_out:
        _lat, _latw = max(shares_out), max(_wv)
        _win0 = sorted(series["N"])[-n_years:]
        _static = len({round(v) for v in shares_out.values()}) <= 2
        _treasury = shares_out[_lat] > 1.15 * _wv[_latw]
        # A third failure, found on TransDigm: the tagged series is neither
        # inflated nor static, just SHORT. CommonStockSharesOutstanding covered
        # 3 of 10 years against a 16-year cover page, so the share change read
        # +0.0 in every year and the whole buyback fell on employees — the same
        # damage as the treasury case, arriving by a different door. Coverage is
        # the thing to test, not the symptom that first made it visible.
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
                if _best[0] < 0.6 * len(_win):
                    notes.append(
                        f"Only {_best[0]} of the {len(_win)} years in this window have a share "
                        "count from any tag this reader knows. Years without one show no share "
                        "change, so their stock-comp cost is the whole buyback and their owners' "
                        "earnings are understated. Treat the year-by-year table as partial.")
            else:
                _pick, _share_route = _wv, "the weighted-average diluted count"
            shares_out, _extra = split_adjust(_pick)
            notes.extend(_extra)
            notes.append(
                ("The share count read as {:,.1f}M against a weighted-average diluted count of "
                 "{:,.1f}M — that far above the average means issued shares, with the difference "
                 "sitting in treasury, so repurchases never showed and every per-share figure "
                 "used too many shares. Switched to {}."
                 ).format(_was / 1e6, _wv[_latw] / 1e6, _share_route)
                if _treasury else
                ("The share count barely moved while the company was buying stock back, so the "
                 "tag being read is not shares outstanding. Switched to {}.").format(_share_route))
            if _share_route.startswith("the weighted"):
                notes.append(
                    "That count is an average over each year rather than a year-end snapshot, so "
                    "its change lags the repurchase and the true stock-comp cost below will be "
                    "erratic — compare it against the GAAP charge before trusting any year.")

    try:
        closes, splits = _monthly_closes(ticker)
    except Exception:
        closes, splits = {}, {}

    # A split reaches the price series within a day and the share counts here
    # not until the next 10-K, up to a year later. In between, every share
    # change was being priced at a market price on the other basis and market
    # cap was wrong by the split factor — which decides the size verdict.
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
    # (dilution, the exit multiple, market cap) comes out invariant.
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
              f"same basis. Without this the market cap would be wrong by that factor, and market "
              f"cap is what the size verdict is decided on. The next annual filing makes the "
              f"adjustment unnecessary and it will stop being applied.")

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
    # and the page rendered a full verdict on them; a hundredfold is a claim
    # about decades, and four annual figures is the least that can support one.
    if len(fys) < 4:
        raise ValueError(
            f"Only {len(fys)} year(s) of annual figures could be read for {ticker}"
            + (f" (FY{min(fys)}" + (f"-FY{max(fys)})" if len(fys) > 1 else ")") if fys else "")
            + ". Everything on this page — the growth a hundredfold requires, what the company "
              "has delivered, what its capital can fund — is a statement about decades. Four "
              "years is the minimum this tool will reason from. A recent listing, a filer using "
              "tags this reader does not know, or a foreign issuer are the usual causes.")

    # Enough history is not the same as the right history — see the window
    # guards above. Revenue first, because both series come from the same
    # filings; the calendar as a backstop for a filer with no revenue read.
    _stale = stale_window_refusal(fys, list(series.get("REV", {})), dt.date.today().year)
    if _stale:
        raise ValueError(f"{ticker} cannot be valued from these filings — " + _stale)

    years: list[Year] = []
    non_sbc_total = 0.0
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
        years.append(Year(fy=fy, N=N / 1e6, G=get("G"), T=get("T"), dS=dS,
                          Cw=get("Cw"), Ce=get("Ce"), A=get("MAV"),
                          price=_avg_price(closes, start, end) or 0.0))

    # V is priced at the year's average, so a year with no price contributes
    # nothing to the stock-comp cost however many shares moved.
    _unpriced = sum(1 for y in years if y.price <= 0)
    _pc = price_coverage_refusal(len(years), _unpriced, bool(closes))
    if _pc:
        raise ValueError(f"{ticker} cannot be valued from these filings — " + _pc)

    # Capital events. A listing converts preferred to common and sells new
    # stock; an all-stock acquisition issues a year's payroll many times over.
    # Priced at market, either one charges the whole transaction to employees.
    priced = [i for i, y in enumerate(years) if y.price > 0]
    for i in priced:
        base = shares_out.get(fys[i] - 1, 0.0) / 1e6
        if base <= 0:
            continue
        jump = years[i].dS / base
        first_priced = (i == priced[0])
        if jump > (0.25 if first_priced else 0.15):
            years[i].excluded = "listing year" if first_priced else "share-funded acquisition"
            notes.append(
                f"FY{years[i].fy} excluded — the share count rose {jump:.0%} in one year, which no "
                "payroll produces. Owners' earnings and ROIC are both blank for that year.")
    if non_sbc_total:
        notes.append(f"Excluded {non_sbc_total:,.1f}M shares issued for acquisitions, offerings or "
                     "conversions. Those are corporate transactions, not compensation.")
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
        notes.append("No share price for some years — their stock-comp cost is understated, so "
                     "owners' earnings and ROIC read high for those years.")
    if not any(y.Cw for y in years) and not capped_any:
        notes.append("No tax-withholding line found. That understates the SBC cost, so owners' "
                     "earnings here are flattering rather than conservative.")

    # ── capital base, per year ───────────────────────────────────────
    # Parent-only equity is preferred because net income is parent-only too.
    # Mixing a consolidated capital base with a parent's earnings understates
    # the return by exactly the minority's share.
    bal: dict[str, list[str]] = {k: [] for k in
                                 ("equity", "debt", "leases", "cash", "investments", "goodwill")}
    _skips: list[tuple[str, int, str, int]] = []
    eq = _instant(facts, EQUITY[0], "USD", bal["equity"], _skips, True) or \
        _instant(facts, EQUITY[1], "USD", bal["equity"], _skips, True)
    minority = _instant_sum(facts, MINORITY, None, None, True)
    debt = _instant_sum(facts, DEBT, bal["debt"], _skips, True)
    fin_lease = _instant_sum(facts, FIN_LEASE, bal["leases"], _skips, True)
    op_lease = _instant_sum(facts, OP_LEASE, bal["leases"], _skips, True)
    restricted = _instant_sum(facts, RESTRICTED, None, None, True)
    cash_ser, which = _instant_first(facts, [CASH_PLAIN, CASH_WITH_RESTRICTED], "USD",
                                     _skips, True)
    if cash_ser:
        bal["cash"].append(CASH_PLAIN[0] if which == 0 else CASH_WITH_RESTRICTED[0])
    invest = _instant_sum(facts, INVESTMENTS, bal["investments"], _skips, True)
    goodwill = _instant_sum(facts, GOODWILL, bal["goodwill"], _skips, True)
    intang = _instant_sum(facts, INTANGIBLES, bal["goodwill"], _skips, True)
    rev = series.get("REV", {})

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

    if which == 1:
        notes.append("This filer tags only the combined cash-including-restricted line. The "
                     "restricted balance has been subtracted back out where it was tagged "
                     "separately; where it was not, deployable cash is overstated and ROIC "
                     "reads high.")
    if not eq:
        notes.append("No shareholders' equity figure found in the annual filings. Without it "
                     "there is no capital base and ROIC cannot be computed at all.")

    caps: dict[int, Capital] = {}
    for fy in fys:
        c_raw = cash_ser.get(fy, 0.0) / 1e6
        r_ = restricted.get(fy, 0.0) / 1e6
        caps[fy] = Capital(
            fy=fy,
            equity=eq.get(fy, 0.0) / 1e6,
            minority=minority.get(fy, 0.0) / 1e6,
            debt=debt.get(fy, 0.0) / 1e6,
            finance_leases=fin_lease.get(fy, 0.0) / 1e6,
            operating_leases=op_lease.get(fy, 0.0) / 1e6,
            cash=max(0.0, c_raw - (r_ if which == 1 else 0.0)) + invest.get(fy, 0.0) / 1e6,
            restricted=r_,
            goodwill=goodwill.get(fy, 0.0) / 1e6,
            intangibles=intang.get(fy, 0.0) / 1e6,
            revenue=rev[fy][2] / 1e6 if fy in rev else 0.0,
            equity_found=fy in eq,
        )

    latest_cap = caps.get(fys[-1], Capital(fy=fys[-1]))
    if latest_cap.minority > 0 and latest_cap.equity > 0 \
            and latest_cap.minority / latest_cap.equity > 0.05:
        notes.append(
            f"Non-controlling interests are {latest_cap.minority/latest_cap.equity:.0%} of "
            "shareholders' equity. Net income here is the parent's slice only, and so is the "
            "equity used in the capital base — consistent, but both understate the "
            "consolidated business. Read ROIC as the return on your slice.")
    if is_financial(sic):
        notes.append(
            f"{sic_desc or 'Financial company'} (SIC {sic}). Leverage is the product for banks, "
            "insurers and REITs rather than a financing decision, so equity plus borrowings "
            "does not describe capital at work and cash is not free. ROIC is not shown.")

    # Share count: outstanding beats trailing weighted average, but a dual-class
    # filer tags each class separately and only one may be picked up.
    sh_out = shares_out[max(shares_out)] / 1e6 if shares_out else 0.0
    wavg = _annual(facts, ["WeightedAverageNumberOfDilutedSharesOutstanding",
                           "WeightedAverageNumberOfSharesOutstandingDiluted"], [])
    # Scaled too, or the dual-class test below compares a post-split count
    # against a pre-split one and fires on a company with one share class.
    wavg_v = wavg[max(wavg)][2] / 1e6 * _split_factor if wavg else 0.0
    diluted = sh_out or wavg_v
    if sh_out > 0 and wavg_v > 0 and sh_out / wavg_v < 0.65:
        diluted = wavg_v
        notes.append(f"Shares outstanding read as {sh_out:,.1f}M but weighted-average diluted is "
                     f"{wavg_v:,.1f}M — too big a gap for buybacks, and the usual cause is a "
                     "second share class that was missed. Using the diluted figure.")

    # Net annual change in the share count, over the window and excluding the
    # capital-event years. Positive is dilution, negative is retirement.
    clean = [fy for fy in fys if not any(y.fy == fy and y.excluded for y in years)]
    dil = None
    if len(clean) >= 3 and clean[0] in shares_out and clean[-1] in shares_out:
        dil = cagr(shares_out[clean[0]], shares_out[clean[-1]], clean[-1] - clean[0])

    proxy = _latest_filing(subs, ("DEF 14A", "DEFA14A", "DEF14A"))
    # A fiscal year ending outside November-January means the balance sheet is
    # a snapshot taken at a busy point in the company's own cycle. H&R Block
    # closes on 30 April, days after tax season delivers its cash.
    fye_month = int(series["N"][fys[-1]][1][5:7]) if fys else 12
    # Computed once, here, and handed to BOTH the panel and the staleness note
    # below. `_sum_latest` re-reads the facts on every call, and a note running
    # its own lookup is one edit away from disagreeing with the panel sitting
    # directly under it.
    _cap_latest = {
        "Shareholders' equity": _latest_fy(eq),
        "Borrowings": _sum_latest(facts, DEBT),
        "Leases": _sum_latest(facts, FIN_LEASE + OP_LEASE),
        "Cash": _latest_fy(cash_ser),
        "Investments": _sum_latest(facts, INVESTMENTS),
        "Goodwill & intangibles": _sum_latest(facts, GOODWILL + INTANGIBLES),
    }
    _stale_cap = stale_capital_lines(_cap_latest, fys[-1] if fys else 0)
    # What the missing component is worth, in $M, taken from the SAME series
    # the capital base is built from rather than looked up again. The estimate
    # is the total when the line was last complete less whatever survives in
    # the current year — so a line that merely grew reads as nothing missing.
    _cur_fy = fys[-1] if fys else 0
    # Per COMPONENT, not per group total — see missing_component_total.
    _cap_groups = {"Shareholders' equity": EQUITY, "Borrowings": DEBT,
                   "Cash": [CASH_PLAIN], "Investments": INVESTMENTS}
    _cap_missing = []
    for _n, _y, _g, _eff in _stale_cap:
        _grp = _cap_groups.get(_n)
        if not _grp:
            continue
        _sers = [_instant(facts, _c, "USD", None, None, True) for _c in _grp]
        _cap_missing.append((_n, missing_component_total(_sers, _cur_fy) / 1e6, _eff))
    if is_financial(sic):
        # ROIC and its ex-goodwill twin are both withheld for financials, so a
        # stale goodwill line has nothing on this page to be wrong about.
        # Progressive, 26 Aug 2026: its only stale capital line is goodwill,
        # and the note fired to describe an effect on a figure the same page
        # refuses to show three notes higher up.
        _stale_cap = [t for t in _stale_cap if t[3] != "exgoodwill"]
    if _stale_cap:
        _up = [t for t in _stale_cap if t[3] == "raises"]
        _down = [t for t in _stale_cap if t[3] == "lowers"]
        _gw = [t for t in _stale_cap if t[3] == "exgoodwill"]
        _lse = [t for t in _stale_cap if t[3] == "leases"]
        _say = "; ".join(f"{_n.lower()} is complete only to FY{_y}, {_g} year"
                         f"{'s' if _g > 1 else ''} behind" for _n, _y, _g, _ in _stale_cap)
        _cost = []
        if _up:
            _cost.append("**Understates the capital base, so ROIC reads high.** "
                         + ", ".join(t[0].lower() for t in _up).capitalize()
                         + (" is" if len(_up) == 1 else " are") + " part of what the business "
                         "runs on, and the missing years are added as zero rather than carried "
                         "forward. This is the direction that costs money.")
        if _down:
            _cost.append("**Overstates the capital base, so ROIC reads low.** "
                         + ", ".join(t[0].lower() for t in _down).capitalize()
                         + (" is" if len(_down) == 1 else " are")
                         + " deducted, and a deduction that goes missing makes the business look "
                           "more capital-hungry than it is. Conservative, but still wrong.")
        if _lse:
            _cost.append("**Raises ROIC where the missing piece is a finance lease**, which is "
                         "always in the capital base. Where it is an operating lease it changes "
                         "nothing unless the leases checkbox is on. The panel row sums both, so "
                         "check which half stopped.")
        if _gw:
            _cost.append("**Moves the ex-goodwill ROIC only**, not the headline figure.")
        notes.append(
            "**A capital line here stops before net income does.** " + _say
            + f". Net income reaches FY{fys[-1] if fys else 0}, and a balance sheet is reported "
              "at every year end, so this is not a quiet year — either the balance moved to a "
              "tag this reader does not know, or the line ended and is genuinely zero now. "
              "Unlike the year-by-year table, a missing piece of a total is added as zero, so "
              "the effect is silent:\n\n"
            + "\n".join(f"- {_c}" for _c in _cost)
            + "\n\nThe tag panel names the tags that answered; the missing name is usually the "
              "whole fix.")

    pre = {
        "sic": sic, "sic_desc": sic_desc, "financial": is_financial(sic),
        "shares": diluted, "dilution": dil, "caps": caps, "fys": fys,
        "cap_missing": _cap_missing,
        "interest": {fy: abs(series["INT"][fy][2]) / 1e6 for fy in series.get("INT", {})},
        "leasepay": {fy: abs(series["LEASEPAY"][fy][2]) / 1e6 for fy in series.get("LEASEPAY", {})},
        "dividends": {fy: abs(series["DIV"][fy][2]) / 1e6 for fy in series.get("DIV", {})},
        "capex": {fy: abs(series["CAPEX"][fy][2]) / 1e6 for fy in series.get("CAPEX", {})},
        "revenue": {fy: rev[fy][2] / 1e6 for fy in rev},
        # The form that resolved against the SEC list; Yahoo uses the same
        # hyphenated spelling, so pricing BRK.B as typed returned nothing.
        "ticker": ticker,
        "name": subs.get("name", ticker),
        "proxy": proxy,
        "form4": _form4_count(subs),
        "cik": str(int(cik)), "fye_month": fye_month,
        "tags": tag_report(facts, series, tag_sources) + [
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
            {"Line": "— Shareholders' equity", "Years read": len(eq),
             "Latest year": _cap_latest["Shareholders' equity"],
             "XBRL tag": " + ".join(bal["equity"]) or "—",
             "Status": "read" if eq else "no equity tag found — ROIC cannot be built"},
            {"Line": "— Borrowings", "Years read": len(debt),
             "Latest year": _cap_latest["Borrowings"],
             "XBRL tag": " + ".join(bal["debt"]) or "—",
             "Status": "read" if debt else "none found (many companies genuinely have none)"},
            {"Line": "— Leases", "Years read": len(op_lease) + len(fin_lease),
             "Latest year": _cap_latest["Leases"],
             "XBRL tag": " + ".join(bal["leases"]) or "—",
             "Status": "read" if (op_lease or fin_lease) else "none found"},
            {"Line": "— Cash", "Years read": len(cash_ser),
             "Latest year": _cap_latest["Cash"],
             "XBRL tag": " + ".join(bal["cash"]) or "—",
             "Status": "read" if cash_ser else "no cash tag found"},
            {"Line": "— Investments", "Years read": len(invest),
             "Latest year": _cap_latest["Investments"],
             "XBRL tag": " + ".join(bal["investments"]) or "—",
             "Status": "read" if invest else "none found"},
            {"Line": "— Goodwill & intangibles", "Years read": len(goodwill) + len(intang),
             "Latest year": _cap_latest["Goodwill & intangibles"],
             "XBRL tag": " + ".join(bal["goodwill"]) or "—",
             "Status": "read" if (goodwill or intang) else "none found"},
        ],
    }
    return years, notes, pre


def build_roic(years: list[Year], pre: dict, op_cash_pct: float,
               other_expense: float, other_capital: float,
               include_leases: bool = False, cash_in_base: bool = False) -> list[RoicYear]:
    """Assemble Burry's ROIC per year.

    The two judgement terms are applied to the latest year only. Spreading a
    single forensic estimate back over a decade would imply a precision the
    estimate does not have, and would move the historical trend — the one thing
    on this page that is pure arithmetic.
    """
    out = []
    last = years[-1].fy if years else None
    for y in years:
        cap = pre["caps"].get(y.fy, Capital(fy=y.fy))
        cap.op_cash_pct = op_cash_pct
        cap.other_capital = other_capital if y.fy == last else 0.0
        cap.include_leases, cap.cash_in_base = include_leases, cash_in_base
        out.append(RoicYear(
            fy=y.fy, OE=y.OE,
            interest_income=pre["interest"].get(y.fy, 0.0),
            lease_payments=pre["leasepay"].get(y.fy, 0.0),
            other_expense=other_expense if y.fy == last else 0.0,
            cap=cap, excluded=y.excluded))
    return out


# ══════════════════════════════════════════════════════════════════════
#  THE VERDICT
#
#  One question, asked in one place: is the growth this price requires
#  inside what the business can fund and has ever managed?
#
#  Three rates, and the answer is which of them is smallest:
#    NEEDS      required_growth, from the price you pay and the multiple
#               you assume at the end
#    CAN FUND   ROIC x retention, plus whatever a shrinking share count
#               adds. Capital's ceiling.
#    HAS DONE   what it has actually delivered. History's ceiling.
#
#  Neither ceiling is a law. Capital's can be raised by borrowing;
#  history's can be broken by a genuinely new business. But when the
#  required rate sits above both, the case is closed by arithmetic
#  rather than by opinion, and that is worth saying plainly.
# ══════════════════════════════════════════════════════════════════════


@dataclass
class Verdict:
    label: str      # short headline
    kind: str       # streamlit method: success / warning / error / info
    why: str        # which constraint decided it


def assess(required: float | None, fundable: float | None,
           delivered: float | None, mcap_m: float,
           trend: float | None = None, oe_m: float | None = None) -> Verdict:
    """The whole tool, in one function, so it can be tested without a browser."""
    # Before anything else: is this input believable? Tool 1 has had this guard
    # for a long time and this page had none. Berkshire, 26 Aug 2026: a share
    # count read in Class A equivalents gave a $160M market cap against
    # $60,599M of owners' earnings, and the page printed a required growth rate
    # of MINUS 10.1% and a verdict of "open on history". A company cannot
    # capitalise at a fraction of one year's earnings; the reading is wrong.
    if oe_m is not None and oe_m > 0 and 0 < mcap_m < oe_m:
        return Verdict("This reading is not believable — an input is wrong",
                       "error", "implausible")
    if mcap_m > 0 and mcap_m * 100 > WORLD_GDP_M * 0.05:
        return Verdict("Closed on size", "error", "size")
    if required is None:
        return Verdict("Cannot be computed", "error", "no earnings base")
    if fundable is None and delivered is None:
        return Verdict("Cannot be computed", "error", "no ceiling of either kind")
    if fundable is not None and required > fundable:
        return Verdict("The arithmetic does not close", "error", "capital")
    # Fundable, but the company has never gone at anything like this rate. Both
    # ceilings are generous here — delivered takes the best of revenue and
    # owners' earnings, read both as endpoints and as a fitted trend — so
    # failing this one is a real finding.
    if delivered is not None and required > max(delivered * 1.5, delivered + 0.03):
        if fundable is None:
            return Verdict("Unprecedented, and no ceiling to check it against",
                           "warning", "history only")
        return Verdict("Fundable, but unprecedented", "warning", "history")
    # The asymmetry that matters on this page. `delivered` is a two-point rate
    # between the first and last year of the window, so it is decided by which
    # years happen to sit at the ends. The kindest reading of a history is the
    # right one to REFUSE on — a refusal that survives it is worth trusting —
    # and exactly the wrong one to PASS on, because a false green is the only
    # error here that costs money. So nothing reaches "open" unless the
    # trend through every year clears the requirement too.
    # Progressive, 25 Aug 2026: endpoints 31.8%, trend 20.6%, needed 26.0%.
    if trend is not None and required > trend:
        return Verdict("Open only on the kindest reading of history",
                       "warning", "growth measure")
    # A verdict whose capital check never ran cannot say "open". `fundable` is
    # None in two situations and neither is a pass: the capital base could not
    # be read, or ROIC was WITHHELD because it is not meaningful for the filer
    # — banks, insurers and REITs, where investments back policyholder or
    # depositor liabilities rather than shareholders.
    #
    # Progressive is the shape that showed it: delivered 31.8% against a 26.0%
    # requirement, with `can fund` n/a because it is an insurer. Only the $128B
    # size gate stopped a green. The same business at $2B market cap sailed
    # past every remaining test and printed "open" with nothing left that could
    # object — the growth leg checking itself. A tool whose whole claim is that
    # it refuses what it cannot verify must not pass what it cannot check.
    if fundable is None:
        return Verdict("Open on history, but the capital check could not run",
                       "warning", "no capital base")
    # The mirror of the branch above, and amber for the same reason: a verdict
    # checked against ONE ceiling is half-checked, whichever ceiling is
    # missing. It was left blue while its twin became a warning, and a young
    # company is exactly where a blue "Open" reads as encouragement — CAVA has
    # four years of filings and no measurable growth rate at all.
    if delivered is None:
        return Verdict("Open on capital, but there is no record to check it against",
                       "warning", "no growth history")
    return Verdict("The arithmetic is open", "success", "both")


@dataclass
class Payout:
    """What a company hands back, kept in pieces.

    A single ratio was the wrong output here. H&R Block came back as "retains
    70%" — which was dividends alone, with several hundred million a year of
    buybacks missing, because one XBRL tag did not match. A ratio cannot show
    you that; its components can.
    """
    dividends: float = 0.0        # $M a year, averaged
    buybacks: float = 0.0         # as filed
    implied: float = 0.0          # from shares retired x price: a floor
    oe: float = 0.0
    used_implied: bool = False

    @property
    def returned(self) -> float:
        return self.dividends + (self.implied if self.used_implied else self.buybacks)

    @property
    def ratio(self) -> float | None:
        return self.returned / self.oe if self.oe > 0 else None

    @property
    def warning(self) -> str:
        if not self.used_implied:
            return ""
        return (f"No repurchase figure was read for these years, but the share count fell by "
                f"about \\${self.implied:,.0f}M a year at market prices. The payout below uses that "
                "implied figure instead of zero — it is a floor, since shares issued to "
                "employees offset some of what was bought.")


def pooled_payout(years: list[Year], dividends: dict[int, float], n: int = 5) -> Payout:
    """Cash returned to shareholders, pooled over several years.

    Pooled rather than taken from the latest year for the same reason ΔE is
    pooled in tool 1: one big repurchase is a decision, not a policy.

    Buybacks appear on both sides of the ceiling and that is not double
    counting. They leave the numerator because a dollar returned is a dollar
    not reinvested; they come back as a share-count effect because a
    hundredfold on fewer shares is still a hundredfold to whoever stayed.
    """
    clean = [y for y in years if not y.excluded][-n:]
    if not clean:
        return Payout()
    k = len(clean)
    p = Payout(
        dividends=sum(dividends.get(y.fy, 0.0) for y in clean) / k,
        buybacks=sum(y.T for y in clean) / k,
        # Shares retired, valued at that year's average price. dS is already net
        # of issuance, so this understates gross repurchases — a floor, which is
        # the right direction for a fallback.
        implied=sum(max(0.0, -y.dS) * y.price for y in clean) / k,
        oe=sum(y.OE for y in clean) / k,
    )
    # Only step in when the filed figure is not merely lower but absent, and the
    # share count says real money was spent. A company that genuinely does not
    # repurchase has an implied figure of zero and is untouched.
    # Materiality matters as much as detection. Progressive's share count moved
    # by a few hundred thousand shares, implying $32M of repurchases against
    # $5.6B of owners' earnings — 0.6% — and that was enough to raise two
    # alarms including "owners' earnings are overstated, and so are tool 1's".
    # A warning that fires on noise teaches you to ignore it on signal.
    if p.implied > 0 and p.buybacks < 0.25 * p.implied and p.implied > 0.02 * max(p.oe, 0.0):
        p.used_implied = True
    return p


def roic_caveat(r: RoicYear, fye_month: int) -> str:
    """One line on how solid a ROIC reading is, instead of a warning box.

    Everything here was, at some point, a place the number went wrong. None of
    them makes it worthless; all of them change how hard you should lean on it.
    """
    c, bits = r.cap, []
    if r.roic is not None and r.roic > 0.50:
        bits.append("at this level capital is not what limits growth — the payout is, and the "
                    "ceiling already reflects that")
    if c.total_capital > 0 and c.deployable_cash > 0.5 * c.total_capital:
        bits.append("over half the gross capital base is cash being subtracted, so the answer "
                    "moves a long way with the operating-cash setting")
    if c.invested > 0 and (c.goodwill + c.intangibles) > 0.5 * c.invested:
        bits.append("acquisitions are most of the base, so the tangible figure is the one that "
                    "describes reinvestment")
    if fye_month not in (11, 12, 1):
        bits.append(f"the fiscal year ends in month {fye_month}, so the balance sheet is a "
                    "snapshot taken mid-cycle rather than at a quiet point")
    return "; ".join(bits)


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


def delivered_rate(endpoint: float | None, trend: float | None) -> float | None:
    """The kindest honest reading of a company's growth history.

    `delivered` is the ceiling a refusal has to clear, so it is deliberately
    generous: a refusal that survives the friendliest reading of the record is
    a refusal worth trusting. It used to be max(revenue CAGR, owners' earnings
    CAGR) — both two-point rates — and the comment claimed that was generous.
    It is not reliably generous. Adobe reads 28.12% on endpoints against 29.67%
    fitted through every year, and AutoZone 7.11% against 8.46%: on both, the
    trend runs ABOVE the endpoint rate, so the supposedly kind measure was the
    harsh one and the tool refused on a rate lower than the company had
    actually managed.

    This does NOT touch the pass side. `assess` gates every open verdict on
    `trend` separately, and that gate is untouched, so a larger `delivered`
    can turn a refusal into a warning but can never produce a green that the
    fitted trend does not also support. Correcting an error in the
    conservative direction is still correcting an error: a wrong number is
    worse than a cautious one.
    """
    vals = [g for g in (endpoint, trend) if g is not None]
    return max(vals) if vals else None


def fund_badge_caption(roic_med: float | None, payout_eff: float, buyback_yield: float,
                       fundable: float | None, payout_assumed: bool, financial: bool,
                       reason: str | None) -> str:
    """The line under `can fund`, saying where that figure actually came from.

    Booking read "ROIC 45% · retains 0%" beside a `can fund` of 3.69% — two
    numbers whose product is zero, printed next to a number that is not zero.
    It caused a real misreading during the session that shipped Drop 18, which
    fixed the same sentence one level down in the ROIC expander and left this
    one alone. Where payout is at or above 100%, `per_share_ceiling` reduces to
    the buyback term and the return on capital contributes nothing at all, so
    the badge names the buyback yield instead of a ROIC it is not using.
    """
    if payout_assumed:
        return f"ROIC {roic_med:.0%} · retention assumed"
    if fundable is not None:
        if payout_eff >= 1.0:
            # Kept under ~30 characters. The first version read "buyback yield
            # 3.6% · payout 170% leaves nothing retained" and Booking rendered
            # it as "buyback yield 3.6% · payout 17…" — the truncation ate the
            # half of the sentence that was the whole point of the fix. The
            # payout figure is in the assumptions block and in the paragraph
            # below; what only this badge can say is which term the number
            # came from.
            return (f"buyback yield alone · {buyback_yield:.1%}" if buyback_yield > 0 else
                    "nothing retained, no buybacks")
        return f"ROIC {roic_med:.0%} · retains {max(0.0, 1 - payout_eff):.0%}"
    if financial:
        return "financial company"
    if roic_med is not None and roic_med <= 0:
        return "return on capital is negative"
    return reason or "capital base unread"


def can_fund_explainer(oe: float, returned: float, dividends: float, buybacks: float,
                       payout: float, roic_med: float | None, buyback_yield: float) -> str:
    """The paragraph under `can fund`, which has to survive a payout above 100%.

    Booking read "leaving 0% retained, reinvested at 45%" — the third printing
    of the same mistake, after the ROIC expander caption (Drop 18) and the
    badge above this. Where payout exceeds earnings there is nothing to
    reinvest and the return on capital is not in the figure at all: the whole
    of `can fund` is the buyback term, because (1+0)/(1-b)-1 is what is left.
    Saying otherwise points the reader at a number that had no effect.
    """
    head = (f"**Can fund** is {money(oe)} of owners' earnings a year, less the "
            f"{money(returned)} handed back — {money(dividends)} of dividends and "
            f"{money(buybacks)} of buybacks")
    if payout >= 1.0:
        return (head + ", which is more than it earned. Nothing is retained, so the return on "
                + (f"capital of {roic_med:.0%} " if roic_med is not None else "capital ")
                + "does not enter this figure at all — what is left is the buyback yield alone. "
                + (f"Retiring {buyback_yield:.1%} of the shares a year raises earnings per share "
                   "by about that much with the business standing still, which is real but is "
                   "not the company compounding."
                   if buyback_yield > 0 else
                   "With no buybacks either, there is nothing here to compound at all."))
    return (head + f" — leaving {max(0.0, 1 - payout):.0%} retained, reinvested at "
            + (f"{roic_med:.0%}" if roic_med is not None else "an unreadable return")
            + ". It is an upper bound, not a forecast: it assumes every retained dollar finds a "
              "project as good as the business already is. A very high return on capital usually "
              "means the business needs little capital, which is also a reason there may be "
              "nowhere to put more of it.")


def interest_gap_note(interest_years: int, cash: float, invested: float, fy: int,
                      latest_int_fy: int | None = None) -> str | None:
    """Say so when nothing was subtracted for interest, and by how much it bites.

    Interest income is removed from the ROIC numerator because the cash that
    earned it was removed from the denominator. Where the tag is absent the
    subtraction silently does not happen and the return reads high, with
    nothing on the page to show it.

    Paychex is the case this was written for: none of InvestmentIncomeInterest,
    InvestmentIncomeInterestAndDividend, InterestIncomeOther or
    InterestAndDividendIncomeOperating is in its filing, and it earns interest
    on roughly $4.8B of client funds tagged FundsHeldForClients — a tag this
    reader does not read at all. Its ROIC is worth about 47% against 42%.
    TransDigm reads zero years too.

    The conversion offered is arithmetic on the company's own capital base and
    nothing else: no interest rate is assumed, because assuming one would be
    inventing the very number that is missing.

    A line that reads SOME years and then stops is the second branch, added
    26 Aug 2026. H&R Block's interest income ends at FY2020 while its ROIC year
    is FY2026, so six years of the subtraction simply did not happen and 84.41%
    reads high — with the earlier years subtracting normally, which is what
    makes it invisible. This is the item 9 disease on a FLOW line, which the
    instants-only guard deliberately does not cover: a balance sheet is
    reported every year end and an income line is not, so a flow that stops
    can legitimately mean the company stopped earning it. The note says both
    readings rather than asserting the tag broke.
    """
    if cash <= 0 or invested <= 0:
        return None
    if interest_years and (latest_int_fy is None or latest_int_fy >= fy):
        return None
    if interest_years:
        return (f"Interest income reads {interest_years} years but stops at FY{latest_int_fy}, "
                f"and the ROIC above is FY{fy}. Nothing was subtracted on that line for this "
                f"year, while earlier years in the median did subtract it — so the latest "
                f"figure reads high against its own history. Every {money(100.0)} of interest "
                f"missing from the numerator is {100.0 / invested:.1%} of ROIC on this capital "
                f"base. A flow line can stop because the company stopped earning it or because "
                f"the tag changed; the two look identical here, and the tag panel gives the "
                f"name to check.")
    return (f"Interest income reads no years, so nothing was subtracted on that line above. "
            f"FY{fy} carried {money(cash)} of cash and investments and whatever it earned is "
            f"still inside this return, which therefore reads high. Every {money(100.0)} of "
            f"interest sitting in the numerator is {100.0 / invested:.1%} of ROIC on this "
            f"capital base. On a filer that holds customer or client money — a payroll "
            f"processor, a broker, a title insurer — the balance earning it can be several "
            f"times the cash line above and none of it is visible here.")


def self_test() -> list[tuple[str, bool, str]]:
    out = []

    # 1. The ported engine still agrees with tool 1, to the dollar.
    goog = [(2016, 19478, 6900, 3693, 3304, 97, 47), (2017, 12662, 7900, 4846, 4166, 78, 55),
            (2018, 30736, 10000, 9075, 4993, -2, 61), (2019, 34343, 11700, 18396, 4765, -158, 70),
            (2020, 40269, 12991, 31149, 5720, -263, 73), (2021, 76033, 15376, 50274, 10162, -264, 125),
            (2022, 59972, 19362, 59296, 9300, -412, 117), (2023, 73795, 22460, 61504, 9837, -374, 115),
            (2024, 100118, 22785, 62222, 12190, -243, 164), (2025, 132170, 24953, 45709, 14167, -93, 206)]
    ys = [Year(fy=f, N=n, G=g, T=t, Cw=c, dS=d, price=p) for f, n, g, t, c, d, p in goog]
    out.append(("Ported engine: Alphabet FY2016 V = $8,252M",
                abs(ys[0].V - 8252) < 1, f"${ys[0].V:,.0f}M"))
    out.append(("Ported engine: Alphabet pooled ΔE = 88.7%",
                abs(pool(ys).dE - 0.887) < 0.002, f"{pool(ys).dE:.2%}"))
    # The seed both pages show: latest net income x 3-year pooled ΔE.
    seed = ys[-1].N * pool_safe(ys[-3:], pool(ys)).dE
    out.append(("Owners' earnings seed matches tool 1's box: $117,743M",
                abs(seed - 117_743) < 500, f"${seed:,.0f}M"))

    # 2. Mayer's arithmetic.
    g25 = required_growth(20, 20, 25)
    out.append(("100x in 25 years, flat multiple → 20.2%/yr",
                abs(g25 - 0.2022) < 0.001, f"{g25:.2%}"))
    g15 = required_growth(20, 20, 15)
    out.append(("100x in 15 years, flat multiple → 35.9%/yr",
                abs(g15 - 0.3594) < 0.001, f"{g15:.2%}"))
    gm = required_growth(10, 20, 25)
    out.append(("Multiple 10x→20x halves the work → 16.9%/yr",
                abs(gm - 0.1694) < 0.001, f"{gm:.2%}"))
    gd = required_growth(20, 20, 25, dilution=0.02)
    out.append(("2%/yr dilution adds 2.4 points → 22.6%/yr",
                abs(gd - 0.2263) < 0.001, f"{gd:.2%}"))

    # 3. The ceiling. The second of these is the H&R Block shape: a superb
    #    return on capital that funds no growth at all, because essentially
    #    every dollar is paid out.
    out.append(("ROIC 20%, half paid out → 10% ceiling",
                abs(sustainable_growth(0.20, 0.5) - 0.10) < 1e-9,
                f"{sustainable_growth(0.20, 0.5):.1%}"))
    hrb = per_share_ceiling(1.05, 1.0, 0.07)
    out.append(("ROIC 105% but everything paid out → 7.5%, not 105%",
                abs(hrb - 0.0753) < 0.001, f"{hrb:.2%}"))

    # 4. Burry's ROIC, wired to a hand-worked example.
    c = Capital(fy=2025, equity=800, debt=200, cash=250, revenue=2500,
                op_cash_pct=0.02, other_capital=50)
    r = RoicYear(fy=2025, OE=100, interest_income=5, lease_payments=2, other_expense=3, cap=c)
    out.append(("ROIC formula wiring: 90 / 850 → 10.59%",
                r.roic is not None and abs(r.roic - 0.10588) < 0.0005,
                f"{r.roic:.2%}" if r.roic else "n/a"))
    out.append(("Operating cash stays in the capital base",
                abs(c.deployable_cash - 200) < 1e-9, f"${c.deployable_cash:,.0f}M deployable"))
    neg = RoicYear(fy=2025, OE=500, interest_income=0, lease_payments=0, other_expense=0,
                   cap=Capital(fy=2025, equity=-300, debt=100, cash=50, revenue=5000,
                               equity_found=True))
    out.append(("Negative capital base refuses rather than flipping sign",
                neg.reason != "" and neg.roic is None, neg.reason or "printed a number"))
    # 4a. BellRing FY2018: nothing read for the year, a real capital base, and
    #     the cell used to print 0.0%. A year with a genuine small profit on
    #     the same base must still compute.
    _absent = RoicYear(fy=2018, OE=0.0, interest_income=0, lease_payments=0, other_expense=0,
                       cap=Capital(fy=2018, equity=452, debt=0, cash=0, revenue=0,
                                   equity_found=True))
    out.append(("A year with no owners' earnings read is n/a, not 0.0%",
                _absent.reason == "no owners' earnings read for this year",
                _absent.reason or f"printed {_absent.roic:.1%}"))
    _small = RoicYear(fy=2018, OE=0.3, interest_income=0, lease_payments=0, other_expense=0,
                      cap=Capital(fy=2018, equity=452, debt=0, cash=0, revenue=0,
                                  equity_found=True))
    out.append(("...but a small real profit on the same base still computes",
                _small.reason == "" and _small.roic is not None and abs(_small.roic - 0.3 / 452) < 1e-9,
                f"{_small.roic:.2%}" if _small.roic is not None else _small.reason))

    # 4b. The payout cross-check. HRB shaped: dividends tagged, buybacks not,
    #     share count visibly shrinking.
    hrb = [Year(fy=f, N=600, G=70, T=0.0, dS=-8.0, price=55.0) for f in range(2022, 2027)]
    pz = pooled_payout(hrb, {f: 190.0 for f in range(2022, 2027)}, 5)
    out.append(("Missing buyback line caught by the share count",
                pz.used_implied and abs(pz.implied - 440) < 1,
                f"implied ${pz.implied:,.0f}M/yr vs ${pz.buybacks:,.0f}M filed"))
    out.append(("...and payout rises from 28% to 94%",
                pz.ratio is not None and abs(pz.ratio - 0.9403) < 0.005,
                f"{pz.ratio:.1%}, filed-only would be {190/670:.0%}"))
    real = pooled_payout([Year(fy=f, N=600, G=70, T=400.0, dS=-8.0, price=55.0)
                          for f in range(2022, 2027)], {}, 5)
    out.append(("A filed buyback line is left alone",
                not real.used_implied, f"used filed ${real.buybacks:,.0f}M"))

    # 4c. The capital-base switches, on the H&R Block shape: they should carry
    #     a triple-digit Burry ROIC down toward what a data provider publishes.
    base = Capital(fy=2026, equity=100, debt=1500, cash=900, operating_leases=250,
                   revenue=3600, equity_found=True)
    r_burry = RoicYear(fy=2026, OE=770, interest_income=0, lease_payments=0,
                       other_expense=0, cap=base)
    conv = Capital(**{**base.__dict__, "include_leases": True, "cash_in_base": True})
    r_conv = RoicYear(fy=2026, OE=770, interest_income=0, lease_payments=0,
                      other_expense=0, cap=conv)
    out.append(("Switches reconcile a Burry ROIC toward a published one",
                r_burry.roic > 0.90 and r_conv.roic < 0.50,
                f"{r_burry.roic:.0%} -> {r_conv.roic:.0%}"))

    # 4d. The bug the tag panel found: a tag that answers for a few years and
    #     stops the search. Built to the exact shape of H&R Block's buybacks.
    def _rows(concept, yrs, val):
        return {concept: {"units": {"USD": [
            {"form": "10-K", "start": f"{y}-07-01", "end": f"{y+1}-06-30",
             "filed": f"{y+1}-08-01", "val": val} for y in yrs]}}}
    facts = {"facts": {"us-gaap": {
        **_rows("PaymentsForRepurchaseOfCommonStock", range(2008, 2011), 100e6),
        **_rows("StockRepurchasedAndRetiredDuringPeriodValue", range(2008, 2026), 500e6)}}}
    first_wins = _annual(facts, CONCEPTS["T"][0], [], None, False)
    filled = _annual(facts, CONCEPTS["T"][0], [], None, True)
    out.append(("First-tag-wins read 3 years and stopped",
                len(first_wins) == 3, f"{len(first_wins)} years"))
    out.append(("Gap-filling reads all 18, priority preserved",
                len(filled) == 18 and abs(filled[2009][2] - 100e6) < 1
                and abs(filled[2020][2] - 500e6) < 1,
                f"{len(filled)} years; 2009 from the cash-flow tag, 2020 from the retirement tag"))
    src = []
    _annual(facts, CONCEPTS["T"][0], [], src, True)
    out.append(("...and both tags are named in the panel", len(src) == 2, " + ".join(src)))

    # 4e. The CXDO crash: needs above what history has done, with no capital
    #     base at all. It used to claim "fundable" about a number that did not
    #     exist, then format None as a percentage and take the page down.
    v = assess(0.38, None, 0.25, 198)
    out.append(("No capital base does not get called 'fundable'",
                v.why == "history only" and v.kind == "warning", f"{v.label} ({v.why})"))
    every = [assess(0.38, None, 0.25, 198), assess(0.20, None, None, 198),
             assess(0.20, 0.30, None, 198), assess(0.10, None, 0.25, 198),
             assess(None, None, None, 198), assess(0.20, 0.30, 0.25, 9e9)]
    out.append(("Every verdict a missing ceiling can produce is reachable",
                len({x.why for x in every}) == 6, ", ".join(sorted(x.why for x in every))))

    # 4f. CXDO's real shape: a readable return on capital, but owners' earnings
    #     that pool below zero so no payout ratio exists. The old code called
    #     that "no capital base" and printed nothing.
    cx = [Year(fy=f, N=2, G=3, T=0.0, dS=1.5, price=5.0) for f in range(2021, 2026)]
    px = pooled_payout(cx, {}, 5)
    out.append(("Owners' earnings below zero give no payout ratio",
                px.ratio is None, f"pooled OE ${px.oe:,.1f}M"))
    ceiling_generous = per_share_ceiling(0.0593, 0.0, 0.0)
    v = assess(0.38, ceiling_generous, 0.25, 198)
    out.append(("...but a generous ceiling still closes the case",
                v.why == "capital", f"needs 38.0% vs {ceiling_generous:.1%} funded"))

    # 4g. The placeholder warning must survive the box rounding to one decimal.
    for n in (5.10, 5.14, 5.149):
        seed, box = n, float(round(n, 1))
        out.append((f"Placeholder warning fires when net income is {n}",
                    abs(box - float(round(seed, 1))) < 0.05, f"box {box:.2f}"))

    # 4h. AutoZone. The withholding guard needed a stock-comp charge to size
    #     against; AZO has none tagged in the window, so the test never ran and
    #     a $1.5B treasury purchase was charged as employee tax withholding on
    #     top of being charged as a buyback.
    azo = Year(fy=2025, N=2498, G=0.0, T=1578, Cw=1532, dS=0.0, price=3530.11)
    unguarded = azo.OE
    if azo.Cw and (azo.G <= 0 or azo.Cw > 3 * azo.G or (azo.T > 0 and azo.Cw > 0.5 * azo.T)):
        azo.Cw = 0.0
    out.append(("AutoZone: treasury purchase no longer charged twice",
                unguarded < 0 and azo.OE > 0, f"${unguarded:,.0f}M -> ${azo.OE:,.0f}M"))
    hrb_ok = Year(fy=2026, N=734, G=30.0, T=505, Cw=29, dS=-10.5, price=41.73)
    keep = not (hrb_ok.G <= 0 or hrb_ok.Cw > 3 * hrb_ok.G
                or (hrb_ok.T > 0 and hrb_ok.Cw > 0.5 * hrb_ok.T))
    out.append(("...and a real withholding line is still accepted",
                keep, f"${hrb_ok.Cw:,.0f}M against ${hrb_ok.G:,.0f}M of stock comp"))
    azo.dS = -2.5   # the count the diluted-average fallback recovers
    out.append(("With a real share count the buyback stops falling on employees",
                azo.V == 0 and abs(azo.OE - 2498) < 1, f"V = ${azo.V:,.0f}M"))
    out.append(("A negative return on capital yields no ceiling",
                per_share_ceiling(-1.383, 0.0, 0.0) < 0, "refused in the UI, not printed"))

    # 4i. AutoZone, part two: a share count that includes treasury stock, and
    #     the crash that followed a negative return on capital.
    issued, diluted = 25.70e6, 16.60e6      # AZO: ~9M shares sit in treasury
    out.append(("Treasury-inflated share count is detected",
                issued > 1.15 * diluted,
                f"{issued/1e6:.1f}M issued vs {diluted/1e6:.1f}M diluted"))
    out.append(("...and market cap corrects with it",
                abs(diluted/1e6 * 2957.95 - 49_101) < 50,
                f"${issued/1e6*2957.95/1000:,.1f}B -> ${diluted/1e6*2957.95/1000:,.1f}B"))
    dual = 60e6      # a missed second class reads BELOW the diluted average
    out.append(("A missed share class is not mistaken for treasury",
                not (dual > 1.15 * 100e6), "still caught by the existing dual-class check"))
    for roic, payout in ((-0.32, None), (0.26, None), (0.26, 0.17)):
        assumed = payout is None and roic is not None and roic > 0
        fundable = per_share_ceiling(roic, payout or 0.0, 0.0) if roic > 0 else None
        assert not (assumed and fundable is None), "would format None"
    out.append(("The retention warning never fires without a ceiling to print",
                True, "checked across negative, positive and measured payout"))

    # 4j. AutoZone, part three. The diluted average fixed the level of the
    #     share count but not its change, and V went haywire against the GAAP
    #     charge. Issued minus treasury is exact and year-end.
    smoothed = [(2021, 3378, -1.3, 1311.10, 56), (2025, 1578, -0.6, 3530.11, 125)]
    worst = max(max(0.0, T + px * dS) / G for _, T, dS, px, G in smoothed)
    out.append(("Diluted-average share change makes V absurd against GAAP comp",
                worst > 25, f"true cost peaked at {worst:.0f}x the GAAP charge"))
    exact = 25.70e6 - 9.10e6
    out.append(("Issued minus treasury recovers the real count",
                abs(exact - 16.60e6) < 1e5, f"{exact/1e6:.2f}M outstanding"))
    # with a real year-end change, the same year reconciles
    v_real = max(0.0, 3378 - 2.5 * 1311.10)
    out.append(("...and the same year then reconciles with the GAAP charge",
                v_real / 56 < 5, f"V ${v_real:,.0f}M against $56M of stock comp"))

    # 4k. The cover-page count must land on the right fiscal year. For a
    #     December filer the cover is dated in February, and keying it by
    #     calendar year would file the whole series twelve months late — every
    #     share change measured between the wrong pair of years.
    nser = {y: ("%d-01-01" % y, "%d-12-31" % y, 0.0) for y in range(2020, 2026)}
    facts_dec = {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        {"form": "10-K", "end": "%d-02-14" % (y + 1), "filed": "%d-02-14" % (y + 1),
         "val": 100e6 - (y - 2020) * 2e6} for y in range(2020, 2026)]}}}}}
    cov = _cover_shares(facts_dec, nser)
    out.append(("Cover-page count lands on the right fiscal year",
                cov.get(2020) == 100e6 and cov.get(2024) == 92e6 and 2026 not in cov,
                f"{len(cov)} years, FY2020 = {cov.get(2020, 0)/1e6:.0f}M"))
    out.append(("...and the change between years is then real",
                abs((cov[2024] - cov[2023]) / 1e6 + 2) < 0.01,
                f"{(cov[2024]-cov[2023])/1e6:+.1f}M shares"))

    # 4l. H&R Block. Treasury shares tagged for 5 years, cover page for 17.
    #     Preferring the exact-but-short series left six of ten years with no
    #     share change, so V became the whole buyback.
    win = list(range(2017, 2027))
    net = {fy: 130e6 for fy in range(2022, 2027)}          # 5 years, exact
    cover = {fy: 130e6 for fy in range(2010, 2027)}        # 17 years, cover page
    wv = {fy: 128.9e6 for fy in range(2009, 2027)}
    cands = [(net, "issued minus treasury shares"), (cover, "the 10-K cover page"),
             (wv, "the weighted-average diluted count")]
    scored = [(sum(1 for fy in win if fy in c), -i, c, name)
              for i, (c, name) in enumerate(cands) if len(c) >= 3]
    best = max(scored)
    out.append(("Coverage beats exactness when the exact series is short",
                best[3] == "the 10-K cover page", f"chose {best[3]} ({best[0]}/10 years)"))
    # and when both cover the window, the exact one still wins
    net_full = {fy: 130e6 for fy in range(2010, 2027)}
    cands2 = [(net_full, "issued minus treasury shares"), (cover, "the 10-K cover page"),
              (wv, "the weighted-average diluted count")]
    best2 = max((sum(1 for fy in win if fy in c), -i, c, name)
                for i, (c, name) in enumerate(cands2) if len(c) >= 3)
    out.append(("...and exactness still wins on equal coverage",
                best2[3] == "issued minus treasury shares", f"chose {best2[3]}"))

    # 4m. The withholding guard must separate a disguised buyback from real
    #     withholding at BOTH ends of the size range. The buyback-size rule it
    #     used before rejected seven legitimate years at IES Holdings, whose
    #     repurchases are small, and pushed owners' earnings up.
    def _reject(N, G, Cw):
        return (Cw > 3 * G) if G > 0 else (Cw > 0.10 * abs(N))
    out.append(("AutoZone's disguised buyback is still rejected",
                _reject(2498, 0, 1532), "no GAAP charge, 61% of net income"))
    out.append(("IES Holdings' real withholding is kept",
                not _reject(13, 2, 1.8) and not _reject(67, 4, 7.0),
                "sized against the GAAP charge"))
    out.append(("A small buyback no longer condemns the withholding beside it",
                not _reject(121, 0, 1.0), "$1M against $121M of net income"))

    # 4n. Progressive. A trivial share-count move implied $32M of repurchases
    #     against $5.6B of owners' earnings and set off warnings written for
    #     H&R Block, where the same signal was worth 73% of earnings.
    pgr = [Year(fy=f, N=5000, G=100, T=0.0, dS=d, price=200.0)
           for f, d in zip(range(2021, 2026), (-0.9, 0.5, 0.3, 0.5, -0.3))]
    pp = pooled_payout(pgr, {f: 1500.0 for f in range(2021, 2026)}, 5)
    out.append(("A 0.5% share wiggle no longer raises a buyback alarm",
                not pp.used_implied,
                f"implied ${pp.implied:,.0f}M = {pp.implied/pp.oe:.1%} of owners' earnings"))
    hrb = [Year(fy=f, N=600, G=70, T=0.0, dS=-8.0, price=55.0) for f in range(2022, 2027)]
    hp = pooled_payout(hrb, {f: 190.0 for f in range(2022, 2027)}, 5)
    out.append(("...and the H&R Block case still does",
                hp.used_implied, f"implied {hp.implied/hp.oe:.0%} of owners' earnings"))

    # 4o. Paychex. Ten rows spanning eighteen calendar years, with FY2016-2023
    #     missing. Revenue growth counted rows while owners' earnings counted
    #     calendar years, so the same company read 11%/yr and 7%/yr at once —
    #     and "has delivered" takes the higher of the two.
    fys_gap = [2009, 2010, 2011, 2012, 2013, 2014, 2015, 2024, 2025, 2026]
    by_rows = cagr(2082.0, 5900.0, len(fys_gap) - 1)
    by_years = cagr(2082.0, 5900.0, fys_gap[-1] - fys_gap[0])
    out.append(("Growth is measured in calendar years, not table rows",
                by_years is not None and abs(by_years - 0.0634) < 0.001,
                f"{by_rows:.1%} by rows vs {by_years:.1%} by years"))
    out.append(("A hole in the filing history is detected",
                (max(fys_gap) - min(fys_gap) + 1) > len(fys_gap),
                f"{len(fys_gap)} rows across {max(fys_gap)-min(fys_gap)+1} years"))
    solid = list(range(2017, 2027))
    out.append(("...and a complete history is not flagged",
                (max(solid) - min(solid) + 1) == len(solid), "10 rows, 10 years"))

    # 4p. Paychex again. The revenue tag overlapped the net-income window in
    #     only 2024-2026, so "has delivered" was a two-year rate across the
    #     Paycor acquisition — and it outranked the seventeen-year owners'
    #     earnings figure because the metric takes the higher of the two.
    two_year = cagr(5300.0, 6500.0, 2)
    seventeen = cagr(559.0, 1807.0, 17)
    out.append(("A two-year rate no longer counts as a track record",
                two_year is not None and 2 < 5,
                f"{two_year:.1%} over 2y discarded; {seventeen:.1%} over 17y kept"))
    out.append(("...and the longer rate is what 'has delivered' reports",
                abs(seventeen - 0.0718) < 0.002, f"{seventeen:.1%}"))
    out.append(("A five-year run is still accepted",
                cagr(100.0, 200.0, 5) is not None and 5 >= 5, "at the boundary"))

    # 4q. Toyota. Two USD convenience translations alongside a full history in
    #     yen sailed past a guard that only asked about currency when NOTHING
    #     was found.
    def _rows(unit, n, form="20-F"):
        return {unit: [{"form": form, "start": f"{2000+i}-04-01", "end": f"{2001+i}-03-31",
                        "filed": f"{2001+i}-06-30", "val": 1.0} for i in range(n)]}
    tm = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {**_rows("JPY", 14),
                                                            **_rows("USD", 2)}}}}}
    cf = currency_facts(tm, CONCEPTS["N"][0] + CONCEPTS["N"][1])
    foreign = {u: n for u, n in cf.items() if u != "USD"}
    main, n = max(foreign.items(), key=lambda kv: kv[1])
    out.append(("A yen filer with two USD years is caught",
                n >= cf.get("USD", 0), f"{n} in {main} vs {cf.get('USD',0)} in USD"))
    us = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": _rows("USD", 19, "10-K")}}}}
    cf2 = currency_facts(us, CONCEPTS["N"][0] + CONCEPTS["N"][1])
    out.append(("...and a plain US filer is not",
                not {u: k for u, k in cf2.items() if u != "USD"}, "USD only"))
    out.append(("Two years of history is refused outright",
                len([2012, 2013]) < 4, "four is the minimum"))

    # 4r. TransDigm. The tagged outstanding count is correct but covers 3 of 10
    #     years, so the share change read +0.0 everywhere and the buyback fell
    #     on employees — the AutoZone damage through a different door.
    win = list(range(2016, 2026))
    tagged = {fy: 52.2e6 for fy in (2023, 2024, 2025)}
    cover = {fy: 52.2e6 + (2025 - fy) * 1e5 for fy in range(2010, 2026)}
    wv = {fy: 57.0e6 for fy in range(2008, 2026)}
    sparse = sum(1 for fy in win if fy in tagged) < 0.6 * len(win)
    out.append(("A short-but-correct share count triggers the repair",
                sparse, f"{sum(1 for fy in win if fy in tagged)} of {len(win)} years"))
    cands = [(dict(tagged), "the tagged share count"), ({}, "issued minus treasury"),
             (cover, "the 10-K cover page"), (wv, "the diluted average")]
    best = max((sum(1 for fy in win if fy in c), -i, name)
               for i, (c, name) in enumerate(cands) if len(c) >= 3)
    out.append(("...and coverage picks the cover page over it",
                best[2] == "the 10-K cover page", f"chose {best[2]} ({best[0]}/10)"))
    full = {fy: 52.2e6 for fy in range(2014, 2026)}
    out.append(("A well-covered tagged count is left alone",
                not (sum(1 for fy in win if fy in full) < 0.6 * len(win)), "10 of 10 years"))

    # 5. The verdict itself.
    v = assess(0.272, 0.075, 0.04, 5_000)
    out.append(("Needs 27%, funds 7.5% → closed by capital",
                v.kind == "error" and v.why == "capital", f"{v.label} ({v.why})"))
    v = assess(0.27, 0.35, 0.10, 5_000)
    out.append(("Fundable at 35% but has only done 10% → unprecedented",
                v.kind == "warning" and v.why == "history", f"{v.label} ({v.why})"))
    v = assess(0.22, 0.30, 0.20, 5_000)
    out.append(("Needs 22%, funds 30%, has done 20% → open",
                v.kind == "success", f"{v.label} ({v.why})"))
    v = assess(0.15, 0.90, 0.60, 4_000_000)
    out.append(("Size closes it even when everything else passes",
                v.why == "size", f"{v.label} ({v.why})"))

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
    out.append(("A blanked ΔE cell renders as an em dash, never the word None",
                (lambda f: f(None) == "\u2014" and f(0.871) == "87.1%")(
                    lambda v: "\u2014" if v is None else f"{v:.1%}"),
                "st.dataframe ignores the styler's na_rep, so the cell is built as text"))

    # 4h. The endpoint trap. `delivered` is a two-point rate, so it is decided
    #     by whichever years sit at the ends of the window. Progressive's real
    #     owners' earnings, 25 Aug 2026, are the fixture: a weak FY2016 and a
    #     record FY2025 put the two readings on opposite sides of what the
    #     price required.
    _pgr_oe = [954.0, 1526.0, 2517.0, 3870.0, 5670.0,
               3351.0, 664.0, 3976.0, 8484.0, 11440.0]
    out.append(("Trend growth fits a clean exponential exactly",
                abs(trend_growth([100.0 * 1.2 ** k for k in range(8)]) - 0.20) < 1e-9,
                f"{trend_growth([100.0 * 1.2 ** k for k in range(8)]):.4%}"))
    out.append(("PGR: endpoints say 31.8%/yr, the trend says 20.6%",
                abs(cagr(_pgr_oe[0], _pgr_oe[-1], 9) - 0.3179) < 0.001
                and abs(trend_growth(_pgr_oe) - 0.2055) < 0.001,
                f"{cagr(_pgr_oe[0], _pgr_oe[-1], 9):.2%} vs {trend_growth(_pgr_oe):.2%}"))
    out.append(("...and 26.0% needed sits between them, so the measure decides",
                trend_growth(_pgr_oe) < 0.2600 <= cagr(_pgr_oe[0], _pgr_oe[-1], 9),
                "which is exactly when the note fires"))
    out.append(("A loss anywhere in the window gives no trend",
                trend_growth([100.0, -5.0, 200.0]) is None
                and trend_growth([100.0, 120.0]) is None,
                "a log needs positive values and at least three of them"))
    out.append(("Trend growth ignores the order-free scale, not the order",
                trend_growth([100.0, 200.0, 400.0]) is not None
                and trend_growth([400.0, 200.0, 100.0]) < 0,
                f"{trend_growth([400.0, 200.0, 100.0]):.1%} on a declining series"))

    # 4i. The pass-side gate. Same three rates, opposite treatment depending on
    #     which way the verdict is about to fall.
    out.append(("PGR's shape at a small-cap size does not reach open on endpoints",
                assess(0.2600, None, 0.3178, 1_980, 0.2055).why == "growth measure",
                assess(0.2600, None, 0.3178, 1_980, 0.2055).label))
    out.append(("...and PGR's own size still settles it before growth is read",
                assess(0.2600, None, 0.3178, 129_730, 0.2055).why == "size",
                "the size gate is checked first and nothing below rescues it"))
    out.append(("A trend that clears the requirement still reaches open",
                assess(0.2600, None, 0.3178, 1_980, 0.2900).why == "no capital base"
                and assess(0.2000, 0.3000, 0.2500, 1_980, 0.2400).why == "both",
                "both readings above the requirement, so nothing is withheld"))
    out.append(("A capital refusal is not overridden by the growth measure",
                assess(0.2600, 0.0683, 0.3178, 1_980, 0.2055).why == "capital",
                "the capital check runs first and is the stronger objection"))
    out.append(("Omitting the trend leaves every old verdict exactly as it was",
                assess(0.2600, None, 0.3178, 1_980).why == "no capital base"
                and assess(0.3800, None, 0.2500, 198).why == "history only"
                and assess(0.2000, 0.3000, 0.2500, 1_980).why == "both",
                "the parameter defaults to None, so the six original paths are untouched"))

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

    # 4r. A dropped growth leg says which of the four reasons applied.
    out.append(("Salesforce's owners' earnings are dropped for starting at a loss, not for length",
                growth_leg_reason("owners' earnings", 9, 9, -27.0, 7563.0)
                == "owners' earnings starts from a loss, so a compound rate has no base "
                   "to grow from",
                "nine clean years, and the FY2017 figure is -27"))
    out.append(("...and a genuinely short series still says it is short",
                growth_leg_reason("revenue", 2, 1, 100.0, 120.0) == "revenue has only 2 "
                "readable years", "2 points"))
    out.append(("...and a long-enough count over too few calendar years says that instead",
                growth_leg_reason("revenue", 4, 3, 100.0, 120.0) == "revenue spans only 3 years",
                "3-year span against a 5-year minimum"))
    out.append(("...and a series ending in a loss is named for that",
                "ends in a loss" in growth_leg_reason("owners' earnings", 9, 9, 500.0, -20.0),
                "the last year is negative"))
    out.append(("...and a usable leg produces no message at all",
                growth_leg_reason("revenue", 10, 9, 100.0, 300.0) == "", "nothing to say"))

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
    # VEEV, 28 Aug 2026. This verdict had no branch in the explanation chain
    # and fell through to the one written for the two half-checked verdicts,
    # which announces that no funding ceiling could be built. Both ceilings
    # existed. These pin that it is its own why, and that it can also arrive
    # with fundable None — which is why the branch guards the format.
    out.append(("An endpoint-only pass is its own verdict, not a half-checked one",
                assess(0.2765, 1.049, 0.4295, 46_490.0, 0.2197).why == "growth measure",
                "VEEV: needs 27.6%, funds 104.9%, delivered 42.9%, trend 22.0%"))
    out.append(("...and it is neither of the two the fallback text describes",
                assess(0.2765, 1.049, 0.4295, 46_490.0, 0.2197).why
                not in ("no capital base", "no growth history"),
                "the fallback says no funding ceiling could be built; VEEV's is 104.9%"))
    out.append(("...and it can arrive with no funding ceiling at all",
                assess(0.10, None, 0.20, 1_000.0, 0.05).why == "growth measure",
                "a readable record with an unreadable capital base reaches the same branch"))
    _ipo = split_adjust({2020: 100e6, 2021: 110e6, 2022: 990e6, 2023: 1032e6})
    out.append(("A listing that looks like a split is still restated, but not announced as one",
                _ipo[0][2021] == 990e6 and _ipo[0][2023] == 1032e6
                and all("Stock split detected" not in m for m in _ipo[1])
                and any("did not split, the restated years are wrong" in m for m in _ipo[1]),
                "RIVN FY2022 reads about 9:1 on a company that has never split"))
    # 4v. Neither half-checked verdict is green, and neither reads as a pass.
    _no_hist = assess(0.10, 0.30, None, 1_980.0, None)
    out.append(("A verdict with no growth record to check is amber, like its mirror",
                _no_hist.kind == "warning" and _no_hist.why == "no growth history",
                _no_hist.label))
    out.append(("...and no input without a growth record reaches success",
                all(assess(r, f, None, 1_980.0, None).kind != "success"
                    for r in (0.05, 0.26, 0.60) for f in (0.10, 0.35, 0.90)),
                "9 combinations, none green"))
    out.append(("...while both ceilings present can still pass",
                assess(0.20, 0.30, 0.25, 1_980.0, 0.24).kind == "success",
                "unchanged"))

    # 4w. RIVN, 27 Aug 2026. `roic_med is not None` and `fundable is not None`
    # are different questions, and the IV15-handoff block asked the wrong one.
    # A negative return on capital funds no growth, so `fundable` is None while
    # `roic_med` is a perfectly good negative number — and the block formatted
    # it, killing the page from the notes expander down. This pins the rule the
    # block now gates on; it cannot exercise the render itself.
    def _fundable_like_the_page(r: float | None) -> float | None:
        return per_share_ceiling(r, 0.0, 0.0) if r is not None and r > 0 else None
    out.append(("A negative return on capital hands no growth ceiling to tool 1",
                _fundable_like_the_page(-0.09) is None
                and _fundable_like_the_page(None) is None,
                "RIVN's 5-year median is negative, so there is no ceiling to print"))
    out.append(("...while a positive one still hands one over",
                _fundable_like_the_page(0.0432) is not None,
                "CAVA's 4.32% still prints"))
    out.append(("Rivian's loss cannot be capitalised at any multiple",
                assess(None, None, None, 20_832.0, None, -3_646.0).why == "no earnings base",
                "1,240.0M shares at $16.80 against -3,646 of owners' earnings"))

    # 4u. CAVA. Name the fallback that was used, not the one above it.
    out.append(("A negative 5-year median is not described as the seed",
                (lambda med: "median" if med > 0 else "ceiling")(-47.0) == "ceiling",
                "CAVA: median -47, so net income is the ceiling instead"))
    out.append(("...while a positive one still is",
                (lambda med: "median" if med > 0 else "ceiling")(556.0) == "median",
                "ARM: median 556"))

    # 4t. ARM. An unusable recent ΔE must not be replaced by an older regime.
    out.append(("A negative 3-year ΔE is refused rather than swapped for the pooled figure",
                not (0 < -0.162 <= DE_UNUSABLE_ABOVE),
                "ARM: -16.2% recent against 27.8% pooled, and 27.8% is pre-IPO"))
    out.append(("...and a usable recent figure is still what gets applied",
                0 < 0.939 <= DE_UNUSABLE_ABOVE, "Salesforce's 93.9% projects normally"))
    out.append(("...and one above the ceiling is refused too, not capped into use",
                not (0 < 1.31 <= DE_UNUSABLE_ABOVE), "131% is above the 125% ceiling"))

    # 4s. Berkshire. A market cap below one year of earnings is a broken read.
    out.append(("A market cap under one year's owners' earnings is refused outright",
                assess(0.10, None, 0.12, 160.0, 0.07, 60_599.0).why == "implausible",
                "$160M against $60,599M of owners' earnings"))
    out.append(("...and it is checked before size, so the bigger error wins",
                assess(0.10, None, 0.12, 160.0, 0.07, 60_599.0).kind == "error",
                "an unbelievable input is not a size verdict"))
    out.append(("...while an ordinary company is untouched by it",
                assess(0.20, 0.30, 0.25, 1_980.0, 0.24, 200.0).why == "both",
                "a $1.98B cap on $200M of owners' earnings is an ordinary 9.9x"))
    out.append(("...and omitting owners' earnings leaves every verdict as it was",
                assess(0.20, 0.30, 0.25, 1_980.0, 0.24).why == "both",
                "the parameter defaults to None"))

    # 4q. An insurer cannot pass on the growth leg alone.
    _pgr_small = assess(0.2600, None, 0.3178, 1_980, 0.2900)
    out.append(("Progressive's shape at $2B does not print a pass when ROIC is withheld",
                _pgr_small.kind == "warning" and "could not run" in _pgr_small.label,
                _pgr_small.label))
    out.append(("...and no combination without a capital ceiling reaches success",
                all(assess(r, None, d, 1_980, t).kind != "success"
                    for r in (0.05, 0.26, 0.60) for d in (0.10, 0.35, 0.90)
                    for t in (0.10, 0.35, 0.90)),
                "27 combinations, none green"))
    out.append(("...while a readable capital base still can",
                assess(0.2000, 0.3000, 0.2500, 1_980, 0.2400).kind == "success",
                "both ceilings present and above the requirement"))
    out.append(("The reason code is unchanged, so every other branch still routes",
                _pgr_small.why == "no capital base"
                and assess(0.2600, None, 0.3178, 1_980).why == "no capital base",
                "only the label and severity moved"))

    # 4k. The median that survives a refusal. `median_roic` drops refused years
    #     rather than zeroing them, so a five-year median can rest on one year
    #     and read exactly like a five-year one.
    class _R:
        def __init__(self, roic, reason=""):
            self.roic, self.reason = roic, reason
    _five = [_R(0.10), _R(None, "negative capital"), _R(None, "negative capital"),
             _R(0.50), _R(None, "negative capital")]
    out.append(("A median over five years can rest on two of them",
                abs(median_roic(_five) - 0.30) < 1e-9
                and len([r for r in _five if not r.reason]) == 2,
                "0.30 is the mean of the two survivors, not a five-year figure"))
    out.append(("...and BKNG's shape — a refused latest year keeps the median",
                median_roic(_five + [_R(None, "negative capital")]) is not None,
                "which is why the refusal branch has to print it"))
    out.append(("No readable year gives no median at all",
                median_roic([_R(None, "negative capital")] * 5) is None,
                "and `can fund` then has no capital ceiling"))
    # 4m. The badge under `can fund` names the figure it is actually built on.
    _b_hi = fund_badge_caption(0.4521, 1.704, 0.03557, 0.03688, False, False, None)
    out.append(("Booking's can-fund badge names the buyback yield, not a ROIC it never uses",
                "3.6%" in _b_hi and "ROIC" not in _b_hi and "buyback yield alone" in _b_hi,
                _b_hi))
    out.append(("...and it is short enough that Streamlit does not truncate the point away",
                max(len(fund_badge_caption(0.45, p, b, 0.03, False, False, None))
                    for p, b in ((1.704, 0.03557), (1.20, 0.0), (0.959, 0.0))) <= 30,
                "longest badge is 30 characters or fewer"))
    _b_lo = fund_badge_caption(0.4685, 0.959, 0.0, 0.0192, False, False, None)
    out.append(("...and a company that does retain earnings still shows ROIC and retention",
                "ROIC 47%" in _b_lo and "retains 4%" in _b_lo, _b_lo))
    _b_none = fund_badge_caption(None, 0.0, 0.0, None, False, True, None)
    out.append(("...and an insurer still says why there is no figure at all",
                _b_none == "financial company", _b_none))
    _b_nobb = fund_badge_caption(0.30, 1.20, 0.0, 0.0, False, False, None)
    out.append(("A payout above 100% with no buyback says so rather than printing a yield",
                "no buybacks" in _b_nobb, _b_nobb))
    _e_hi = can_fund_explainer(3820.0, 6510.0, 484.0, 6020.0, 1.704, 0.4521, 0.03557)
    out.append(("The paragraph under it stops crediting a ROIC that contributed nothing",
                "does not enter this figure at all" in _e_hi and "reinvested at" not in _e_hi
                and "3.6%" in _e_hi, "buyback yield alone, 45% explicitly excluded"))
    _e_lo = can_fund_explainer(1738.0, 1667.0, 1300.0, 367.0, 0.959, 0.4685, 0.0)
    out.append(("...and a company that does retain earnings keeps the original wording",
                "reinvested at 47%" in _e_lo and "4% retained" in _e_lo,
                "4% retained, reinvested at 47%"))

    # 4n. Interest income that reads nothing at all is a silent overstatement.
    _ig = interest_gap_note(0, 4900.0, 7293.0, 2026)
    out.append(("A ROIC with no interest line at all says the return reads high",
                _ig is not None and "reads high" in _ig and "1.4%" in _ig,
                "every $100M of interest is 1.4% of ROIC on a 7,293M base"))
    out.append(("...and a filer whose interest income IS read stays silent",
                interest_gap_note(18, 4900.0, 7293.0, 2026, 2026) is None,
                "18 years read to FY2026, no note"))
    _hrb = interest_gap_note(4, 900.0, 729.0, 2026, 2020)
    out.append(("H&R Block's interest income stops at FY2020 and the FY2026 ROIC says so",
                _hrb is not None and "stops at FY2020" in _hrb and "FY2026" in _hrb,
                "4 years to FY2020 against a FY2026 return"))
    out.append(("...and it is scaled to the capital base, not to an assumed rate",
                "13.7% of ROIC" in _hrb, "$100M is 13.7% on a 729M base"))
    out.append(("...and it does not claim the tag broke, since a flow may simply stop",
                "stopped earning it" in _hrb, "both readings offered"))
    out.append(("...and so does one with no cash to earn any",
                interest_gap_note(0, 0.0, 7293.0, 2026) is None, "nothing to earn on"))

    _sum_ok = test_summary([("a", True, ""), ("b", True, "")])
    out.append(("The expander header counts what actually ran, not what was written",
                _sum_ok == ("success", "**2 checks, 0 failed.**"), _sum_ok[1]))
    _sum_bad = test_summary([("a", True, ""), ("b", False, ""), ("c", False, "")])
    out.append(("...and a red one says so first and names the failures",
                _sum_bad[0] == "error" and "2 FAILED" in _sum_bad[1]
                and "b; c" in _sum_bad[1], _sum_bad[1]))
    # 4o. Item 2. The refusal ceiling takes the kindest reading, and on Adobe
    #     and AutoZone the kindest reading is the fitted trend, not the
    #     endpoints the old rule used.
    out.append(("Adobe's delivered takes the trend, which runs above its endpoint rate",
                abs(delivered_rate(0.2812, 0.2967) - 0.2967) < 1e-9,
                "28.12% endpoints, 29.67% fitted — the higher one is the ceiling"))
    out.append(("AutoZone likewise: 7.11% endpoints, 8.46% fitted",
                abs(delivered_rate(0.0711, 0.0846) - 0.0846) < 1e-9, "8.46%"))
    out.append(("Progressive keeps its endpoint rate, which is the higher one there",
                abs(delivered_rate(0.3178, 0.2055) - 0.3178) < 1e-9,
                "31.78% endpoints beats a 20.55% trend"))
    out.append(("A company with only one readable reading still gets a ceiling",
                delivered_rate(None, 0.09) == 0.09 and delivered_rate(0.09, None) == 0.09
                and delivered_rate(None, None) is None, "either alone, or neither"))
    # The direction of the correction: a refusal can soften to a warning...
    _old = assess(0.12, 0.50, 0.0711, 50_000.0, 0.0846)
    _new = assess(0.12, 0.50, delivered_rate(0.0711, 0.0846), 50_000.0, 0.0846)
    out.append(("A kinder ceiling can turn 'unprecedented' into a milder verdict",
                _old.why == "history" and _new.why == "growth measure",
                f"{_old.label} -> {_new.label}"))
    # ...but never into a green, because the trend gate is a separate test.
    out.append(("...but it can never manufacture a pass the trend does not support",
                all(assess(0.12, 0.50, delivered_rate(0.0711, t), 50_000.0, t).why != "both"
                    for t in (0.0846, 0.10, 0.1199)),
                "every trend below the requirement still refuses to reach 'open'"))
    out.append(("...and a trend that does clear it still passes",
                assess(0.12, 0.50, delivered_rate(0.0711, 0.13), 50_000.0, 0.13).why == "both",
                "the gate is the trend, not delivered"))

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

    # 4p. Item 4 — the same disagreement, priced.
    _mv = [("Borrowings", 1_200.0, "raises")]
    _adj, _alt = carried_forward_capital(5_785.0, 3_211.0, _mv)
    out.append(("AutoZone's missing borrowings would raise the capital base, lowering ROIC",
                abs(_adj - 6_985.0) < 1e-6 and _alt < 3_211.0 / 5_785.0,
                f"{_adj:,.0f}M, ROIC {_alt:.1%}"))
    _mv2 = [("Investments", 1_000.0, "lowers")]
    out.append(("...and a missing deduction runs the other way",
                abs(carried_forward_capital(5_785.0, 3_211.0, _mv2)[0] - 4_785.0) < 1e-6,
                "4,785M"))
    out.append(("Leases and goodwill are left out of the swing on purpose",
                carried_forward_capital(5_785.0, 3_211.0,
                                        [("Leases", 900.0, "leases"),
                                         ("Goodwill & intangibles", 900.0, "exgoodwill")])[0]
                == 5_785.0, "neither has one unambiguous effect on this figure"))
    out.append(("AutoZone's stopped debt component is worth its own last figure, not a delta",
                abs(missing_component_total(
                    [{2023: 8.0e9, 2024: 8.4e9, 2025: 8.8e9}, {2013: 1.7e8, 2014: 1.81e8}],
                    2025) - 1.81e8) < 1e-6,
                "181M missing while the component beside it grew to 8,800M"))
    out.append(("...and a line whose components all reach the current year is missing nothing",
                missing_component_total([{2024: 5.0, 2025: 7.0}, {2025: 3.0}], 2025) == 0.0,
                "nothing stopped"))
    _swn = stale_capital_swing_note(5_785.0, 3_211.0, 0.5551, _mv)
    out.append(("The note prices the disagreement and refuses to call either side right",
                "6,985M" in _swn and "5,785M" in _swn and "The tag name settles it" in _swn,
                _swn[:70] + "…"))
    out.append(("...and stays silent when there is nothing unambiguous to price",
                stale_capital_swing_note(5_785.0, 3_211.0, 0.5551,
                                         [("Leases", 900.0, "leases")]) == "",
                "no sentence rather than a misleading one"))

    out.append(("A payout above 100% leaves can fund as buyback yield alone",
                abs(per_share_ceiling(0.4521, 1.704, 0.03557) - 0.03688) < 1e-4
                and abs(per_share_ceiling(0.0, 1.704, 0.03557)
                        - per_share_ceiling(0.4521, 1.704, 0.03557)) < 1e-9,
                "BKNG's 3.69%: the 45.21% median multiplies by zero retention"))

    # 4j. Item 9 on this page. Same disease as tool 1, opposite arithmetic: a
    #     missing component is added as zero, so the direction of the error
    #     depends on which side of the capital base the line sits.
    _azo_cap = stale_capital_lines(
        {"Shareholders' equity": "2025", "Borrowings": "2014", "Leases": "2025",
         "Cash": "2025", "Investments": "\u2014", "Goodwill & intangibles": "2020"}, 2025)
    out.append(("AZO: borrowings to FY2014 and goodwill to FY2020 are both caught",
                [(n, y, g) for n, y, g, _ in _azo_cap]
                == [("Borrowings", 2014, 11), ("Goodwill & intangibles", 2020, 5)],
                f"{[(n, y, g) for n, y, g, _ in _azo_cap]}"))
    out.append(("...and borrowings is flagged as the direction that costs money",
                dict((n, e) for n, _, _, e in _azo_cap)["Borrowings"] == "raises"
                and dict((n, e) for n, _, _, e in _azo_cap)["Goodwill & intangibles"]
                == "exgoodwill",
                "capital-side holds raise ROIC; goodwill moves only the ex-goodwill figure"))
    out.append(("A missing investments deduction is flagged as lowering ROIC instead",
                [e for n, _, _, e in stale_capital_lines(
                    {"Investments": "2025"}, 2026)] == ["lowers"],
                "PAYX's shape — conservative, but still wrong"))
    out.append(("A line with no data at all is not called stale here either",
                stale_capital_lines({"Borrowings": "\u2014", "Cash": "2025"}, 2025) == [],
                "none found is a different finding with a different fix"))
    out.append(("A fully current capital base fires nothing",
                stale_capital_lines(
                    {"Shareholders' equity": "2025", "Borrowings": "2025", "Leases": "2025",
                     "Cash": "2025", "Investments": "2025",
                     "Goodwill & intangibles": "2025"}, 2025) == [],
                "PGR's shape after the Drop 11 debt repair, goodwill aside"))
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
    return out


# ══════════════════════════════════════════════════════════════════════
#  UI
# ══════════════════════════════════════════════════════════════════════
#
# NOTE ON DOLLAR SIGNS: Streamlit markdown parses $...$ as LaTeX, so any
# literal dollar amount inside st.write/markdown/success/error/info/warning
# must be escaped. st.metric, st.code and st.dataframe are unaffected.


def money(x: float) -> str:
    """$M in, human-readable out. Escaped, so it is safe inside markdown."""
    if abs(x) >= 1_000_000:
        return f"\\${x/1_000_000:,.2f}T"
    if abs(x) >= 1_000:
        return f"\\${x/1_000:,.2f}B"
    return f"\\${x:,.0f}M"


def plain(x: float) -> str:
    """Same, unescaped, for st.metric and st.code."""
    return money(x).replace("\\", "")


st.set_page_config(
    page_title="100-Bagger Checker — Mayer's criteria and Burry's ROIC",
    page_icon="💯",
    layout="centered",
    initial_sidebar_state="collapsed",
)
st.title("💯 100-Bagger Checker")
st.caption("What a hundredfold requires, against what this business can fund and has ever done")

if not _sec_contact():
    st.warning(
        "**No SEC contact address set.** The SEC requires a real email in the request header "
        "and blocks generic user agents, so lookups will fail. Add `sec_contact = "
        "\"you@example.com\"` in Streamlit Settings → Secrets, or set a SEC_CONTACT "
        "environment variable locally.")

if "hb_years" not in st.session_state:
    st.info(
        "**A hundredfold is two engines multiplied: earnings growth and multiple change.** "
        "This works out the growth rate your holding period actually requires, then checks it "
        "against two ceilings — what the company's return on capital can fund, and what it has "
        "ever delivered. Below both, the case is open. Above either, it is closed by arithmetic "
        "rather than by opinion.\n\n"
        "Enter a US-listed ticker you already like. This is a checker, not a screener.")

with st.form("hb_lookup"):
    ticker = st.text_input("Stock ticker",
                           placeholder="CPRX · MATX · CXDO · AGX — press Enter").upper().strip()
    submitted = st.form_submit_button("Check", type="primary")

if submitted:
    if not ticker:
        st.warning("Enter a ticker first.")
    else:
        try:
            with st.spinner(f"Reading {ticker} annual filings…"):
                yrs, nts, pre_ = load(ticker, 10)
            st.session_state.update(hb_years=yrs, hb_notes=nts, hb_pre=pre_, hb_tk=ticker)
        except ValueError as e:
            st.error(f"Could not load {ticker}: {e}")
        except Exception as e:
            st.error(
                f"Could not load {ticker} — {type(e).__name__}: {e}\n\n"
                "This is a gap in how the filings were read, not something you did. Recent "
                "listings, several share classes and foreign issuers are the usual causes.")

years = st.session_state.get("hb_years", [])
if years and ticker and st.session_state.get("hb_tk") == ticker:
    notes = list(st.session_state["hb_notes"])
    pre = st.session_state["hb_pre"]
    tk = st.session_state["hb_tk"]
    fys, financial = pre["fys"], pre["financial"]
    alerts: list[tuple[str, str]] = [("info", n) for n in notes]
    latest = years[-1]

    # ── owners' earnings, seeded exactly as tool 1 seeds its box ─────
    # Both pages run the same engine on the same filings. What differed was
    # which figure got shown: tool 1 puts forward net income x pooled ΔE in
    # its box, this page was showing the latest year as filed. Same formula,
    # different year, and the two disagreed by whatever that year was unusual
    # by. They now seed identically.
    pooled = pool(years)
    recent = pool_safe(years[-3:], pooled)
    # ARM, 26 Aug 2026. This line used to fall back to the POOLED figure
    # whenever the recent one was unusable, which on ARM meant seeding owners'
    # earnings from a 5-year ΔE of 27.8% earned largely before its IPO, while
    # the last three years read -16.2%. Tool 1 applies the recent figure, finds
    # it unprojectable and falls back to the MEDIAN — so the two pages seeded
    # 251 and 556 for the same company on the same day, under a caption
    # claiming they seed identically.
    #
    # Substituting an older regime for a recent one is a fallback in the
    # flattering direction, which is the error class this project keeps
    # finding. The recent figure now stands or is refused, as on tool 1.
    _recent_usable = dE_projectable(recent)
    use_dE = recent.dE if _recent_usable else pooled.dE
    _seed_from_pooled = not _recent_usable
    # Measurement untouched; only the projection is held to 100%.
    applied_dE = seed_dE(use_dE)
    # Only true when the cap actually applied — see tool 1.
    dE_capped = _recent_usable and dE_was_capped(use_dE)
    hist_oe = sorted(y.OE for y in years[-5:] if not y.excluded)
    median_OE = hist_oe[len(hist_oe) // 2] if hist_oe else 0.0
    if _recent_usable:
        seed_OE, seed_is_placeholder = latest.N * applied_dE, False
    elif median_OE > 0:
        seed_OE, seed_is_placeholder = median_OE, False
    else:
        # Nothing usable: ΔE is negative or absurd AND every recent year lost
        # money. Net income is at least a defensible ceiling to revise down
        # from, but it is not a measurement and must not read as one.
        seed_OE, seed_is_placeholder = latest.N, True

    if _seed_from_pooled:
        st.warning(
            (f"**ΔE over the last three years is {recent.dE:.1%}, which cannot be projected "
             "forward.** Stock compensation has swamped earnings over that window, and a "
             "negative or absurd ratio applied to next year's profit is not a forecast. "
             if recent.dE_defined else
             # Rivian, 27 Aug 2026: the sentence above would have called a
             # 107.3% ratio "stock compensation swamping earnings", which is
             # not what happened and does not even sound wrong. Name the
             # denominator instead.
             f"**ΔE cannot be measured here: net income over the last three years pools to "
             f"{money(recent.sum_N)}.** Both sides of the ratio are negative, so it comes out "
             f"positive — the {recent.dE:.1%} it produces is arithmetic on a loss, not a share "
             "of profit reaching shareholders, because there is no profit to share. ")
            # CAVA, 27 Aug 2026: this said "seeded from the 5-year median" in
            # both branches. CAVA's median is -47, so the seed actually fell
            # through to net income as a ceiling, and the sentence named a
            # fallback that had not been used.
            + (f"Owners' earnings below are seeded from the 5-year median of {money(median_OE)} "
               "instead."
               if median_OE > 0 else
               "The 5-year median is negative too, so there is nothing to fall back to: the box "
               f"below holds {money(latest.N)} of net income as a CEILING, not a measurement.")
            + " Set the figure by hand from what the business earns in a normal year.")

    # ══ inputs ═══════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Inputs")

    c1, c2, c3 = st.columns(3)
    price = c1.number_input("Price", value=float(current_price(pre.get("ticker", tk)) or 100.0),
                            step=0.01)
    shares = c2.number_input("Diluted shares (M)", value=float(round(pre["shares"], 1)), step=1.0,
                             help="Everything per-share divides by this. Check it against the "
                                  "market cap below — a missed second share class is the most "
                                  "common reading error there is.")
    OE = c3.number_input(
        "Owners' earnings ($M)", value=float(round(seed_OE, 1)), step=1.0,
        help="Seeded exactly as the IV15 tool seeds its box: latest net income times pooled ΔE. "
             "Both pages should show the same figure for the same ticker.")
    mcap = shares * price

    c4, c5, c6 = st.columns(3)
    horizon = c4.slider("Holding period (years)", 5, 30, 20, 1,
                        help="Mayer's study runs on 25-year holds. The requirement is brutally "
                             "non-linear in this: five years less can add ten points a year.")
    exit_default = (mcap / OE) if OE > 0 else 20.0
    exit_mult = c5.number_input(
        "Exit multiple", value=float(round(min(max(exit_default, 3.0), 60.0), 1)), step=0.5,
        min_value=1.0,
        help="What the market pays for a dollar of owners' earnings at the end. Seeded flat at "
             "today's multiple, which is the honest default — assuming expansion is where most "
             "of the wishful thinking in this arithmetic hides.")
    dil_seed = pre.get("dilution")
    dilution = c6.number_input(
        "Share issuance (%/yr)",
        value=float(round((dil_seed if dil_seed is not None else 0.015) * 100, 1)), step=0.1,
        help="Measured from the actual share count over the window, capital events excluded. "
             "Negative means the count is shrinking. A straight drag on your per-share "
             "result, compounding for the whole holding period.") / 100.0
    st.caption(
        f"Market cap {money(mcap)} · a hundredfold is {money(mcap*100)}"
        + (f" · trading at {mcap/OE:,.1f}x owners' earnings" if OE > 0 else "")
        + ("" if dil_seed is not None else
           " · share-count history too short to measure dilution, so 1.5% is a placeholder"))

    with st.expander("Judgement inputs — the parts EDGAR cannot answer"):
        st.caption(
            "Burry's ROIC has two terms that are not tagged anywhere in XBRL, so they are seeded "
            "at zero and left to you. Zero is not neutral: it makes the return read high.\n\n"
            "**Other expense** — forensic depreciation and amortisation, a normalised tax rate, "
            "cyclical adjustment.\n\n"
            "**Other capital** — what funds the business without appearing as capital: purchase "
            "obligations, customer float, restricted cash, loans held for settlement.\n\n"
            "**Operating cash** — the split between cash the business needs and cash you could "
            "actually have. Only the second comes out of the capital base. Burry publishes no "
            "percentage; 2% of revenue is this tool's convention, not his figure.")
        j1, j2, j3 = st.columns(3)
        other_expense = j1.number_input("Other expense ($M)", value=0.0, step=1.0)
        other_capital = j2.number_input("Other capital ($M)", value=0.0, step=1.0)
        op_cash_pct = j3.number_input("Operating cash (% of revenue)", value=2.0, step=0.5,
                                      min_value=0.0, max_value=25.0) / 100.0

        st.caption(
            "**Reconciling with the websites.** A ROIC here will usually read higher than "
            "GuruFocus, Stockanalysis or Morningstar, and two choices account for most of the "
            "gap. Burry takes deployable cash out of the capital base; almost no provider does. "
            "And this base is equity plus borrowings, which leaves a leased store or office "
            "network out of capital altogether. Turn both on to see roughly the number they "
            "publish, then decide which question you wanted answered.")
        k1, k2 = st.columns(2)
        include_leases = k1.checkbox(
            "Count long-term operating leases as capital", value=False,
            help="For a company with thousands of leased locations, the lease obligation is the "
                 "asset base. Leaving it out flatters the return enormously.")
        cash_in_base = k2.checkbox(
            "Leave cash in the capital base", value=False,
            help="What most data providers do. Turn it on to compare like with like; turn it off "
                 "to follow Burry.")

    # ── the three rates ──────────────────────────────────────────────
    rows = build_roic(years, pre, op_cash_pct, other_expense, other_capital,
                      include_leases, cash_in_base)
    rows[-1].OE = OE                      # latest year follows the box above
    rows[-1].other_expense = other_expense
    latest_r = rows[-1]
    roic_med = None if financial else median_roic(rows, 5)

    pay = pooled_payout(years, pre["dividends"], 5)
    payout = pay.ratio
    buyback_yield = (pay.implied if pay.used_implied else pay.buybacks) / mcap if mcap > 0 else 0.0
    # Owners' earnings that pool to zero or below give no denominator for a
    # payout ratio. Rather than abandon the ceiling, assume full retention —
    # the most generous case — and say so. A refusal that survives the kindest
    # assumption is worth more than a blank.
    payout_assumed = payout is None and roic_med is not None and roic_med > 0
    payout_eff = 0.0 if payout is None else payout
    fundable = (per_share_ceiling(roic_med, payout_eff, buyback_yield)
                if roic_med is not None and roic_med > 0 else None)

    # A growth rate needs a run of years behind it, not just three data points.
    # Paychex's revenue tag overlapped the net-income window in only 2024-2026,
    # so "has delivered 11%" was a two-year rate spanning the Paycor
    # acquisition — and because "has delivered" takes the HIGHER of the two
    # rates, that two-year number beat the seventeen-year owners'-earnings
    # figure of 7% and became the company's track record.
    MIN_SPAN = 5

    rev_years = [fy for fy in fys if pre["revenue"].get(fy)]
    rev_hist = [pre["revenue"][fy] for fy in rev_years]
    rev_span = rev_years[-1] - rev_years[0] if len(rev_years) >= 2 else 0
    rev_cagr = (cagr(rev_hist[0], rev_hist[-1], rev_span)
                if len(rev_hist) >= 3 and rev_span >= MIN_SPAN else None)
    oe_clean = [y for y in years if not y.excluded]
    oe_span = oe_clean[-1].fy - oe_clean[0].fy if len(oe_clean) >= 2 else 0
    oe_cagr = (cagr(oe_clean[0].OE, oe_clean[-1].OE, oe_span)
               if len(oe_clean) >= 3 and oe_span >= MIN_SPAN else None)
    # The better of the two two-point rates. Kept as its own name because the
    # growth-measure warning below has to be able to say which reading is which.
    endpoint = max([g for g in (rev_cagr, oe_cagr) if g is not None], default=None)
    # The same two series read as a trend rather than as endpoints. Same
    # generosity about WHICH series describes the business, none about which
    # two years happen to sit at the ends of it. See trend_growth's docstring.
    rev_trend = (trend_growth(rev_hist)
                 if len(rev_hist) >= 3 and rev_span >= MIN_SPAN else None)
    oe_trend = (trend_growth([y.OE for y in oe_clean])
                if len(oe_clean) >= 3 and oe_span >= MIN_SPAN else None)
    trend = max([g for g in (rev_trend, oe_trend) if g is not None], default=None)
    # Generous on purpose, and now actually generous — see delivered_rate.
    delivered = delivered_rate(endpoint, trend)
    short_spans = [r for r in (
        growth_leg_reason("revenue", len(rev_hist), rev_span,
                          rev_hist[0] if rev_hist else 0.0, rev_hist[-1] if rev_hist else 0.0),
        growth_leg_reason("owners' earnings", len(oe_clean), oe_span,
                          oe_clean[0].OE if oe_clean else 0.0,
                          oe_clean[-1].OE if oe_clean else 0.0)) if r]

    required = (required_growth(mcap / OE, exit_mult, horizon, 100.0, dilution)
                if OE > 0 and mcap > 0 else None)
    v = assess(required, fundable, delivered, mcap, trend, OE)

    # ══ verdict ══════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader(f"Verdict · {tk}")

    m1, m2, m3 = st.columns(3)
    m1.metric("Needs", f"{required:.1%}" if required is not None else "—",
              f"100x in {horizon}y at {exit_mult:g}x")
    m2.metric("Has delivered", f"{delivered:.1%}" if delivered is not None else "n/a",
              (f"revenue {rev_cagr:.0%} over {rev_span}y" if rev_cagr is not None
               else "revenue n/a")
              + (f" · OE {oe_cagr:.0%} over {oe_span}y" if oe_cagr is not None else ""))
    m3.metric("Can fund", f"{fundable:.1%}" if fundable is not None else "n/a",
              fund_badge_caption(roic_med, payout_eff, buyback_yield, fundable,
                                 payout_assumed, financial, latest_r.reason))

    if seed_is_placeholder and abs(OE - float(round(seed_OE, 1))) < 0.05:
        st.error(
            f"**The owners' earnings above are a placeholder, so every figure on this page "
            f"rests on it.** "
            + (f"ΔE came out at {use_dE:.0%} and every recent year was negative, so "
               if pooled.dE_defined else
               f"Net income over this window pools to {money(pooled.sum_N)}, so ΔE has no "
               f"denominator to be a share of — the {use_dE:.0%} it computes is two negatives "
               "divided. Every recent year was negative, so ")
            + f"the box holds {plain(latest.N)} of net income as a ceiling — not a measurement of "
            "what reaches shareholders. On this company stock issuance has been running ahead "
            "of profit, which is exactly what owners' earnings are meant to capture. Enter what "
            "you think the business earns in a normal year, and the arithmetic below becomes "
            "worth reading.")

    # Fires only on the verdict the growth measure actually decided. When the
    # size gate or the capital check already settled the page, saying "the
    # verdict depends on which growth measure you read" is simply false.
    if v.why == "growth measure":
        st.warning(
            f"**This price is open on one reading of the history and not the other.** *Has "
            f"delivered* is {delivered:.1%} — the kindest reading available, here the rate "
            f"between the first and last year of the window, which is decided by whichever two "
            f"years sit at the ends. Fitted through every year instead, the same history gives "
            f"{trend:.1%}. This price needs "
            f"{required:.1%} a year, which the trend does not reach. Nothing here is called "
            "open on an endpoint rate alone: a refusal that survives the kindest reading is "
            "worth trusting, but a pass that only survives it is the one error on this page "
            "that costs money. Both figures are in the assumptions block.")

    if short_spans:
        st.info(
            "**" + " and ".join(short_spans).capitalize()
            + "**, so it is not counted in *has "
              "delivered*. A compound rate needs three readable years, a span of at least five, "
              "and a positive figure at BOTH ends. Three points spanning two years is a recent "
              "trend rather than a record, and an acquisition inside that window would read as "
              "organic growth; a rate measured from a loss year is not a growth rate at all, "
              "whatever arithmetic it produces. Where a line reads fewer years than net income, "
              "the tag panel says so.")

    if pay.warning:
        st.warning("**Check this before trusting the ceiling.** " + pay.warning)
    if payout_assumed:
        st.warning(
            f"**Can fund assumes this company keeps everything it earns.** Cash returned could "
            f"not be measured against owners' earnings — they pool to zero or below over this "
            f"window, so there is no denominator. The {fundable:.1%} above therefore reinvests "
            f"every dollar at {roic_med:.1%}, which is the most generous reading available. Any "
            "dividend or buyback lowers it.")
    elif payout is not None and fundable is not None:
        st.caption(can_fund_explainer(
            pay.oe, pay.returned, pay.dividends,
            pay.implied if pay.used_implied else pay.buybacks,
            payout, roic_med, buyback_yield))

    if v.why == "size":
        st.error(
            f"**{v.label}.** A hundredfold on {money(mcap)} is {money(mcap*100)}, against a world "
            "economy of roughly \\$110 trillion. Mayer's whole point is that the base has to be "
            "small enough for the arithmetic to have somewhere to go. Nothing below rescues this.")
    elif v.why == "no earnings base":
        st.error(
            f"**{v.label}.** Owners' earnings are {money(OE)}, and Mayer's arithmetic is a ratio "
            "of what you pay to what the business earns. A hundredfold from a loss is not a "
            "calculation, it is a story about a recovery. Enter the owners' earnings you believe "
            "the business reaches in a normal year and this page will mean something.")
    elif v.why == "capital":
        st.error(
            f"**{v.label}.** A hundredfold in {horizon} years needs {required:.1%} a year in "
            f"owners' earnings. This business funds about {fundable:.1%}"
            + (f" — it earns {roic_med:.0%} on capital but returns {payout:.0%} of its earnings "
               "to shareholders, so the compounding happens in your dividend and your share of "
               "the company, not in the earnings themselves."
               if payout is not None and payout > 0.6 else
               f" — that is a {roic_med:.0%} return on capital reinvesting everything, and it "
               "still is not enough." if payout_assumed else
               f" from a {roic_med:.0%} return on capital with {max(0.0,1-payout_eff):.0%} "
               "retained.")
            + " The gap would have to come from outside — debt, which runs out, or stock, which "
              "is the dilution you already set. Raising the exit multiple is the only other "
              "lever, and that is a bet on the market, not on the business.")
    elif v.why == "history only":
        st.warning(
            f"**{v.label}.** A hundredfold in {horizon} years needs {required:.1%} a year, and "
            f"the company has delivered {delivered:.1%} — the kinder of its revenue and "
            "owners'-earnings rates. Return on capital could not be read for this filer, so "
            "there is no funding ceiling to set against it and half this page is missing. "
            "The reason is in the ROIC section below; the tag panel usually says which line "
            "did not load.")
    elif v.why == "history":
        st.warning(
            f"**{v.label}.** {required:.1%} a year is inside the {fundable:.1%} its capital could "
            f"fund, but the company has delivered {delivered:.1%} — and that is the kinder of its "
            "revenue and owners'-earnings rates. It has never grown at the rate this price "
            "requires. Not impossible; it is a bet on an inflection, and you should be able to "
            "say what causes it.")
    elif v.kind == "success":
        st.success(
            f"**{v.label}.** {required:.1%} a year sits inside both ceilings — the {fundable:.1%} "
            f"its capital funds and the {delivered:.1%} it has delivered. That makes a "
            "hundredfold possible, not likely. Everything now turns on how long the return on "
            "capital and the runway last, which no filing can tell you.")
    elif v.why == "implausible":
        st.error(
            f"**{v.label}.** A market capitalisation of {money(mcap)} against {money(OE)} of "
            "owners' earnings is a price of less than one year's profit, which no market "
            "offers. The usual cause is the share count: a company with two share classes "
            "reports its diluted average in one class's equivalents, so the count can be "
            "hundreds of times too small. Check the share box against the market "
            "capitalisation you know — nothing below means anything until it agrees.")
    elif v.why == "growth measure":
        # VEEV, 28 Aug 2026: this verdict had no branch, so it fell through to
        # the else below — which is written for "no capital base" and "no
        # growth history" — and told a company with a 104.9% funding ceiling
        # on screen that "no funding ceiling could be built ... a bank,
        # insurer or REIT". Both ceilings were built here; it is the two
        # readings of ONE history that disagree, and the box above already
        # argues that in full. `fundable` can still be None on this branch —
        # a readable growth record can arrive with an unreadable capital base
        # — so it is never formatted unguarded.
        st.warning(
            f"**{v.label}.** {required:.1%} a year is inside the {delivered:.1%} rate between "
            "the first and last year of the window"
            + (f" and inside the {fundable:.1%} its capital could fund"
               if fundable is not None else ", and no funding ceiling could be built")
            + f", but not inside the {trend:.1%} the same history gives fitted through every "
              "year. The box above has the argument.")
    elif v.why == "no ceiling of either kind":
        st.error(
            f"**{v.label}.** {required:.1%} a year is what the price requires, and neither "
            "ceiling could be built: no readable capital base and too little growth history. "
            "There is nothing here to check that number against.")
    else:
        (st.warning if v.kind == "warning" else st.info)(
            f"**{v.label}.** {required:.1%} a year, checked against only one ceiling — "
            + ("no growth history long enough to measure. A capital ceiling says what this "
               "business COULD fund; only the record says whether it ever has, and there is "
               "no record here. What it says is that the price has been checked against half "
               "the question."
               if v.why == "no growth history" else
               "no funding ceiling could be built. Either the capital base could not be read, "
               "or return on capital was withheld because it is not meaningful for this kind "
               "of filer — a bank, insurer or REIT holds investments that back policyholder "
               "and depositor liabilities rather than shareholders. Growth alone cannot carry "
               "a verdict: what it says here is that the price has been checked against half "
               "the question.")
            + " Treat this page as incomplete for this company.")

    # ══ criteria ═════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Mayer's criteria")

    insider = st.number_input(
        "Insider ownership (%) — from the proxy", value=0.0, step=0.5,
        min_value=0.0, max_value=100.0,
        help="Never tagged in XBRL. It lives in the beneficial ownership table of the DEF 14A, "
             "linked below. Type what you find there and it joins the table.")

    up_years = sum(1 for a, b in zip(rev_hist, rev_hist[1:]) if b > a)
    readable = [r for r in rows if not r.reason]
    facts_rows = [
        {"Criterion": "Small base",
         "Filings": f"{plain(mcap)} cap, {plain(pre['revenue'].get(fys[-1], 0.0))} revenue"
                    if mcap > 0 else "no price or share count",
         "Reading": size_band(mcap) if mcap > 0 else "n/a"},
        {"Criterion": "Return on capital",
         "Filings": f"{roic_med:.1%} median, 5 years" if roic_med is not None else "n/a",
         "Reading": ("n/a — financial company" if financial else
                     "n/a — " + (latest_r.reason or "capital base unread") if roic_med is None
                     else "high — reinvestment compounds" if roic_med >= 0.20
                     else "adequate" if roic_med >= 0.12 else "too low to compound from")},
        {"Criterion": "Reinvests it",
         "Filings": f"returns {payout:.0%} of owners' earnings" if payout is not None else "n/a",
         "Reading": ("n/a — owners' earnings negative over the window" if payout is None
                     else "distributes nearly everything — a payer, not a compounder"
                     if payout > 0.8 else "retains most of it" if payout < 0.35 else "mixed")},
        {"Criterion": "Sustained, not a spike",
         "Filings": (f"{sum(1 for r in readable if r.roic and r.roic >= 0.15)} of {len(readable)}"
                     " years above 15%" if readable and not financial else "n/a"),
         "Reading": "the year-by-year table is the evidence"},
        {"Criterion": "Durable growth",
         "Filings": (f"revenue {rev_cagr:.1%}/yr over "
                     f"{rev_years[-1]-rev_years[0]} years, up in {up_years} of {len(rev_hist)-1}"
                     if rev_cagr is not None else "too few years of revenue"),
         "Reading": (f"owners' earnings {oe_cagr:.1%}/yr" if oe_cagr is not None
                     else "owners' earnings growth n/a — negative at one end")},
        {"Criterion": "Entry multiple",
         "Filings": f"{mcap/OE:,.1f}x owners' earnings" if OE > 0 and mcap > 0 else "n/a",
         "Reading": "half the engine, and the half you control at purchase"},
        {"Criterion": "Dilution",
         "Filings": f"{dil_seed:+.1%}/yr share count" if dil_seed is not None
                    else "history too short",
         "Reading": ("retiring stock — a tailwind" if dil_seed is not None and dil_seed < 0 else
                     "modest" if dil_seed is not None and dil_seed < 0.02 else
                     "heavy — it compounds against you" if dil_seed is not None else "n/a")},
        {"Criterion": "Owner-operator",
         "Filings": "not in XBRL — read the proxy",
         "Reading": "n/a — no filing tags founder involvement"},
        {"Criterion": "Insider ownership",
         "Filings": f"{insider:.1f}% — your figure" if insider > 0 else "not in XBRL",
         "Reading": ("aligned" if insider >= 10 else "some skin in the game" if insider >= 3
                     else "low, if that is the whole picture" if insider > 0 else "n/a")},
    ]
    st.dataframe(pd.DataFrame(facts_rows), width="stretch", hide_index=True)

    p1, p2 = st.columns(2)
    if pre.get("proxy"):
        url, date = pre["proxy"]
        p1.markdown(f"[Proxy statement (DEF 14A), filed {date}]({url})")
        p1.caption("Beneficial ownership table — insider percentage, founder holdings, who "
                   "controls the vote.")
    else:
        p1.caption("No DEF 14A found. Foreign private issuers do not file proxies; a recent "
                   "listing may not have filed its first one yet.")
    p2.markdown(f"[Insider transactions (Form 4)]"
                f"(https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={pre['cik']}"
                f"&type=4&dateb=&owner=include&count=40)")
    p2.caption(f"{pre['form4']} filings in the last twelve months. A count is not a signal — "
               "open them and see whether anyone bought with their own money.")

    # ══ detail, all folded away ══════════════════════════════════════
    st.markdown("---")

    with st.expander("What it would take at other exit multiples"):
        if required is not None:
            mult_now = mcap / OE
            grid = []
            for h in (10, 15, 20, 25):
                row = {"Held for": f"{h} years"}
                for m in (10.0, 15.0, 20.0, 25.0, 30.0):
                    g = required_growth(mult_now, m, h, 100.0, dilution)
                    row[f"{m:g}x"] = ("—" if g is None else
                                      f"{g:.0%} ✗" if fundable is not None and g > fundable
                                      else f"{g:.0%}")
                grid.append(row)
            st.dataframe(pd.DataFrame(grid), width="stretch", hide_index=True)
            st.caption(
                f"Required growth in owners' earnings. Today's multiple is {mult_now:,.1f}x and "
                f"{dilution:.1%} annual issuance is included. "
                + (f"A ✗ marks a rate above the {fundable:.1%} this business can fund. "
                   if fundable is not None else "")
                + "Notice how much work the exit multiple does — buying cheap and selling dear "
                  "is half of Mayer's engine, and it is the half you fix at purchase.")
        else:
            st.caption("Needs a positive owners' earnings figure.")

    # Counted once, outside the branches, because BOTH need it: the refusal
    # branch never printed it at all and the assumptions block printed a median
    # with no indication of how many years went into it.
    _roic_readable = len([r for r in rows[-5:] if not r.reason])
    with st.expander("Return on invested capital — Burry's formula, line by line"):
        if financial:
            st.error(
                f"**Not shown for financials.** {pre['sic_desc'] or 'This company'} (SIC "
                f"{pre['sic']}) runs on leverage as its product rather than as a financing "
                "choice. Equity plus borrowings is not capital at work, and its cash is not "
                "free — it backs deposits or policyholder liabilities. Return on equity against "
                "a combined ratio or a net interest margin is the right frame, and this tool "
                "does not contain it.")
        elif latest_r.reason:
            w = latest_r.cap
            st.error(f"**n/a for FY{latest_r.fy} — {latest_r.reason}.**"
                     + ("\n\nEquity is negative, which for a profitable company almost always "
                        "means buybacks have retired more capital than the balance sheet "
                        "carries. Usually strength rather than distress, but a return on a "
                        "negative denominator flips sign, so nothing is printed."
                        if w.equity < 0 and w.equity_found else
                        f"\n\nThe cause is cash, not losses: {money(w.deployable_cash)} "
                        f"deployable against {money(w.total_capital)} of total capital. The "
                        "operating business runs on less than nothing — a genuinely excellent "
                        "property and not a computable one. Check that the cash really is "
                        "deployable rather than earmarked, and raise the operating-cash "
                        "percentage if the business needs more of it."
                        if w.deployable_cash > w.total_capital and w.equity_found else ""))
            # The median is computed, printed in the assumptions block, and fed
            # to `can fund` — but this branch used to render the red box and
            # nothing else, so the one place that explains the figure was the
            # one place that stayed silent about it. BKNG, 24 Aug 2026: ROIC
            # moved 58.09% to 45.21% when cash entered the base. A shrinking
            # denominator should RAISE a return; it fell because years crossed
            # into negative capital and were refused, leaving the median to the
            # lower survivors. Nothing on the page could show that.
            st.caption(
                (f"The five-year median still reads **{roic_med:.1%}**, from "
                 f"**{_roic_readable} of {len(rows[-5:])}** readable years — the refused years "
                 "are dropped, not counted as zero, so the median describes only the years that "
                 "computed. It is the figure the assumptions block prints, and the one "
                 + ("`can fund` multiplies by the share of earnings retained — which here is "
                    "**nothing**, because payout exceeds earnings, so `can fund` is buyback "
                    "yield alone and this median does not reach it."
                    if payout_eff >= 1.0 else
                    "`can fund` multiplies by the share of earnings retained, so it carries "
                    "straight through to that ceiling.")
                 if roic_med is not None else
                 "No year in the last five produced a readable return, so there is no median "
                 "and `can fund` has no capital ceiling to work from.")
                + (f" With only {_roic_readable} readable year"
                   f"{'' if _roic_readable == 1 else 's'}, that is a thin base for a ceiling — "
                   "read it as one year's return, not a trend."
                   if roic_med is not None and _roic_readable <= 2 else ""))
        else:
            k1, k2, k3 = st.columns(3)
            k1.metric("ROIC", f"{latest_r.roic:.1%}", f"FY{latest_r.fy}")
            k2.metric("Median, 5 years", f"{roic_med:.1%}" if roic_med is not None else "—",
                      f"{_roic_readable} of {len(rows[-5:])} readable")
            tang = latest_r.tangible_roic
            k3.metric("Ex-goodwill", f"{tang:.1%}" if tang is not None else "n/a",
                      "return on tangible capital")
            cav = roic_caveat(latest_r, pre.get("fye_month", 12))
            if cav:
                st.caption("How hard to lean on this: " + cav + ".")

            w = latest_r.cap
            wf = [
                ("Owners' earnings", latest_r.OE, "the figure in the box above"),
                ("less interest income", -latest_r.interest_income,
                 "the cash left the denominator, so its income leaves the numerator"),
                ("less capital lease payments", -latest_r.lease_payments,
                 "a financing outflow earnings never saw"),
                ("less other expense", -latest_r.other_expense,
                 "forensic D&A, normalised tax, cyclical — yours to set"),
                ("= adjusted return", latest_r.numerator, ""),
                ("Shareholders' equity", w.equity, "parent's share" if w.minority else ""),
                ("plus borrowings", w.debt, "short and long term"),
                ("plus finance leases", w.finance_leases, "capitalised leases are debt in all "
                                                          "but name"),
                ("= total capital", w.total_capital, ""),
                ("less deployable cash", -w.deployable_cash,
                 f"of {plain(w.cash)} held; {plain(w.op_cash_need)} kept in as working cash"),
                ("plus other capital", w.other_capital, "float, obligations — yours to set"),
                ("= invested capital", w.invested, ""),
            ]
            st.dataframe(
                pd.DataFrame([{"Line": a, "$M": b, "Why": c} for a, b, c in wf])
                .style.format({"$M": "{:,.0f}"}), width="stretch", hide_index=True)
            st.caption(
                f"Long-term operating leases of {money(w.operating_leases)} are **not** "
                "subtracted, and that is deliberate: Burry's formula removes them from a "
                "total-capital figure that included them. This base is equity plus borrowings, "
                "which never did, so subtracting again would count them twice. Restricted cash "
                f"of {money(w.restricted)} stays in for the reason it always should — it funds "
                "the business and you cannot have it.")
            # Directly under the "less interest income" row it is about, which
            # reads 0 on exactly the filers where the money is largest.
            _int_series = pre.get("interest", {})
            _int_gap = interest_gap_note(len(_int_series), w.cash, w.invested, latest_r.fy,
                                         max(_int_series) if _int_series else None)
            if _int_gap:
                st.info(_int_gap)
            # Item 4. Priced here rather than in the notes list because this is
            # the only place invested capital and the return are both on screen.
            _swing = stale_capital_swing_note(w.invested, latest_r.numerator, latest_r.roic,
                                              pre.get("cap_missing", []))
            if _swing:
                st.caption("**The other page reads this line differently.**" + _swing)

            st.write("**Year by year** — the trend matters more than the level")
            st.dataframe(pd.DataFrame([{
                "FY": r.fy, "Owners' earnings": r.OE, "Invested capital": r.cap.invested,
                "ROIC": r.roic if not r.reason else None,
                "Ex-goodwill": r.tangible_roic if not r.reason else None,
                "n/a because": r.reason} for r in rows]).style.format(
                {"Owners' earnings": "{:,.0f}", "Invested capital": "{:,.0f}",
                 "ROIC": "{:.1%}", "Ex-goodwill": "{:.1%}"}, na_rep="n/a"),
                width="stretch", hide_index=True)
            st.caption(
                f"FY{latest_r.fy} uses the owners' earnings figure from the input box; earlier "
                "years are as filed. Capital is measured at each year end rather than averaged, "
                "which understates the return for anything growing its asset base quickly — the "
                "conservative direction, and the one Burry's formula reads literally.")

    with st.expander("Feed this into the IV15 tool"):
        st.caption(
            "The Tragic Algebra Analyzer asks for a growth rate and has no way to sanity-check "
            "it. This is that ceiling, computed.")
        # Gated on `fundable`, not on `roic_med`. They are not the same test:
        # `fundable` is None whenever ROIC is None OR NOT POSITIVE, because a
        # negative return funds no growth. Rivian, 27 Aug 2026: a negative
        # 5-year median passed the `roic_med is not None` gate, the per-share
        # ceiling below formatted None, and the page died with a TypeError —
        # taking the notes, the year-by-year table, the assumptions block and
        # the tag panel with it. Every other site that prints `fundable`
        # already asks the right question; this one did not.
        if fundable is not None:
            g_ceiling = sustainable_growth(roic_med, payout_eff)
            st.code(
                f"{tk}\n"
                f"owners' earnings     {OE:,.0f} M      <- same figure tool 1 seeds\n"
                f"shares               {shares:,.1f} M\n"
                f"ROIC, 5y median      {roic_med:.1%}\n"
                f"cash returned        "
                + ("not measurable — full retention assumed" if payout is None
                   else f"{payout:.0%} of owners' earnings") + "\n"
                f"growth ceiling       {g_ceiling:.1%}   <- do not exceed this in tool 1\n"
                f"per-share ceiling    {fundable:.1%}   (adds the buyback effect)",
                language="text")
            st.caption(
                f"A growth rate above {g_ceiling:.1%} in tool 1 is a claim that this company "
                "funds expansion from outside — more debt, or stock. Sometimes true, always "
                "worth stating out loud rather than assuming.")
        else:
            st.warning("No ROIC or no positive earnings base, so no ceiling. Tool 1's growth "
                       "input stays unconstrained here.")

    label = "Notes and detail" + (f" · {len(alerts)} to review" if alerts else "")
    with st.expander(label):
        for kind_, msg in alerts:
            getattr(st, kind_)(msg)

        st.write("**Owners' earnings, year by year** — identical to tool 1 for the same ticker")
        # PDEX: one decimal count for all five dollar columns, chosen from
        # the table's own largest value. See money_decimals. Same rule as
        # tool 1, so the two tables stay identical at every scale.
        _mfmt = money_fmt([v for y in years for v in (y.N, y.G, y.T, y.omega, y.OE)])
        st.dataframe(pd.DataFrame([{
            "FY": f"{y.fy}*" if y.excluded else str(y.fy),
            "Net income": y.N, "GAAP SBC": y.G, "Buybacks": y.T, "Share change": y.dS,
            "Avg price": y.price, "True SBC cost": y.omega, "Owners' earnings": y.OE}
            for y in years]).style.format({
                "Net income": _mfmt, "GAAP SBC": _mfmt, "Buybacks": _mfmt,
                "Share change": "{:+,.1f}", "Avg price": "${:,.2f}",
                "True SBC cost": _mfmt, "Owners' earnings": _mfmt}, na_rep="—"),
            width="stretch", hide_index=True)
        st.caption(
            f"ΔE pooled: {pooled.dE:.1%} over {pooled.years} years, {recent.dE:.1%} over the "
            f"last three. "
            + (f"The last three years cannot be projected, so the box above shows "
               + (f"the 5-year median of owners' earnings rather than a ΔE applied to net "
                  f"income — the same fallback tool 1 uses. " if median_OE > 0 else
                  f"{plain(latest.N)} of net income as a ceiling, because the 5-year median is "
                  f"negative as well — the same fallback tool 1 uses. ")
               if _seed_from_pooled else
               f"The box above shows {plain(latest.N)} of net income times "
               f"{applied_dE:.1%}, which is how tool 1 seeds it too — ")
            + f"the latest year as filed "
            f"came in at {plain(latest.OE)}."
            + (f" The {use_dE:.1%} measured over the last three years is left as filed above "
               "but is not projected: shareholders cannot keep more than every reported dollar "
               "for fifteen years running." if dE_capped else ""))

        # Two tells that owners' earnings here are flattering. Both are visible
        # in the table above, and neither announced itself before.
        _neg = [y for y in years if y.omega < 0 and not y.excluded]
        if pay.used_implied:
            st.error(
                "**Owners' earnings are overstated in this table, and so are tool 1's.** No "
                "repurchase figure was read for these years, so the market value of shares "
                "delivered to employees "
                "floors at zero: V = max(0, buybacks + price x share change), and with buybacks "
                "missing and the share count shrinking, that maximum is always zero. Real "
                "repurchases are gross of the stock issued to employees, so the true cost is "
                "positive. Both tools read this filer the same way and are wrong the same way. "
                "The tag panel below is what to send me to fix it.")
        elif len(_neg) >= max(2, len(years) // 3):
            st.warning(
                f"**The true stock-comp cost reads negative in {len(_neg)} years**, which adds to "
                "owners' earnings rather than subtracting. That happens when option and ESPP "
                "proceeds exceed the tax withheld on vesting — real, but it usually also means "
                "the share-delivery term is being floored at zero because a buyback or issuance "
                "line was not read. Treat these owners' earnings as a ceiling.")

        st.write("**Assumptions used** — paste this if something looks wrong")
        st.code(
            f"{tk}   price {price:,.2f}   shares {shares:,.1f}M   mkt cap {plain(mcap)}\n"
            f"owners' earnings    {OE:,.0f} M   ({mcap/OE:,.1f}x)\n"
            f"needs               "
            + (f"{required:.2%}/yr" if required is not None else "n/a") + "\n"
            f"can fund            "
            + (f"{fundable:.2%}/yr" if fundable is not None else "n/a") + "\n"
            f"has delivered       "
            + (f"{delivered:.2%}/yr" if delivered is not None else "n/a")
            + ("   (the kinder of the two below)"
               if endpoint is not None and trend is not None else "") + "\n"
            f"  endpoint basis    "
            + (f"{endpoint:.2%}/yr" if endpoint is not None else "n/a")
            + "   (first to last year)\n"
            f"  trend basis       "
            + (f"{trend:.2%}/yr" if trend is not None else "n/a")
            + "   (log-linear, all years)\n"
            f"ROIC 5y median      "
            + (f"{roic_med:.2%}   ({_roic_readable} of {len(rows[-5:])} years readable)"
               if roic_med is not None else "n/a") + "\n"
            f"invested capital    {latest_r.cap.invested:,.0f} M\n"
            f"payout              "
            + (f"{payout:.1%}" if payout is not None else "n/a") + "\n"
            f"other expense       {other_expense:,.0f} M   other capital {other_capital:,.0f} M\n"
            f"operating cash      {op_cash_pct:.1%} of revenue\n"
            f"dilution            {dilution:+.2%}/yr   horizon {horizon}y   exit {exit_mult:g}x\n"
            f"verdict             {v.label} ({v.why})", language="text")

    with st.expander("What was read from the filings — every tag, found or missing"):
        st.caption(
            "A zero in this app is either something the company did not do or a tag this reader "
            "does not know. Only the filing settles which, and this is where to look. If a line "
            "you know exists reads zero years, that is a bug worth reporting — the tag name is "
            "the whole fix.")
        st.dataframe(pd.DataFrame(pre.get("tags", [])), width="stretch", hide_index=True)

# ══════════════════════════════════════════════════════════════════════
#  REFERENCE
# ══════════════════════════════════════════════════════════════════════

st.divider()
_r1, _r2 = st.columns(2)
with _r1:
    with st.expander("What the numbers mean"):
        st.markdown(
            "**Needs** — the annual growth in owners' earnings that turns today's price into a "
            "hundredfold over your holding period, after the exit multiple you assume and the "
            "dilution along the way.\n\n"
            "**Can fund** — return on capital multiplied by the share of earnings retained, plus "
            "the lift from any stock retired. The fastest a business compounds per share without "
            "outside money. A company earning 100% on capital and paying all of it out funds "
            "almost no growth, which is why this is the number to read rather than ROIC "
            "itself.\n\n"
            "**Has delivered** — the better of its revenue and owners'-earnings growth over the "
            "window. History is not a ceiling, but a price that requires a rate the company has "
            "never reached is a bet on an inflection.\n\n"
            "**ROIC** — Burry's fully-adjusted return on invested capital, computed on owners' "
            "earnings over capital genuinely at work: deployable cash removed, operational cash "
            "left in.")

with _r2:
    with st.expander("Verify the engine"):
        st.caption(
            "Three kinds of check. The Alphabet lines re-run **Burry's published inputs** through "
            "this page's copy of the Tragic Algebra engine and confirm it still matches tool 1 to "
            "the dollar. The Mayer lines check the 100x arithmetic against the figures his book "
            "leads with. The rest are wiring and verdict tests.\n\n"
            "There is no published ROIC to validate against the way Alphabet validates owners' "
            "earnings, so that test proves the plumbing is right, not that the framework is.")
        if st.button("Run checks"):
            _results = self_test()
            _sev, _line = test_summary(_results)
            getattr(st, _sev)(_line)
            for name, ok, got in _results:
                st.write(("✅ " if ok else "❌ ") + f"{name} — {got}")

st.caption(
    "Research aid, not financial advice. Outputs depend on estimates you supply. Method follows "
    "Christopher Mayer's published framework and Michael Burry's published ROIC formula; this "
    "project is independent and is not affiliated with or endorsed by either of them.")
