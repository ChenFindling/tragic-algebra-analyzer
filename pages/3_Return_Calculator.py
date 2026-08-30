"""
Return Calculator
=================
The ordinary compound-return calculator, kept inside the tool kit so the
other pages' user does not have to leave the app for it. Reads nothing,
fetches nothing, imports nothing from the other pages.

Two forms, one table:

    Ending amount     start, return, years, optional yearly contribution
                      -> ending amount, total put in, gain, multiple
    Required return   start, target, years -> the yearly return that gets there

    ending  = start x (1+r)^n  +  C x ((1+r)^n - 1) / r      C paid at END of year
    ending  = start + n x C                                    when r = 0
    r       = (target / start)^(1/n) - 1
    multiple = ending / (start + n x C)

Known answers: 10,000 at 10% for 10 years -> 25,937.42.
100x in 20 years -> 25.89%/yr (10^0.1 - 1).

Saved scenarios live in session state: they survive re-runs while the tab
is open and are gone on browser refresh. The CSV is the persistence.

Run:  streamlit run pages/3_Return_Calculator.py
"""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

# ══════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════

MAX_YEARS = 100

FORM_FORWARD = "Ending amount"
FORM_REVERSE = "Required return"

# Column order for the scenario table and the CSV. Rates are stored as
# fractions internally and shown as percentages; the CSV column says so.
COLUMNS = ["Name", "Solved for", "Start", "Return %", "Years",
           "Contribution", "Ending", "Put in", "Multiple"]


# ══════════════════════════════════════════════════════════════════════
#  ARITHMETIC
# ══════════════════════════════════════════════════════════════════════

def future_value(start: float, rate: float, years: int, contribution: float = 0.0) -> float:
    """Ending amount. The contribution is paid at the end of each year, so
    the last one earns nothing and the first earns years-1 periods."""
    if rate == 0.0:
        # The annuity factor divides by the rate. At exactly zero the
        # answer is the plain sum, not a division by zero.
        return start + years * contribution
    g = (1.0 + rate) ** years
    return start * g + contribution * (g - 1.0) / rate


def required_return(start: float, target: float, years: int) -> float:
    """The yearly return that turns start into target in years. Negative
    when the target is below the start — a real answer, not an error."""
    return (target / start) ** (1.0 / years) - 1.0


def total_put_in(start: float, years: int, contribution: float) -> float:
    return start + years * contribution


# ── refusals ─────────────────────────────────────────────────────────
# Each returns the reason as text, or None when the inputs are usable.
# The page prints the reason instead of a number.

def check_common(start: float, years: int) -> str | None:
    if start <= 0:
        return "Start amount must be above zero."
    if years < 1:
        return "Years must be at least 1."
    if years > MAX_YEARS:
        return f"Years must be {MAX_YEARS} or fewer."
    return None


def check_forward(start: float, rate: float, years: int, contribution: float) -> str | None:
    bad = check_common(start, years)
    if bad:
        return bad
    if rate <= -1.0:
        return "Return must be above -100%."
    if contribution < 0:
        return ("Contribution must be zero or more. A withdrawal plan can run the balance "
                "to zero, and then 'ending amount' means nothing — not in this version.")
    return None


def check_reverse(start: float, target: float, years: int) -> str | None:
    bad = check_common(start, years)
    if bad:
        return bad
    if target <= 0:
        return "Target amount must be above zero."
    return None


# ── scenario rows ────────────────────────────────────────────────────
# A row holds the inputs AND the outputs, so the table can show them side
# by side and a re-load can be checked against what was saved. Reverse
# rows fit the same columns: the target is the ending, the solved rate is
# the return, contribution is zero.

def forward_row(name: str, start: float, rate: float, years: int, contribution: float) -> dict:
    ending = future_value(start, rate, years, contribution)
    put_in = total_put_in(start, years, contribution)
    return {"Name": name, "Solved for": "ending", "Start": start, "Return %": rate * 100.0,
            "Years": years, "Contribution": contribution, "Ending": ending,
            "Put in": put_in, "Multiple": ending / put_in}


def reverse_row(name: str, start: float, target: float, years: int) -> dict:
    rate = required_return(start, target, years)
    return {"Name": name, "Solved for": "return", "Start": start, "Return %": rate * 100.0,
            "Years": years, "Contribution": 0.0, "Ending": target,
            "Put in": start, "Multiple": target / start}


def recompute(row: dict) -> dict:
    """The same row rebuilt from its own inputs. A saved row must reproduce
    its outputs exactly when re-loaded; this is that check, without the UI."""
    if row["Solved for"] == "return":
        return reverse_row(row["Name"], row["Start"], row["Ending"], int(row["Years"]))
    return forward_row(row["Name"], row["Start"], row["Return %"] / 100.0,
                       int(row["Years"]), row["Contribution"])


def rows_match(a: dict, b: dict) -> bool:
    """Money within half a cent, rates within a millionth of a percent."""
    for k in COLUMNS:
        x, y = a[k], b[k]
        if isinstance(x, str):
            if x != y:
                return False
        elif k == "Return %":
            if abs(x - y) > 1e-6:
                return False
        elif k == "Multiple":
            if abs(x - y) > 1e-9:
                return False
        elif abs(x - y) > 0.005:
            return False
    return True


def default_name(form: str, start: float, rate: float, years: int,
                 contribution: float, target: float) -> str:
    """The name a scenario gets when the box is left blank."""
    if form == FORM_REVERSE:
        return f"{start:,.0f} → {target:,.0f} in {years}y"
    s = f"{start:,.0f} @ {rate * 100:g}% for {years}y"
    if contribution:
        s += f" +{contribution:,.0f}/yr"
    return s


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
        rec["Years"] = int(rec["Years"])
        for k in ("Start", "Return %", "Contribution", "Ending", "Put in", "Multiple"):
            rec[k] = float(rec[k])
        out.append(rec)
    return out


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

    # 1. the known answer
    v = future_value(10_000, 0.10, 10)
    out.append(("10,000 @ 10% × 10y = 25,937.42", abs(v - 25_937.42) < cent, f"{v:,.2f}"))

    # 2. the other known answer
    r = required_return(1, 100, 20)
    out.append(("100× in 20y needs 25.89%", abs(r - 0.258925) < 5e-7, f"{r:.4%}"))

    # 3. reverse of check 1 lands on 10.00% exactly
    r = required_return(10_000, 25_937.42, 10)
    out.append(("Reverse of 25,937.42 → 10.00%", abs(r - 0.10) < 1e-7, f"{r:.4%}"))

    # 4. contribution alone: annuity factor (1.1^10 − 1)/0.1 = 15.9374
    v = future_value(0, 0.10, 10, 1_000)
    out.append(("1,000/yr @ 10% × 10y = 15,937.42", abs(v - 15_937.42) < cent, f"{v:,.2f}"))

    # 5. start plus contribution, and the multiple is against total put in
    row = forward_row("t", 10_000, 0.10, 10, 1_000)
    out.append(("10,000 + 1,000/yr @ 10% × 10y = 41,874.85",
                abs(row["Ending"] - 41_874.85) < cent, f"{row['Ending']:,.2f}"))
    out.append(("… put in 20,000, multiple 2.0937×",
                abs(row["Put in"] - 20_000) < cent and abs(row["Multiple"] - 2.0937) < 5e-5,
                f"{row['Put in']:,.2f} / {row['Multiple']:.4f}×"))

    # 6. the zero-rate branch
    v = future_value(10_000, 0.0, 10, 1_000)
    out.append(("0% with 1,000/yr × 10y = 20,000.00", abs(v - 20_000) < cent, f"{v:,.2f}"))

    # 7. one year
    v = future_value(100, 0.05, 1)
    out.append(("100 @ 5% × 1y = 105.00", abs(v - 105) < cent, f"{v:,.2f}"))

    # 8. negative return
    v = future_value(10_000, -0.10, 2)
    out.append(("10,000 @ −10% × 2y = 8,100.00", abs(v - 8_100) < cent, f"{v:,.2f}"))

    # 9. target below start prints a negative return, not a refusal
    r = required_return(100, 50, 1)
    out.append(("100 → 50 in 1y = −50.00%", abs(r + 0.5) < 1e-9 and check_reverse(100, 50, 1) is None,
                f"{r:.2%}"))

    # 10. refusals fire, and only when they should
    refused = [check_forward(0, 0.1, 10, 0), check_forward(1, 0.1, 0, 0),
               check_reverse(1, 0, 10), check_forward(1, -1.0, 10, 0),
               check_forward(1, 0.1, 10, -1), check_forward(1, 0.1, MAX_YEARS + 1, 0)]
    allowed = [check_forward(1, 0.1, 1, 0), check_forward(1, -0.999, MAX_YEARS, 0),
               check_reverse(1, 1, 1)]
    out.append(("Refuses start 0, years 0, target 0, −100%, contribution −1, 101y",
                all(refused), f"{sum(bool(x) for x in refused)} of 6 refused"))
    out.append(("Accepts the edges: 1y, −99.9%, 100y, target = start",
                not any(allowed), f"{sum(bool(x) for x in allowed)} of 3 refused"))

    # 11. a saved row reproduces itself from its own inputs
    fwd = forward_row("f", 12_345.67, 0.0725, 17, 250)
    rev = reverse_row("r", 1_000, 100_000, 20)
    out.append(("Forward row round-trips exactly", rows_match(fwd, recompute(fwd)),
                f"{fwd['Ending']:,.2f}"))
    out.append(("Reverse row round-trips exactly", rows_match(rev, recompute(rev)),
                f"{rev['Return %']:.4f}%"))
    # and a reverse row's solved rate, run forward, lands on its own target
    back = future_value(rev["Start"], rev["Return %"] / 100.0, rev["Years"])
    out.append(("Reverse row's rate run forward hits the target",
                abs(back - rev["Ending"]) < cent, f"{back:,.2f}"))

    # 12. save replaces by name; CSV round-trips
    rows, rep1 = save_row([], fwd)
    rows, rep2 = save_row(rows, rev)
    rows, rep3 = save_row(rows, forward_row("f", 1, 0.01, 1, 0))
    out.append(("Save appends new names and replaces an existing one",
                (rep1, rep2, rep3) == (False, False, True) and len(rows) == 2
                and rows[0]["Start"] == 1, f"{len(rows)} rows, replaced={rep3}"))
    parsed = csv_to_rows(rows_to_csv([fwd, rev]))
    out.append(("CSV export → parse gives the same rows",
                len(parsed) == 2 and all(rows_match(a, b) for a, b in zip([fwd, rev], parsed)),
                f"{len(parsed)} rows"))

    # 13. default names
    out.append(("Default names read as expected",
                default_name(FORM_FORWARD, 10_000, 0.10, 10, 0, 0) == "10,000 @ 10% for 10y"
                and default_name(FORM_FORWARD, 10_000, 0.10, 10, 1_000, 0)
                == "10,000 @ 10% for 10y +1,000/yr"
                and default_name(FORM_REVERSE, 10_000, 0, 20, 0, 1_000_000)
                == "10,000 → 1,000,000 in 20y",
                default_name(FORM_FORWARD, 10_000, 0.10, 10, 1_000, 0)))
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
st.caption("Compound returns, forward and in reverse, with a table of saved scenarios")

# INPUT STORE. The first version kept each input's value in its widget's own
# session-state key. Streamlit drops the state of any keyed widget that is
# not drawn in a run, and switching forms hides two of the five inputs — so
# on the first switch the target read 0, and on switching back the return
# read 0 (Chen, 30 Aug 2026). The headless test never saw it because it
# loaded a row between switches, which rewrote the keys.
#
# The inputs now live in one dict of their own, `vals`, that the widgets
# read their value from and write back to on change. Load writes `vals`.
# Nothing the page needs is ever stored only inside a hidden widget.
_DEFAULTS = {"start": 10_000.0, "rate": 10.0, "years": 10,
             "contrib": 0.0, "target": 1_000_000.0}
st.session_state.setdefault("vals", dict(_DEFAULTS))
st.session_state.setdefault("form", FORM_FORWARD)
if "scenarios" not in st.session_state:
    st.session_state["scenarios"] = []
vals = st.session_state["vals"]


def _keep(k: str) -> None:
    """on_change: copy the widget's value into the store."""
    st.session_state["vals"][k] = st.session_state["w_" + k]


# A re-load writes the store before the widgets are drawn — Streamlit refuses
# to change a widget's value after it exists in the same run.
if "pending_load" in st.session_state:
    _row = st.session_state.pop("pending_load")
    vals["start"] = float(_row["Start"])
    vals["years"] = int(_row["Years"])
    if _row["Solved for"] == "return":
        st.session_state["form"] = FORM_REVERSE
        vals["target"] = float(_row["Ending"])
    else:
        st.session_state["form"] = FORM_FORWARD
        vals["rate"] = float(_row["Return %"])
        vals["contrib"] = float(_row["Contribution"])
    st.session_state["loaded_name"] = _row["Name"]
    # A widget that stayed on screen keeps its own state and ignores a
    # changed `value=`; dropping the widget keys makes every input rebuild
    # from the store. Allowed here because no widget has been drawn yet.
    for _k in _DEFAULTS:
        st.session_state.pop("w_" + _k, None)

form = st.radio("Solve for", [FORM_FORWARD, FORM_REVERSE], horizontal=True, key="form")

_c1, _c2, _c3 = st.columns(3)
with _c1:
    start = st.number_input("Start amount", value=vals["start"], step=1_000.0, format="%.2f",
                            key="w_start", on_change=_keep, args=("start",))
with _c2:
    if form == FORM_FORWARD:
        rate_pct = st.number_input("Yearly return %", value=vals["rate"], step=0.5,
                                   format="%.2f", key="w_rate", on_change=_keep, args=("rate",))
    else:
        target = st.number_input("Target amount", value=vals["target"], step=1_000.0,
                                 format="%.2f", key="w_target", on_change=_keep, args=("target",))
with _c3:
    years = st.number_input("Years", value=vals["years"], min_value=0, max_value=MAX_YEARS + 1,
                            step=1, key="w_years", on_change=_keep, args=("years",))
if form == FORM_FORWARD:
    contrib = st.number_input("Yearly contribution (optional)", value=vals["contrib"], step=100.0,
                              format="%.2f", key="w_contrib", on_change=_keep, args=("contrib",),
                              help="Paid at the end of each year.")
else:
    contrib = 0.0
    rate_pct = 0.0
    st.caption("The reverse form has no contribution in this version.")
if form == FORM_FORWARD:
    target = 0.0

_loaded = st.session_state.pop("loaded_name", None)
if _loaded:
    st.info(f"Loaded **{_loaded}** into the inputs.")

# ── compute, or refuse ───────────────────────────────────────────────
if form == FORM_FORWARD:
    refusal = check_forward(start, rate_pct / 100.0, int(years), contrib)
else:
    refusal = check_reverse(start, target, int(years))

row = None
if refusal:
    st.error(refusal)
elif form == FORM_FORWARD:
    row = forward_row("", start, rate_pct / 100.0, int(years), contrib)
    _m = st.columns(4)
    _m[0].metric("Ending amount", f"{row['Ending']:,.2f}")
    _m[1].metric("Put in", f"{row['Put in']:,.2f}")
    _m[2].metric("Gain", f"{row['Ending'] - row['Put in']:,.2f}")
    _m[3].metric("Multiple", f"{row['Multiple']:,.2f}×")
else:
    row = reverse_row("", start, target, int(years))
    _m = st.columns(2)
    _m[0].metric("Required return", f"{row['Return %']:.2f}% / yr")
    _m[1].metric("Multiple", f"{row['Multiple']:,.2f}×")

with st.expander("Assumptions used"):
    st.code(
        f"form                {form}\n"
        f"start               {start:,.2f}\n"
        + (f"return              {rate_pct:.2f}% per year, compounded yearly\n"
           f"contribution        {contrib:,.2f} per year, paid at the END of each year\n"
           if form == FORM_FORWARD else
           f"target              {target:,.2f}\n"
           f"contribution        none in the reverse form\n")
        + f"years               {int(years)}\n"
        f"multiple            ending ÷ total put in (start + years × contribution)\n"
        f"inflation, tax      not applied",
        language="text")

# ── scenarios ────────────────────────────────────────────────────────
st.markdown("---")
st.write("**Saved scenarios**")
st.caption("Rows last while this tab is open. Download the CSV to keep them.")

_auto = default_name(form, start, rate_pct / 100.0, int(years), contrib, target)
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
        "Start": "{:,.2f}", "Return %": "{:.2f}%", "Contribution": "{:,.2f}",
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
            "yearly contribution compounded from the end of the year it was paid.\n\n"
            "**Put in** — start plus every contribution. What you actually handed over.\n\n"
            "**Multiple** — ending divided by put in. With no contribution it is the plain "
            "multiple of the start; with one it is the multiple of everything you put in, "
            "which is the honest figure.\n\n"
            "**Required return** — the single yearly return that turns the start into the "
            "target in the years given. 100× in 20 years needs 25.89% a year."
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
