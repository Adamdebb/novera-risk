# Decision log

Every design question asked during the build, the options offered, the recommended option
(marked ★), the choice made, and where it landed. Use it to revisit choices: a decision is
easy to change while the module is young and expensive once other modules depend on it.
Dates are the day the choice was made.

Legend: **Choice** is what was picked. **Where** names the code or record that implements it.
**Revisit** notes what would change if the choice were reversed.

---

## Round 1 — Project setup (2026-09-14)

### 1.1 Platform name
- Options: Novera ★ · Veyra · Valyra · working name only
- Choice: **Novera, provisional, must stay renameable**
- Where: `settings.platform_name`, `scripts/rename_platform.py`, ADR 0005
- Revisit: trademark and domain clearance not done. Rename is a five-minute job as long as
  the brand stays in config and no one hard-codes it.

### 1.2 Market-risk MVP instrument set
- Options: one product per asset class ★ (10 products) · rates-heavy set · minimal five
- Choice: **One per asset class**
- Where: `domain/instruments.py`, `pricing/` (PR-001 to PR-009), roadmap Phase 2
- Revisit: Phase 6 adds repos, swaptions, IR futures, single-name CDS, ETFs, exotics.

### 1.3 Market data
- Options: synthetic first, real adapters later ★ · real free data from day one · fully synthetic only
- Choice: **Synthetic first**
- Where: `simulation/market_data.py` (SIM-001); adapters added in Phase 3 (MD-001)
- Revisit: `novera fetch` can now overlay real history; the demo stays synthetic by default.

### 1.4 Version control
- Options: local git, GitHub later ★ · create private GitHub repo now · no git
- Choice: **Private GitHub repo now**
- Where: https://github.com/Adamdebb/novera-risk, commits as Adamdebb with the GitHub
  no-reply address (the account blocks pushes exposing a private email)

---

## Round 2 — Simulated world (2026-09-14)

### 2.1 Git author name
- Options: Oussama · GitHub username ★
- Choice: **GitHub username (Adamdebb)**

### 2.2 GitHub repo name
- Options: novera-risk ★ · novera · skip
- Choice: **novera-risk**

### 2.3 Simulated organisation
- Options: Global Macro Bank, ~1,500 trades ★ · smaller bank, ~300 · multi-strategy hedge fund first
- Choice: **Global Macro Bank, ~1,500 trades**
- Where: `simulation/organisation.py`; 3 legal entities, 14 desks, 29 books, 30 counterparties

### 2.4 Synthetic history length
- Options: 3 years daily ★ · 5 years · 1 year
- Choice: **3 years daily** (783 business days)
- Revisit: 5 years would allow a longer backtest window than the current 250 + 250 days.
- Superseded 2026-09-15 by 20.1: five years, built as this three-year core plus a two-year extension.

---

## Round 3 — Market data detail (2026-09-14)

### 3.1 Volatility surfaces
- Options: ATM term structure plus smile ★ · ATM term structure only · flat vol
- Choice: **ATM plus smile** (5 expiries × 5 moneyness points per underlying)
- Where: `market_data/vol_surface.py`, SIM-001

### 3.2 Crisis episodes in the history
- Options: two stylised episodes ★ · calm only · replay real crises later
- Choice: **Two stylised episodes** (risk-off crash, rates shock)
- Where: `DEFAULT_EPISODES` in `simulation/market_data.py`; named real crises arrive with `novera fetch`

### 3.3 What to build after market data
- Options: pricing engine for all ten products ★ · EOD workflow skeleton first · dashboard shell first
- Choice: **Pricing engine first**

---

## Round 4 — Risk engine (2026-09-14)

### 4.1 VaR convention
- Options: 99% 1-day historical simulation, 2-year window, plus 97.5% ES ★ · 99% 1-year window ·
  95% exponentially weighted
- Choice: **99% 1-day HS over 500 days, with 97.5% ES**; 10-day by square root of time
- Where: `risk/var.py`, MR-002, MR-003
- Revisit: the window is a `VaRConfig` field; the challenger and reconciliation already
  demonstrate window sensitivity.

### 4.2 VaR revaluation method
- Options: full revaluation plus sensitivity-based challenger ★ · full revaluation only ·
  sensitivity-based only
- Choice: **Full revaluation official, delta-gamma-vega as challenger**
- Where: MR-002, MR-004; the Compare view on the VaR page

### 4.3 Seed a limit hierarchy
- Options: seed the full hierarchy calibrated so planted problems breach ★ · firm and desk VaR
  and stress only · no limits yet
- Choice: **Full hierarchy** (79 bank limits)
- Where: `simulation/limits.py`, MR-006
- Revisit: amounts were hand-calibrated twice (after the option restrike); recalibrate after
  any generator change.

---

## Round 5 — Workflow (2026-09-14)

### 5.1 What next after the risk core
- Options: EOD run plus dashboard ★ · dashboard first, persistence later · counterparty engine first
- Choice: **EOD run plus dashboard**

### 5.2 P&L explain method
- Options: full-revaluation waterfall ★ · sensitivity-based · both, sensitivity as challenger
- Choice: **Both, sensitivity as challenger**
- Where: MR-007; unexplained residuals feed data quality (`PNL_UNEXPLAINED`)

### 5.3 Data-quality handling of planted problems
- Options: run anyway, verdict AMBER, flag affected trades ★ · block publication on RED ·
  auto-repair with proxies
- Choice: **Run anyway, AMBER, flag**
- Where: DQ-001; the run always completes, RED is advisory
- Revisit: a market-data proxy module (auto-repair) is still a candidate for Phase 6.

---

## Round 6 — Breach workflow (2026-09-14)

### 6.1 Breach lifecycle
- Options: OPEN → ACKNOWLEDGED → ESCALATED → CLOSED with actions logged ★ · simple OPEN → CLOSED ·
  full approval matrix
- Choice: **Full approval matrix** (lifecycle plus temporary limit increases with approvers and expiry)
- Where: `limits/workflow.py`, MR-008; desk limits to Head of Market Risk or CRO, firm and
  business limits and any increase above 25% to the CRO, requester cannot self-approve,
  45-day maximum

### 6.2 Where actions are taken
- Options: API write endpoints plus Streamlit buttons ★ · CLI only · Streamlit writes directly
- Choice: **API endpoints plus dashboard buttons** (and a `novera breach` CLI)

### 6.3 Actor identity
- Options: named actor field, no auth ★ · simple API key per role
- Choice: **Named actor, no authentication**
- Revisit: roles and authentication are still open; the audit trail is complete but unverified.

### 6.4 Second simulated business day
- Options: add a T+1 snapshot with new trades ★ · not yet
- Choice: **Yes**; later extended by the scheduler's day advance

---

## Round 7 — Risk Copilot (2026-09-14)

### 7.1 API key
- Options: user adds a key to .env ★ · build with the scripted provider only for now
- Choice: **Scripted provider for now**; Anthropic provider ready when `ANTHROPIC_API_KEY` is set
- Where: `ai/provider.py`, AI-001
- Revisit: the live Claude path has never been exercised; verify and tune prompts once a key exists.

### 7.2 Copilot powers
- Options: read, explain and run what-if scenarios ★ · read and explain only · also take breach
  actions with confirmation
- Choice: **Read, explain, what-if**; no writes (ADR 0003)

### 7.3 Model
- Options: Claude Opus 5 ★ · Claude Sonnet 5
- Choice: **Claude Opus 5** (`settings.llm_model = claude-opus-5`)

### 7.4 Morning commentary
- Options: automatic at the end of each EOD run ★ · on demand only
- Choice: **On demand only** (button on the Copilot page, `POST /copilot/commentary`)
- Revisited in 38.1: drafted automatically after every EOD as well.

---

## Round 8 — Phase 3 operations (2026-09-14)

### 8.1 Scheduler
- Options: in-process scheduler command ★ · cron-friendly one-shot only · Prefect
- Choice: **In-process** (`novera schedule`, retries, job log, day advance by bootstrap)
- Where: `workflows/scheduler.py`, `simulation/advance.py`, OPS-001

### 8.2 Alerts
- Options: alert log plus Slack webhook and email if configured ★ · database and dashboard only
- Choice: **Stored always, Slack and email when configured**
- Revisit: no channel has been exercised live; only fake channels in tests.

### 8.3 Independent challenger
- Options: simulated vendor feed with planted differences ★ · ingestion and gap only
- Choice: **Simulated feed with four planted differences** (scope, market-data timestamp,
  pricing model, methodology)
- Where: `simulation/vendor_feed.py`, `reconciliation/`, MR-009

### 8.4 Real market-data adapters
- Options: build adapters, keep synthetic as default ★ · defer
- Choice: **Build now, synthetic default** (FRED with key, Yahoo, Coinbase)
- Where: `market_data/adapters/`, MD-001; Yahoo and Coinbase checked live, FRED with recorded responses

---

## Round 9 — Phase 2 leftovers (2026-09-14)

### 9.1 Monte Carlo VaR
- Options: correlated shocks, full revaluation, 2,000 paths ★ · 10,000 paths on the delta-gamma-vega
  approximation · skip
- Choice: **10,000 paths on the delta-gamma-vega approximation**
- Where: `risk/monte_carlo.py`, MR-010
- Revisit: Gaussian tails put it below historical VaR; a full-revaluation variant would need
  the process pool and about four times the historical run time.

### 9.2 Backtesting
- Options: static-portfolio hypothetical backtest over 250 days ★ · live runs only
- Choice: **Static plus a live series that grows per run**
- Where: `risk/backtest.py`, MR-011

### 9.3 Concentration and liquidity
- Options: full set ★ · concentration only
- Choice: **Full set**
- Where: `risk/concentration.py`, `risk/liquidity.py`, MR-012, MR-013 (volume and spread assumptions are synthetic)

### 9.4 Risk pack format
- Options: HTML and PDF plus Excel ★ · Excel only · HTML only
- Choice: **HTML, PDF and Excel**
- Where: `reporting/pack.py`, RP-001; PDF through Playwright's Chromium

---

## Round 10 — Phase 4 counterparty risk (2026-09-14)

### 10.1 Exposure simulation
- Options: Monte Carlo, 1,000 paths, 12 dates, full revaluation ★ · 500 paths, 8 dates ·
  analytic per trade
- Choice: **1,000 paths, 12 dates, full revaluation** (about three minutes)
- Where: `counterparty_risk/simulation.py`, `exposure.py`, CR-001

### 10.2 Collateral model
- Options: threshold, MTA, independent amount, 10-day margin period ★ · perfect daily collateral
- Choice: **Full CSA terms with 10-day MPoR**; gross and collateralised profiles stored, CSA what-if on stored paths
- Where: CR-002

### 10.3 CVA
- Options: unilateral CVA from PD with DVA from own spread ★ · unilateral only
- Choice: **CVA and DVA**, bilateral CVA reported; wrong-way indicator separate
- Where: CR-003, CR-004; wrong-way flag threshold raised from 0.3 to 0.5 after review

### 10.4 Initial margin
- Options: simplified SIMM now ★ · defer to Phase 5
- Choice: **Deferred to Phase 5** (then delivered there as REG-004). SIMM needs a weighted-
  sensitivity aggregation framework, which is the same machinery FRTB SA needed a phase later;
  building it in Phase 4 for one consumer would have meant rebuilding it in Phase 5.
- Where: `regulatory/simm.py`, REG-004; consumed by `counterparty_risk/exposure.py`
  (`collateralise`, the `initial_margin` argument shipped in Phase 4 defaulting to zero, so
  Phase 5 supplied a number rather than rewiring the engine)
- Revisit: parameters are published-style approximations, not the licensed ISDA calibration, so
  the figure must not be used for margin calls. IM is modelled one-directionally (received only)
  and with no AANA in-scope test. See Round 31.

---

## Round 11 — Phase 5 segment modules (2026-09-14)

### 11.1 Order
- Options: bank first, then hedge fund ★ · hedge fund first · a thin slice of each
- Choice: **Bank first**

### 11.2 Bank scope
- Options: FRTB SA, SA-CCR, SIMM-lite, capital attribution ★ · add FRTB internal models ·
  capital only, no initial margin
- Choice: **Add FRTB internal models** (ES with liquidity horizons, P&L attribution test, NMRF)
- Where: `regulatory/`, REG-001 to REG-006
- Revisit: parameters are published-style approximations, not licensed calibrations.

### 11.3 Hedge-fund data
- Options: second simulated organisation with NAV and investors ★ · reuse the bank book as a fund
- Choice: **Second organisation** (Meridian Multi-Strategy Fund, own database, firm selector)
- Where: `simulation/fund.py`, `novera simulate --template hedge_fund`

### 11.4 Hedge-fund modules
- Options: margin, leverage, factor exposures, redemption stress, attribution ★ · margin, leverage,
  factors only · all of the above plus crowding
- Choice: **All plus crowding**
- Where: `fund/`, HF-001 to HF-006

---

## Round 12 — Phase 6 instrument breadth (2026-09-14)

### 12.1 Products
- Options: all eight (repo/reverse repo, IR future, swaption, single-name CDS, commodity option,
  ETF, mutual fund, equity barrier and digital) ★ · the four flow products first (repo, IR
  future, swaption, single-name CDS) · the four investor products first (ETF, mutual fund,
  commodity option, exotics)
- Choice: **All eight**
- Where: `domain/instruments.py`, `pricing/breadth.py`, `pricing/exotic_formulas.py`,
  PR-010 to PR-016; nine new bank books (USD_REPO, USD_STIR, EUR_STIR, SN_CDS_IG, SN_CDS_HY,
  ENERGY_OPTIONS, METALS_OPTIONS, FUNDS_ETF, EQ_EXOTICS); fund strategies hold the new
  products in their existing books.
- Revisit: the day-1 limit calibration moved with the larger book (see 4.3 and "second look" 5).

### 12.2 Funds
- Options: look-through to constituents ★ · price funds as a single equity-like factor per
  fund · look-through for ETFs only, single factor for mutual funds
- Choice: **Look-through to constituents** (synthetic baskets; pricing, sensitivities and VaR see
  through automatically; MR-014 reports direct versus via-fund exposure)
- Where: `pricing/breadth.py` (PR-015), `risk/lookthrough.py` (MR-014), dashboard
  "Concentration & liquidity", Copilot tool `lookthrough`

### 12.3 Exotics
- Options: closed-form barrier and digital under Black–Scholes, benchmarked to QuantLib ★ ·
  Monte Carlo under local vol · both
- Choice: **Closed form, QuantLib-benchmarked** (Reiner–Rubinstein barriers, cash-or-nothing
  digitals, all cases within 1e-8 of QuantLib; residual risk add-on in FRTB SA)
- Where: `pricing/exotic_formulas.py`, PR-016, `regulatory/frtb_sa.py`
- Revisit: barrier risk near the barrier is understated under flat vol; a Monte Carlo
  challenger would be the natural Phase 7 model-risk item.

### 12.4 Market-data proxies
- Options: proxy stale or missing factors with an audit trail ★ · keep pricing off the raw
  snapshot and only flag · proxy silently
- Choice: **Proxy with an audit trail** (interpolate missing nodes, roll missing singles,
  re-level stale families with a proxy family; INFO findings; raw snapshot kept as the run's
  market snapshot id; every downstream engine rebuilds the same proxied market from the stored
  actions)
- Where: `market_data/proxies.py`, MD-002, `NOVERA_PROXIES_ENABLED`, run frame `md_proxies`,
  dashboard "Data quality"

---

## Round 13 — Phase 7 AI and automation (2026-09-15)

### 13.1 MCP server
- Options: stdio MCP server over the same Copilot tools ★ · stdio plus HTTP transport · defer
- Choice: **Deferred at first, then built the same day** after discussing confidentiality and
  cost: stdio server over the Copilot tools, read-only by default, agents behind a flag, every
  call audited; the model runs wherever the client points it (AI-004).
- Where: `novera mcp`, `mcp_server.py`, AI-004

### 13.2 Agents
- Options (multi-select): breach investigation ★ · scenario suggestion ★ · model-validation
  report drafting · ISDA/CSA document ingestion
- Choice: **All four**. Every agent gathers its evidence deterministically from stored runs
  and the engine, then drafts text through the provider (Claude when a key exists, templated
  otherwise); numbers in the drafts come only from the evidence.
- Where: `ai/agents/`, AI-002 (agents), AI-003 (document ingestion)

### 13.3 Portfolio Lab
- Options: dashboard page plus CLI ★ · CLI only · defer
- Choice: **Dashboard page plus CLI** (`novera lab`): pick a template, choose the problems to
  plant and their size, run into a sandbox database, see what the platform detected.
- Where: `lab/`, LAB-001

## Round 14 — API contract before any front-end change (2026-09-15)

Context: the owner asked whether to move the dashboard to React. Decision: stay on Streamlit
while the data model keeps changing (every Streamlit screen is disposable; a React screen is
not), but make the API the contract a React app would build on, so the port is a
screen-by-screen job whenever a screen's response has stopped moving. A React rebuild of the
overview screen over the real stored runs was produced as a private artifact to judge the gain.

### 14.1 Response typing depth
- Options: typed rows, shapes unchanged ★ · strict everywhere, grouped endpoints restructured
  to `{by, rows}` · errors plus morning-dashboard endpoints only
- Choice: **Typed rows, shapes unchanged.** Every route declares a Pydantic response model.
  Grouped rows (VaR by desk, stress by asset class, SA-CCR add-ons, SIMM by class) declare
  their fixed fields and carry the dimension as an extra field, since its name depends on
  `by`. Models allow extra fields so a new engine column reaches a screen before the schema
  catches up; `tests/test_api.py` fails when the schema is behind the engine.
- Where: `api/schemas.py`, `api/app.py`
- Reversal: restructuring grouped rows is a breaking change to the client and screens.

### 14.2 Error format
- Options: RFC 9457 Problem Details with a machine `code` ★ · keep FastAPI `{detail}` and add
  a `code`
- Choice: **Problem Details** (`application/problem+json`: type, title, status, detail,
  instance, `code`, `context`). Codes: `RUN_NOT_FOUND`, `NOT_FOUND`, `LAB_NOT_FOUND`,
  `RECONCILIATION_NOT_FOUND`, `RISK_PACK_NOT_BUILT`, `WORKFLOW_CONFLICT`, `VALIDATION_FAILED`.
  Both clients raise the same `ApiError` classes, so the dashboard no longer imports an engine
  exception (`WorkflowError`) to tell a workflow rule from a missing run.
- Where: `api/errors.py`, `api/client.py`, `ui/app.py`

### 14.3 Path prefix
- Options: keep unversioned ★ · move to `/api/v1` now
- Choice: **Keep unversioned** while there is one consumer; version when a second appears.

### 14.4 OpenAPI file
- Options: commit `docs/api/openapi.json` with a staleness test ★ · serve on demand only
- Choice: **Commit it.** `scripts/export_openapi.py` writes it; the test suite fails when it is
  stale, so a client project can generate types from git without running the server.
- Also in this round: `NOVERA_CORS_ORIGINS` (comma-separated; empty means no browser origin
  is allowed) and `GET /runs/{run_id}/risk-pack/{html|pdf|xlsx}` so a browser client can read
  a pack that `POST /runs/{run_id}/risk-pack` built; the dashboard uses it through the client.

## Round 15 — Reference data page (2026-09-15)

Context: the owner asked where to see legal entities, desks, books and counterparties; until
now only the API (`/organisation`) and the run-scoped counterparty page showed them.

### 15.1 Tree rendering
- Options: expanders with an indented bullet tree inside ★ · cascading selectors · one
  indented text block
- Choice: **Expanders with indented bullets.** One expander per business (or legal entity, or
  counterparty group); desks, books, traders, netting sets and CSA terms as nested bullets.
  A filter box narrows every tree to matching ids and names and opens the matches.
- Revised the same day: desks are expanders of their own inside the business or legal-entity
  expander (Streamlit 1.63 allows nesting), so a desk's books open only on click.
- Where: `ui/app.py` page "Reference data"

### 15.2 Run figures on the page
- Options: trade counts and PV per node ★ · trade counts only · static reference only
- Choice: **Static reference only.** The page shows what is stored, not what a run did; the
  drill-down and counterparty pages carry the figures.

### 15.3 Legal entities in the organisation tree
- Options: toggle between business and legal-entity grouping ★ · business only, entity as a tag
- Choice: **Toggle.** Default firm → business → desk → book with the entity on each book; the
  legal-entity view regroups as firm → entity → desk → book for finance and regulators.
- Also in this round: `GET /reference/counterparties` (counterparties, netting sets, CSAs, no
  run needed) and `/organisation` resolves the firm stored in the database instead of assuming
  the bank, so the fund face works too.

### 15.4 Product hierarchy
- Options: venue, model, methodology record and defining fields ★ · venue, model, methodology
  only · also the instruments booked in today's snapshot
- Choice: **Venue, model, methodology, defining fields.** A third tree, asset class → product
  (19 products, 6 asset classes), each opening to listed/OTC, the pricing model and version,
  the PR-0xx record, and the fields that define an instrument of that type. Read from code
  through a product catalogue (`pricing/catalogue.py`); a test checks the catalogue against
  the pricer registry, the model names written on valuation rows, and the methodology files.
- Where: `pricing/catalogue.py`, `GET /reference/products`, `ui/app.py`

### 15.5 Risk hierarchy
- Options (multi-select): risk measures ★ · risk-factor universe · limit hierarchy
- Choice: **Risk measures and risk-factor universe.** Two more trees. Measures: risk area
  (market, counterparty, regulatory capital, fund, control) → measure → definition, unit,
  screen, methodology record and version, face; read from a measure catalogue in code
  (`risk/catalogue.py`) that a test checks against `docs/methodology/` titles and versions and
  against the set of MR, CR, REG, HF and DQ records. Risk factors: asset class → factor type
  → underlying, with curve nodes, surface expiries × moneyness and cube expiries × tenors
  summarised per line, read from the stored risk-factor universe. The limit hierarchy was not
  chosen; the Limit management page lists definitions with utilisation.
- Where: `risk/catalogue.py`, `GET /reference/measures`, `GET /reference/risk-factors`, `ui/app.py`

## Round 16 — Limit management module (2026-09-15)

Context: the owner asked for a limit management page, starting with the limit hierarchy,
to grow into the rest of the limit function later.

### 16.1 Page shape
- Options: one module page with tabs ★ · separate page, hierarchy only
- Choice: **One module page with tabs** ("Limit management": Hierarchy now; Utilisation,
  Breaches and Increases as placeholders that point at the existing pages until their content
  moves in, after which those pages are retired).
- Same day: the Limits page was retired. Its only distinct role, opening on breaches and
  warnings, became the module's Utilisation tab (highest utilisation first, trades in scope
  as a column). Breaches and Increases still point at the Breaches page.

### 16.2 Limit rows
- Options: definition plus current status ★ · definition only
- Choice: **Definition plus current status.** Type, scope, amount, warning threshold, owner,
  approver, approval status and effective dates from the definition; utilisation, status and
  the amount in force (temporary increases marked) from the selected run.

### 16.3 Layout
- Options: hierarchy tree with a limit-type toggle ★ · hierarchy tree only
- Choice: **Table, not a tree**, with the toggle. Rows ordered firm → business → desk →
  counterparty with a hierarchy path column; "Group by limit type" reorders by type. Level,
  run-status and text filters; selecting a row opens its full definition. The join of
  definitions, organisation names and run figures happens in the API
  (`GET /limits/hierarchy`), so the screen computes nothing.
- Where: `api/service.py` `limit_hierarchy`, `ui/app.py` page "Limit management"

## Round 17 — Trade extract (2026-09-15)

Context: the owner asked for a page to download all or a filtered set of trades as CSV.

### 17.1 Columns
- Options (multi-select): trade and valuation ★ · instrument terms · per-trade risk
- Choice: **Trade and valuation plus instrument terms.** Each row is the run's valuation of
  the trade joined with the booked trade and its instrument's fields flattened into columns;
  a term a product lacks is empty. A derived `maturity_date` holds maturity, expiry or end
  date, whichever applies. Per-trade risk was not chosen; it stays on the trade lookup.

### 17.2 Filters
- Options (multi-select): hierarchy and product ★ · dates and size · free text and id list
- Choice: **All three.** Multi-selects on the thirteen dimensions, trade-date and maturity
  ranges, minimum |PV| and |quantity|, a search over trade id, instrument id and description,
  and a paste box of trade ids.

### 17.3 Page
- Options: own page with preview ★ · section on the drill-down page
- Choice: **Own page "Trade extract"** after Drill-down: filters, count and PV of the
  selection, a 200-row preview, and the CSV download of the whole selection. The extract is
  an API endpoint (`GET /runs/{id}/trade-extract`, `.csv` and `/options`), so the same file is
  available to any client and is named after the run for reproducibility.
- Where: `api/service.py` `trade_extract*`, `ui/app.py` page "Trade extract"

## Round 18 — Model inventory (2026-09-15)

Context: the owner asked how to be sure each product is priced with the right model, as
distinct from a correctly implemented one. The QuantLib benchmarks prove implementation;
appropriateness needed its own record.

### 18.1 Location
- Options: in code, surfaced everywhere ★ · markdown document only · both, hand-written doc
- Choice: **In code, surfaced everywhere.** `ProductSpec` in `pricing/catalogue.py` gained
  `market_standard`, `simplifications`, `appropriateness` and `validation`. They flow to
  `GET /reference/products`, a new `GET /reference/model-inventory`, the Reference data
  page (a "Model inventory" view and the product cards) and the MV-001 record, whose table
  `scripts/export_model_inventory.py` renders and a test keeps current.
- Where: `pricing/catalogue.py`, `api/schemas.py`, `api/app.py`, `ui/app.py`,
  `docs/methodology/MV-001-model-inventory.md`
- Reversal: a hand-written document would drift from the code; the fields are cheap to keep.

### 18.2 Rating
- Options: three-level rating ★ · text only
- Choice: **Three-level rating**: market standard (5 products), acceptable simplification
  (11), known weakness (3: EUR swaps on a single curve, swaptions without a strike smile,
  barrier options on one flat vol). The record states the rating is a methodology judgment
  by the owner, not a validation opinion, and that AI may quote but not change it.
- Reversal: drop the column; the text columns stand on their own.

### 18.3 Gap test
- Options: swap only ★ · swap and CDS · no tests now
- Choice: **Swap only.** `test_eur_swap_single_curve_gap_is_measured` prices a 7-year EUR
  receiver swap in QuantLib with ESTR discounting and EURIBOR-6M projection (15bp basis) and
  bounds the single-curve error between 0.05% and 1% of PV; measured 0.53%. USD and GBP
  swaps reference SOFR and SONIA, so the single curve is exact for them. The CDS flat-hazard
  gap stays at its stated 5% tolerance.
- Where: `tests/test_pricing.py`, PR-002 limitations, MV-001 "Measured gaps"
- Reversal: none needed; a dual-curve EUR pricer would turn the gap test into a benchmark.

### 18.4 Record
- Options: new record MV-001 ★ · section in 04-governance.md
- Choice: **New record MV-001** "Model inventory and appropriateness", owner Market Risk
  Methodology, with the rating scale, the rendered table, the measured gaps and its own
  validation tests. `04-governance.md` gained a short "Model inventory" section pointing at it.
- Reversal: fold the record into governance; the code and tests would not change.

---

## Round 19 — Market data sources page (2026-09-15)

Context: the owner asked which market data is real and which is synthetic, learned that
only USD Treasuries, spots and crypto have adapters, and asked for a page showing each
market datum, its source, and a potential free and paid source. Built without a question
round (non-interactive session); the choices below are defaults to confirm.

### 19.1 Status source of truth
- Options: derived from the store and the adapter maps ★ · a hand-maintained table
- Choice (default): **Derived.** REAL comes from `market_provenance`, AVAILABLE from the
  adapters' symbol maps, SYNTHETIC otherwise; a family whose nodes differ is PARTIAL.
- Where: `market_data/catalogue.py`, MD-001 "Source catalogue"
- Reversal: a hand table would drift from the adapters within a phase.

### 19.2 Granularity
- Options: one row per family with a factor drill-down ★ · one row per factor only
- Choice (default): **Family rows, factor detail in an expander.** 1,398 factors collapse
  to 121 families (curves, surfaces, cubes, single factors); the free and paid text is
  written per group (29 groups: each rates currency, FX spot, energy curves, FX vol, ...).
- Where: `ui/app.py` "Market data" page, `GET /reference/market-data-sources`
- Reversal: none needed; the route already returns both levels.

### 19.3 Location
- Options: own "Market data" page in the sidebar ★ · a tab under Reference data
- Choice (default): **Own page**, placed before Reference data, since it is the first
  question a reviewer asks about a demo built on simulated history.
- Reversal: move the block under the Reference data radio; no API change.

### 19.4 Candidate sources
- Options: free and paid text per group in code ★ · in the methodology record only
- Choice (default): **In code**, so the page, the API and MD-001 read one catalogue. The
  text names concrete sources (FRED series, ECB and Bank of England curves, Cboe vol
  indices, Markit, Bloomberg pages) and says where no free daily history exists.
- Reversal: none; the record would still point at the module.

---

## Round 20 — Five-year synthetic history (2026-09-15)

Context: the owner asked to extend the synthetic history from three to five years (flagged
in 2.4). A first attempt regenerated five years end to end: the equity and crypto drifts are
tuned to land near the reference levels after three years, so the extra years moved every
level and day 1 showed 22 breaches instead of the four planted ones. Built without a question
round (non-interactive session); the choices below are defaults to confirm.

### 20.1 How the extra years are generated
- Options: keep the three-year core identical and join an earlier extension segment in
  front ★ · regenerate five years end to end and recalibrate all 79 limits · end to end with
  limits calibrated from a target utilisation (item 5 below)
- Choice (default): **Core plus extension.** The last three years (`core_years`) are drawn
  exactly as before, so the limits, the planted breaches, the two episodes, the VaR window
  and the backtest are untouched. The two earlier years come from a second run of the same
  model with its own seed (`seed + 101`) and no episodes, re-levelled so the junction is
  continuous (shift for zero rates, scale for prices, spreads and vols), with the overlap
  day dropped so the junction step is an ordinary daily return.
- Where: `simulation/market_data.py` (`generate_market_data`, `_Sim.prepend`), SIM-001 v1.1.0,
  `MarketSimConfig.years=5.0` and `core_years=3.0`, `novera simulate --years`.
- Reversal: set `core_years` equal to `years` for one segment, and expect to recalibrate
  every limit.

### 20.2 What uses the extra history
- Options: nothing yet, windows unchanged ★ · widen the backtest to use the extra years
- Choice (default): **Windows unchanged.** VaR stays at 500 days and the backtest at
  250 + 250; the extension makes a backtest of up to about 1,050 test days possible later.
- Reversal: none; the data is stored.

---

## Round 21 — Swaption smile (2026-09-15)

Context: the owner asked which vols carry a smile (equity, FX and commodity surfaces do;
the swaption cube did not, item 8 below) and then asked for SABR smile parameters. Built
without a question round (non-interactive session); the choices below are defaults to confirm.

### 21.1 Smile model
- Options: normal SABR with beta fixed at zero ★ · shifted lognormal SABR with beta 0.5 ·
  a moneyness grid like the equity and FX surfaces
- Choice (default): **Normal SABR, beta 0.** Hagan's normal-vol expansion handles low and
  negative forwards without choosing a shift, and the cube stays in normal vol as brokers
  quote it. Rho and nu are risk factors per cube node (`SWRHO:`, `SWNU:`, 96 factors); alpha
  is implied from the ATM cube so the existing `SWVOL:` quote remains the vega instrument.
- Where: `market_data/sabr.py`, `MarketSnapshot.swaption_normal_vol_bp(forward, strike)`,
  `pricing/breadth.py` (swaption model 1.1.0), PR-012 v1.1.0, SIM-001 v1.2.0, MV-001.
- Reversal: drop the two cubes from the universe; a snapshot without them prices flat at
  the ATM vol, as before.

### 21.2 Risk treatment of rho and nu
- Options: full revaluation only, sensitivities unchanged ★ · add rho and nu bump rows ·
  bump rho and nu inside the VEGA row
- Choice (default): **Full revaluation only.** Historical VaR shocks rho absolutely and nu
  relatively; the VEGA row bumps the ATM cube with rho and nu held, so the challenger and the
  FRTB and SIMM vega inputs are unchanged. FRTB IMA gives the smile factors the cube's
  60-day liquidity horizon.
- Reversal: add `SMILE_RHO` and `SMILE_NU` measures in `risk/sensitivities.py` and MR-001.

### 21.3 Simulated smile dynamics
- Options: per-currency mean-reverting paths tied to the episodes ★ · static parameters ·
  a path per node
- Choice (default): **Per-currency paths.** Rho steepens with the crash skew multiplier and nu
  rises with the episode vol multiplier; both are drawn after every earlier family so the
  five-year history of the other 1,398 factors is unchanged and the limit calibration holds.
- Reversal: none needed; the reference levels are in `simulation/reference_levels.py`.

---

## Round 22 — Stress library page (2026-09-15)

Context: the owner asked whether real crises can be replayed (not yet: the history is
synthetic and the named windows are not wired into the run) and then asked for a page
listing the stress tests by category with the shocks used. Built without a question round
(non-interactive session); the choices below are defaults to confirm.

### 22.1 Where the catalogue lives
- Options: engine module read by a reference route ★ · built in the dashboard from the
  run's stress table · a hand-written table in MR-005
- Choice (default): **Engine module** `risk/stress_catalogue.py` behind
  `GET /reference/stress-library`, so the page, the API and MR-005 describe one library and
  a rule change shows up everywhere. The page reads the route and renders (rule 2).
- Reversal: none needed; the route is additive.

### 22.2 Named crises as a third category
- Options: list them with an honest status ★ · hide them until real data is fetched · run
  them on the synthetic history because the dates are covered
- Choice (default): **List with status.** The 2022 and 2023 windows now fall inside the
  five-year synthetic history, so a naive replay would show synthetic moves over a real
  calendar; the status SYNTHETIC_WINDOW says so, and REAL requires provenance for every
  factor across the window. None of the seven runs in the daily EOD.
- Reversal: wire `named_crisis_scenarios` into `run_eod` once a real fetch reaches REAL.

### 22.3 Historical shocks shown
- Options: fourteen headline factors ★ · every factor · family averages
- Choice (default): **Headline factors**, one per market a reviewer asks about, computed
  from the stored history by the same `episode_shocks` the engine uses.
- Reversal: extend `HEADLINE_FACTORS`; the route loads only those series.

---

## Round 23 — Admin page and partial re-runs (2026-09-15)

Context: asked what the EOD skeleton is, the owner learned it has no partial re-run of a
single stage and asked for an Admin page holding administrative configurations, starting
with that one. Built without a question round (non-interactive session); the choices below
are defaults to confirm.

### 23.1 What a partial re-run produces
- Options: a new RERUN run with the parent's tables copied and one stage replaced ★ ·
  overwrite the stage's tables on the parent run · a run holding only the recomputed stage
- Choice (default): **New RERUN run.** Rule 5 says stored results are immutable, and a run
  with a single table would break every screen; copying the parent (about fifty tables, under
  a second) and replacing one stage keeps both. The parent keeps its config hash; the child's
  differs by the `rerun` block.
- Where: `workflows/rerun.py`, `copy_run_frames`, OPS-002.
- Reversal: none; the parent is exactly as it was.

### 23.2 Dependencies between stages
- Options: recompute only the requested stage and list its dependents as not recomputed ★ ·
  recompute the stage and everything downstream · refuse stages with dependents
- Choice (default): **Stage only, dependents listed.** Upstream inputs are recomputed in
  memory (deterministic, so they match the stored numbers, which the tests check); downstream
  tables stay the parent's and `stale_stages` says so on the run, the page and the CLI.
- Reversal: a `--cascade` flag that walks `Stage.dependents` in order.

### 23.3 Workflow side effects
- Options: none, ever ★ · sync breaches when limits are re-run · send alerts
- Choice (default): **None.** A re-run monitors limits and stores the table but never raises,
  escalates or closes a breach, never dispatches an alert, never becomes the latest EOD run and
  never counts in the live backtest. Flowing a corrected result into the workflow is a full
  EOD or a breach action by a named actor.
- Reversal: expose `sync_breaches` behind an explicit admin flag once a sign-off step exists.

### 23.4 Admin page shape
- Options: one page with a configuration selector ★ · one page per admin action · fold into
  Runs & audit
- Choice (default): **Configuration selector**, so later administrative settings (sign-off,
  cascade, scheduler overrides) slot in as entries. Every action names an actor; there is
  still no authentication (item 1 below).
- Reversal: split into pages when a second configuration is heavy.

---

## Round 24 — Sign-off and release (2026-09-16)

Context: the owner asked for a sign-off process with a dashboard page, and an Admin section
to choose the metrics that need signing. Built without a question round (non-interactive
session); the choices below are defaults to confirm.

### 24.1 Unit of sign-off
- Options: per metric, with the run released when every required metric is signed ★ · one
  signature per run · per desk
- Choice (default): **Per metric.** Ten metrics map to the methodology records (VaR and ES,
  stress, limits, P&L, data quality, backtest, concentration, counterparty, capital, fund),
  each with an expected signer role. The value seen is frozen with the signature.
- Where: `workflows/signoff.py`, OPS-003, tables `signoff`, `signoff_policy`, `signoff_release`.
- Reversal: a policy requiring one metric behaves as one signature per run.

### 24.2 Release status
- Options: computed from the current policy, with a release row as the historical record ★ ·
  stored status changed by each action · policy snapshot per run
- Choice (default): **Computed.** Narrowing the policy releases runs that already meet it;
  widening it re-opens them; the row records who completed the release and when, and
  rejecting a required metric withdraws it with an audit event.
- Reversal: snapshot the policy on the run at first sign-off.

### 24.3 RED verdicts
- Options: data-quality sign-off on a RED run is an override needing a comment ★ · RED runs
  cannot be released · RED treated like AMBER
- Choice (default): **Override with comment**, flagged on the signature and the release,
  which is what DQ-001 asked for.
- Reversal: refuse the sign in `signoff.sign` when the verdict is RED.

### 24.4 Who may sign
- Options: named actor, expected signer shown not enforced ★ · enforce the signer role ·
  require two signatures
- Choice (default): **Named, not enforced**, consistent with every other workflow action
  (decision 6.3, item 1 below). Enforcement comes with authentication.
- Reversal: check `actor` against `Metric.signer` in `signoff.sign`.

---

## Round 25 — VaR setup (2026-09-16)

Context: the owner asked for an Admin section where a bank or a hedge fund chooses which
VaR measures to produce daily, which feed limits and which are for information, from a
matrix like the one in `VaR Matrixcsv.csv` (goal, metric, percentile, shocks, compute,
window, lambda). Built without a question round (non-interactive session); the choices below
are defaults to confirm.

### 25.1 Shape of the setup
- Options: a matrix of measures with a goal per row, one LIMIT row per metric ★ · a fixed
  list of switches (VaR on/off, ES on/off) · one config per limit
- Choice (default): **Matrix of measures.** Each row is goal, metric (VaR, ES, stressed
  VaR), confidence, shocks (historical, weighted, Monte Carlo), compute (full revaluation,
  sensitivities), window (years or fixed dates), decay, enabled. The LIMIT row of each
  metric feeds that metric's limit type; the LIMIT VaR row is the headline. Rows sharing
  scenarios and compute share the P&L matrix. Stored per database (so per firm face) and
  frozen in every run's config.
- Where: `risk/var_measures.py`, `workflows/var_setup.py`, OPS-004, table `var_setup`,
  `GET/POST /admin/var/setup`, `novera var-setup`, Admin page "VaR measures".
- Reversal: the defaults reproduce the pre-setup platform exactly; delete the table to go back.

### 25.2 Weighted historical VaR
- Options: weighted historical simulation (Boudoukh, Richardson, Whitelaw) on the same P&L
  matrix ★ · RiskMetrics parametric EWMA on the sensitivities · both
- Choice (default): **Weighted historical simulation**, because it keeps full revaluation
  and only weights the tail; the same code with lambda None is the unweighted measure
  (bit-identical figures). The hedge-fund template uses 95%, one year, lambda 0.94.
- Where: `VaRConfig.decay`, `scenario_weights`, `tail_measures`, MR-015.
- Reversal: an EWMA-covariance row could be added as a fourth `shocks` value.

### 25.3 Stressed VaR window
- Options: a fixed date range inside the stored history, with the bank template proposing
  the most volatile year of the equity index ★ · a named crisis from the stress library ·
  the worst rolling year by portfolio loss
- Choice (default): **Fixed range, proposed from the history.** Real crisis years need the
  history to reach them (`novera fetch`), and the stress library already says which are
  covered. A new limit type STRESSED_VAR carries the limits; none is seeded.
- Where: `VaRConfig.window_start/end`, `scenario_shocks`, `most_volatile_year`, MR-016.
- Reversal: pick the window from the worst portfolio year once runs cover several years.

### 25.4 Limits when the matrix changes
- Options: limit amounts stay, utilisations move with the measure ★ · recalibrate limits to
  the new measure · refuse a change that alters the limit measure
- Choice (default): **Amounts stay.** Switching the demo bank to the hedge-fund template
  drops the firm VaR utilisation because a 95% weighted VaR is smaller than a 99% two-year
  one; that is visible on the Overview and is the point of the demo. An ES limit with no
  LIMIT ES row falls back to the headline's scenarios so seeded limits keep a value.
- Reversal: a limit recalibration action next to the setup.

---

## Round 26 — The assistant is named Novera Analyst (2026-09-16)

Context: while drafting CV text the owner noted that "Risk Copilot" is read as Microsoft
Copilot by most people, and in a bank could raise a false "is this built on Microsoft?"
question. Asked for alternatives, picked **Novera Analyst**, and asked for the rename.

### 26.1 Name
- Options: Novera Analyst ★ · Ask Novera · Risk Desk · Novera Explain · Novera Lens
- Choice: **Novera Analyst**, because everyone understands what a good junior analyst does:
  you ask, they go to the numbers, they come back with a sourced answer. It sets the right
  expectation (reports, never decides), fits ADR 0003, and is not claimed by a major vendor.
  Per ADR 0005 the code reads `f"{settings.platform_name} Analyst"`, so a rename of the
  platform renames the assistant.
- Rejected: Advisor (implies advice, which it does not give), Navigator and Pilot (crowded,
  and Pilot is a step from Copilot), Assistant alone (generic), Agent (already used for the
  four agents of AI-002).

### 26.2 Scope of the rename
- Choice: **everything, including the API and storage.** Module `ai/analyst.py`, classes
  `Analyst` and `AnalystAnswer`, routes `/analyst/*` (OpenAPI regenerated), client methods
  `analyst_history` and `analyst_provider`, dashboard page "Analyst", CLI help, audit actor
  `analyst` and event type `ANALYST_ANSWER`, methodology record `AI-001-novera-analyst.md`,
  test `tests/test_analyst.py`. Earlier rounds of this log keep the old name as history.
- Storage: table `copilot_answer` became `analyst_answer`; `init_schema` copies the rows of a
  legacy table across and drops it, so databases built before the rename keep their answer
  history the next time they are opened for writing (any EOD run or question). The demo
  databases were migrated on the day.
- Reversal: the same substitution backwards; old audit rows keep `COPILOT_ANSWER`.

## Round 27 — Manual full run from the Admin page (2026-09-16)

Context: the owner asked for a button in Admin to run the whole process by hand for the bank
or the fund, and whether it should be merged with the partial re-run. Built without a
question round; the choices below are defaults to confirm.

### 27.1 One Admin configuration or two
- Options: merge into one "Runs" configuration with a mode switch ★ · keep two entries
- Choice (default): **one configuration, "Runs", with two modes**: "Full end-of-day run" and
  "One stage of a stored run". Both launch a run and both name an actor and a reason, so they
  belong together; the mode switch keeps the semantics apart (a full run is the official EOD
  that becomes the latest, synchronises breaches and alerts; a partial re-run is a RERUN that
  touches nothing else). The help text on the switch says exactly that.
- Where: Admin page "Runs"; `GET /admin/run/options`, `POST /admin/run`; `run_manual` in
  `workflows/scheduler.py`; OPS-001 section "Manual run".

### 27.2 Which firm
- Options: the firm selected in the sidebar ★ · a bank/fund choice on the button
- Choice (default): **the sidebar firm.** The page is already on one database; the button is
  labelled with that firm's name so there is no doubt which one runs.

### 27.3 What a manual run records
- Options: a job like the scheduler's, with the launcher named ★ · an audit event only · nothing
- Choice (default): **a job (action MANUAL_EOD or MANUAL_ADVANCE_AND_EOD) plus an audit event
  under the launcher's name**, so manual and scheduled runs share one log on the Alerts & jobs
  page. One attempt and no RUN_FAILED alert, because the launcher is watching; a failure is a
  FAILED job with its error, returned to the page rather than raised.
- Reversal: retries or the failure alert are one argument each on `run_manual`.

### 27.4 Waiting for it
- Options: the page waits with a spinner ★ · a background job the page polls
- Choice (default): **the page waits**, with the expected duration stated before the button
  (about four and a half minutes for the demo bank, under a minute for the fund). The HTTP
  client uses the long timeout already used by the agents. A queued background job is the
  right design once runs move to a server; the job table is already the record it would need.

### 27.5 Day advance on the fund
- Found while smoke-testing on a copy of the fund database: `advance_business_day` calls the
  bank's trade recipes, which the fund organisation does not fit (`ValueError: high <= 0`
  from `evolve_portfolio`). This also means `novera schedule` cannot advance the fund, which
  the scheduler had never claimed to do (it has no `--fund` flag).
- Choice (default): **the option is offered on the bank only** (`advance_supported` in the
  run options, from the firm type); a request on the fund is noted on the job and the run
  covers the latest stored day. A fund-aware new-business generator is a simulation item,
  listed under "Choices worth a second look". Resolved in Round 28 the same day.

## Round 28 — The scheduler serves the fund (2026-09-16)

Context: the owner asked for the hedge-fund scheduler after Round 27 found that the day
advance failed on the fund. Built without a question round; defaults to confirm.

### 28.1 Why the advance failed, and the fix
- Finding: `advance_business_day` rebuilt the bank's counterparty universe and let
  `evolve_portfolio` default to the bank template, although `novera simulate --template
  hedge_fund` already builds the fund's day 2 with `FUND_TEMPLATE` and
  `build_fund_counterparties`. Nothing was missing in the simulator; the advance did not
  select the fund face.
- Choice (default): **`face_of(org)` in `simulation/advance.py`** returns the template and
  counterparty universe from the firm type, and the advance passes the template through.
  `advance_supported` and the Admin gating from 27.5 are removed; the checkbox is offered
  on both firms.

### 28.2 One scheduler process per firm
- Options: `novera schedule --fund` as a second process ★ · one process serving both
  databases in turn · a firm list in settings
- Choice (default): **one process per firm face**, `novera schedule [--fund]`, the firm id
  read from the database. Two databases are two independent worlds with their own EOD time
  and job log; a single loop would couple their failures and retries. A supervisor that
  starts both is deployment, not engine.
- Where: `novera schedule --fund`, OPS-001 "Scheduler" and "Day advance", test
  `test_fund_world_advances_with_its_own_template`.

## Round 29 — A free LLM provider for simulations (2026-09-16)

Context: the owner does not want to pay per token while simulating and asked whether a free
provider's key can be linked. The Claude API has no free tier and a Claude subscription
cannot be used by an application. One question round.

### 29.1 Which free provider
- Options: Google Gemini free tier ★ · Groq free tier · OpenRouter free models · Ollama local
- Choice (owner): **Gemini first**, because its Flash models handle function calling well
  and the AI Studio key is free. Ollama was advised against on this machine (8 GB of RAM: a
  model that fits is weak at tool calling and competes with DuckDB and the dashboard).
- Trade-off stated: on the free tier Google may use prompts to improve its products; every
  number in the platform is simulated, so accepted.

### 29.2 One adapter for all of them
- Options: one OpenAI-compatible adapter with presets ★ · a Gemini SDK adapter · one adapter
  per provider
- Choice (default): **one adapter**, `OpenAICompatProvider`, since Gemini, Groq, OpenRouter
  and Ollama all expose the OpenAI chat-completions shape with function tools. `PRESETS`
  holds base URL, default model and key variable per provider; `NOVERA_LLM_PROVIDER` selects,
  `auto` takes the first key found (Anthropic, Gemini, Groq, OpenRouter), then
  `NOVERA_LLM_BASE_URL`, else scripted. Uses `httpx`, already a dependency; no new package.
  `additionalProperties` is stripped from tool schemas because some compatible endpoints
  reject it.
- Where: `ai/provider.py`, `make_provider` in `ai/analyst.py`, settings `llm_provider`,
  `llm_base_url`, `llm_api_key`, `gemini_api_key`, `groq_api_key`, `openrouter_api_key`;
  `llm_model` now defaults to None (the provider's default). AI-001 "Providers".
- Tested live the same evening once the owner added a key. Two things surfaced and were
  fixed: `gemini-2.5-flash` is closed to new keys (preset moved to `gemini-3.6-flash`), and
  Gemini 3 rejects a replayed tool call without its `thought_signature`, so provider extras
  now travel on the `ToolUseBlock` and are echoed back (stripped for Anthropic). First
  sourced answer: three turns, 14 s, correct run ids. Preset model ids will drift again;
  `NOVERA_LLM_MODEL` is the override.

## Round 30 — Fallback chain across the free tiers (2026-09-17)

Context: the owner wants Novera to start on Gemini and move to Groq, then OpenRouter, then
Ollama by itself when a tier runs out of credit. Built without a question round.

### 30.1 How the chain is expressed
- Options: comma list in `NOVERA_LLM_PROVIDER` ★ · a separate `NOVERA_LLM_FALLBACKS` · fixed
  order in code
- Choice (default): **the same setting takes a comma-separated chain**
  (`gemini,groq,openrouter,ollama`), so one variable says everything. Members without a key
  are dropped at build time; `auto` becomes the chain of every keyed provider (Anthropic,
  Gemini, Groq, OpenRouter). `.env.example` ships the owner's order.

### 30.2 What counts as "no credit"
- Choice (default): **any `ProviderError`** (429 quota, 5xx, bad key, model gone, unreachable)
  moves to the next member; the failed member rests for the endpoint's `retry-after` or five
  minutes so a dead tier is not re-tried on every turn, and when every member is resting they
  are all tried again. If all fail, one error lists each failure. The answer records the
  provider that actually answered (`name`/`model` follow the last success).
- Mid-conversation switch: Gemini's thought signatures are stripped when another provider
  takes over the same history (Round 29 extras).
- Where: `FallbackProvider` in `ai/provider.py`, `make_provider`; `ProviderInfo.chain` shows
  the order on the Analyst page.

### 30.3 Models in a chain
- Found live: the owner had `NOVERA_LLM_MODEL` set to an OpenRouter id and the chain applied
  it to Gemini too, which answered 404. Model ids do not carry across providers.
- Choice (default): **the global model applies to a single provider only; a chain member
  names its own model after a colon** (`openrouter:google/gemma-4-31b-it:free`, split on the
  first colon so OpenRouter's `:free` suffix survives), else the preset default.

### 30.4 Seeing why the chain moved
- Found live: a chain answered from OpenRouter while Gemini was fine a minute later; the
  per-minute quota hit was invisible. Groq's preset model had also been retired.
- Choice (default): **each skipped member is logged as a warning** (`novera.ai.provider`,
  printed on stderr by `novera ask`), naming the provider and the error; Groq's preset is
  `openai/gpt-oss-120b`. Preset ids will keep drifting; the chain makes that survivable.

## Round 31 — Initial margin gaps found reading back 10.4 (2026-09-26)

Context: the owner asked what was missing for entry 10.4 to improve the situation. Tracing the
IM path from `regulatory/simm.py` to the exposure profiles turned up one defect, one modelling
gap and several documentation gaps. All four were taken in one round.

### 31.1 The CSA what-if dropped the initial margin
- Found reading the code: headline profiles were built with `initial_margin`, but `csa_what_if`
  called `collateralise` without it, so every what-if ran at IM = 0. The API computes **both**
  legs through it, so `peak_pfe95_before` did not tie to the PFE95 on the Counterparty page for
  any margined netting set — two figures shown side by side, disagreeing, with no explanation.
- Choice: **`csa_what_if` takes the run's initial margin and the service passes it to both legs**;
  `stored_initial_margin` is now the single accessor both the engine and the API read, so they
  cannot drift apart again. `load_exposure_result` recomputes the ageing factors from the stored
  snapshot rather than persisting them.
- Where: `counterparty_risk/engine.py`, `api/service.py`, CR-002 1.1.0;
  `tests/test_regulatory.py::test_csa_what_if_baseline_ties_to_the_reported_profile`

### 31.2 Initial margin held constant across the exposure grid
- Options: leave it constant · scale by notional-duration outstanding ★ · recompute
  sensitivities on the aged portfolio at each grid point
- Found: IM is computed on today's sensitivities and was subtracted unchanged at every grid
  date, including points where the netting set has largely run off. The exposure paths age but
  the collateral did not, so the collateral benefit was overstated at the long end and
  far-dated EE, PFE and CVA understated.
- Choice: **scale IM at each grid date by the notional-duration still outstanding**, clipped at
  one, matching the ageing already applied to the paths. A full recomputation needs sensitivities
  per grid point on the aged book, which the 12-point grid was not built to carry.
- Where: `counterparty_risk/exposure.py` (`im_scales`, `_notional_duration`), REG-004 1.1.0
- Revisit: notional units are mixed across product types, so the factor is a ratio within one
  netting set over time and not comparable between sets.

### 31.3 An IM-free run looked identical to a margined one
- Found: `run_counterparty` falls back to no IM when the regulatory engine has not run on the
  same run id. EOD orders regulatory first, but a standalone `novera run counterparty`, a fund
  run or `regulatory=False` produced VM-only exposure whose only trace was
  `initial_margin_sets: 0` in the notes. The same portfolio reported two different exposures
  with nothing visible to explain it.
- Choice: **the run warns** when collateralised netting sets exist and no IM was found, in
  `notes["warnings"]` and printed by the CLI. The ordering dependency stays implicit; the
  warning makes its absence loud instead of enforcing it.
- Where: `counterparty_risk/engine.py`, `cli.py`

### 31.4 Claims that outran their evidence
- Found: REG-004 cited validation for "IM positive, reduces collateralised exposure" but the
  only assertion was that IM is positive; nothing compared exposure with and without it. The
  roadmap bundled delivered SIMM-lite with undelivered CCP exposure on one unticked line.
- Choice: **test what the record claims and split what the roadmap bundled** — an assertion that
  the same paths without IM give higher exposure, one that the margin decays across the grid, and
  the CCP line separated out with what is missing named (no netting sets on CCPs, so no cleared
  exposure, default-fund contributions or cleared IM).
- Where: `tests/test_regulatory.py`, `docs/03-roadmap.md`, REG-004 1.1.0

## Round 32 — A run is COMPLETED only when every engine ran (2026-09-27)

Context: second-look item 18. The run record was saved COMPLETED at step 14, before the
regulatory, counterparty and fund engines ran, so a failure in any of them left a COMPLETED run
without their tables, and a failure in counterparty lost the limits second pass and the alerts
too. One question round; the owner took every recommended option.

### 32.1 What a late failure does
- Options: continue, mark PARTIAL, no retry ★ · stop at the first failure, mark PARTIAL, raise
  so the scheduler retries
- Choice (owner): **continue and end PARTIAL**. Each of regulatory, counterparty, fund and the
  limits second pass runs guarded: an exception is logged, recorded in
  `summary.failed_stages` under the re-run stage name with its error, and the steps after it
  still run. A RUN_PARTIAL alert (WARNING) names the stage; `novera run eod` and `schedule
  --once` exit 2; the job is PARTIAL. No retry: a retry is a whole new four-minute run that
  would most likely fail the same way. The fix is a re-run of the stage (OPS-002 1.1.0), which
  accepts a PARTIAL parent and drops the stage from `failed_stages` on the new run.
- Alert dispatch failing is kept in `summary.alerts_error` and does not make the run PARTIAL:
  delivery never changes the results.
- Where: `_guarded` and `_second_pass` in `workflows/eod.py`, `RunRecord.failed_stages`,
  `workflows/rerun.py`, `workflows/alerts.py`, `workflows/scheduler.py`, `cli.py`

### 32.2 Is a PARTIAL run the latest run?
- Options: yes, with a warning badge ★ · no, COMPLETED only
- Choice (owner): **yes**. VaR, stress, limits and P&L are valid on a PARTIAL run; hiding it
  would put yesterday's numbers on the morning view because one engine failed.
  `latest_run` defaults to COMPLETED or PARTIAL (`USABLE_STATUSES`); every page header shows a
  banner naming the failed stages; the sidebar marks the run.

### 32.3 Status while the late engines run
- Options: RUNNING until the end ★ · COMPLETED early, downgraded on failure
- Choice (owner): **RUNNING until the final save**. A process killed mid-run leaves a RUNNING
  run instead of a COMPLETED one with missing tables; it is never the latest, so the next
  scheduled job runs the day again. Cost: for the three minutes of the counterparty engine the
  dashboard keeps showing the previous run. RUN_FINISHED is now written at the very end with
  the final status and any failed stages.

### 32.4 Sign-off of a PARTIAL run
- Options: only metrics that exist ★ · no sign-off on PARTIAL
- Choice (owner): **only metrics that exist**. Each metric names the late stages it reads;
  one read from a failed stage is UNAVAILABLE and cannot be signed, so a policy that requires
  it keeps the run PENDING until the re-run is signed instead (OPS-003 1.1.0).
- Choice (default): **LIMITS reads counterparty, fund and the second pass**, because the
  counterparty-exposure and fund limits are only measured there; signing the limit metric with
  them unmeasured would sign an incomplete breach count.
- Where: `Metric.stages` and `Metric.unavailable` in `workflows/signoff.py`; test
  `test_a_failed_late_engine_leaves_a_partial_run`

## Round 33 — Dual-curve EUR pricing (2026-10-14)

Context: second-look item 15. EUR swaps reference EURIBOR-6M but were projected and discounted
on one curve, a gap measured at 0.53% of PV in PR-002. One question round; the owner took every
recommended option.

### 33.1 How the projection curve is held
- Options: basis spreads over ESTR ★ · a full second EURIBOR curve
- Choice (owner): **basis spreads over ESTR**. The existing `IR:EUR:{tenor}` curve is read as
  the ESTR discount curve, so its history does not move. New factors `IRBASIS:EUR:{index}:{tenor}`
  hold the EURIBOR-minus-ESTR zero spread; projection = ESTR + basis. Basis risk then shows as
  its own line in sensitivities and VaR, as a desk reports it, instead of hiding inside two
  near-identical curves.
- Where: `market_data/risk_factors.py` (`IR_BASIS`, `irbasis_id`, `TENOR_BASIS_INDICES`),
  `MarketSnapshot.projection_curve`, `pricing/rates.py` (`swap_multi_curve` 2.0.0), PR-002 2.0.0.
  A snapshot without the basis (real data) projects off the discount curve and says so in
  `details.tenor_basis_applied`.
- Found: reading `IR:EUR` as ESTR moves EURIBOR forwards up by the basis, so the old 0.53%
  measurement (curve read as EURIBOR, decision 18.3) does not describe the fallback any more:
  on the test swap the single-curve fallback now misses by notional × basis × annuity, 19% of
  PV. The test was rewritten as a QuantLib dual-curve benchmark.

### 33.2 Which products move
- Options: every EURIBOR product ★ · EUR swaps only
- Choice (owner): **every EURIBOR product**: EUR swaps (6M basis), EUR swaptions (forward swap
  rate projected off EURIBOR-6M, annuity on ESTR) and EURIBOR-3M futures (3M basis).
- Choice (default): **BASIS01** per basis node in the sensitivities, DV01 left as the full rate
  risk (the projection rides on ESTR), a 13th hypothetical stress `eur_basis_wider_20`, and an
  `eur_basis` what-if target. FRTB SA and SIMM take EURIBOR as a second curve: its delta is
  BASIS01 and ESTR's is DV01 less BASIS01, at 0.999 (SA) and 0.993 (SIMM) across curves, so an
  outright swap is not charged twice; IMA gives basis the IR liquidity horizon.
- Where: `pricing/breadth.py` (PR-011 1.1.0, PR-012 1.2.0), `risk/factor_mapping.py`,
  `risk/sensitivities.py` (MR-001 1.1.0), `risk/stress.py` (MR-005 1.2.0),
  `regulatory/frtb_sa.py` (REG-001 1.1.0), `regulatory/simm.py` (REG-004 1.2.0),
  `regulatory/frtb_ima.py`, `ai/whatif.py`, MV-001 1.1.0 (swap now acceptable simplification).

### 33.3 The demo breaches
- Options: keep the seven ★ · accept what comes
- Choice (owner): **keep the seven demo breaches**. Trades are re-struck at par under the new
  pricing, the demo is rebuilt and the same breaches are confirmed; an EUR limit moves only if a
  breach does, and the move is logged here.
- Result: rebuilt with the same recipe (bank simulate, EOD 11 and 14 September with the Brent
  breach acknowledged between them, `schedule --once` for the 15th, vendor feed and
  reconciliation; fund simulate and two EODs). The bank keeps the same seven breaches and the
  fund the same three, so **no limit was moved**. EUR_RATES_DV01_EUR stays at 1.023 on the 15th
  (DV01 is unchanged by design: the projection rides on ESTR); EUR_RATES_VAR falls 1 to 2 points
  (0.952 to 0.932 on the 15th) and firm VaR rises about 2 points; the drivers were not
  decomposed. Two warning flags cross the 80%
  line: EUR_RATES_VAR on the 11th drops to OK (0.791) and MACRO_VAR on the 14th becomes a
  WARNING (0.801).

### 33.4 How the basis moves
- Options: simulated with crisis widening ★ · static spread
- Choice (owner): **simulated**: a term structure, mean-reverting, widening in the two stylised
  crisis episodes as interbank stress does. Drawn after every existing family so the calibrated
  history stays bit-identical.
- Where: `_Sim.tenor_basis` in `simulation/market_data.py`, levels in
  `simulation/reference_levels.py` (`TENOR_BASIS`, `BASIS_CRISIS_LOADING`), SIM-001 1.3.0. Ten
  factors (1,504 in all). Checked against the committed code: all 1,949,670 earlier history
  values identical; `test_tenor_basis_is_drawn_last_and_widens_in_a_crisis` keeps it so.
- Revisit: the composite stresses (global risk-off) leave the basis where it is, which
  understates an interbank widening; only the basis scenario and the historical episodes move it.

## Round 34 — MXN and ZAR tenor basis, market swap conventions (2026-10-14)

Context: after Round 33 the owner asked about the other currencies. USD SOFR, GBP SONIA, JPY
TONA and BRL CDI are overnight indices, so one curve is exact for them; MXN TIIE-28D and ZAR
JIBAR-3M are term rates like EURIBOR. Reading the generated book also showed every swap on one
generic convention (floating quarterly, fixed semi-annual 30/360), including EURIBOR-6M swaps
resetting quarterly. One question round; collateral-currency discounting is the next round.

### 34.1 How MXN and ZAR are read
- Options: same as EUR ★ · keep IR as the term curve and subtract the basis
- Choice (owner): **same as EUR**. `IR:MXN` is the F-TIIE overnight discount curve and `IR:ZAR`
  the ZARONIA curve, history unchanged; `IRBASIS:MXN:28D:*` and `IRBASIS:ZAR:3M:*` project TIIE
  and JIBAR over them. One model for every term-rate index.
- Choice (default): assumed levels of 25 to 30bp (TIIE over F-TIIE) and 40 to 50bp (JIBAR over
  ZARONIA), not quotes; drawn after the EUR basis so EUR and every earlier family stay
  bit-identical (checked against the Round 33 code: all 1,962,720 earlier values equal); an
  `em_basis` what-if target. The basis stress stays EUR-only.
- Where: `TENOR_BASIS_INDICES`, `reference_levels.TENOR_BASIS`, `market_data/catalogue.py`
  (`IRBASIS:{CCY}` source rows), SIM-001 1.4.0, PR-002 2.1.0. 1,514 factors.

### 34.2 Swap conventions
- Options: reset frequency only ★ · full market conventions · leave them
- Choice (owner): **full market conventions** per index: fixed and floating frequency and day
  count, 28-day periods for TIIE, and zero-coupon business/252 for the CDI swap.
- Where: `reference_levels.SWAP_CONVENTIONS`, `simulation/instruments.py` (`swap`, `swaption`:
  a TIIE swap runs 13 periods of 28 days a year), `Frequency.EVERY_28_DAYS` and `ZERO_COUPON`,
  `DayCount.BUS_252` (weekdays, no Brazilian calendar) in `domain/enums.py` and
  `pricing/schedule.py`; the CDI fixed leg compounds `(1 + K)^τ − 1` and its par rate solves
  that (`pricing/rates.py`, `swap_multi_curve` 2.1.0). Swaption fixed legs follow the same
  conventions. Tests: `test_generated_swaps_follow_market_conventions`,
  `test_brl_cdi_zero_coupon_swap`, `test_mxn_tiie_swap_is_dual_curve_on_28_day_periods`.

### 34.4 The floating period already running
- Found: with no stored fixings the pricer projected only the part of the current floating
  period from today and counted the interest already accrued since its start as zero, while the
  fixed leg paid its full coupon: a bias of up to one period's floating interest on every
  seasoned swap, and the whole life of a zero-coupon CDI swap.
- Options: estimate the elapsed accrual for all swaps ★ · zero-coupon only · compound the
  stored history (needs the history in the pricer)
- Choice (owner, first): **estimate it for all swaps** at today's shortest rate on the
  projection curve.
- Found on the demo rebuild: the estimate made interest that is already fixed move with today's
  curve. GBP DV01 at the 1M node went from −1.4k to −64k (SONIA swaps now pay annually, so up to
  a year is elapsed), breaching GBP_RATES_DV01_GBP, and the BRL 1M node moved the same way;
  CPTY_CORP_AIR breached through a 495m USD swap's PV. False risk, so no limit was moved.
- Options: store the fixing on the trade ★ · take past fixings at the fixed rate · revert
- Choice (owner, revised): **store the fixing on the trade**, as a trade system does.
  `InterestRateSwap.fixing` (`FloatingFixing`: period start, rate, through) is set from the
  simulated history whenever a dated portfolio snapshot is built (`simulation/fixings.py`, in
  `generate_portfolio` and `evolve_portfolio`): a term index's rate off that day's projection
  curve at the period start, which then pays for the whole period; an overnight index's realised
  rate (the shortest node of each day's curve, compounded) through the business date, with the
  rest projected. The short-rate estimate remains only for a period with no stored fixing (a
  later period on an exposure path).
- Where: `pricing/rates.py`; tests `test_stored_fixings_remove_false_short_rate_risk`,
  `test_generator_attaches_the_running_fixing`, and
  `test_running_period_accrues_at_the_short_rate` for the fallback against QuantLib.

### 34.3 The demo
- Options: rebuild, check, swap ★ · rebuild and report only
- Choice (owner): **rebuild, check, swap** as in 33.3: the same seven bank and three fund
  breaches are required, a limit moves only if a breach does, the current databases are backed up.
- Result: the fund keeps its three breaches. The first rebuild added GBP_RATES_DV01_GBP and
  CPTY_CORP_AIR; the GBP one was false risk from the accrual estimate and went with the stored
  fixings (34.4). CPTY_CORP_AIR stayed: with its fixing stored, the 495m USD pay-fixed swap
  IRS_000021 is worth +1.6m instead of −8.6m, the old figure being the bias 34.4 removes, and the
  New York netting set's peak PFE95 goes from 18.0m to 32.3m against a 30m limit (107.5% on the
  11th, 104.5% on the 14th, 105.8% on the 15th).
- Choice (owner): **keep it as an eighth bank breach** rather than raise the limit as the rule
  said: TransAtlantic Air is BB+ and on the watchlist, already the wrong-way-credit counterparty,
  and raising its limit to quieten the demo is what a risk function would not do. Day 1 now shows
  five breaches. BRL DV01 utilisation rises from 6–18% to 31–63% (zero-coupon swaps pay at
  maturity); EUR_RATES_STRESS on the 11th and MACRO_VAR on the 14th drop below the warning line.

## Round 35 — Cross-currency basis: collateral discounting and FX forwards (2026-10-14)

Context: second-look item 25. All 17 CSAs in the simulated bank post USD cash, yet non-USD
bilateral swaps discounted on their own OIS curve, and FX forwards came from covered interest
parity on the two OIS curves. The owner asked for the difference between stamping the collateral
on the trade and passing CSAs into pricing, and which discounting suits trades without a CSA in
production. The owner took the recommended options for currencies and for trades
without a CSA, and chose to pass CSAs into pricing and to include CDS and repos.

### 35.1 Currencies
- Options: all 14 non-USD ★ · G10 plus the swap-book EM · swap currencies only
- Choice (owner): **all 14**: every FX pair in the book is basis-consistent. Factors
  `XCCY:{CCY}:{TENOR}`, a zero spread on the currency's OIS curve that gives its cash flows under
  USD collateral (USD has none).
- Choice (default): assumed levels from −200bp (ARS) and −35bp (JPY) to +12bp (NZD), narrowing
  towards 30Y, one mean-reverting path per currency, pushed down by the episode's USD rally (the
  dollar-funding squeeze). Drawn after the tenor basis: all 1,975,770 earlier history values are
  identical to Round 34. 70 factors, 1,584 in all.
- Where: `simulation/market_data.py` (`_Sim.xccy_basis`), `reference_levels.XCCY_*`,
  `MarketSnapshot.collateral_curve` (a non-USD collateral currency triangulates through USD),
  SIM-001 1.5.0.

### 35.2 How a pricer learns the collateral
- Options: stamp it on the trade with a data-quality check ★ · pass the CSAs into pricing
- Choice (owner): **pass the CSAs into pricing**. Valuation receives a collateral map (netting set
  to its CSA's collateral currency, built from the CSA table) and gives each pricer its trade's
  collateral, so the CSA table stays the one source of truth; the cost is carrying the map
  through every pricing call site (valuation, revaluation for sensitivities and VaR, P&L explain,
  exposure paths, SA-CCR, reconciliation, the vendor feed).
- Where: `pricing/collateral.py` (`CollateralMap`, `COLLATERAL_AWARE`), `value_trade` and
  `value_portfolio`, `Portfolio(collateral=...)` (bumps, VaR scenarios and what-ifs reuse it,
  forked VaR workers inherit it), `DependencyIndex` (a trade under foreign collateral depends on
  both currencies' basis), EOD, partial re-runs, regulatory engine, SA-CCR, exposure paths, P&L
  explain, reconciliation, the vendor feed (so the official system differs only where planted),
  the CLI and the generator's fair-value strike. The cash ladder stays without: it reports
  contractual cash flows, which do not depend on discounting.

### 35.3 Products
- Options: swaps, swaptions, FX forwards and options ★ · also CDS and repos · swaps only
- Choice (owner): **also CDS and repos**: every collateralised OTC product discounts on its
  collateral curve. FX forwards and options take the basis-consistent market forward, which the
  interbank market sets under USD collateral whatever the trade's own CSA, and discount on the
  trade's collateral curve.
- Choice (default): XCCY01 per node; FRTB SA treats the basis as one GIRR factor per currency
  (risk weight 1.6%, no correlation with the curves, as read from MAR21 and flagged in REG-001 to
  be checked against the text), SIMM-lite likewise; IMA gives it the IR liquidity horizon; a 14th
  stress `usd_funding_squeeze`; an `xccy_basis` what-if target. The FX forward model is renamed
  `fx_forward_xccy`.
- Where: `pricing/fx.py` (PR-003 2.0.0, PR-004 2.0.0), `pricing/rates.py` (PR-002 2.2.0),
  `pricing/breadth.py` (PR-010, PR-013 1.1.0, PR-012 1.3.0), `pricing/credit.py` (PR-008 1.1.0),
  `risk/sensitivities.py` (MR-001 1.2.0), `risk/stress.py` (MR-005 1.3.0), `regulatory/frtb_sa.py`
  (REG-001 1.2.0), `regulatory/simm.py` (REG-004 1.3.0; it now also charges a currency with basis
  but no DV01). Tests: `tests/test_xccy.py`, `test_cross_currency_basis_is_its_own_girr_and_simm_factor`.

### 35.5 The demo
- Rule as in 34.3 (rebuild, compare, bring any breach change to the owner). Result: the bank keeps
  its eight breaches and the fund its three, with no status change anywhere; the largest move is
  about one point (CPTY_CORP_ENERGY 106.5% to 105.4% on the 15th). Small because the generator
  strikes every trade at fair value under the new pricing, so only the basis drift since each
  trade date shows. On the 15th the bank carries −54k per bp of EUR basis (97 trades), −25k MXN,
  +24k INR, +16k CHF; the USD funding squeeze gains 2.1m.

### 35.4 Trades without a CSA
- Options: own-currency OIS with FVA logged ★ · a bank funding curve
- Choice (owner): **own-currency OIS**, the clean price production desks use, with funding left to
  a separate FVA on the exposure profiles. Discounting at a funding curve double counts DVA,
  ignores netting and mixes funding into PV.

## Round 36 — Stuck runs are swept (2026-10-14)

Context: second-look item 22. A run is saved RUNNING at the start of an EOD and only made final
at the end (32.3), so a process killed in between leaves it RUNNING for good, and the scheduler
job that launched it too. DuckDB lets one process write to a file at a time, so no other process
can be mid-run while a sweep holds the write connection; within one process (the API serves
requests on threads) a live run can still overlap a sweep. The owner asked what the trigger and
scope questions meant, then took every recommended option.

### 36.1 Detection
- Options: owner process with an age backstop ★ · age only
- Choice (owner): **owner process with an age backstop**: each run and job records its host and
  process id; a RUNNING one is abandoned when that process is no longer alive on this machine,
  or after two hours when it was started elsewhere or carries no owner (runs from before this
  round). A live run, including one in the sweeping process itself, is never touched.

### 36.2 Status
- Options: FAILED with a reason ★ · a new ABANDONED status
- Choice (owner): **FAILED with a reason** in the run summary and a RUN_ABANDONED audit event;
  the status set, the API contract and the screens are unchanged.

### 36.3 Triggers
- Options (several): at the start of every run or job ★ · when the scheduler starts ★ · a CLI
  command ★ · an Admin page button
- Choice (owner): **the three recommended**: start of every EOD, partial re-run, manual run and
  scheduler job; scheduler start-up; `novera run sweep`.

### 36.4 Scope
- Options: runs and scheduler jobs ★ · runs only
- Choice (owner): **both**, so the run list and the scheduler log agree.
- Found building it: scheduler and manual jobs were saved only when they ended, so a killed job
  left no record at all rather than a stuck one. Each job is now saved RUNNING as it starts, with
  its owner, and the sweep closes it.
- Found testing it: DuckDB returns TIMESTAMP columns as naive local time, not UTC; the sweep reads
  a naive start time as local before comparing ages.
- Where: `workflows/sweep.py` (`owner`, `abandoned_reason`, `sweep_stuck`), the owner on
  `RunRecord.summary` (EOD, partial re-runs) and on `JobRecord`, `repo.list_running_runs`, the
  sweep in `run_eod`, `rerun_stage`, `run_once`, `run_manual` and `serve`, `novera run sweep`,
  OPS-001 1.3.0, `tests/test_sweep.py`; the API run summary and job schemas declare `owner` and
  `abandoned` (the schema drift test caught the new summary key).

## Round 37 — Funding valuation adjustment (2026-10-14)

Context: second-look item 26. Uncollateralised trades discount on their own OIS curve (35.4), the
clean price, so their funding cost and benefit were nowhere. The exposure engine already produces
the EPE and ENE profiles CVA and DVA use. One question round; the owner took every recommended
option.

### 37.1 Funded exposure
- Options: collateralised EPE and ENE for every netting set ★ · uncollateralised sets only
- Choice (owner): **collateralised profiles for every netting set**, the ones CVA uses: a CSA
  with zero thresholds leaves only the margin-period gap to fund, an uncollateralised set all of it.

### 37.2 Funding spread
- Options: a setting ★ · a simulated market factor
- Choice (owner): **a setting**, `funding_spread_bp`, flat, 80bp by default like the own credit
  spread behind DVA; FVA moves with exposure, not with the market.

### 37.3 FBA and DVA
- Options: report all four, headline XVA = CVA − DVA + FCA ★ · FCA only · fully symmetric
- Choice (owner): **report CVA, DVA, FCA and FBA; headline XVA = CVA − DVA + FCA**. FBA and DVA
  both come from the bank's own credit, so adding both would count that benefit twice; FBA is
  shown and left out of the total.

### 37.4 Where it shows
- Options: Counterparty page and the CSA what-if ★ · the page only
- Choice (owner): **both**: FCA, FBA and XVA per netting set and in total, and the change in
  FVA when CSA terms change.
- Choice (default): FCA and FBA weight each grid step by both parties' survival, so funding
  stops at either default, and discount on the reporting-currency OIS curve, as CVA does.
- Where: `counterparty_risk/cva.py` (`fva_from_profile`, `netting_set_xva`, `cva_table`, model
  1.1.0), `Settings.funding_spread_bp`, the counterparty engine (per netting set and counterparty,
  `notes.funding_spread_bp`), the CSA what-if (`xva_before`, `xva_after`), the Counterparty page,
  the API schemas, CR-005 1.0.0 (new) and CR-003 1.1.0. Tests: `test_fva_formula_and_xva`, the
  engine and API checks.
- Result on the rebuilt demo (15th): CVA 0.42m, DVA 4.81m, FCA 0.60m, FBA 4.64m, XVA −3.80m.
  Negative because the bank owes more uncollateralised value than it is owed, so DVA outweighs
  CVA. The largest funding costs sit on the uncollateralised names (Northsea Energy 0.20m,
  TransAtlantic Air 0.14m). Breaches unchanged (8 bank, 3 fund): FVA feeds no limit.

## Round 38 — Automatic morning commentary (2026-10-14)

Context: the owner asked whether the morning commentary is drafted automatically after each EOD
as well as on demand; decision 7.4 had made it on demand only. The Analyst opens its own
read-only and read-write connections, and DuckDB will not let a process that holds a database
read-write also open it read-only, so the draft cannot run inside `run_eod`. When every provider
in the LLM chain fails the Analyst raises; it does not fall back by itself. One question round;
the owner took every recommended option.

### 38.1 Which runs
- Options: every EOD ★ · scheduled only · every EOD and partial re-runs
- Choice (owner): **every EOD**: scheduled, manual from the Admin page and `novera run eod`, bank
  and fund. Revisits 7.4: on demand stays, automatic is added.

### 38.2 Provider
- Options: the configured LLM chain with the scripted provider as fallback ★ · always scripted ·
  LLM only
- Choice (owner): **the LLM chain, scripted when it fails**, so a commentary always exists; the
  answer records which provider wrote it.

### 38.3 Relation to the run
- Options: a separate step after the run is final ★ · part of the run
- Choice (owner): **a separate step** after the run's connection is closed: it never changes the
  run's status; a failure goes to the job's notes and the audit trail; a time cap stops a slow
  LLM holding up the scheduler. A setting switches it off.

### 38.4 Where it shows
- Options: Analyst page and an Overview banner ★ · also the risk pack · Analyst page only
- Choice (owner): **the Analyst page and a banner on the morning Overview**, with run, time and
  provider; the on-demand button still regenerates and earlier drafts stay in the history.
- Where: `ai/commentary.py` (`draft_after_run`, `note_for_job`, session `auto-commentary`),
  `Settings.commentary_auto` and `commentary_timeout_seconds`, `scheduler.comment_on_job` (the
  `serve` loop and `novera schedule --once`), `novera run eod`, `api/service.with_commentary` (the
  `POST /admin/run` endpoint opens and closes its own connection first, and the local client does
  the same), `GET /runs/{run_id}/commentary`, the Analyst page and the Overview banner, AI-001
  1.1.0, OPS-001 1.4.0. Tests: `test_auto_commentary_drafts_falls_back_and_can_be_off`,
  `test_scheduler_job_notes_the_commentary`.

## Round 39 — Posted initial margin and CCP exposure (2026-10-14)

Context: second-look items 20 and 21. Initial margin was SIMM-lite received only, applied to every
netting set with a CSA; nothing modelled the margin we post. CCPs (LCH, CME, ICE) existed as
reference data with no netting sets, so the 304 trades cleared at LCH and ICE carried no
counterparty exposure and no capital. One question round; the owner took every recommended option.

### 39.1 Posted margin
- Options: SIMM-lite on our side with the regulatory threshold ★ · the received amount, no threshold
- Choice (owner): **SIMM-lite on our side of each netting set**, and both directions exchanged only
  above the uncleared-margin threshold per counterparty (closes the "IM on every CSA" gap of 31.2).

### 39.2 What posted margin changes
- Options: the counterparty's exposure to us plus MVA ★ · exposure only
- Choice (owner): **both**: posted margin is segregated, so it lowers the counterparty's exposure to
  us (ENE, hence DVA and FBA) and is not lost if they default; its funding cost is a new MVA line
  at the funding spread.

### 39.3 Which cleared trades
- Options: OTC-cleared at LCH and ICE ★ · also listed derivatives at exchange clearing houses
- Choice (owner): **OTC-cleared at LCH and ICE**.

### 39.4 What CCP exposure includes
- Options: trade exposure, default fund and qualifying-CCP capital, with limits ★ · exposure and
  limits only · capital only
- Choice (owner): **all of it**: trade exposure after daily variation margin plus the margin we post
  at the CCP (a house account is not bankruptcy-remote), the default fund contribution, the
  qualifying-CCP charge (2% risk weight on trade exposure plus a default-fund charge, simplified
  from CRE54) and a limit per CCP.

### 39.5 Demo
- Options: same process, CCP limits that do not breach ★ · plant a CCP breach
- Choice (owner): **same process**: rebuild, compare, bring any change to the owner; no new planted
  problem.
- Where: `counterparty_risk/ccp.py` (`build_ccp_sets`, `qccp_capital`, `CCP_DEFAULT_FUND`), SIMM-lite
  both ways with `Settings.im_threshold` (`regulatory/simm.py`, REG-004 1.4.0), `collateralise` with
  posted margin and CCP sets (CR-002 1.2.0), the CCP sets in the exposure engine and the what-if
  (CR-006 1.0.0, new), MVA in `netting_set_xva` (CR-005 1.1.0), SA-CCR with `cleared=True` and the
  two capital components (REG-007 1.0.0, new), `CCP_LIMITS` (LCH 625m, ICE 150m, about 60% on the
  demo), the Capital and Counterparty pages, the API schemas. Tests: `tests/test_ccp.py`.
- Measured before the rebuild on the latest run: margin received 159m after the threshold against
  1,312m before it; margin posted 512m with the CCPs; LCH peak PFE95 375m, ICE 93m; QCCP capital
  15.6m and 6.2m, mostly the default-fund charge; XVA 15.1m of which MVA 16.6m, most of it on the
  margin posted at LCH. CVA rises (0.42m to 1.19m) because the threshold takes received margin away.
- Result of the rebuild: the bank keeps its eight breaches and the fund its three. The threshold
  raises several bilateral exposures (dealers G and H, Bank E, Corporate Auto, up to 46 points);
  the only status change is CPTY_CORP_AUTO turning WARNING on the 14th (81%). The CCP limits run at
  53% to 62%. On the 15th: XVA 15.13m (CVA 1.19m, DVA 4.32m, FCA 1.68m, MVA 16.58m); capital adds
  0.72m for CCP trade exposure and 21.0m for the default funds.

## Round 40 — Limits calibrated from a target utilisation (2026-10-14)

Context: second-look items 5 and 18. Every limit amount was typed into `simulation/limits.py`
and tuned by hand so day 1 shows the four planted breaches; every model change since has moved
utilisations and needed a breach check. The owner asked whether calibration would run every day;
it must not: a limit that follows positions never breaches. Fixed rules agreed before the round:
calibration never runs from the EOD or the scheduler; it refuses to touch a limit in breach (that
stays an increase request); the planted breaches carry explicit target utilisations. One
question round; the owner took every recommended option.

### 40.1 Reference
- Options: the day-1 EOD run ★ · a historical percentile of the measure
- Choice (owner): **the day-1 run**. Positions are stored for three days only, so a historical
  percentile would re-measure today's book on past markets (market moves, not desk activity) at
  the cost of hours of VaR runs.

### 40.2 How it takes effect
- Options: seed APPROVED, later recalibrations as DRAFTs through the approval matrix ★ · apply both
  directly
- Choice (owner): **seed approved, recalibrate as drafts**: a later `novera limits calibrate`
  proposes DRAFT versions that the limit's approver signs off under the existing matrix
  (`APPROVERS_BY_LEVEL`: desk-level limits Head of Market Risk or CRO, business and firm limits CRO,
  counterparty limits Head of Counterparty Risk or CRO; never the proposer).

### 40.3 History
- Options: keep the old version ★ · overwrite
- Choice (owner): **keep it**: an approved recalibration ends the old version and starts the new
  one the next business day; old amounts stay visible.

### 40.4 Targets
- Options: a table by limit type ★ · one target · the owner's figures
- Choice (owner): **a table by type**: VaR, ES and stressed VaR 75%; sensitivities 70%;
  concentration 70%; stress loss 60%; counterparty and CCP 60%; fund leverage and margin 70%.

### 40.5 Scope
- Options: bank and fund ★ · bank only
- Choice (owner): **both**.
- Choice (default): seeding needs a measure before any run exists, so `novera simulate` runs a day-1
  EOD on a throwaway copy of the database (alerts off, `EODConfig.alerts`), reads its limit table
  and sets the limits; the real day-1 run then reproduces the measures (about one EOD more to
  build a firm; `--no-calibrate` skips it). The limit table was keyed by limit id alone, so
  superseded versions go to a new `limit_version` table and drafts to `limit_draft`;
  `load_limits(on)` reads the version in force on the date. Story targets are the day-1
  utilisations of the demo before calibration (bank 1.99, 1.65, 1.50, 1.38, 1.08 breaching and
  0.98, 0.92, 0.85 crossing later; fund 2.52, 1.61 and 0.95).

### 40.6 Calibration within the appetite
- Found on the first rebuild: day 1 opened as designed (5 breaches, 4 warnings instead of 20),
  but day 2 showed 10 bank breaches and the fund 4. Pure usage calibration had thrown away the
  scale of each desk: a desk using 0.8% of its designed limit on day 1 got a limit 80 times
  smaller (HY_CREDIT_STRESS 30m to 0.375m, then 884% on day 3; SINGLE_NAME_EQ_STRESS 120m to
  2.25m; G10_FX_DELTA 6m to 0.9m), so a few new trades breached it. Nearest-quarter rounding also
  rounded some limits down, opening EM_RATES_VAR at 84% against a 75% target.
- Options: a band around the designed appetite ★ · a floor only · the maximum of days 1 and 2 ·
  drop seeding
- Choice (owner): **a band of 50% to 200% of the designed amount** (floor raised to 100% in 40.7), kept as `Limit.appetite`: the
  hand-set amounts are the risk appetite, set top-down, and calibration moves each limit towards
  its target only within the band. Rounding is now always up, so no limit opens above its target.
  Story limits stay exact.
- Where: `limits/calibration.py` (`propose`, `round_amount`, `APPETITE_BAND`), the `appetite`
  field on `Limit`, MR-017. Test `test_calibration_stays_within_the_appetite_band`.

### 40.7 The floor of the band
- Found on the second rebuild: the fund back to its three breaches and the bank's stress limits
  fixed, but three quiet day-1 desks floored at half their appetite grew over days 2 and 3 to about
  52% to 64% of their designed amount and breached the halved limit (EM_RATES_DV01_BRL 126%,
  G10_FX_DELTA 104%, INDEX_EQ_VEGA 128%): the same manufactured breach, smaller.
- Options: floor at 100% of appetite ★ · floor at 75% · keep 50% and accept three new breaches
- Choice (owner): **floor at the appetite**, cap at double: `limit = max(appetite, usage / target)`,
  capped at 2× appetite. Calibration never cuts a limit; a quiet desk behaves exactly as designed,
  a desk running hot earns headroom.
- Result of the third rebuild: every limit seeded (bank 81, 8 of them story limits; fund 35, 3), the
  same story breaches (bank 5, 7 and 8 over the three days; fund 2 then 3), and bank warnings down
  from about twenty a day to 3, 1 and 1.

- Where (40.1 to 40.5): `limits/calibration.py` (`TARGETS`, `STORY_TARGETS`, `propose`, `round_amount`),
  `workflows/limit_calibration.py` (`seed_limits`, `propose_recalibration`, `decide_draft`),
  `novera simulate --calibrate`, `novera limits calibrate | drafts | approve | reject`,
  `GET /limits/drafts`, `POST /limits/recalibrate`, `POST /limits/drafts/{id}/decide`, the
  Calibration tab of Limit management, MR-017 1.0.0 (new), MR-006 1.1.0. Tests:
  `tests/test_limit_calibration.py`.

## Round 40b — Build and run time (2026-10-15)

Context: each development round took 30 to 50 minutes of rebuild and tests. The owner asked how to
avoid it and took three measures together.

- **Parallel tests**: `pytest-xdist`, four workers by file (`-n 4 --dist loadfile` in the pytest
  options; eight cores but 8 GB of RAM, each worker builds its own fixtures). Full suite about 20
  minutes in one process, 2.5 minutes in parallel, 70 seconds after the next item.
- **Rebuild only when the world changes**: a full demo rebuild runs when a round changes market
  history, the trade generator, limits or broad pricing; other rounds are checked with targeted
  tests or a single-stage re-run, and the owner is told which.
- **Faster discount factors**: profiling one counterparty run (100 paths, one worker, 232 s) put
  83% of the time in `ZeroCurve.df`/`zero`, 15.7 million calls of about 12 µs, numpy's array
  machinery spent on one number at a time. A plain-Python path for a single number (same formula,
  linear in r·t, flat outside the nodes) takes 0.3 µs and agrees with the array path to one
  rounding unit. The bank's counterparty step fell from 361 s to 42 s with results equal to the
  stored run within 1e-13 relative; the rest of the EOD benefits too. Vectorising the pricers
  across paths was left for later: the remaining time is spread across the swap pricer's loops.
- Where: `market_data/curves.py`, `pyproject.toml`, test
  `test_zero_curve_scalar_fast_path_matches_the_array_path`.

## Round 41 — Capital allocated to desks by Euler contributions (2026-10-15)

Context: the owner asked to make Euler the official desk allocation on the Capital page for
every component, with an incremental view next to it for decisions. Until then the desk table
mixed three methods. FRTB SA and IMA used standalone charges scaled pro rata to the total.
SA-CCR and BA-CVA used each desk's share of absolute effective notional within the netting set.
CCP capital was not allocated. One question round; the owner took every recommended option.

### 41.1 Charges that are not scale-invariant
- Options: rescale to the charge and report the scale ★ · keep raw contributions with an
  "Unallocated" row
- Choice (owner): **rescale and report**. SA-CCR is rescaled per netting set, and every
  component is checked against its charge. `reg_allocation_summary` stores the raw sum and the
  scale, and the page caption names any component scaled by more than 0.5%.
- Found: on the demo book, raw SA-CCR contributions cover 58% of the charge (scale 1.72). The gap
  is the replacement-cost floor TH + MTA − NICA (9.5m on most hedge-fund and corporate CSAs),
  which does not depend on positions. Unmargined sets reconcile exactly. Every other component
  has a scale of 1.00.

### 41.2 Scope
- Options: all six charges including CCP ★ · the four main ones · five, without the default fund
- Choice (owner): **all six**. FRTB SA, FRTB IMA, SA-CCR, BA-CVA, CCP trade exposure (SA-CCR
  Euler on the cleared sets plus the Euler of the margin posted), and the CCP default fund split
  by each desk's Euler share of the trade exposure at that CCP. The standardised total now
  includes CCP capital, so desks add up to the firm's standardised stack. FRTB IMA is
  allocated separately as the alternative to FRTB SA.

### 41.3 The incremental measure
- Options: remove the desk ★ · grow the desk by 1% · both
- Choice (owner): **remove the desk**: the charge less the charge without the desk. Standalone
  capital is shown too, so diversification (Σ standalone − charge) is visible. The default fund
  has zero incremental, since the firm stays a clearing member.

### 41.4 Layout
- Options: Euler table with a decision panel beside it ★ · paired columns in one table · two
  tables side by side
- Choice (owner): **Euler table + panel**. The official table shows Euler by desk for every
  charge and the standardised total. The panel beside it takes a charge and shows Euler,
  incremental and standalone by desk, with the diversification.

- Choice (default): Euler for FRTB IMA and BA-CVA is analytic. IMA uses tail-scenario means
  through the liquidity-horizon cascade, which is exact for ES. FRTB SA, SA-CCR and the CCP
  margin use a central difference with a bump of 10⁻⁴. The one trade without a desk
  (`IRS_001516`) is allocated to `UNASSIGNED` instead of a blank row. SA-CCR keeps each netting
  set's inputs (`NettingSetInputs`) so a set can be re-measured; its numbers are unchanged
  (old and new code agree within 1e-8 on every set). The regulatory stage takes about 6 s
  instead of 3.5 s.
- Where: `regulatory/allocation.py` (new), `frtb_sa.frtb_sa`, `frtb_ima._ima_by_desk`,
  `saccr.NettingSetInputs`, `cva_capital.ba_cva_charge`, `engine.run_regulatory`
  (`reg_desk_allocation`, `reg_allocation_summary`, `reg_by_desk` now Euler); `GET /capital`
  (`desk_allocation`, `allocation_summary`); Capital page; the Analyst's capital tool.
  REG-008 1.0.0 (new), REG-001 1.3.0, REG-002 1.1.0. Tests: `tests/test_capital_allocation.py`.

### 41.5 One source of collateral for SA-CCR (closes second-look item 27)
- Found while testing the allocation: re-running the regulatory stage after the counterparty stage
  gave a different SA-CCR. `run_regulatory` read the exposure engine's t0 collateral per netting
  set from `cp_profiles` when it existed. The EOD's own regulatory step runs first and never has
  it. On the demo run, SA-CCR capital was 31.3m stored and 41.0m on a re-run, and BA-CVA moved
  with it.
- Owner: fix it. Choice (default): **collateral always from the CSA terms on today's value**,
  what every EOD run already used and what REG-003 states. The other way round is not possible:
  the counterparty stage reads the initial margin the regulatory stage stores (`reg_simm`), so
  regulatory has to run first. The override path is removed (`saccr(collateral_by_set=…)`,
  `NettingSetInputs.collateral`), so the desk allocation's scaled sets and the reported sets use
  the same collateral. Stored EOD results are unchanged.
- Where: `regulatory/saccr.py` (REG-003 1.1.0), `regulatory/engine.py`; the end-to-end test
  re-runs the stage after the counterparty run and must reproduce SA-CCR and BA-CVA.

## Round 42 — Stress episodes that end on their moves, an inflation bear, a history backtest (2026-10-15)

Context: second-look items 6 (the extension years are calm) and 17 (the crash window is noisy).
Measured first on the generator: the crash window gave S&P 500 −14% and USD 10Y +18bp against a
defined −27% and −60bp. At 2.5× daily vol over 22 days, the rates noise (about 70bp, one
standard deviation) is larger than the drift. The rates shock overshot its targets the other way. Two
question rounds; the owner took every recommended option.

### 42.1 The crash fix
- Options: bridge both episodes ★ · lower the vol multiplier · stronger drift
- Choice (owner): **bridge**. Inside each episode window every random draw series (the global
  factor and each group's own draws) has its mean over the window removed. The net move is the
  episode's drift alone, and each day keeps the noise its multiplier gives it. Seed 42: crash
  S&P 500 −26.5%, 10Y −63bp, Euro Stoxx −26.5%, Brent −32%; rates shock S&P 500 −12.2%, 10Y +113bp
  (the 10Y sits between the curve's short and long loads). Holds for seed 7 too.

### 42.2 The extension episode
- Options: a 2022-style inflation bear ★ · a short liquidity crunch · both
- Choice (owner): **the inflation bear**: 125 days from early January 2022, stocks and bonds down
  together. Realised: S&P 500 −22.1%, USD 10Y +206bp, 2Y +249bp, EUR/USD −10.7%, Bitcoin −60%,
  Brent +35%. The inputs sit above those figures (equities −25%, +365bp short, +215bp long),
  because six months of equity drift and rates mean reversion take back part of the move. The
  10Y move still varies by seed (+222bp for seed 7), because the pull depends on where rates
  start. The core three years are unchanged by this choice.

### 42.3 The backtest
- Options: add a five-year information backtest ★ · the same on full revaluation · leave it
- Choice (owner): **information backtest on the delta-gamma-vega P&L**: today's book on every
  daily move of the stored history after a 250-day lookback (1,055 days on the bank, 14
  exceptions against 10.6 expected; fund 1,054 days, 16), stored as `STATIC_SENSITIVITY_HISTORY`
  with its own series. The 250-day static result still alone sets the IMA multiplier.

### 42.4 Episode dates (default)
- Found: the stress engine counted each episode window back from the end of the stored history,
  which grows by a day per scheduled run, so the live demo's windows had slid two business days
  (crash 13 November instead of 11 November). The generator now returns each window from the
  close before its first day to its last day, and `simulate` and the Lab store them
  (`market_episode`). Every stress caller reads them; a database without them falls back to the
  old offsets.

### 42.5 The rebuild
- Bridging moves the core path after the crash, so the levels on the business date move (Bitcoin,
  equity indices, rates), trades are struck at different levels and limits are recalibrated.
  Firm VaR on the 15th is 64.0m (was 70.2m). The fund's worst stress is now the inflation bear
  (−207m on the 11th, was −146m for equity −20%). The bank's 250-day backtest is AMBER on the 15th
  (5 exceptions) instead of the 14th, so the IMA multiplier moves with it.
- First rebuild, brought to the owner (second round):
  - EUR_RATES_STRESS breached on the 15th at 100.1%. The EUR rates desk's worst loss is now 150m
    in the risk-off crash: once the crash ends on its rates rally, the desk's exposure to falling
    rates shows, which the noisy window had hidden. Calibration capped the limit at 150m (twice the
    75m appetite). Options: keep it as a ninth breach ★ · raise its appetite. Choice (owner):
    **keep it**, the same reasoning as CPTY_CORP_AIR in 34.3.
  - Two story crossings drifted just short: SINGLE_NAME_EQ_VEGA ended the 15th at 99.0% (it still
    breached on the 14th), and the fund's EQ_LS_US_VAR reached 97.7% on the 14th. Options: re-aim
    the targets ★ · accept the shorter story. Choice (owner): **re-aim**: `STORY_TARGETS`
    0.92 → 0.94 and 0.95 → 0.98.
- Second rebuild: bank 5, 7 and 9 breaches (EUR_RATES_STRESS the ninth); SINGLE_NAME_EQ_VEGA
  94%, 104.8%, 101.1%; fund 2 then 3 (EQ_LS_US_VAR 98%, 100.8%).
- Where: `simulation/market_data.py` (`_Sim._bridge`, `EXTENSION_EPISODES`,
  `GeneratedMarketData.episodes`; SIM-001 1.6.0), `storage` (`market_episode`,
  `save/load_market_episodes`), `risk/stress.py` (`historical_episodes`; MR-005 1.4.0),
  `risk/backtest.py` (`history_backtest`; MR-011 1.1.0), `workflows/eod.py` and `rerun.py`,
  `GET /backtest` (`history_series`), the VaR page, `limits/calibration.py` (`STORY_TARGETS`).
  Tests: `test_episodes_end_on_their_defined_moves_whatever_the_seed`,
  `test_stored_episode_windows_do_not_move_as_the_history_grows`,
  `test_results_persisted_and_reloadable` (three backtest series).

## Round 43 — Identity and permissions (2026-10-15)

Context: second-look items 1 (no authentication on breach actions) and 10 (MCP over HTTP has no
authentication). Measured first: every write took `actor` as free text (API body, CLI `--actor`,
dashboard field), across breaches, increases, sign-off, limit drafts, the VaR setup, re-runs and
agents. The approval matrix, "never the requester" and the named signers therefore held only if
people typed their names honestly. The work was added to the roadmap before it started. One
question round; the owner took every recommended option. Recorded as ADR 0006.

### 43.1 Users
- Options: role accounts from the organisation ★ · named people with roles · a handful of personas
- Choice (owner): **role accounts**: the risk function, a head per business and desk, other
  firm-level owners (the fund's CFO), each sign-off role a metric names, and a Viewer. Bank 27
  users, fund 24. A user's name is the string the workflows already record. The fund's sign-off
  role "Chief Risk Officer" became "CRO" (one person, one name).

### 43.2 Tokens
- Options: issued by the CLI and stored hashed ★ · signed JWTs · static tokens in .env
- Choice (owner): **personal access tokens**: `novera users token` prints `nvr_…` once; the
  database keeps the SHA-256 hash, a prefix, an expiry (90 days) and a revocation time. Times
  are stored as naive UTC, since DuckDB's TIMESTAMPTZ needs pytz, which the project does not
  ship.

### 43.3 Permissions
- Options: role matrix enforced ★ · identity only
- Choice (owner): **the matrix in ADR 0006**, enforced in the write service, so the API, the
  dashboard and the CLI agree. A limit's desk comes from its scope, not its owner string: the
  fund's limits name the same PM two ways ("PM CREDIT_LS" and "PM Credit Long/Short").

### 43.4 Scope
- Options: writes on API and MCP, CLI names a known user ★ · everything including the CLI · API
  writes only
- Choice (owner): **writes on API and MCP**. Reads stay open unless
  `NOVERA_AUTH_REQUIRED_FOR_READS`. The CLI accepts `--actor` only as a known user and stamps
  `via: {channel: cli, os_user}` on its audit events. MCP over streamable-http verifies the same
  tokens and refuses to start without users.

- Contract change: the request bodies lose `actor`, `approver` and `requested_by`; the client
  methods lose those arguments; `/me` and `/users` are added; `401 UNAUTHENTICATED` and
  `403 FORBIDDEN` join the error codes. The dashboard's "Acting as" picker replaces the free-text
  fields, and a refused action shows its reason. No figure changes, so no rebuild: the live bank,
  fund and Lab databases were seeded with `novera users seed`.
- Where: `security/` (users, tokens, permissions), `storage` (`app_user`, `api_token`),
  `api/app.py` (`current_user`, `read_guard`), `api/service.py` (`RiskWriteService(repo, user)`),
  `api/agents_api.py`, `api/client.py`, `cli.py` (`_as`, `_write`, `users` commands),
  `mcp_server.py` (`TokenVerifier`), `ui/app.py`, `workflows/runs.py` (`set_audit_channel`).
  Records: ADR 0006, MR-008 1.1.0, OPS-003 1.2.0, AI-004 1.1.0. Tests: `tests/test_security.py`,
  `tests/test_api.py::test_writes_need_a_token_and_a_role_that_allows_them`.

## Standing instructions given outside the question rounds

- Do not read or use `../z-My_Tests` (private brainstorming).
- Ask questions at each step when something is unclear.
- The three brainstorming documents were merged into the vision, architecture and roadmap.
- Finish Phases 1 to 3 completely before Phase 4 (done); Phase 5 before Phase 6.

## Choices worth a second look

1. **Closed in round 43** (ADR 0006): writes take the actor from a personal access token,
   checked against a role matrix. Was: **No authentication on breach actions** (6.3). Still
   open: no real single sign-on, and the CLI trusts the operator to name a known user.
2. **Scripted Copilot** (7.1): the live Claude path is untested until a key is added.
3. **Alert channels** (8.2): untested against a real Slack webhook or SMTP server.
4. **Monte Carlo on the approximation** (9.1): a full-revaluation option would strengthen
   the model-risk story for option books.
5. **Closed in round 40** (MR-017). Was: **Limit calibration by hand** (4.3): any generator change moves the utilisations;
   consider calibrating limits from a target utilisation instead.
6. **Closed in round 42**: the extension carries a 2022-style inflation bear, and a five-year
   information backtest sits beside the 250-day one. Was: **Extension years are calm** (20.1): the
   two earlier years carry no stress episode and the backtest still uses 250 + 250 days.
7. **Synthetic parameters** (9.3, 11.2, HF-002, HF-006): volume, spread, margin schedules,
   crowding scores and capital risk weights are assumptions; each record says so.
8. **Swaption smile with beta fixed at zero and no rho or nu sensitivities** (21.1, 21.2):
   the smile is simulated, not calibrated to broker quotes, and its risk shows only in
   full-revaluation VaR; the earlier gap (no smile at all, 12.1) is closed.
9. **Re-levelling stale data with a proxy family** (12.4): the basis between the stale family
   and its proxy is not measured; a bank would back-test the proxy choice.
10. **Closed in round 43**: MCP over streamable-http verifies the platform's tokens and audits each
    call under the token's user. Was: **MCP over HTTP has no authentication of its own** (13.1).
11. **Templated agent drafts** (13.2): with the scripted provider the notes are traceable but
    flat; the Claude path for `draft` is untested until a key is added (same as item 2).
12. **Lab uses production limit calibration** (13.3): small sandbox books under-utilise
    limits, so detection scores depend on the background size chosen.
13. **Schemas pass extra fields through** (14.1): the API never drops an engine column, but
    a client generating types sees `additionalProperties`; tighten to `extra="forbid"` once the
    data model settles and the drift test has been quiet for a phase.
14. **Grouped rows carry the dimension as an extra field** (14.1): a `{by, rows}` shape would
    type cleanly; deferred because it breaks every screen that reads those endpoints.
15. **EUR tenor basis is simulated, not stripped** (33.1, closes the single-curve gap of 18.3):
    EURIBOR projects off ESTR plus a zero-spread basis drawn by the simulator; with real data
    the 3M and 6M curves should be bootstrapped from ESTR/EURIBOR basis swaps, and no free source
    gives that term structure. The market standard named per product in MV-001 is sell-side
    practice; a fund marking listed products to screen would rate the ETF row differently.
16. **Candidate market-data sources are untested text** (19.4): the free and paid sources
    per group were written from knowledge of the vendors, not from a fetch; wire one before
    quoting it to a client, and re-check FRED's ICE swap-rate series, which may be
    discontinued.
17. **Closed in round 42**: the draws are bridged inside each episode window, so the crash ends at
    S&P 500 −26.5% and USD 10Y −63bp. Was: **The stylised crash window is noisy in the demo seed**
    (22.3): the risk-off crash showed S&P 500 −9.7% and USD 10Y +71bp against −27% and −60bp.
18. **Addressed in round 40**: a VaR template switch is followed by a recalibration draft of the VaR
    and ES limits (`novera limits calibrate --types VAR,EXPECTED_SHORTFALL`). Still open: calibration
    resizes existing limits only, and no stressed-VaR limit is seeded yet. Was: **Limit amounts do not follow the VaR measure** (25.4): loading a template that changes
    the LIMIT VaR row leaves the seeded amounts where they were, so utilisations jump or
    collapse; the stressed-VaR limit type has no seeded limits at all. A calibration step
    from a target utilisation would make template switches comparable.
19. **The stressed window is chosen by equity volatility** (25.3): `most_volatile_year`
    looks at the S&P 500 only; a rates or credit book would pick a different year. With real
    history the window should be a named crisis, which the stress library will then mark
    REAL.
20. **Closed in round 39** (posted margin, threshold, MVA). Was: **Initial margin is received-only and unconditional** (31.2): IM we post is not modelled,
    so it neither reduces the counterparty's exposure to us nor feeds DVA, and its gap risk on
    their default is not captured. There is no AANA in-scope test or group-level threshold
    either, so every netting set with a CSA gets IM where the rules would exempt the small ones.
    Both widen the collateral benefit the platform reports.
21. **Closed in round 39 for LCH and ICE** (CR-006, REG-007); listed derivatives at exchange clearing
    houses remain out, and the CCP margin model is SIMM-lite standing in for PAIRS or SPAN. Was:
    **CCP exposure is not modelled** (10.4, roadmap Phase 4): CCPs and exchanges exist in the
    counterparty reference data but carry no netting sets, so cleared exposure, default-fund
    contributions and cleared initial margin are absent. Counterparty risk covers the bilateral
    book only, which a reader may not infer from the Counterparty page.
22. **Closed in round 36**: runs and jobs left RUNNING by a killed process are swept. Remaining:
    a process that hangs without dying keeps its run RUNNING, since its process is alive; only a
    watchdog with a heartbeat would catch that.
23. **Fixings come from the simulated curves** (34.4): a trade's running-period fixing is read
    off the history (the 1M node stands for the overnight rate, the projection curve for the term
    fixing), not from published fixings; with real data a fixings table per index should feed it.
24. **No holiday calendars** (34.2): schedules are unadjusted and BUS/252 counts weekdays, so DI
    pre accrual and payment dates differ slightly from B3 and the other markets' calendars.
25. **The cross-currency basis is simulated** (35.1, closes the collateral gap): forwards and
    collateral discounting use a simulated basis, not one stripped from basis-swap quotes or FX
    forward points; the FRTB SA weight and correlation for it are as read, to be checked.
26. **FVA on a flat funding spread** (37.2, closes the FVA gap of 35.4): one spread for every
    currency and tenor, set rather than observed, so FVA has no market sensitivity; no MVA for
    initial margin posted, no ColVA.
27. **Closed in round 41 (41.5)**: SA-CCR collateral always comes from the CSA terms. Was: **a regulatory
    re-run reads collateral the EOD step did not have**. A re-run after the counterparty stage used
    the exposure engine's simulated balance, so SA-CCR went from 31.3m to 41.0m on the demo run.
