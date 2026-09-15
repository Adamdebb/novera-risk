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
- Choice: **Deferred to Phase 5** (then delivered there as REG-004)

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
