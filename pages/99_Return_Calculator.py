"""
Return Calculator
=================
The ordinary compound-return calculator, kept inside the tool kit so the
other pages' user does not have to leave the app for it. Reads nothing,
fetches nothing, imports nothing from the other pages.

Five quantities — start, yearly return, years, contribution, target — and
the page solves for any one of four:

    Ending amount          start, return, years, contribution -> ending
    Required return        start, target, years, contribution -> return
    Years to target        start, return, target, contribution -> years
    Required contribution  start, return, years, target -> contribution

    ending  = start x (1+r)^n  +  C x ((1+r)^n - 1) / i      C paid at END of each period
              i = r for yearly contributions, (1+r)^(1/12) - 1 for monthly
    ending  = start + periods x C                             when r = 0
    return  = (target / start)^(1/n) - 1                      no contribution: closed form
              otherwise solved by bisection (ending is strictly increasing in r)
    years   = ln((target + C/i) / (start + C/i)) / ln(1+i)   periods, then / 12 if monthly
    C       = (target - start x (1+r)^n) x i / ((1+r)^n - 1)
    multiple = ending / (start + periods x C)

A monthly contribution compounds at the monthly rate that grows to the stated
yearly return, never at r/12: the yearly return means the same thing for the
start amount and for the contributions.

Known answers: 10,000 at 10% for 10 years -> 25,937.42.
100x in 20 years -> 25.89%/yr (10^0.1 - 1).
1,000/month at 10% for 10 years -> 199,863.86.

Saved scenarios live in session state: they survive re-runs while the tab
is open and are gone on browser refresh. The CSV is the persistence.

Run:  streamlit run pages/3_Return_Calculator.py
"""

from __future__ import annotations

import io
import math

import pandas as pd
import streamlit as st

# ══════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════

MAX_YEARS = 100            # as an input
MAX_SOLVED_YEARS = 1_000   # as an answer
RATE_FLOOR = -0.9999       # bisection bracket for the required return
RATE_CEIL = 10.0           # 1,000%/yr

FORM_FORWARD = "Ending amount"
FORM_RATE = "Required return"
FORM_YEARS = "Years to target"
FORM_CONTRIB = "Required contribution"
FORMS = [FORM_FORWARD, FORM_RATE, FORM_YEARS, FORM_CONTRIB]

# "Solved for" values in a saved row, and the form each loads back into.
SOLVED = {FORM_FORWARD: "ending", FORM_RATE: "return",
          FORM_YEARS: "years", FORM_CONTRIB: "contribution"}
FORM_OF = {v: k for k, v in SOLVED.items()}

PER_YEAR = "yearly"
PER_MONTH = "monthly"

# Column order for the scenario table and the CSV. Rates are stored as
# fractions internally and shown as percentages; the CSV column says so.
# Years is a whole number except in a years-to-target row, where it is the
# exact fractional answer.
COLUMNS = ["Name", "Solved for", "Start", "Return %", "Years",
           "Contribution", "Per", "Ending", "Put in", "Multiple"]


# ══════════════════════════════════════════════════════════════════════
#  ARITHMETIC
# ══════════════════════════════════════════════════════════════════════

def periods(years: float, per: str) -> float:
    return years * 12 if per == PER_MONTH else years


def period_rate(rate: float, per: str) -> float:
    """The rate one contribution period earns. Monthly is the rate that
    compounds twelve times to the yearly return — never r/12."""
    return (1.0 + rate) ** (1.0 / 12.0) - 1.0 if per == PER_MONTH else rate


def future_value(start: float, rate: float, years: float, contribution: float = 0.0,
                 per: str = PER_YEAR) -> float:
    """Ending amount. The contribution is paid at the end of each period, so
    the last one earns nothing."""
    if rate == 0.0:
        # The annuity factor divides by the rate. At exactly zero the
        # answer is the plain sum, not a division by zero.
        return start + periods(years, per) * contribution
    g = (1.0 + rate) ** years
    return start * g + contribution * (g - 1.0) / period_rate(rate, per)


def total_put_in(start: float, years: float, contribution: float, per: str = PER_YEAR) -> float:
    return start + periods(years, per) * contribution


def required_return(start: float, target: float, years: float, contribution: float = 0.0,
                    per: str = PER_YEAR) -> float:
    """The yearly return that turns start (plus contributions) into target.
    Closed form without contributions. With them there is none, so the rate
    is found by bisection: ending is strictly increasing in the rate, so the
    answer is unique. Callers check the bracket first (check_rate)."""
    if contribution == 0.0:
        return (target / start) ** (1.0 / years) - 1.0
    lo, hi = RATE_FLOOR, RATE_CEIL
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if future_value(start, mid, years, contribution, per) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def years_to_target(start: float, rate: float, target: float, contribution: float = 0.0,
                    per: str = PER_YEAR) -> float:
    """Exact (fractional) years until the balance first equals the target.
    Callers check the inputs first (check_years): rate >= 0, target > start,
    and not (rate == 0 and contribution == 0)."""
    i = period_rate(rate, per)
    if i == 0.0:
        n = (target - start) / contribution
    else:
        n = math.log((target + contribution / i) / (start + contribution / i)) / math.log(1.0 + i)
    return n / 12.0 if per == PER_MONTH else n


def reached_after(start: float, rate: float, target: float, contribution: float,
                  per: str) -> tuple[int, float]:
    """The first whole period at or above the target, and the balance then.
    Contributions land at period ends, so this is the figure you would see."""
    n = periods(years_to_target(start, rate, target, contribution, per), per)
    whole = math.ceil(n - 1e-9)
    years = whole / 12.0 if per == PER_MONTH else float(whole)
    return whole, future_value(start, rate, years, contribution, per)


def required_contribution(start: float, rate: float, years: float, target: float,
                          per: str = PER_YEAR) -> float:
    """The contribution per period that reaches the target. Callers check
    that the start alone does not already exceed it (check_contrib)."""
    if rate == 0.0:
        return (target - start) / periods(years, per)
    g = (1.0 + rate) ** years
    return (target - start * g) * period_rate(rate, per) / (g - 1.0)


def format_periods(whole: int, per: str, short: bool = False) -> str:
    """'22 years 3 months', or '22y 3m' for the metric box, which crops
    anything longer than a few characters."""
    if per == PER_MONTH:
        y, m = divmod(whole, 12)
        parts = []
        if y:
            parts.append(f"{y}y" if short else f"{y} year" + ("s" if y != 1 else ""))
        if m or not y:
            parts.append(f"{m}m" if short else f"{m} month" + ("s" if m != 1 else ""))
        return " ".join(parts)
    return f"{whole}y" if short else f"{whole} year" + ("s" if whole != 1 else "")


# ── refusals ─────────────────────────────────────────────────────────
# Each returns the reason as text, or None when the inputs are usable.
# The page prints the reason instead of a number.

def _check_start(start: float) -> str | None:
    return "Start amount must be above zero." if start <= 0 else None


def _check_years(years: float) -> str | None:
    if years < 1:
        return "Years must be at least 1."
    if years > MAX_YEARS:
        return f"Years must be {MAX_YEARS} or fewer."
    return None


def _check_rate_input(rate: float) -> str | None:
    return "Return must be above -100%." if rate <= -1.0 else None


def _check_contrib(contribution: float) -> str | None:
    if contribution < 0:
        return ("Contribution must be zero or more. A withdrawal plan can run the balance "
                "to zero, and then 'ending amount' means nothing — not in this version.")
    return None


def check_forward(start: float, rate: float, years: float, contribution: float) -> str | None:
    return (_check_start(start) or _check_years(years) or _check_rate_input(rate)
            or _check_contrib(contribution))


def check_rate(start: float, target: float, years: float, contribution: float = 0.0,
               per: str = PER_YEAR) -> str | None:
    bad = _check_start(start) or _check_years(years) or _check_contrib(contribution)
    if bad:
        return bad
    if target <= 0:
        return "Target amount must be above zero."
    if contribution == 0.0:
        return None
    floor = future_value(start, RATE_FLOOR, years, contribution, per)
    if target < floor:
        return (f"Target is below {floor:,.2f}, which is where {RATE_FLOOR:.2%}/yr leaves the "
                "balance — you cannot end below what you paid in last. No return gets there.")
    ceil = future_value(start, RATE_CEIL, years, contribution, per)
    if target > ceil:
        return f"Target needs more than {RATE_CEIL:.0%}/yr. Not computed."
    return None


def check_years(start: float, rate: float, target: float, contribution: float = 0.0,
                per: str = PER_YEAR) -> str | None:
    bad = _check_start(start) or _check_rate_input(rate) or _check_contrib(contribution)
    if bad:
        return bad
    if target <= start:
        return "Target must be above the start amount — there is nothing to reach."
    if rate < 0:
        return ("Return is below zero. With contributions the balance can stall below the "
                "target for ever; without them it never gets there. Not in this version.")
    if rate == 0 and contribution == 0:
        return "At 0% with no contribution the target is never reached."
    years = years_to_target(start, rate, target, contribution, per)
    if years > MAX_SOLVED_YEARS:
        return f"That takes {years:,.0f} years. Not printed beyond {MAX_SOLVED_YEARS:,}."
    return None


def check_contrib(start: float, rate: float, years: float, target: float,
                  per: str = PER_YEAR) -> str | None:
    bad = _check_start(start) or _check_years(years) or _check_rate_input(rate)
    if bad:
        return bad
    if target <= 0:
        return "Target amount must be above zero."
    alone = future_value(start, rate, years)
    if target <= alone:
        return (f"The start alone grows to {alone:,.2f} at {rate:.2%} over {years:g} years, "
                "which already meets the target. No contribution needed.")
    return None


# ── scenario rows ────────────────────────────────────────────────────
# A row holds the inputs AND the outputs, so the table can show them side
# by side and a re-load can be checked against what was saved. Every form
# fits the same columns: the target is the ending, the solved figure sits
# in its own column, and "Solved for" says which one it was.

def _row(name: str, solved: str, start: float, rate: float, years: float,
         contribution: float, per: str, ending: float) -> dict:
    put_in = total_put_in(start, years, contribution, per)
    return {"Name": name, "Solved for": solved, "Start": start, "Return %": rate * 100.0,
            "Years": years, "Contribution": contribution, "Per": per, "Ending": ending,
            "Put in": put_in, "Multiple": ending / put_in}


def forward_row(name: str, start: float, rate: float, years: int, contribution: float,
                per: str = PER_YEAR) -> dict:
    return _row(name, "ending", start, rate, years, contribution, per,
                future_value(start, rate, years, contribution, per))


def rate_row(name: str, start: float, target: float, years: int, contribution: float = 0.0,
             per: str = PER_YEAR) -> dict:
    return _row(name, "return", start, required_return(start, target, years, contribution, per),
                years, contribution, per, target)


def years_row(name: str, start: float, rate: float, target: float, contribution: float = 0.0,
              per: str = PER_YEAR) -> dict:
    return _row(name, "years", start, rate,
                years_to_target(start, rate, target, contribution, per),
                contribution, per, target)


def contrib_row(name: str, start: float, rate: float, years: int, target: float,
                per: str = PER_YEAR) -> dict:
    return _row(name, "contribution", start, rate, years,
                required_contribution(start, rate, years, target, per), per, target)


def recompute(row: dict) -> dict:
    """The same row rebuilt from its own inputs. A saved row must reproduce
    its outputs exactly when re-loaded; this is that check, without the UI."""
    s = row["Solved for"]
    if s == "return":
        return rate_row(row["Name"], row["Start"], row["Ending"], row["Years"],
                        row["Contribution"], row["Per"])
    if s == "years":
        return years_row(row["Name"], row["Start"], row["Return %"] / 100.0, row["Ending"],
                         row["Contribution"], row["Per"])
    if s == "contribution":
        return contrib_row(row["Name"], row["Start"], row["Return %"] / 100.0, row["Years"],
                           row["Ending"], row["Per"])
    return forward_row(row["Name"], row["Start"], row["Return %"] / 100.0,
                       row["Years"], row["Contribution"], row["Per"])


def rows_match(a: dict, b: dict) -> bool:
    """Money within half a cent, rates within a millionth of a percent,
    years within a millionth."""
    for k in COLUMNS:
        x, y = a[k], b[k]
        if isinstance(x, str):
            if x != y:
                return False
        elif k == "Return %":
            if abs(x - y) > 1e-6:
                return False
        elif k in ("Multiple", "Years"):
            if abs(x - y) > 1e-6:
                return False
        elif abs(x - y) > 0.005:
            return False
    return True


def default_name(form: str, start: float, rate: float, years: int,
                 contribution: float, target: float, per: str = PER_YEAR) -> str:
    """The name a scenario gets when the box is left blank."""
    unit = "mo" if per == PER_MONTH else "yr"
    plus = f" +{contribution:,.0f}/{unit}" if contribution else ""
    if form == FORM_RATE:
        return f"{start:,.0f} → {target:,.0f} in {years}y{plus}"
    if form == FORM_YEARS:
        return f"{start:,.0f} → {target:,.0f} @ {rate * 100:g}%{plus}"
    if form == FORM_CONTRIB:
        return f"{start:,.0f} → {target:,.0f} in {years}y @ {rate * 100:g}% per {unit}"
    return f"{start:,.0f} @ {rate * 100:g}% for {years}y{plus}"


def save_row(rows: list[dict], row: dict) -> tuple[list[dict], bool]:
    """Append, or replace the row with the same name. Returns (rows, replaced)."""
    for i, r in enumerate(rows):
        if r["Name"] == row["Name"]:
            out = list(rows)
            out[i] = row
            return out, True
    return rows + [row], False


def rows_to_csv(rows: list[dict]) -> str:
    """Full precision, so a future import reproduces the rows exactly."""
    return pd.DataFrame(rows, columns=COLUMNS).to_csv(index=False)


def csv_to_rows(text: str) -> list[dict]:
    df = pd.read_csv(io.StringIO(text))
    out = []
    for rec in df.to_dict("records"):
        y = float(rec["Years"])
        rec["Years"] = int(y) if y.is_integer() else y
        for k in ("Start", "Return %", "Contribution", "Ending", "Put in", "Multiple"):
            rec[k] = float(rec[k])
        out.append(rec)
    return out


def fmt_years(y: float) -> str:
    y = float(y)
    return f"{y:.0f}" if y.is_integer() else f"{y:.2f}"


# ══════════════════════════════════════════════════════════════════════
#  SELF-TEST
# ══════════════════════════════════════════════════════════════════════

def test_summary(results: list[tuple[str, bool, str]]) -> tuple[str, str]:
    """One line at the TOP of the expander: how many ran, how many failed.
    Same helper as the other pages; the count is printed by the page itself
    so it cannot drift from the source."""
    bad = [name for name, ok, _ in results if not ok]
    if not bad:
        return "success", f"**{len(results)} checks, 0 failed.**"
    return "error", (f"**{len(results)} checks, {len(bad)} FAILED:** "
                     + "; ".join(bad[:4])
                     + (f" — and {len(bad) - 4} more" if len(bad) > 4 else ""))


def self_test() -> list[tuple[str, bool, str]]:
    out = []
    cent = 0.005

    # ── ending amount ────────────────────────────────────────────────
    v = future_value(10_000, 0.10, 10)
    out.append(("10,000 @ 10% × 10y = 25,937.42", abs(v - 25_937.42) < cent, f"{v:,.2f}"))
    v = future_value(0, 0.10, 10, 1_000)
    out.append(("1,000/yr @ 10% × 10y = 15,937.42", abs(v - 15_937.42) < cent, f"{v:,.2f}"))
    row = forward_row("t", 10_000, 0.10, 10, 1_000)
    out.append(("10,000 + 1,000/yr @ 10% × 10y = 41,874.85",
                abs(row["Ending"] - 41_874.85) < cent, f"{row['Ending']:,.2f}"))
    out.append(("… put in 20,000, multiple 2.0937×",
                abs(row["Put in"] - 20_000) < cent and abs(row["Multiple"] - 2.0937) < 5e-5,
                f"{row['Put in']:,.2f} / {row['Multiple']:.4f}×"))
    v = future_value(10_000, 0.0, 10, 1_000)
    out.append(("0% with 1,000/yr × 10y = 20,000.00", abs(v - 20_000) < cent, f"{v:,.2f}"))
    v = future_value(100, 0.05, 1)
    out.append(("100 @ 5% × 1y = 105.00", abs(v - 105) < cent, f"{v:,.2f}"))
    v = future_value(10_000, -0.10, 2)
    out.append(("10,000 @ −10% × 2y = 8,100.00", abs(v - 8_100) < cent, f"{v:,.2f}"))

    # monthly contributions compound at the effective monthly rate
    v = future_value(0, 0.10, 10, 1_000, PER_MONTH)
    out.append(("1,000/mo @ 10% × 10y = 199,863.86", abs(v - 199_863.86) < cent, f"{v:,.2f}"))
    yr_end = future_value(0, 0.10, 10, 12_000)
    yr_start = yr_end * 1.10
    out.append(("… between 12,000/yr paid at year-end and at year-start",
                yr_end < v < yr_start, f"{yr_end:,.2f} < {v:,.2f} < {yr_start:,.2f}"))
    nominal = 1_000 * ((1 + 0.10 / 12) ** 120 - 1) / (0.10 / 12)
    out.append(("… and is not the nominal r/12 figure", abs(v - nominal) > 1_000,
                f"nominal would be {nominal:,.2f}"))
    row = forward_row("t", 10_000, 0.10, 10, 1_000, PER_MONTH)
    out.append(("10,000 + 1,000/mo @ 10% × 10y = 225,801.28, put in 130,000",
                abs(row["Ending"] - 225_801.28) < cent and abs(row["Put in"] - 130_000) < cent,
                f"{row['Ending']:,.2f} / {row['Put in']:,.2f}"))
    v = future_value(0, 0.0, 10, 1_000, PER_MONTH)
    out.append(("0% with 1,000/mo × 10y = 120,000.00", abs(v - 120_000) < cent, f"{v:,.2f}"))
    v = future_value(10_000, 0.10, 10, 0, PER_MONTH)
    out.append(("Start alone is unchanged by the monthly setting",
                abs(v - 25_937.42) < cent, f"{v:,.2f}"))

    # ── required return ──────────────────────────────────────────────
    r = required_return(1, 100, 20)
    out.append(("100× in 20y needs 25.89%", abs(r - 0.258925) < 5e-7, f"{r:.4%}"))
    r = required_return(10_000, 25_937.42, 10)
    out.append(("Reverse of 25,937.42 → 10.00%", abs(r - 0.10) < 1e-7, f"{r:.4%}"))
    r = required_return(100, 50, 1)
    out.append(("100 → 50 in 1y = −50.00%", abs(r + 0.5) < 1e-9 and check_rate(100, 50, 1) is None,
                f"{r:.2%}"))
    r = required_return(10_000, 41_874.85, 10, 1_000)
    out.append(("10,000 + 1,000/yr → 41,874.85 in 10y needs 10.00%",
                abs(r - 0.10) < 1e-7, f"{r:.4%}"))
    r = required_return(10_000, 225_801.28, 10, 1_000, PER_MONTH)
    out.append(("10,000 + 1,000/mo → 225,801.28 in 10y needs 10.00%",
                abs(r - 0.10) < 1e-7, f"{r:.4%}"))
    r = required_return(5_000, 200_000, 15, 300, PER_MONTH)
    back = future_value(5_000, r, 15, 300, PER_MONTH)
    out.append(("5,000 + 300/mo → 200,000 in 15y: the rate found (13.82%) hits the target",
                abs(r - 0.138196) < 5e-7 and abs(back - 200_000) < cent, f"{r:.4%} → {back:,.2f}"))
    r1, r2 = required_return(10_000, 25_937.42, 10, 0), required_return(10_000, 25_937.42, 10, 1e-12)
    out.append(("Bisection agrees with the closed form when the contribution vanishes",
                abs(r1 - r2) < 1e-7, f"{r1:.6%} vs {r2:.6%}"))

    # ── years to target ──────────────────────────────────────────────
    # targets below are rounded to the cent, which moves the answer by ~2e-5 years
    y = years_to_target(10_000, 0.10, 25_937.42)
    out.append(("10,000 → 25,937.42 @ 10% takes 10.00y", abs(y - 10) < 1e-4, f"{y:.4f}"))
    y = years_to_target(1, 100 ** 0.05 - 1, 100)
    out.append(("1 → 100 @ 25.8925% takes 20.00y", abs(y - 20) < 1e-9, f"{y:.4f}"))
    y = years_to_target(0, 0.10, 199_863.86, 1_000, PER_MONTH)
    out.append(("0 + 1,000/mo → 199,863.86 @ 10% takes 10.00y", abs(y - 10) < 1e-4, f"{y:.4f}"))
    y = years_to_target(1, 0.08, 2)
    out.append(("Doubling at 8% takes 9.01y (rule of 72 says 9)", abs(y - 9.0065) < 5e-4, f"{y:.4f}"))
    y = years_to_target(0, 0.0, 12_000, 1_000, PER_MONTH)
    out.append(("0% branch: 1,000/mo → 12,000 takes exactly 1.00y", abs(y - 1) < 1e-9, f"{y:.4f}"))
    w, bal = reached_after(10_000, 0.10, 25_000, 0, PER_YEAR)
    out.append(("10,000 → 25,000 @ 10%: 9.61y, reached after 10 years with 25,937.42",
                w == 10 and abs(bal - 25_937.42) < cent,
                f"{years_to_target(10_000, 0.10, 25_000):.2f}y / {format_periods(w, PER_YEAR)} / {bal:,.2f}"))
    w, bal = reached_after(0, 0.10, 199_000, 1_000, PER_MONTH)
    before = future_value(0, 0.10, (w - 1) / 12, 1_000, PER_MONTH)
    out.append(("0 + 1,000/mo → 199,000: reached after 10 years (120 months), the month before falls short",
                w == 120 and bal >= 199_000 and before < 199_000,
                f"{format_periods(w, PER_MONTH)} / {bal:,.2f} (month {w - 1}: {before:,.2f})"))
    out.append(("Whole-period wording", format_periods(1, PER_YEAR) == "1 year"
                and format_periods(25, PER_YEAR) == "25 years"
                and format_periods(290, PER_MONTH) == "24 years 2 months"
                and format_periods(5, PER_MONTH) == "5 months"
                and format_periods(24, PER_MONTH) == "2 years"
                and format_periods(290, PER_MONTH, short=True) == "24y 2m"
                and format_periods(24, PER_MONTH, short=True) == "2y"
                and format_periods(25, PER_YEAR, short=True) == "25y",
                format_periods(290, PER_MONTH) + " / " + format_periods(290, PER_MONTH, short=True)))

    # ── required contribution ────────────────────────────────────────
    c = required_contribution(10_000, 0.10, 10, 41_874.85)
    out.append(("10,000 @ 10% × 10y → 41,874.85 needs 1,000.00/yr", abs(c - 1_000) < cent, f"{c:,.2f}"))
    c = required_contribution(10_000, 0.10, 10, 225_801.28, PER_MONTH)
    out.append(("10,000 @ 10% × 10y → 225,801.28 needs 1,000.00/mo", abs(c - 1_000) < cent, f"{c:,.2f}"))
    c = required_contribution(0, 0.07, 30, 1_000_000, PER_MONTH)
    out.append(("0 → 1,000,000 in 30y @ 7% needs 855.10/mo", abs(c - 855.10) < cent, f"{c:,.2f}"))
    c = required_contribution(0, 0.0, 1, 12_000, PER_MONTH)
    out.append(("0% branch: 12,000 in 1y needs 1,000.00/mo", abs(c - 1_000) < cent, f"{c:,.2f}"))

    # ── refusals fire, and only when they should ─────────────────────
    refused = [check_forward(0, 0.1, 10, 0), check_forward(1, 0.1, 0, 0),
               check_rate(1, 0, 10), check_forward(1, -1.0, 10, 0),
               check_forward(1, 0.1, 10, -1), check_forward(1, 0.1, MAX_YEARS + 1, 0)]
    allowed = [check_forward(1, 0.1, 1, 0), check_forward(1, -0.999, MAX_YEARS, 0),
               check_rate(1, 1, 1)]
    out.append(("Refuses start 0, years 0, target 0, −100%, contribution −1, 101y",
                all(refused), f"{sum(bool(x) for x in refused)} of 6 refused"))
    out.append(("Accepts the edges: 1y, −99.9%, 100y, target = start",
                not any(allowed), f"{sum(bool(x) for x in allowed)} of 3 refused"))
    refused = [check_rate(10_000, 1_000, 10, 1_000, PER_MONTH),      # below the floor (1,866.22)
               check_rate(10_000, 1e40, 10, 1_000)]                   # above 1,000%/yr
    allowed = [check_rate(10_000, 2_000, 10, 1_000, PER_MONTH), check_rate(10_000, 1e12, 10, 1_000)]
    out.append(("Required return refuses targets below the floor and above 1,000%/yr",
                all(refused) and not any(allowed) and "1,866.22" in refused[0],
                f"{sum(bool(x) for x in refused)} of 2 refused"))
    refused = [check_years(10_000, 0.10, 10_000), check_years(10_000, -0.01, 20_000, 1_000),
               check_years(10_000, 0.0, 20_000), check_years(1, 0.00001, 1e9)]
    allowed = [check_years(10_000, 0.0, 20_000, 1_000), check_years(10_000, 0.10, 10_001)]
    out.append(("Years refuses target ≤ start, negative return, 0% alone, > 1,000 years",
                all(refused) and not any(allowed), f"{sum(bool(x) for x in refused)} of 4 refused"))
    refused = [check_contrib(10_000, 0.10, 10, 20_000), check_contrib(10_000, 0.10, 10, 0)]
    allowed = [check_contrib(10_000, 0.10, 10, 25_937.43)]
    out.append(("Contribution refuses a target the start alone meets, naming 25,937.42",
                all(refused) and not any(allowed) and "25,937.42" in refused[0],
                f"{sum(bool(x) for x in refused)} of 2 refused"))

    # ── rows: every form round-trips from its own inputs ─────────────
    fwd = forward_row("f", 12_345.67, 0.0725, 17, 250)
    mon = forward_row("m", 12_345.67, 0.0725, 17, 250, PER_MONTH)
    rev = rate_row("r", 1_000, 100_000, 20)
    revc = rate_row("rc", 5_000, 200_000, 15, 300, PER_MONTH)
    yrs = years_row("y", 10_000, 0.10, 25_000, 1_000, PER_MONTH)
    con = contrib_row("c", 0, 0.07, 30, 1_000_000, PER_MONTH)
    allrows = [fwd, mon, rev, revc, yrs, con]
    out.append(("Forward, monthly, return, return+contribution, years, contribution rows round-trip",
                all(rows_match(r, recompute(r)) for r in allrows),
                f"{sum(rows_match(r, recompute(r)) for r in allrows)} of {len(allrows)}"))
    back = future_value(rev["Start"], rev["Return %"] / 100.0, rev["Years"])
    out.append(("A return row's rate run forward hits its target",
                abs(back - rev["Ending"]) < cent, f"{back:,.2f}"))
    back = future_value(yrs["Start"], yrs["Return %"] / 100.0, yrs["Years"], yrs["Contribution"], yrs["Per"])
    out.append(("A years row's exact years run forward hit its target",
                abs(back - yrs["Ending"]) < cent, f"{yrs['Years']:.4f}y → {back:,.2f}"))
    back = future_value(con["Start"], con["Return %"] / 100.0, con["Years"], con["Contribution"], con["Per"])
    out.append(("A contribution row's amount run forward hits its target",
                abs(back - con["Ending"]) < cent, f"{con['Contribution']:,.2f}/mo → {back:,.2f}"))

    rows, rep1 = save_row([], fwd)
    rows, rep2 = save_row(rows, rev)
    rows, rep3 = save_row(rows, forward_row("f", 1, 0.01, 1, 0))
    out.append(("Save appends new names and replaces an existing one",
                (rep1, rep2, rep3) == (False, False, True) and len(rows) == 2
                and rows[0]["Start"] == 1, f"{len(rows)} rows, replaced={rep3}"))
    parsed = csv_to_rows(rows_to_csv(allrows))
    out.append(("CSV export → parse gives the same rows, fractional years included",
                len(parsed) == len(allrows)
                and all(rows_match(a, b) for a, b in zip(allrows, parsed))
                and parsed[0]["Years"] == 17 and isinstance(parsed[0]["Years"], int),
                f"{len(parsed)} rows"))
    out.append(("Years prints whole or to two decimals",
                fmt_years(10) == "10" and fmt_years(10.0) == "10" and fmt_years(9.61377) == "9.61",
                fmt_years(yrs["Years"])))
    out.append(("Default names read as expected",
                default_name(FORM_FORWARD, 10_000, 0.10, 10, 0, 0) == "10,000 @ 10% for 10y"
                and default_name(FORM_FORWARD, 10_000, 0.10, 10, 1_000, 0, PER_MONTH)
                == "10,000 @ 10% for 10y +1,000/mo"
                and default_name(FORM_RATE, 10_000, 0, 20, 0, 1_000_000)
                == "10,000 → 1,000,000 in 20y"
                and default_name(FORM_RATE, 10_000, 0, 20, 500, 1_000_000, PER_MONTH)
                == "10,000 → 1,000,000 in 20y +500/mo"
                and default_name(FORM_YEARS, 10_000, 0.10, 0, 0, 1_000_000)
                == "10,000 → 1,000,000 @ 10%"
                and default_name(FORM_CONTRIB, 0, 0.07, 30, 0, 1_000_000, PER_MONTH)
                == "0 → 1,000,000 in 30y @ 7% per mo",
                default_name(FORM_CONTRIB, 0, 0.07, 30, 0, 1_000_000, PER_MONTH)))
    return out


# ══════════════════════════════════════════════════════════════════════
#  UI
# ══════════════════════════════════════════════════════════════════════
#
# NOTE ON DOLLAR SIGNS: Streamlit markdown parses $...$ as LaTeX, so no
# literal dollar signs in st.write/markdown/success/error/info/warning.
# Nothing on this page needs one — amounts print as plain numbers.

st.set_page_config(
    page_title="Return Calculator — Tragic Algebra Analyzer",
    page_icon="📈",
    layout="centered",
    initial_sidebar_state="collapsed",
)
st.title("📈 Return Calculator")
st.caption("Compound returns: ending amount, required return, years to target, "
           "required contribution — with a table of saved scenarios")

# INPUT STORE. Streamlit drops the state of any keyed widget that is not
# drawn in a run, and switching forms hides some of the inputs — the first
# version read 0 after a switch (Chen, 30 Aug 2026). So the inputs live in
# one dict of their own, `vals`, that the widgets read their value from and
# write back to on change. Load writes `vals`. Nothing the page needs is
# ever stored only inside a hidden widget.
_DEFAULTS = {"start": 10_000.0, "rate": 10.0, "years": 10,
             "contrib": 0.0, "per": PER_YEAR, "target": 1_000_000.0}
st.session_state.setdefault("vals", dict(_DEFAULTS))
st.session_state.setdefault("form", FORM_FORWARD)
st.session_state.setdefault("gen", 0)
if "scenarios" not in st.session_state:
    st.session_state["scenarios"] = []
vals = st.session_state["vals"]


def _keep(k: str, wkey: str) -> None:
    """on_change: copy the widget's value into the store."""
    st.session_state["vals"][k] = st.session_state[wkey]


def _wkey(k: str) -> str:
    """Widget key for input k. The generation number is part of it, so a
    Load — which bumps the generation — gives every box a new identity.
    With a fixed key the browser keeps the box's typed value as local state
    and a new `value=` from the server never lands (Chen, 31 Aug 2026: the
    return box stayed on 7 after loading a 10% row). New identity, new box."""
    return f"w_{k}_{st.session_state['gen']}"


# A re-load writes the store before the widgets are drawn — Streamlit refuses
# to change a widget's value after it exists in the same run.
if "pending_load" in st.session_state:
    _ld = st.session_state.pop("pending_load")   # not `_row`: that is the helper above
    _s = _ld["Solved for"]
    st.session_state["form"] = FORM_OF[_s]
    vals["start"] = float(_ld["Start"])
    vals["per"] = _ld["Per"]
    if _s != "years":
        vals["years"] = int(round(_ld["Years"]))
    if _s != "return":
        vals["rate"] = float(_ld["Return %"])
    if _s != "contribution":
        vals["contrib"] = float(_ld["Contribution"])
    if _s != "ending":
        vals["target"] = float(_ld["Ending"])
    st.session_state["loaded_name"] = _ld["Name"]
    st.session_state["gen"] += 1   # see _wkey

form = st.radio("Solve for", FORMS, horizontal=True, key="form")


def _num(label: str, k: str, step: float, **kw):
    return st.number_input(label, value=vals[k], step=step, key=_wkey(k),
                           on_change=_keep, args=(k, _wkey(k)), **kw)


# Which inputs each form shows. The solved quantity is the one missing.
_c1, _c2, _c3 = st.columns(3)
with _c1:
    start = _num("Start amount", "start", 1_000.0, format="%.2f")
with _c2:
    if form in (FORM_FORWARD, FORM_YEARS, FORM_CONTRIB):
        rate_pct = _num("Yearly return %", "rate", 0.5, format="%.2f")
    else:
        target = _num("Target amount", "target", 1_000.0, format="%.2f")
with _c3:
    if form == FORM_YEARS:
        target = _num("Target amount", "target", 1_000.0, format="%.2f")
    else:
        years = _num("Years", "years", 1, min_value=0, max_value=MAX_YEARS + 1)
_k1, _k2 = st.columns([2, 1])
with _k1:
    if form == FORM_CONTRIB:
        target = _num("Target amount", "target", 1_000.0, format="%.2f")
    else:
        contrib = _num("Contribution (optional)", "contrib", 100.0, format="%.2f",
                       help="Paid at the end of each year or month.")
with _k2:
    per = st.radio("Contribution paid", [PER_YEAR, PER_MONTH],
                   index=[PER_YEAR, PER_MONTH].index(vals["per"]),
                   horizontal=True, key=_wkey("per"), on_change=_keep, args=("per", _wkey("per")))

_loaded = st.session_state.pop("loaded_name", None)
if _loaded:
    st.info(f"Loaded **{_loaded}** into the inputs.")

# ── compute, or refuse ───────────────────────────────────────────────
row = None
if form == FORM_FORWARD:
    refusal = check_forward(start, rate_pct / 100.0, int(years), contrib)
    if not refusal:
        row = forward_row("", start, rate_pct / 100.0, int(years), contrib, per)
        _m = st.columns(4)
        _m[0].metric("Ending amount", f"{row['Ending']:,.2f}")
        _m[1].metric("Put in", f"{row['Put in']:,.2f}")
        _m[2].metric("Gain", f"{row['Ending'] - row['Put in']:,.2f}")
        _m[3].metric("Multiple", f"{row['Multiple']:,.2f}×")
elif form == FORM_RATE:
    refusal = check_rate(start, target, int(years), contrib, per)
    if not refusal:
        row = rate_row("", start, target, int(years), contrib, per)
        _m = st.columns(3)
        _m[0].metric("Required return", f"{row['Return %']:.2f}% / yr")
        _m[1].metric("Put in", f"{row['Put in']:,.2f}")
        _m[2].metric("Multiple", f"{row['Multiple']:,.2f}×")
elif form == FORM_YEARS:
    refusal = check_years(start, rate_pct / 100.0, target, contrib, per)
    if not refusal:
        row = years_row("", start, rate_pct / 100.0, target, contrib, per)
        _whole, _bal = reached_after(start, rate_pct / 100.0, target, contrib, per)
        _m = st.columns(3)
        _m[0].metric("Years needed", f"{row['Years']:.2f}")
        _m[1].metric("Reached after", format_periods(_whole, per, short=True))
        _m[2].metric("Balance then", f"{_bal:,.2f}")
        st.caption(f"Reached after {format_periods(_whole, per)} — the first whole "
                   f"{'month' if per == PER_MONTH else 'year'} at or above the target — "
                   f"with a balance of {_bal:,.2f}.")
else:
    refusal = check_contrib(start, rate_pct / 100.0, int(years), target, per)
    if not refusal:
        row = contrib_row("", start, rate_pct / 100.0, int(years), target, per)
        _m = st.columns(3)
        _m[0].metric("Required contribution",
                     f"{row['Contribution']:,.2f} / {'mo' if per == PER_MONTH else 'yr'}")
        _m[1].metric("Put in", f"{row['Put in']:,.2f}")
        _m[2].metric("Multiple", f"{row['Multiple']:,.2f}×")
if refusal:
    st.error(refusal)

with st.expander("Assumptions used"):
    _lines = [f"solve for           {form}",
              f"start               {start:,.2f}"]
    if form != FORM_RATE:
        _lines.append(f"return              {rate_pct:.2f}% per year, compounded yearly")
    if form != FORM_YEARS:
        _lines.append(f"years               {int(years)}")
    if form != FORM_FORWARD:
        _lines.append(f"target              {target:,.2f}")
    if form != FORM_CONTRIB:
        _lines.append(f"contribution        {contrib:,.2f} per {'month' if per == PER_MONTH else 'year'}")
    _lines.append(f"contribution timing paid at the END of each {'month' if per == PER_MONTH else 'year'}")
    if per == PER_MONTH and form != FORM_RATE:
        _lines.append("monthly rate        the rate that compounds to the yearly return, "
                      f"{period_rate(rate_pct / 100.0, PER_MONTH):.4%}/mo — not return ÷ 12")
    elif per == PER_MONTH:
        _lines.append("monthly rate        the rate that compounds to the yearly return — not return ÷ 12")
    if form == FORM_RATE and contrib:
        _lines.append("method              bisection on the return (no closed form with contributions)")
    if form == FORM_YEARS:
        _lines.append("years needed        exact; 'reached after' is the first whole period at or above target")
    _lines.append("multiple            ending ÷ total put in (start + every contribution)")
    _lines.append("inflation, tax      not applied")
    st.code("\n".join(_lines), language="text")

# ── scenarios ────────────────────────────────────────────────────────
st.markdown("---")
st.write("**Saved scenarios**")
st.caption("Rows last while this tab is open. Download the CSV to keep them.")

_auto = default_name(form, start,
                     (rate_pct / 100.0) if form != FORM_RATE else 0.0,
                     int(years) if form != FORM_YEARS else 0,
                     contrib if form != FORM_CONTRIB else 0.0,
                     target if form != FORM_FORWARD else 0.0, per)
_n1, _n2 = st.columns([3, 1])
with _n1:
    name = st.text_input("Name", placeholder=_auto, label_visibility="collapsed")
with _n2:
    if st.button("Save", disabled=row is None, width="stretch"):
        row["Name"] = name.strip() or _auto
        st.session_state["scenarios"], _replaced = save_row(st.session_state["scenarios"], row)
        st.session_state["save_msg"] = (("Replaced" if _replaced else "Saved")
                                        + f" **{row['Name']}**.")
        st.rerun()
if "save_msg" in st.session_state:
    st.success(st.session_state.pop("save_msg"))

rows = st.session_state["scenarios"]
if rows:
    st.dataframe(pd.DataFrame(rows, columns=COLUMNS).style.format({
        "Start": "{:,.2f}", "Return %": "{:.2f}%", "Years": fmt_years, "Contribution": "{:,.2f}",
        "Ending": "{:,.2f}", "Put in": "{:,.2f}", "Multiple": "{:,.2f}×"}),
        width="stretch", hide_index=True)
    _s1, _s2, _s3, _s4 = st.columns([3, 1, 1, 1])
    with _s1:
        pick = st.selectbox("Scenario", [r["Name"] for r in rows], label_visibility="collapsed")
    with _s2:
        if st.button("Load", width="stretch"):
            st.session_state["pending_load"] = next(r for r in rows if r["Name"] == pick)
            st.rerun()
    with _s3:
        if st.button("Delete", width="stretch"):
            st.session_state["scenarios"] = [r for r in rows if r["Name"] != pick]
            st.rerun()
    with _s4:
        if st.button("Clear all", width="stretch"):
            st.session_state["scenarios"] = []
            st.rerun()
    st.download_button("Download CSV", rows_to_csv(rows), file_name="return_scenarios.csv",
                       mime="text/csv")
else:
    st.caption("Nothing saved yet.")

# ══════════════════════════════════════════════════════════════════════
#  REFERENCE — at the foot of the page, as on the other pages
# ══════════════════════════════════════════════════════════════════════

st.divider()
_r1, _r2 = st.columns(2)
with _r1:
    with st.expander("What the numbers mean", expanded=False):
        st.markdown(
            "**Ending amount** — the start compounded yearly at the return, plus each "
            "contribution compounded from the end of the year or month it was paid. A monthly "
            "contribution grows at the monthly rate equivalent to the yearly return, not at "
            "one twelfth of it.\n\n"
            "**Put in** — start plus every contribution. What you actually handed over.\n\n"
            "**Multiple** — ending divided by put in. With no contribution it is the plain "
            "multiple of the start; with one it is the multiple of everything you put in, "
            "which is the honest figure.\n\n"
            "**Required return** — the single yearly return that turns the start (plus "
            "contributions) into the target in the years given. 100× in 20 years needs 25.89% "
            "a year. With contributions there is no formula, so the page searches for the rate "
            "and checks it lands on the target.\n\n"
            "**Years to target** — the exact, fractional time until the balance equals the "
            "target. Because contributions land at the end of each period, **reached after** "
            "gives the first whole year or month at or above the target and the balance then. "
            "A saved years row keeps the exact figure; its put-in counts the exact fraction "
            "of a contribution.\n\n"
            "**Required contribution** — the amount per year or month that reaches the target "
            "on top of what the start alone does."
        )
with _r2:
    with st.expander("Verify the engine"):
        st.caption("Arithmetic checks on known answers, refusals and the save/load round trip.")
        if st.button("Run checks"):
            _results = self_test()
            _sev, _line = test_summary(_results)
            getattr(st, _sev)(_line)
            for _nm, _ok, _got in _results:
                st.write(("✅ " if _ok else "❌ ") + f"{_nm} — {_got}")
            st.caption("Tolerances: money within half a cent, rates within a millionth "
                       "of a percent.")

st.caption("Research aid, not financial advice. Compounding arithmetic only; no inflation, "
           "tax or fees.")
