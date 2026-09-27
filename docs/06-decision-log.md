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

## Standing instructions given outside the question rounds

- Do not read or use `../z-My_Tests` (private brainstorming).
- Ask questions at each step when something is unclear.
- The three brainstorming documents were merged into the vision, architecture and roadmap.
- Finish Phases 1 to 3 completely before Phase 4 (done); Phase 5 before Phase 6.

## Choices worth a second look

1. **No authentication on breach actions** (6.3): fine for a demo, first thing a bank
   security review will raise.
2. **Scripted Copilot** (7.1): the live Claude path is untested until a key is added.
3. **Alert channels** (8.2): untested against a real Slack webhook or SMTP server.
4. **Monte Carlo on the approximation** (9.1): a full-revaluation option would strengthen
   the model-risk story for option books.
5. **Limit calibration by hand** (4.3): any generator change moves the utilisations;
   consider calibrating limits from a target utilisation instead.
6. **Extension years are calm** (20.1): the two earlier years carry no stress episode and
   the backtest still uses 250 + 250 days; a longer backtest and an older third episode are
   the natural next steps.
7. **Synthetic parameters** (9.3, 11.2, HF-002, HF-006): volume, spread, margin schedules,
   crowding scores and capital risk weights are assumptions; each record says so.
8. **Swaption smile with beta fixed at zero and no rho or nu sensitivities** (21.1, 21.2):
   the smile is simulated, not calibrated to broker quotes, and its risk shows only in
   full-revaluation VaR; the earlier gap (no smile at all, 12.1) is closed.
9. **Re-levelling stale data with a proxy family** (12.4): the basis between the stale family
   and its proxy is not measured; a bank would back-test the proxy choice.
10. **MCP over HTTP has no authentication of its own** (13.1): the stdio default needs none;
    the streamable-http transport must sit behind the firm's gateway.
11. **Templated agent drafts** (13.2): with the scripted provider the notes are traceable but
    flat; the Claude path for `draft` is untested until a key is added (same as item 2).
12. **Lab uses production limit calibration** (13.3): small sandbox books under-utilise
    limits, so detection scores depend on the background size chosen.
13. **Schemas pass extra fields through** (14.1): the API never drops an engine column, but
    a client generating types sees `additionalProperties`; tighten to `extra="forbid"` once the
    data model settles and the drift test has been quiet for a phase.
14. **Grouped rows carry the dimension as an extra field** (14.1): a `{by, rows}` shape would
    type cleanly; deferred because it breaks every screen that reads those endpoints.
15. **EUR swaps on a single curve** (18.3): the gap is measured at 0.53% of PV on the test
    trade but the EUR book is still priced without the ESTR/EURIBOR basis; dual-curve is the
    next pricing upgrade. The market standard named per product in MV-001 is sell-side
    practice; a fund marking listed products to screen would rate the ETF row differently.
16. **Candidate market-data sources are untested text** (19.4): the free and paid sources
    per group were written from knowledge of the vendors, not from a fetch; wire one before
    quoting it to a client, and re-check FRED's ICE swap-rate series, which may be
    discontinued.
17. **The stylised crash window is noisy in the demo seed** (22.3): on the Stress library
    page the risk-off crash shows S&P 500 -9.7% first-to-last and USD 10Y +71bp, although the
    episode is defined with -27% and -60bp. The 2.5x daily vol multiplier over 22 days lets
    noise dominate the drift for seed 42 (the 20% drawdown test measures peak to trough over
    the whole history). A cleaner episode needs a stronger drift or a lower multiplier, which
    changes the core history and so every calibrated limit; deferred for that reason.
18. **Limit amounts do not follow the VaR measure** (25.4): loading a template that changes
    the LIMIT VaR row leaves the seeded amounts where they were, so utilisations jump or
    collapse; the stressed-VaR limit type has no seeded limits at all. A calibration step
    from a target utilisation would make template switches comparable.
19. **The stressed window is chosen by equity volatility** (25.3): `most_volatile_year`
    looks at the S&P 500 only; a rates or credit book would pick a different year. With real
    history the window should be a named crisis, which the stress library will then mark
    REAL.
20. **Initial margin is received-only and unconditional** (31.2): IM we post is not modelled,
    so it neither reduces the counterparty's exposure to us nor feeds DVA, and its gap risk on
    their default is not captured. There is no AANA in-scope test or group-level threshold
    either, so every netting set with a CSA gets IM where the rules would exempt the small ones.
    Both widen the collateral benefit the platform reports.
21. **CCP exposure is not modelled** (10.4, roadmap Phase 4): CCPs and exchanges exist in the
    counterparty reference data but carry no netting sets, so cleared exposure, default-fund
    contributions and cleared initial margin are absent. Counterparty risk covers the bilateral
    book only, which a reader may not infer from the Counterparty page.
22. **A RUNNING run left by a killed process stays RUNNING** (32.3): it is never the latest
    and the next job re-runs the day, but nothing marks it abandoned; a start-up sweep that
    turns RUNNING runs older than a threshold into FAILED would tidy the run list.
