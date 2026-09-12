"""
Home.py — entrypoint for the Investor Toolkit (retitled in place, 12 Sep 2026;
the filename stays Home.py because the Cloud main-file setting pins it).

Streamlit turns every file in pages/ into a nav item automatically, ordered by
the numeric prefix. THE MENU MAP IS FROZEN (toolkit pass, 12 Sep 2026):

    1   Tragic Algebra Analyzer
    2   Hundred Bagger Checker      (renders "100-Bagger Checker" on the page)
    4   Inflection Checker
    5   Financials Checker
    6   NonUS Checker               (renders "Non-US Checker" on the page)
    7   DCF Evaluator
    8   (reserved: the watchlist page, next session)
    99  Return Calculator           (structurally last for the life of the kit)

3 is retired and is never reused; 8 is reserved. Never reuse a number. The
self-test below pins this map: every page Home names must exist on disk at
exactly the frozen path, so the menu's claims cannot drift from the repo.
"""

from pathlib import Path

import streamlit as st

# ══════════════════════════════════════════════════════════════════════
#  THE MENU — single source for the links below AND the self-test, so the
#  page cannot render a map its own checks do not pin.
# ══════════════════════════════════════════════════════════════════════

MENU: list[tuple[str, str, str]] = [
    # (path under the repo root, icon, label as rendered on Home)
    ("pages/1_Tragic_Algebra_Analyzer.py", "🎯", "Tragic Algebra Analyzer"),
    ("pages/2_Hundred_Bagger_Checker.py",  "💯", "100-Bagger Checker"),
    ("pages/4_Inflection_Checker.py",      "🌱", "Inflection Checker"),
    ("pages/5_Financials_Checker.py",      "🏦", "Financials Checker"),
    ("pages/6_NonUS_Checker.py",           "🌍", "Non-US Checker"),
    ("pages/7_DCF_Evaluator.py",           "🧮", "DCF Evaluator"),
    ("pages/99_Return_Calculator.py",      "📈", "Return Calculator"),
]

PAGE_BLURBS: dict[str, str] = {
    "Tragic Algebra Analyzer": (
        "The main page. Burry's Tragic Algebra: owners' earnings after the true cost of "
        "stock compensation — the cash spent on buybacks that exist only to offset employee "
        "grants, plus the market value of shares actually delivered — then the IV ladder "
        "from IV8 to IV20 at a 15% required return, a stress test, and a watchlist mode "
        "ranking up to 25 tickers by ΔE, the share of reported profit that survives."
    ),
    "100-Bagger Checker": (
        "Chris Mayer's 100-bagger criteria on the same owners' earnings: the return a "
        "hundredfold in twenty years needs, against what the business has delivered and "
        "what its return on capital — Burry's fully-adjusted formula — can fund."
    ),
    "Inflection Checker": (
        "Companies crossing from loss to profit. The evidence is the filed trend — "
        "operating margin, gross margin, cash generation — and the pricing is Burry's "
        "Stage 0 on an operating basis. It refuses often, and a shape that is not an "
        "inflection is sent by name to the page that fits it."
    ),
    "Financials Checker": (
        "Banks, insurers and REITs, which every other page refuses. Priced on a filed "
        "base per share — tangible common equity, or FFO for REITs — the return on it, "
        "and what is kept. Burry publishes no method for financials beyond the stock-comp "
        "adjustment, so this page is the toolkit's own design, and it says so on the page."
    ),
    "Non-US Checker": (
        "The same algebra for companies that file 20-F or 40-F, in the filing currency "
        "with no FX conversion ever, plus a paste-your-own-figures mode for companies "
        "EDGAR has never heard of."
    ),
    "DCF Evaluator": (
        "The standard two-stage free-cash-flow DCF (Aswath Damodaran's), with every "
        "assumption in a box you can change — and beside it the same DCF on SBC-corrected "
        "cash flow, so the cost that the usual \"add stock comp back\" convention hides "
        "is visible in dollars per share."
    ),
    "Return Calculator": (
        "A plain compound-return calculator: ending amount, required return, years to "
        "target, or required contribution, with saved scenarios. Nothing here is wired "
        "to the valuation pages."
    ),
}

# ══════════════════════════════════════════════════════════════════════
#  SELF-TEST — 7 checks, one per page: the frozen path exists on disk.
#  The numeric prefix in each path IS the frozen map, so these checks pin
#  the renumbering as well as the menu's existence claims.
# ══════════════════════════════════════════════════════════════════════

def test_summary(results: list[tuple[str, bool, str]]) -> tuple[str, str]:
    """One line at the top: how many ran, how many failed. The count is taken
    from the page itself, never from the source, so it cannot drift."""
    bad = [name for name, ok, _ in results if not ok]
    if not bad:
        return "success", f"**{len(results)} checks, 0 failed.**"
    return "error", (f"**{len(results)} checks, {len(bad)} FAILED:** "
                     + "; ".join(bad[:4])
                     + (f" — and {len(bad) - 4} more" if len(bad) > 4 else ""))


def self_test() -> list[tuple[str, bool, str]]:
    out = []
    root = Path(__file__).resolve().parent
    for path, _icon, label in MENU:
        p = root / path
        out.append((f"Menu: {label} exists at {path}",
                    p.is_file(),
                    "present" if p.is_file() else "MISSING — menu and repo disagree"))
    return out

# ══════════════════════════════════════════════════════════════════════
#  PAGE
# ══════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Investor Toolkit — Burry owners' earnings, IV15 and DCF from SEC filings",
    page_icon="🧰",
    layout="centered",
    # Expanded on Home: the nav IS the toolkit now, seven pages deep, and the
    # menu is the fastest answer to "what does this do". Pages keep their own
    # collapsed default.
    initial_sidebar_state="expanded",
)

st.title("🧰 Investor Toolkit")
st.caption("One page per question, computed from audited SEC filings — "
           "the Tragic Algebra page first")

st.markdown(
    """
Reported profit is not what reaches you. Shares handed to employees cost real money the
income statement never shows — either through dilution, or through buybacks that exist only
to offset employee grants.

Burry's study of the NASDAQ-100 over the ten years to 2025 put that cost at **$1.73 trillion**,
leaving shareholders about **83 cents** of every reported GAAP dollar. That figure is his,
for that period. This toolkit computes the same thing for any company, from whatever the
filings say today — and then answers the questions that follow from it, one page per
question. The methods are published ones, each named on its page: Michael Burry's Tragic
Algebra and IV15 where they apply, and other named authors — or the toolkit's own stated
design — where they do not.
"""
)

st.divider()

for path, icon, label in MENU:
    st.page_link(path, label=f"**{label}**", icon=icon)
    st.markdown(PAGE_BLURBS[label])

st.divider()

st.markdown(
    """
**Three rules hold on every page.** A page never prints a number it cannot stand behind —
it refuses out loud instead, and the refusal names its reason. What was read from a filing
and what is your judgement are labelled apart, because the judgement column is where the
work is. And every page carries an "assumptions used" block you can paste if a figure looks
wrong, and a tag panel naming every XBRL element it read or failed to find.

**The honest state of the engine.** It is checked against Burry's published master table:
19 of the 21 comparable rows reproduce within their stated bands. The two that do not
(Intuit and Adobe) are disagreements with documented causes, carried openly on the
regression page rather than tuned to match.
"""
)

st.divider()

with st.expander("Verify this page"):
    st.markdown(
        "Seven checks, one per menu entry: the page each link names exists in the "
        "repository at exactly the path the frozen menu map states. If the map and "
        "the repo ever disagree, this goes red before any user finds a dead link."
    )
    if st.button("Run checks"):
        _results = self_test()
        _sev, _line = test_summary(_results)
        getattr(st, _sev)(_line)
        for name, ok, got in _results:
            st.write(("✅ " if ok else "❌ ") + f"{name} — {got}")

st.caption(
    "Research aid, not financial advice. Outputs depend on estimates you supply — change the "
    "growth rate and the answer changes a great deal. Methods follow the published writing of "
    "the authors named on each page; this project is independent and is not affiliated with "
    "or endorsed by any of them, including Michael Burry or Scion Asset Management."
)
