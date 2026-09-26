# 🧰 Investor Toolkit

**Michael Burry's Tragic Algebra and IV15 owners' earnings framework, computed from audited
SEC EDGAR filings, grown into a toolkit of one page per question.** A free set
of stock valuation tools that measures the true cost of stock-based compensation (the cash
spent on buybacks to offset employee grants, plus the market value of shares actually
delivered) and prices what survives at a 15% required return.

The app lives at **[investor-toolkit.streamlit.app](https://investor-toolkit.streamlit.app/)**.
It began as a single Tragic Algebra page and this repository keeps that name,
`tragic-algebra-analyzer`, because links to the code are pinned in public posts; the app
outgrew the name, and the address now says what it is.

Implements the full formula from *AP SBC: The Tragic Algebra Recurrence*, including the
share-issuance term that pure-dilution companies need, and reproduces Burry's published
figures (see [Validation](#-validation-and-the-regression-baseline)).

---

## 📌 The problem: free cash flow lies in software

Standard tools add stock-based compensation back as a "non-cash expense." It is not free.
Granting equity either **dilutes owners** by expanding the share count, or **drains cash**
through buybacks that exist only to neutralise employee grants.

Across the NASDAQ-100 over ten years, that cost totals **$1.73 trillion**. Shareholders keep
about **83 cents** of every reported GAAP dollar. Wall Street's "adjusted" earnings,
which add SBC back with no offset, overstate the real figure by **42%**.

---

## 🗺 The pages

Three rules hold on every page. A page never prints a number it cannot stand behind: it
refuses out loud instead, and the refusal names its reason. What was read from a filing and
what is your judgement are labelled apart. And every page carries an "assumptions used"
block you can paste if a figure looks wrong, and a tag panel naming every XBRL element it
read or failed to find.

| Page | Question it answers |
| :--- | :--- |
| **Tragic Algebra Analyzer** | Owners' earnings after the true cost of stock comp, and the IV ladder that follows |
| **100-Bagger Checker** | Can this business fund a hundredfold in twenty years, on Mayer's criteria |
| **Inflection Checker** | Is a loss-to-profit story actually in the filings, and what is it worth if it continues |
| **Financials Checker** | Banks, insurers, REITs and client-asset brokers, which every other page refuses |
| **Non-US Checker** | The same algebra for IFRS and non-dollar filers, in the filing currency |
| **DCF Evaluator** | The standard two-stage DCF, with its stock-comp blind spot priced beside it |
| **Expectations** | What today's price implies: the growth path and the year-15 exit it takes to deliver the required return |
| **EPV** | What zero growth is worth: normalized earnings power capitalized, and the growth payment inside today's price |
| **Magic Formula** | Greenblatt's two legs for one ticker: earnings yield and return on capital, honestly, without the ranking |
| **Net-Nets** | Graham's deep-value floor: current assets minus every liability and prior claim, against the price |
| **Piotroski F-Score** | Piotroski's nine financial-strength tests, each shown with its filed inputs, passed out of readable |
| **Dividends** | The filed dividend record, and its coverage against both reported earnings and owners' earnings after stock comp |
| **Return Calculator** | Plain compound-return arithmetic, deliberately last in the menu |

---

## 🧮 Tragic Algebra

| Symbol | Meaning | Source |
| :--- | :--- | :--- |
| $N$ | GAAP net income | Income statement |
| $G$ | GAAP SBC expense | Cash flow, operating |
| $C_w$ | Tax withheld on vesting | Cash flow, financing |
| $C_e$ | Option and ESPP proceeds | Cash flow, financing |
| $T$ | Buyback dollars | Cash flow, financing |
| $W$ | Shares repurchased | Repurchase footnote |
| $\Delta S$ | Change in shares outstanding | Balance sheet |

$$I = \Delta S + W \qquad P = T / W \qquad V = I \times P$$

$$C = C_w - C_e \qquad \Omega = C + V \qquad OE = N + G - \Omega \qquad \Delta E = OE / N$$

$\Omega$ **replaces** $G$ rather than supplementing it, because leaving the GAAP charge in would
double-count. Pooling over ~10 years uses $\sum OE / \sum N$, never an average of annual
ratios, which blows up on near-zero-earnings years.

### The simplification that makes this automatable

$W$ is almost never tagged in XBRL; it lives in the share repurchase footnote. But since
$P = T/W$:

$$V = T \cdot \frac{W + \Delta S}{W} = T + \frac{T}{W}\Delta S = T + P \cdot \Delta S$$

$W$ cancels. Only the average share price is needed, and that is always obtainable. The
identity is exact before the price substitutions, and is verified against all ten published
Alphabet years using his prices. For $P$ the app uses the year's average market price, which is how
his May 2026 formula table defines it, and his choice for companies with no buyback; in the
NASDAQ-100 study he used the buyback program's own average price where one existed. On
Salesforce the difference is worth about four points of ΔE. The floor of $V$ at zero is his
rule (10-K extraction protocol, step 9).

### What ΔE says, and the condition on compounding

$\Delta E$ is a **level ratio**: how much of this year's reported dollar reached owners. It
is not, by itself, an annual retention factor: if ΔE stays constant while net income grows
at $g$, owners' earnings grow at $g$ too. What genuinely compounds is the annual dilution
rate, which ΔE only proxies **if the dilution pace behind it persists**. Under that stated
assumption, and only under it, per-share value retains about $\Delta E^{t}$ of the
undiluted path after $t$ years, the break-even of the conditional claim is
$\Delta E = 1/1.15 \approx 87\%$, and 15% reported growth at the NASDAQ-100's 83.5% compounds
per share at about −4% a year. The app's metric and verdict banners state the condition out
loud; the valuation engine never used the compounding form; it applies ΔE once as a level
haircut and grows owners' earnings at $g$.

---

## ✅ Validation and the regression baseline

The engine reproduces Burry's published figures. Run the self-test in the sidebar.

| Check | Published | Engine |
| :--- | :---: | :---: |
| Alphabet FY2016 $V$ | $8,252M | $8,252M |
| Alphabet FY2025 $V$ | $26,551M | $26,551M |
| Alphabet pooled ΔE | 88.7% | 88.68% |
| Meta pooled ΔE | 83.35% | 83.35% |
| Meta FY2016 ΔE (no buyback) | 83.4% | 83.4% |
| NDX-97 GAAP overstatement | 19.78% | 19.77% |
| Salesforce IV15 | $69.81 | $69.63 |
| Salesforce IVB | 8.6% | 8.6% |
| Apple pooled ΔE, FY2016–25 (his master table) | 93.1% | 93.6% |
| Netflix pooled ΔE, FY2016–25 (his master table) | 81.4% | 82.6% |

Beyond the published-figure self-tests, every deployment runs against Burry's 21-row master
table in a separate regression app, before and after each change. The current state:
**19 pass, 2 fail, 2 refused as pinned, 6 window mismatch, 1 not comparable.**

What those words mean. **Pass**: the pooled ΔE lands inside the row's stated tolerance
band, exact for filed figures. **Fail**: it does not, and the two standing fails are Intuit
(+8.9 points) and Adobe (+3.8), both honest disagreements carried openly with their causes
decomposed on the row rather than tuned to match; both trace to treasury-stock lines
offered as tax withholding, which this kit rejects by an equality test. **Refused as
pinned**: the kit declines the name on purpose (a dual-class share count it will not
price), and the refusal itself is the pinned expectation. **Window mismatch**: his window
and the filings' cannot be aligned, so the comparison is reported, not scored. **Not
comparable**: the row is measured on a basis the filings do not carry.

Some figures exist only in a filing's own XBRL instance, not in the aggregation API the
pages normally read: Alphabet tags its withholding on a custom element, and dual-class
filers carry per-class share counts the API strips. For a short registry of named tickers
the kit fetches the filing's instance directly, and every entry is gated on verification
figures taken from the filing's own fact panel: a mismatch discards the whole entry. Custom
tags are read verified-or-not-at-all; an unregistered ticker takes one dictionary lookup
and leaves.

---

## 🏰 AICT moat tiers

| Tier | Stage 1 | Stage 2 | Stage 2 growth | Terminal cap | Debt capacity | Exit multiple |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Fortress**: regulated or platform, owns its AI | 8y | 16y | 70% | 7.0% | 3.0× EBITDA | 20.0× |
| **Castle**: strong moat, owned AI at scale | 7y | 13y | 55% | 5.0% | 2.5× EBITDA | 16.0× |
| **Chapel**: acute AI threat, real defences | 5y | 10y | 45% | 4.0% | 2.0× EBITDA | 14.5× |
| **Stone**: threatened, limited adaptability | 4y | 7y | 35% | 3.0% | 0 | 9.0× |
| **Wood**: borrowed AI, no credible R&D | 2y | 4y | 25% | 0.0% | 0 | 5.0× |

Total horizon is **24 / 20 / 15 / 11 / 6 years**, not 15 for everything. Tier sets how long
growth lasts and how fast it fades, never the starting rate.

Stage durations, stage-2 multipliers, terminal caps and debt capacity are published (the
"AICT Tiers = Banquet of Consequences" slide in *D'AI of the Triffids*). **Exit multiples
are not**; those are calibrated so the growth needed to reproduce a published IV15 matches
the company's actual growth. Adobe anchors them: at 14.5×, reaching his $262 needs 11.1%
growth, and Adobe grew 11%.

---

## 📐 The IV ladder

$IV_n$ is the price returning $n\%$ annually over the long run. Every rung is **one earnings
stream discounted at its own rate**, never scaled off another. Published IV12/IV15 ratios
span 1.33–1.48 across companies, so no constant multiplier fits.

Two models share the stream and are blended:

1. **Long-horizon**: stages 1 and 2, then a terminal perpetuity at the tier cap.
2. **Exit multiple**: project to year 15, apply a market multiple.

**IVB** inverts the ladder: the CAGR today's price implies. It needs no target return chosen
in advance, which arguably makes it the most useful single output.

A **negative IV15 is meaningful**: no share price delivers that return, not even $0.01. The
engine never floors it.

---

## 🚦 What is calculated vs. what is judgement

| Calculated (trust it) | Judgement (yours to set) |
| :--- | :--- |
| Every Tragic Algebra term | Normalised recurring owner earnings |
| Pooled ΔE and retention | Stage 1 growth rate |
| The full IV ladder and IVB | Moat tier |
| Split, listing and M&A adjustments | Exit multiple and model blend |

Burry writes thousands of words per company largely to justify the right-hand column. The
app seeds sensible defaults and flags when they cannot be trusted; it does not pretend to
replace the judgement.

---

## 💯 100-Bagger Checker

Chris Mayer's criteria on the same owners' earnings: the return a hundredfold in twenty
years needs, against what the business has delivered and what its return on capital can
fund. The return on capital is Burry's fully-adjusted formula, and the growth ceiling it
implies (return times retention) is computed and handed to the Tragic Algebra Analyzer as
the rate not to exceed. A growth assumption above that ceiling is a claim that the company
funds expansion from outside, more debt or stock, stated out loud instead of assumed.

## 🌱 Inflection Checker

For companies whose income statement looks terrible but whose trend tells a story: revenue
compounding, losses narrowing, operating leverage appearing. The page answers two questions
in order: is that story actually in the annual filings, and if it continues, what is it
worth at a 15% required return.

The trend evidence is this project's own tabulation: ten years of GAAP operating margin,
gross margin, incremental margin (how much of each new revenue dollar reached operating
income), total costs against revenue, cash burn against cash, and dilution as the price of
the runway. The pricing is Burry's Stage 0: the margin projected geometrically from the
company's own trend to a terminal margin you set, then his normal stages on the same engine
as the Tragic Algebra Analyzer. ΔE here is measured on after-tax operating income over the
profitable years since the turn, so a tax benefit or a gain below the operating line cannot
flatter it.

Where no operating-income subtotal is filed, the H&R Block shape, the operating column is
derived: revenue minus the filed all-in expense total, the subtraction printed year by year
in an expander, a banner naming the situation, a note naming any filed interest line the
total swallows (as the filing signs it), and a reconciliation line bracketing the derived
figure against filed pretax income. Every surface that shows the figure says it is derived.
Where neither a subtotal nor an all-in expense total exists, the cells refuse and the
caption names the real cause.

The page refuses often, out loud, with the years and figures: companies profitable
throughout or with a loss year on a profitable record are sent to the Tragic Algebra
Analyzer; a trend that reversed, is one year old, or is flat is not priced; a company still
in operating losses is shown but not priced; a company whose true stock-compensation cost
exceeds its operating profit is told that GAAP inflected and shareholders' earnings did not.
Annual filings only, so a turn that happened this year arrives with the next 10-K. Two grids
under the verdict show what the terminal margin and the growth assumption each do to the
answer.

## 🏦 Financials Checker

The pages above refuse financial companies, and for good reason: a bank's cash is its
inventory, an insurer's investments back its policies, and net income projected like a
software company's produces nonsense. Burry publishes no method for these businesses beyond
the stock-comp adjustment, so this page is the toolkit's own design, and it says so on the
page.

It prices the one thing all three structures share: a filed base per share (tangible common
equity for insurers and banks, Nareit FFO for REITs), the return on it, and what is kept.
Growth is derived, not assumed: return × (1 − payout), and the return fades to the lower of
the company's own 5-year and 10-year medians, and the exit assumes the next buyer also
demands 15% (terminal return ÷ 15% times book). The verdict prints how much of the value is
the owner's cash stream and how much is the year-10 sale, so an attractive answer that is
really a bet on the exit multiple says so. Buffett-definition float, combined ratios, bank
efficiency and FFO reconciliations are shown line by line from the filings; net cash, ROIC,
NIM, NAV and the moat tiers do not appear. Refusals are frequent and specific: no deposits
means no bank, goodwill exceeding equity means no book to price, a dividend above FFO is
not projected, and a preferred-stock line that stops being filed is carried forward and
named rather than silently zeroed.

Brokers whose balance sheets are client assets (payables to customers, segregated cash,
margin receivables) are priced on the same frame; a broker that holds deposits is still a
bank, and exchanges and dealers are still refused. Where a broker sits under an Up-C holding
structure, book and income are the parent's slice only, each read from its own filed line,
consolidated figures are never scaled by an ownership ratio, and a year whose parent slice
cannot be stated from filed lines is refused.

## 🌍 Non-US Checker

The identical algebra for companies that do not file US GAAP in dollars. 20-F and 40-F
filers are read in their filing currency with no FX conversion ever. Burry excludes ASML
from his own index over FX alone, and this page keeps that discipline as a rule, against a
registry of IFRS tag names, each trusted only after a live filing has answered on it; a line
with no verified name is refused by name, never guessed. Prices come only from a listing
quoting in the filing currency, which the page discovers itself, and an ADS ratio converts
units, never currency. A paste mode takes typed figures through the identical engine for
companies EDGAR has never heard of, and a US-GAAP dollar filer (Shopify's 40-F included)
is sent back to the Tragic Algebra Analyzer by name.

## 🧮 DCF Evaluator

The standard two-stage free-cash-flow DCF, the same model the valuation sites print, with
every assumption in a box you can change and its blind spots stated on the page. The model
follows Aswath Damodaran's published FCFF framework: free cash flow grown at a stage-1 rate
for a stated number of years, a terminal value at a rate below the discount rate, less net
debt, per share. The defaults are conventions, named as such; two grids show what the
discount rate, terminal rate, growth and years each do to the answer on their own.

What no other site prints: the same DCF a second time, on SBC-corrected cash flow. The
standard definition (cash from operations less capex) prices stock comp at zero, because
the GAAP charge expensed in net income is added straight back inside CFO. On the income
statement the correction for that charge is −(Ω − G), since net income already charges G;
on the cash-flow statement it is the full −Ω, because the add-back has already neutralised
the charge. The page shows both values side by side with the difference in dollars per
share, and the gap is exactly the discounted cost of stock comp. Ω is the same measured
cost the rest of the toolkit uses.

The page refuses rather than guesses: financials go to the Financials Checker, companies
with no positive free cash flow to project go to the Inflection Checker, terminal growth at
or above the discount rate is refused outright, and when the measured cost of stock comp
swallows the cash flow entirely, or too few years are both priced and free of capital
events to measure it at all, the corrected leg says so instead of printing a number it
cannot stand behind. Where a filer's stock-comp line or per-class share counts live only in
the filings' own XBRL instances, the verified registry reads them there; and
umbrella-partnership C-corps (Carvana, Ryan Specialty) are priced on the as-exchanged
basis: filed consolidated net income over the full Class A + Class B count, verified year
by year against the filings' own figures, with the Tax Receivable Agreement and the NCI
tax-status difference named on the page as real limits, stated with their direction,
never adjusted for. Where those consolidated legs cannot be read and verified, the
per-share valuation is still refused with the reason stated.

## 🔭 Expectations

A reverse DCF, after Alfred Rappaport and Michael Mauboussin's *Expectations Investing*.
The page never says what a company is worth. It solves what today's price implies: the
owners'-earnings growth rate at which the Tragic Algebra Analyzer's IV15 equals the price
at the chosen tier and required return (default 15%, the kit's standard; the box opens
down to 8% for the market-cost-of-capital reading), and, holding growth at the revenue
seed, the year-15 exit multiple the price implies when the cash flows are bought and the
business sold in year 15. Each solve is engine arithmetic run backwards on the same
verbatim copy of the valuation code every page carries, and each reproduces the forward
identity live: IV15 at the solved rate must equal the price to the cent on the page, or
the page refuses its own answer.

The implied rate is stated as the path the tier actually shapes (the stage-1 rate, the
fade, the exit) together with the demand it places on the first ten years and its
15-year flat equivalent, because a stage-1 rate quoted as if it ran fifteen years flat
would overstate the ask. That path is then put against two panels of filed evidence: the
company's own best five-year stretch and full-window rate, on owners' earnings and on
revenue; and a base-rate table computed from the 26 names the kit already reads: the
Burry master set plus the internal regression names, with KNSL and GRAB dropped by the
kit's own doctrine and the note saying so. The table is pinned data with its as-of date,
recomputed by session and never fetched live. "Sustained g for k years" means the best
k-year endpoint CAGR anywhere in a sixteen-year read, endpoints positive, deliberately
the generous reading, and a best stretch can start at a trough, so every figure carries
its window. Coverage is honest per column: revenue history under the current tags
reaches about ten years for many filers, so the ten-year revenue column counts 8 names
where the ten-year owners'-earnings column counts 21, and the table says so rather than
pooling around it.

It refuses everything the Tragic Algebra Analyzer refuses, and where that page withholds
a verdict and still shows its measurements, this page prints no implied number at all:
financials route to the Financials Checker, IFRS filers to the Non-US Checker, and
missing share counts stop with the reason stated. Umbrella-partnership C-corps solve on
the as-exchanged basis, consolidated earnings over the full Class A + Class B count,
behind the same basis statement and named limits as the Tragic Algebra Analyzer, and
stop only when that basis cannot be verified from filed lines. Its
own refusals follow the same rule. A base at or below zero routes to the Inflection
Checker, because an implied growth rate solved from a negative base is noise. A price at
or below the zero-growth IV15 is stated as implying decline rather than solved into a
negative rate. An implied rate beyond 60% a year is stated as beyond any base rate on
record rather than printed as if it meant something. And the output paragraph carries no
adjective: the arithmetic, the windows and the counts, with the judgement left where it
belongs; a self-test scans every output sentence and holds the page to it.

## ⚓ EPV

Bruce Greenwald's Earnings Power Value, the no-growth anchor. Normalized operating
earnings, the **mean** operating margin over the readable window applied to current
revenue, taxed at the median filed effective rate and capitalized at the required return
(default 15%, the kit's standard; the box floors at 8%, Greenwald's own cost-of-capital
neighbourhood, because capitalization at low rates explodes), plus net cash, per share.
The mean is the method: the cycle average is what normalization exists to compute, and
clipping trough years is what it exists not to do, so the median, the latest year and
the full range print beside it on every run, unconditionally. His two judgement
adjustments, depreciation in excess of maintenance capex and SG&A spent on growth,
are boxes with stated defaults of zero, never silent estimates, because filings do not
encode either split. EPV at r is a price, not a property of the company: the price at
which a buyer earns r a year on the business exactly as it stands, growing never.

Beside the standard leg, the kit's signature: the same EPV on SBC-corrected earnings,
the margin recomputed with the true stock-comp cost Ω in place of the GAAP charge, both
legs on the **same** pool of years. That construction is the page's spine: because the
pools are identical and the arithmetic is linear, the gap between the legs is exactly the
capitalized (Ω − G) margin effect and nothing else: the no-growth price of stock
compensation, pinned by a live self-test rather than asserted. A pool needs at least
four readable years (a cycle of two years is not a normalization), and excluded years
(listings, share-funded deals, no-price years under Gate 2) leave it the way they leave
every other pool in the kit.

It refuses what it cannot stand behind. Banks, insurers, REITs and client-asset brokers
stop before the inputs and route to the Financials Checker, since an operating-margin
normalization is the wrong frame for a balance-sheet business. IFRS filers route to the
Non-US Checker. A window too thin to normalize routes to the Tragic Algebra Analyzer,
which prices on net income and does not need it; a normalized margin at or below zero
routes to the Inflection Checker, because a no-growth value built on a negative margin
is a guess wearing arithmetic. Umbrella-partnership C-corps are priced on the same
as-exchanged basis as everywhere else in the kit, behind the same verification, and stop
when it cannot be read. Where capitalized earnings power does not cover net debt, the
per-share figure floors at \$0.00 with the arithmetic shown unfloored beside it; and
with no live price the growth-payment split is refused rather than computed against a
default.

Four filers that present no operating subtotal, ADP, HRB, PBI and BBW, are admitted by
a derived-OI registry local to this page (19 Sep 2026). These filers never tag
OperatingIncomeLoss, so every window year used to refuse; the registry derives each
year's operating income from the filer's own tagged lines (ADP: revenues minus total
expenses plus the interest expense presented inside them; HRB: revenues minus costs,
which is already the filer's own operating total; PBI: the same with a two-element
interest splice, every spliced year verified against its own printed statement face;
BBW: gross profit minus SG&A) and admits a year only when a completeness bracket, the
same filed lines summed against pretax income, closes within a \$2 float epsilon. The
bracket is also the vintage pin: a year whose tag vintages diverge fails it and refuses
with the reason printed, and PBI's FY2016 is refused permanently because its era's
interest tagging is not reconcilable. Every term is a filed figure; no ratio
apportionment exists anywhere on the route, and the derivation, its bracket and the
route's caveats print hand-checkable per year under Notes and detail. A docket finding
worth recording: the brief assumed these names would need the per-filing instance
route, and zero of the four did, every derivation input being a standard
companyconcept tag, which is why the registry lives on this page alone and every
future docket entry is a single-file deploy.

The point of the anchor is the triad: EPV here, the price there, and the difference,
stated in dollars per share and as a share of the price, is the growth payment, the
part of the price that is a bet on the future. The Expectations page states the growth
path that payment implies, which closes the loop: one page prices zero growth, the other
prices the growth the market is charging for. One honest limit, stated on the page:
Greenwald's reproduction-cost leg is not built, so the asset-value-vs-EPV comparison
that separates a franchise from an ordinary business is out of scope. EPV here is the
no-growth anchor only, and nothing on the page calls it more than that.

## 🪄 Magic Formula

Joel Greenblatt's two legs from *The Little Book That Beats the Market*: earnings yield,
which is EBIT over enterprise value (market cap plus total debt minus excess cash), and
return on capital, the same EBIT over working capital plus net fixed assets. His definitions, his
exclusions, one ticker at a time, from the filings.

The refusal the page is built on: the Magic Formula is a market-wide **ranking**.
Greenblatt's screen orders thousands of companies by the two legs combined, and a ranking
needs a universe this kit does not fetch. So the page computes the legs and shows where
the ticker sits against a small pinned table of the names this kit reads. The table
carries its capture date, the yield column ages with it (return on capital moves only
with new 10-Ks), and the page says so. It states position per leg (a higher yield than
so many of the table's names, a higher return on capital than so many) and never
combines the two into one score, because that combined score *is* the ranking operation,
and running it on a few dozen names would be a fake universe wearing the method's name.

Beside each leg, the kit's signature: the same leg on SBC-corrected EBIT (the GAAP charge
added back, the true stock-comp cost Ω taken out). Capital and enterprise value are
identical between the legs, so the yield gap is exactly (G − Ω)/EV and the capital gap
exactly (G − Ω)/capital, pinned by a live self-test, not asserted. A formula screened
on pre-SBC earnings is exactly where the hidden cost hides.

The awkward parts are handled in the open. Where no operating-income subtotal is filed,
mostly in older filer histories, EBIT is derived as revenue minus the filed all-in expense
total, with the subtraction printed as arithmetic on the page, a note naming any filed
interest line the total swallows (as the filing signs it), and a reconciliation line
bracketing the derived figure against filed pretax income. Excess cash follows a stated
convention shared with the 100-Bagger Checker's ROIC waterfall: cash up to a working
floor (default 2% of revenue, a box you can change) stays in working capital, the rest
nets out of enterprise value. No published figure exists for that split, so the page
names it as a convention rather than pretending it is Greenblatt's. Where the capital
base is nearly empty, the asset-light royalty and services shapes, the return-on-capital
figure prints with a caption calling it the artifact of a nearly empty denominator,
because at that extreme the ratio stops carrying information. Balance-sheet lines a
filer stopped presenting are excluded at zero and named, never silently carried forward
a decade.

His exclusions are the gate's reasons. Banks, insurers, REITs and client-asset brokers
stop and route to the Financials Checker, because Greenblatt excludes financials himself, the
rare case where the author's rule and this kit's gate agree exactly. Utilities stop and
route to the Tragic Algebra Analyzer, with the detection named as SIC-range honest.
One boundary is decided in the open: managed care, which the kit's other pages treat as
insurance but Greenblatt's own screen publishes as health care. This page follows the
method's boundary, computes the legs behind a banner stating the decision, and defaults
excess cash to zero for the class since the pool likely backs policy liabilities. The
year table shows each year as filed, no averaging: cyclicality is shown, not smoothed;
normalization is EPV's job, and its page does it properly.

## 🧊 Net-Nets

Benjamin Graham's net-current-asset-value test from *Security Analysis* and *The
Intelligent Investor*: current assets minus total liabilities minus every tagged claim
ahead of the common (preferred stock, temporary equity, minority interest), per share,
against the price. Graham's two-thirds buying threshold prints as his criterion and
nothing more. Deducting minority interest and temporary equity is this project's own
addition to his test, labelled as such on the page, because today's consolidated balance
sheets carry claims his era's statements did not present.

Where a filer does not tag a total liabilities line, the page adds the filed current and
noncurrent halves and prints the addition, or refuses if it cannot; it never derives
liabilities from the equity side. Every figure is an annual report's own year-end balance
date: quarterly balance sheets are excluded, nothing fills or carries across years, and a
year missing a line shows a dash naming it. No income statement is read anywhere on the
page.

Most companies this kit reads print NCAV far below the price, often negative. The page
says so plainly, because that is the normal answer today, not a failure: true net-nets
are nearly extinct among filers large enough to file readable XBRL. NCAV is a liquidation
floor, not a going-concern value, and a real wind-down realizes assets below book, so the
true floor is lower than the printed one.

## 🩺 Piotroski F-Score

Joseph Piotroski's nine binary tests from "Value Investing: The Use of Historical
Financial Statement Information" (2000), computed with his definitions and his scaling:
ROA, cash flow and asset turnover scale by beginning of year total assets, and the
leverage ratio by average total assets, so three of the tests need three consecutive
year end balance dates and the page says so. The nine: positive ROA, positive operating
cash flow, improving ROA, cash flow above net income, leverage not rising, improving
current ratio, no new shares issued, improving gross margin, improving asset turnover.

Every test prints its own filed inputs and arithmetic, so each line can be checked by
hand against the filing. The leverage test passes on unchanged as well as lower, stated
on the page as this project's reading of the paper's intent, because a strict fall would
fail every company that has no debt and keeps none. The share test reads the same
year end share counts the main page grades, and beside the pass or fail it prints the
decomposition: what part of the year's change was employee compensation and what part
was tagged corporate issuance such as acquisitions, offerings or conversions.

A test whose inputs the filings do not carry for the needed years refuses by name: the
element, the year, and where a balance line went stale, the last year it was filed. A
stale line is never served as zero into a deterioration test. The summary counts tests
passed out of tests readable, with the denominator stated plainly. Fewer than five
readable tests refuses the page, because a score on a minority of the tests is not the
F-Score. Change tests demand consecutive fiscal years, 330 to 400 days between year end
dates, and refuse across a fiscal year end change rather than bridge it. Banks,
insurers, REITs and client-asset brokers route to the Financials Checker, because the
leverage and liquidity tests presume a nonfinancial balance sheet; foreign filers route
to the Non-US Checker. The paper found the score does its work among cheap, unloved
names, which is what the Net-Nets and Inflection pages surface, and the page is built
to sit behind them.

## 🪙 Dividends

The filed dividend record first, with the discipline the record needs. Dividends per share are read for every year the toolkit's window covers, from one reporting element for the whole series, never stitched together from different elements, because a seam between declared and paid amounts can invent a raise out of pure timing. The streak label says how long the company has paid and raised within the readable window, which is the window this page reads and not the company's full history. Growth is the plain rate between the first and last verified years, with both years named and no smoothing. A raise only counts between fiscal years whose end dates sit 330 to 400 days apart, so a fiscal year-end change refuses instead of pretending the years line up, and a series that crosses a stock split is refused for streak and growth purposes, because a split halves the filed figure exactly the way a cut does, and this page will not guess which happened. Where the per-share line and the dollars-paid line disagree about the implied share count, the gap is noted with its size, never resolved silently.

The reason this page exists is the coverage pair. For the latest engine year it shows the dividend as a fraction of reported earnings, and beside it the same dividend against owners' earnings after the true cost of stock compensation, using the toolkit's pooled delta-E. The second number is the first divided by delta-E, and the page prints that arithmetic: a dividend taking 40 cents of each reported dollar takes 50 cents of each dollar that reaches you at a delta-E of 80 percent. Negative or zero earnings refuse the ratio out loud rather than print a meaningless percentage. A company that has never tagged a dividend gets a clean answer saying so, which is different from a payer whose data went stale, and the page names which one it found. There is no dividend discount model here by decision: a Gordon model needs an assumed growth rate, and the Expectations page already answers the price implied question rigorously.

## 📈 Return Calculator

A plain compound-return calculator (ending amount, required return, years to target,
required contribution) with saved scenarios. Nothing in it is wired to the valuation
pages, and it sits last in the menu on purpose.

---

## 🚀 Features

* **SEC EDGAR ingestion**: annual facts only, filtered on period duration and deduped by
  filing, with an IFRS fallback for foreign issuers. Rate-limited to ~6.7 req/s with backoff.
* **Watchlist mode**: up to 25 tickers ranked by ΔE, with CSV export. IV15 appears only
  where inputs pass every sanity check.
* **Stress testing**: downgrade the tier and cut growth, then re-value.
* **Calibration**: enter a published IV15 and solve for the growth it implies.
* **Structural adjustments**: stock splits restated onto a current basis (Gate 3
  continuity), listing years and share-funded acquisitions excluded, non-compensation
  issuance deducted from ΔS.
* **Guards that refuse to guess**: dual-class share counts, implausible P/E ratios,
  ΔE outside a meaningful range, financial-sector structures, unbounded growth seeds, and
  balance-sheet lines that stop before net income does all produce a warning rather than a
  confident wrong number. A forward year that is a loss on a profitable record (a one-off
  write-down) seeds owners' earnings from the five-year median and says so.
* **Works at microcap scale**: the year-by-year table chooses its own precision from the
  figures in it, so a company with 3M shares reads in tenths or hundredths of a million
  rather than rounding its stock comp to zero.

---

## ⚠️ Known limitations

* **Financials on the analyzer pages.** The Tragic Algebra Analyzer detects banks, insurers
  and REITs, zeroes net cash, withholds the verdict and routes to the Financials Checker,
  which prices them on tangible book or FFO, the toolkit's own design, labelled as such.
  Exchanges and the remaining financial structures are priced by no page yet.
* **Complex structures.** Umbrella-partnership C-corps are priced on the as-exchanged
  basis (filed consolidated net income over the full Class A + Class B count) with two
  named limits stated on the page and never adjusted for: the Tax Receivable Agreement
  (a real liability transferring value to pre-IPO holders outside the arithmetic) and the
  NCI tax-status difference (the noncontrolling slice of LLC income is pre-tax at the
  member level). Both flatter the figures; the page says so. A filer whose consolidated
  legs cannot be read and verified is still refused with the reason stated.
* **Bundled line items.** Burry reads the 10-K footnotes by hand because filers combine
  line items; the buyback line often carries RSU withholding tax. Because Ω = C + V, that
  particular error cancels here under Burry's tax treatment assumption (V is overstated by
  exactly what C is understated by), and this tool never derives price from T/W, so the
  channel that corrupts his figures does not apply. Filers reporting a single net proceeds
  line are still a genuine gap.
* **M&A share issuance.** Deducted where XBRL tags it, and whole years are excluded when the
  share count jumps more than 15%. Smaller untagged issuance still inflates ΔS.
* **Owner earnings normalisation.** Where ΔE is negative or absurd, the figure must be set by
  hand. Burry does the same: DocuSign's ΔE is deeply negative, yet he assigns ~$195M of
  forward owner earnings on judgement.
* **Paylocity** remains unreconciled against its published IV15. Burry states that he applies
  a judgement discount to Paylocity's ΔE rather than the calculated figure; its size is not
  recoverable from the article, so the app cannot reproduce it.
* **Cash-flow lines that stop early.** A withholding or proceeds line that ends while stock
  comp continues shows in the tag panel but is not yet flagged in the notes.
* **Fiscal year-end changes.** Years are labelled by the calendar year they end in, so a
  company that moved its year end (Build-A-Bear, 2018) reads as having a missing year. The
  note now says which of the two it might be; it cannot yet tell them apart.

---

## 🛠 Setup

The SEC requires a real contact email in every request header and blocks generic user agents,
so this must be set before anything will load. It is read from Streamlit secrets or an
environment variable, never from the source, so it stays out of the repository.

### Deployed on Streamlit Community Cloud
App → Settings → Secrets:

```toml
sec_contact = "you@example.com"
```

### Running locally

```bash
pip install -r requirements.txt
export SEC_CONTACT="you@example.com"     # Windows: set SEC_CONTACT=you@example.com
streamlit run Home.py
```

Or create `.streamlit/secrets.toml` with the same `sec_contact` line; Streamlit reads it
automatically. **Add `.streamlit/secrets.toml` to your `.gitignore`** so it is never
committed.

Use an address you actually monitor. The SEC's fair-access policy exists so they can contact
you if an app misbehaves, and a dead address risks a block. A dedicated one is sensible, since
anything in a public repo gets scraped.

If the contact is missing the app shows a warning at the top and every lookup fails.

Dependencies: `streamlit`, `pandas`, `requests`. Nothing else.

---

## ⚖️ Disclaimer

Educational and analytical software. Not financial, tax, or investment advice. Outputs depend
on estimates you supply: change the growth rate and the answer changes a great deal. Methods
follow the published writing of the authors named on each page; this project is independent
and is not affiliated with or endorsed by any of them, including Michael Burry or Scion Asset
Management.

---

Free, and staying free. If it's been useful: [ko-fi.com/investortoolkit](https://ko-fi.com/investortoolkit)
