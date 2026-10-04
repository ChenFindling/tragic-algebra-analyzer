"""
Home.py: entrypoint for the Investor Toolkit (retitled in place, 12 Sep 2026;
the filename stays Home.py because the Cloud main-file setting pins it).

Streamlit turns every file in pages/ into a nav item automatically, ordered by
the numeric prefix. THE MENU MAP IS FROZEN (toolkit pass, 12 Sep 2026;
9 added 13 Sep 2026; 10 added 17 Sep 2026; 11 added 18 Sep 2026;
12 added 20 Sep 2026; 13 added 21 Sep 2026; 14 added 25 Sep
2026; 15 added 27 Sep 2026; 16 added 29 Sep 2026;
17 added 30 Sep 2026):

    1   Tragic Algebra Analyzer
    2   Hundred Bagger Checker      (renders "100-Bagger Checker" on the page)
    4   Inflection Checker
    5   Financials Checker
    6   NonUS Checker               (renders "Non-US Checker" on the page)
    7   DCF Evaluator
    8   (reserved: the watchlist page)
    9   Expectations                (added 13 Sep 2026)
    10  EPV                         (added 17 Sep 2026)
    11  Magic Formula               (added 18 Sep 2026)
    12  Net-Nets                    (added 20 Sep 2026)
    13  Piotroski F-Score           (added 21 Sep 2026)
    14  Dividends                   (added 25 Sep 2026)
    15  Altman Z-Score              (added 27 Sep 2026)
    16  Serial Acquirers            (added 29 Sep 2026)
    17  Runway & Dilution           (added 30 Sep 2026)
    99  Return Calculator           (structurally last for the life of the kit)

3 is retired and is never reused; 8 is reserved. Never reuse a number. The
self-test below pins this map: every page Home names must exist on disk at
exactly the frozen path, so the menu's claims cannot drift from the repo.

THE ROUTER (added 4 Oct 2026): a one-screen "which tool do I need" section
above the in-depth list — one plain question per tool, grouped by intent,
each a page link. ROUTER is pinned by its own checks: every menu entry
appears in it exactly once, so a future page cannot ship without joining
the router (the front-door rule, extended).
"""

from pathlib import Path

import streamlit as st

# ══════════════════════════════════════════════════════════════════════
#  THE MENU: single source for the links below AND the self-test, so the
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
    ("pages/9_Expectations.py",            "🔭", "Expectations"),
    ("pages/10_EPV.py",                    "⚓", "EPV"),
    ("pages/11_Magic_Formula.py",          "🪄", "Magic Formula"),
    ("pages/12_Net_Nets.py",               "🧊", "Net-Nets"),
    ("pages/13_Piotroski.py",              "🩺", "Piotroski F-Score"),
    ("pages/14_Dividends.py",              "🪙", "Dividends"),
    ("pages/15_Altman.py",                 "🌡️", "Altman Z-Score"),
    ("pages/16_Serial_Acquirers.py",       "🧲", "Serial Acquirers"),
    ("pages/17_Runway.py",                 "⏳", "Runway & Dilution"),
    ("pages/99_Return_Calculator.py",      "📈", "Return Calculator"),
]

# ══════════════════════════════════════════════════════════════════════
#  THE ROUTER (4 Oct 2026): one question per tool, grouped by what the
#  visitor is trying to do. Labels reference MENU labels; the self-test
#  pins the two lists to each other exactly.
# ══════════════════════════════════════════════════════════════════════

ROUTER: list[tuple[str, list[tuple[str, str]]]] = [
    ("Price it", [
        ("Is this stock cheap at today's price?", "Tragic Algebra Analyzer"),
        ("What growth does today's price demand?", "Expectations"),
        ("What is it worth with zero growth assumed?", "EPV"),
        ("Want to test your own DCF assumptions?", "DCF Evaluator"),
    ]),
    ("Check its health", [
        ("Is the fundamental trend improving?", "Piotroski F-Score"),
        ("How close is it to financial distress?", "Altman Z-Score"),
        ("Is a loss-maker turning the corner?", "Inflection Checker"),
        ("Good business at a cheap price, by the numbers?", "Magic Formula"),
        ("Is the dividend real, covered, and growing?", "Dividends"),
    ]),
    ("Special shapes", [
        ("Trading below liquidation value?", "Net-Nets"),
        ("Does it grow by buying companies?", "Serial Acquirers"),
        ("How long until a cash burner must dilute you?", "Runway & Dilution"),
        ("Could it be a 100-bagger?", "100-Bagger Checker"),
    ]),
    ("Banks, insurers, foreign filers, and math", [
        ("Is it a bank or an insurer?", "Financials Checker"),
        ("Does it file outside the US?", "Non-US Checker"),
        ("Just need compound-return math?", "Return Calculator"),
    ]),
]

PAGE_BLURBS: dict[str, str] = {
    "Tragic Algebra Analyzer": (
        "The main page. Burry's Tragic Algebra: owners' earnings after the true cost of "
        "stock compensation (the cash spent on buybacks that exist only to offset employee "
        "grants, plus the market value of shares actually delivered), then the IV ladder "
        "from IV8 to IV20 at a 15% required return, a stress test, and a watchlist mode "
        "ranking up to 25 tickers by ΔE, the share of reported profit that survives."
    ),
    "100-Bagger Checker": (
        "Chris Mayer's 100-bagger criteria on the same owners' earnings: the return a "
        "hundredfold in twenty years needs, against what the business has delivered and "
        "what its return on capital, Burry's fully-adjusted formula, can fund."
    ),
    "Inflection Checker": (
        "Companies crossing from loss to profit. The evidence is the filed trend "
        "(operating margin, gross margin, cash generation) and the pricing is Burry's "
        "Stage 0 on an operating basis. It refuses often, and a shape that is not an "
        "inflection is sent by name to the page that fits it."
    ),
    "Financials Checker": (
        "Banks, insurers and REITs, which every other page refuses. Priced on a filed "
        "base per share (tangible common equity, or FFO for REITs), the return on it, "
        "and what is kept. Burry publishes no method for financials beyond the stock-comp "
        "adjustment, so this page is the toolkit's own design, and it says so on the page. "
        "Brokers that hold client assets without taking deposits, Interactive Brokers' "
        "shape, are priced here too, on the same tangible-book frame, with the client "
        "float shown beside the firm's own capital."
    ),
    "Non-US Checker": (
        "The same algebra for companies that file 20-F or 40-F, in the filing currency "
        "with no FX conversion ever, plus a paste-your-own-figures mode for companies "
        "EDGAR has never heard of."
    ),
    "DCF Evaluator": (
        "The standard two-stage free-cash-flow DCF (Aswath Damodaran's), with every "
        "assumption in a box you can change, and beside it the same DCF on SBC-corrected "
        "cash flow, so the cost that the usual \"add stock comp back\" convention hides "
        "is visible in dollars per share."
    ),
    "Expectations": (
        "The Tragic Algebra Analyzer's question, run backwards. This page never says what "
        "a company is worth: it solves what today's price implies, the owners'-earnings "
        "growth that makes IV15 equal the price at your tier and required return, and the "
        "year-15 exit the price implies at the seeded growth, then puts that implied "
        "path against the company's own best filed stretches and a pinned base-rate "
        "table of how many names this kit reads ever sustained such rates. Below the "
        "evidence, the page also prices the record itself: IV15 at every five, seven "
        "and nine year growth stretch the filer actually delivered, with today's price "
        "placed inside that set as a count. Rappaport and Mauboussin's *Expectations "
        "Investing*, on that page's engine inverted."
    ),
    "EPV": (
        "Bruce Greenwald's Earnings Power Value: what the business is worth assuming zero "
        "growth: the average operating margin over the readable years, on current revenue, "
        "taxed at the filed rate and capitalized at your required return, less net debt. "
        "Beside it, the same value on SBC-corrected earnings, so the cost stock comp hides "
        "is priced in the no-growth frame too. The gap between EPV and the price is the "
        "dollar amount the market is paying for growth; the Expectations page states the "
        "path that payment implies."
    ),
    "Magic Formula": (
        "Joel Greenblatt's two legs from *The Little Book That Beats the Market*: "
        "earnings yield, which is operating income against enterprise value, and return on "
        "capital, the same operating income against working capital plus net fixed "
        "assets, computed for one ticker at a time with his definitions and his "
        "exclusions. The book's edge is a market-wide ranking, and a ranking needs a "
        "universe this kit does not fetch, so the page refuses to rank and instead "
        "shows the ticker beside a small dated table of the names this kit reads. "
        "Where no operating-income subtotal is filed, EBIT is derived from two filed "
        "lines with the subtraction printed on the page. Beside each leg, the same "
        "figure on SBC-corrected operating income, because a formula screened on "
        "pre-SBC earnings is exactly where the hidden cost hides."
    ),
    "Net-Nets": (
        "Benjamin Graham's net-current-asset-value test: what the market pays against "
        "current assets alone, with every liability and prior claim deducted. NCAV per "
        "share is compared with the price, and Graham's two-thirds buying threshold is "
        "stated as his criterion, never a verdict. Among today's readable filers a true "
        "net-net is rare, so the page says plainly when the normal answer arrives: NCAV "
        "far below the price. This is a balance-sheet floor, not a going-concern value; "
        "a wind-down realizes assets below book, so the true floor sits lower still."
    ),
    "Piotroski F-Score": (
        "Joseph Piotroski's nine binary financial-strength tests from his 2000 paper, "
        "with his definitions and his scaling: profitability, leverage and liquidity, "
        "and operating efficiency, one point each. Every test prints its own filed "
        "inputs and arithmetic. A test whose inputs the filings do not carry for the "
        "needed years refuses by name, and the summary counts tests passed out of "
        "tests readable, with the denominator stated plainly. Fewer than five readable "
        "tests refuses the page, because a score on a minority of the tests is not the "
        "F-Score. Built for the cheap and ugly names the Net-Nets and Inflection pages "
        "surface, which is where the paper found the score does its work."
    ),
    "Dividends": (
        "The filed dividend record and what it costs. How long this filer has paid "
        "and raised within the readable window, at what growth, and then the coverage "
        "pair no other tool prints: the fraction of reported earnings the dividend "
        "consumes, beside the same dividend against owners' earnings after the true "
        "cost of stock comp at this filer's \u0394E. A dividend that looks modest "
        "against reported profit can be most of what actually reaches you. No "
        "dividend model is fitted here on purpose: the growth a price implies is the "
        "Expectations page's question."
    ),
    "Altman Z-Score": (
        "Edward Altman's bankruptcy score: five filed ratios, his 1968 coefficients, "
        "summed into the single distress number that half a century of credit work "
        "still leans on. The page selects the model Altman fitted for the filer's "
        "kind, the original for manufacturers and his re-fit without the turnover "
        "ratio for everyone else, prints each ratio's own arithmetic so a row can be "
        "checked by hand, and states his zone labels as his labels with his own later "
        "warning attached: the boundaries are regression constants from decades-old "
        "samples, and Altman himself no longer recommends the old cutoff as a default "
        "verdict. This is the survival lens the other pages assume. A turnaround "
        "story or a cheap balance sheet reads differently when the score sits in the "
        "zone Altman called distress."
    ),
    "Serial Acquirers": (
        "The filed evidence of growth bought versus grown, for the roll-up shape the "
        "other pages' exclusion rules thin out. Per year, side by side: revenue and its "
        "growth, cash spent on acquisitions, goodwill and its change, the stock the "
        "engine excludes from its dilution measure because it is deal currency rather "
        "than pay, and buybacks beside it. The filings do not tag the split between "
        "organic and acquired revenue, so this page never prints one; it shows the "
        "ingredients and says why it refuses. Return on capital here counts the "
        "goodwill, because a serial acquirer judged on capital that forgets what it "
        "paid for its deals grades itself on the wrong denominator; the Magic Formula "
        "page's return on tangible capital is the deliberate contrast, and the page "
        "names it. Michael Burry's essay on serial acquirers names the shape; the "
        "surfaces are this toolkit's own design, and the page says so."
    ),
    "Runway & Dilution": (
        "How long the cash lasts at the rate the filings report, and what past equity "
        "issuance cost the holders who were already there. Every valuation page in this "
        "kit refuses a company that burns cash, and rightly: there is no earnings power "
        "to capitalize. What can still be asked from filings alone is the cash position "
        "at the latest balance date, last year's burn with capital spending named beside "
        "it, the months the filed rate would cover, and, per year, the equity issuance "
        "proceeds as the filings tag them beside the shares issued, priced per share on "
        "the as-filed count basis. The months figure never prints without the sentence "
        "that is part of it: the filed rate is last year's rate, a burning company is "
        "usually changing that rate on purpose, and the filings do not say what next "
        "year's rate will be. The proceeds column is labelled as filed, never as money "
        "raised, because the tagged elements can also carry employee plan issuance and "
        "the filings do not split them. A filer that generates cash gets a clean answer "
        "saying so, with its cash position still shown. No survival verdict is printed "
        "here, ever, and none is implied."
    ),
    "Return Calculator": (
        "A plain compound-return calculator: ending amount, required return, years to "
        "target, or required contribution, with saved scenarios. Nothing here is wired "
        "to the valuation pages."
    ),
}

# ══════════════════════════════════════════════════════════════════════
#  SELF-TEST: one check per menu entry: the frozen path exists on disk.
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
                     + (f", and {len(bad) - 4} more" if len(bad) > 4 else ""))


def self_test() -> list[tuple[str, bool, str]]:
    out = []
    root = Path(__file__).resolve().parent
    for path, _icon, label in MENU:
        p = root / path
        out.append((f"Menu: {label} exists at {path}",
                    p.is_file(),
                    "present" if p.is_file() else "MISSING: menu and repo disagree"))
    # ── Router checks (4 Oct 2026): the one-screen router and the frozen
    #    menu pin each other — a page cannot ship without joining the router.
    _menu_labels = [label for _, _, label in MENU]
    _router_labels = [lab for _, entries in ROUTER for _, lab in entries]
    out.append(("Router covers every menu entry exactly once",
                sorted(_router_labels) == sorted(_menu_labels),
                f"{len(_router_labels)} router lines over {len(_menu_labels)} menu "
                "entries, one each" if sorted(_router_labels) == sorted(_menu_labels)
                else f"mismatch: {sorted(set(_router_labels) ^ set(_menu_labels))}"))
    _qs = [q for _, entries in ROUTER for q, _ in entries]
    out.append(("Router questions are distinct, non-empty, and question-shaped",
                len(set(_qs)) == len(_qs) and all(q.strip().endswith("?") for q in _qs),
                f"{len(_qs)} questions, all unique, all end with a question mark"))
    return out

# ══════════════════════════════════════════════════════════════════════
#  PAGE
# ══════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Investor Toolkit: Burry owners' earnings, IV15 and DCF from SEC filings",
    page_icon="🧰",
    layout="centered",
    # Expanded on Home: the nav IS the toolkit, and the menu is the fastest
    # answer to "what does this do". No page count here on purpose: counts
    # go stale as pages are added. Pages keep their own collapsed default.
    initial_sidebar_state="expanded",
)

st.title("🧰 Investor Toolkit")
st.caption("One page per question, computed from audited SEC filings, "
           "the Tragic Algebra page first")

st.markdown(
    """
Reported profit is not what reaches you. Shares handed to employees cost real money the
income statement never shows, either through dilution or through buybacks that exist only
to offset employee grants.

Burry's study of the NASDAQ-100 over the ten years to 2025 put that cost at **$1.73 trillion**,
leaving shareholders about **83 cents** of every reported GAAP dollar. That figure is his,
for that period. This toolkit computes the same thing for any company, from whatever the
filings say today, and then answers the questions that follow from it, one page per
question. The methods are published ones, each named on its page: Michael Burry's Tragic
Algebra and IV15 where they apply, and other named authors, or the toolkit's own stated
design, where they do not.
"""
)

st.divider()

# ── The router: one screen, one question per tool ──────────────────────
st.subheader("Which tool do I need?")
_by_label = {label: (path, icon) for path, icon, label in MENU}
_cols = st.columns(2)
for _gi, (_group, _entries) in enumerate(ROUTER):
    with _cols[_gi % 2]:
        st.markdown(f"**{_group}**")
        for _q, _lab in _entries:
            _p, _ic = _by_label[_lab]
            st.page_link(_p, label=_q, icon=_ic)
        st.write("")

st.divider()

st.subheader("The tools in depth")
for path, icon, label in MENU:
    st.page_link(path, label=f"**{label}**", icon=icon)
    st.markdown(PAGE_BLURBS[label])

st.divider()

st.markdown(
    """
**Three rules hold on every page.** A page never prints a number it cannot stand behind;
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
        "One check per menu entry: the page each link names exists in the "
        "repository at exactly the path the frozen menu map states. If the map and "
        "the repo ever disagree, this goes red before any user finds a dead link. "
        "Two more pin the router above: every menu entry appears in it exactly "
        "once, and every router line is a distinct question."
    )
    if st.button("Run checks"):
        _results = self_test()
        _sev, _line = test_summary(_results)
        getattr(st, _sev)(_line)
        for name, ok, got in _results:
            st.write(("✅ " if ok else "❌ ") + f"{name}: {got}")

st.markdown(
    "Free, and staying free. If it's been useful: "
    "[ko-fi.com/investortoolkit](https://ko-fi.com/investortoolkit)"
)

st.caption(
    "Research aid, not financial advice. Outputs depend on estimates you supply: change the "
    "growth rate and the answer changes a great deal. Methods follow the published writing of "
    "the authors named on each page; this project is independent and is not affiliated with "
    "or endorsed by any of them, including Michael Burry or Scion Asset Management."
)
