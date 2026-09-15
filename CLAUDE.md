# Novera — AI-native Market & Counterparty Risk Intelligence Platform

Working notes for AI-assisted development. Read fully before changing code.

## What this is
A demonstrable prototype of how a modern market-risk function operates: a deterministic
risk engine over a simulated global trading organisation, wrapped in workflow automation,
governance and an AI Risk Copilot. Target audiences: investment banks and hedge funds.
Read `docs/01-product-vision.md` for positioning, `docs/03-roadmap.md` for what is
in scope now, and `docs/06-decision-log.md` before proposing to change a past choice. Do not build ahead of the current phase.

## Non-negotiable rules
1. **The engine is deterministic and authoritative.** No LLM ever computes, adjusts or
   rounds a risk number. AI explains, investigates, drafts and queries validated outputs
   through tool calls that hit the engine or the database.
2. **No business logic in the UI.** `novera/ui` only calls `novera/api` (or the service
   layer behind it) and renders. If a screen needs a number, the API must expose it.
3. **No SQL outside `novera/storage`.** Every other module talks to typed repository
   methods. Keep SQL ANSI where possible; isolate DuckDB-only syntax in named functions.
4. **Every metric has a methodology record** in `docs/methodology/` using the template
   there (definition, maths, inputs, assumptions, limitations, validation, version, owner).
   A metric without one is not "done".
5. **Every calculation run has a run ID** and records: business date, portfolio snapshot
   ID, market-data snapshot ID, model versions, config hash. Results are immutable once
   written. Re-running with the same inputs must reproduce the same numbers.
6. **Tests before "done".** Pricers are benchmarked against QuantLib or closed-form
   results. Risk aggregation is tested for additivity. Fixtures live in `tests/fixtures`.
7. **The brand name is a config value.** Use `settings.platform_name` in user-facing text.
   The package name `novera` may be renamed with `scripts/rename_platform.py`; do not
   hard-code the brand in strings that the script would miss (see that script's rules).

## Layout
```
src/novera/
  config.py            Settings (pydantic-settings, NOVERA_* env vars)
  domain/              Pure data models: instruments, trades, organisation, counterparties, limits
  simulation/          Organisation, trade, market-data and scenario generators
  market_data/         Snapshots, curves, surfaces, swaption cubes, risk-factor universe, proxies (MD-002)
  pricing/             One pricer per product (breadth.py holds the Phase 6 eight); returns PV and cashflows only
  risk/                sensitivities, var, stress, pnl_attribution, concentration, liquidity, lookthrough
  counterparty_risk/   netting, collateral, exposure (EE/PFE), cva, wrong-way risk
  regulatory/          FRTB SA and IMA, SA-CCR, SIMM-lite, BA-CVA, cash ladder (bank face)
  fund/                exposures, PB margin, factor betas, redemption stress, attribution, crowding (fund face)
  limits/              Limit definitions, utilisation, breach lifecycle, escalation
  data_quality/        Checks that answer "can I trust today's run?"
  workflows/           End-of-day pipeline, run registry, audit events
  storage/             Repository layer. All SQL lives here.
  reporting/           Risk packs, tables, exports
  api/                 FastAPI app and typed schemas
  ai/                  Risk Copilot: tools, prompts, provider adapter, agents/ (AI-002/003). Never computes.
  lab/                 Portfolio Lab: sandbox organisations with chosen planted problems (LAB-001)
  ui/                  Streamlit thin client
```

## Conventions
- Python 3.12, `uv` for env and deps. `uv run pytest`, `uv run ruff check .`, `uv run mypy src`.
- Pydantic v2 models for all domain objects. Frozen where the object is a fact (trade,
  snapshot), mutable only for workflow state (breach, run).
- Money and notionals are `float` in reporting currency unless the field name says
  otherwise (`notional_ccy`, `pv_local`). Reporting currency comes from settings.
- Dates are `datetime.date`; timestamps are timezone-aware UTC.
- Aggregation hierarchy is `trade -> book -> desk -> business -> firm`. Legal entity is a
  dimension carried by the book, since one desk books into several entities.
  Aggregation functions accept a level name, never a hard-coded column.
- Name things by what they are in a risk department: `dv01`, `cs01`, `pfe_95`, `ee`,
  `stress_pnl`, `limit_utilisation`. No abbreviations that a risk manager would not use.
- Commit after each coherent milestone. Commit messages state what a risk manager gains.

## Commands
```
uv sync --all-extras          # create .venv and install
uv run pytest                 # tests
uv run novera --help          # CLI
uv run novera simulate        # simulated bank, portfolio, market data, limits
uv run novera run eod         # governed EOD run, stores results and audit events (--business-date)
uv run novera breach list     # breach workflow: list, ack, escalate, close, request-/decide-increase
uv run novera ask "..."       # Risk Copilot; scripted provider unless ANTHROPIC_API_KEY is set
uv run novera schedule --once # advance a simulated day and run EOD; `schedule` alone loops daily
uv run novera vendor-feed / reconcile <csv>   # independent-challenger demo
uv run novera fetch           # real market data into the history (network, optional FRED key)
uv run novera run counterparty # exposure engine on a stored run (EOD does this too unless NOVERA_EXPOSURE_ENABLED=false)
uv run novera run regulatory  # FRTB SA/IMA, SA-CCR, SIMM, BA-CVA, cash ladder (bank face; EOD runs it too)
uv run novera simulate --template hedge_fund   # fund face into NOVERA_FUND_DB_PATH; `run eod --fund`
uv run novera report          # daily risk pack (HTML, PDF via Playwright Chromium, Excel)
uv run novera agent investigate <breach_id> | scenarios | validation | ingest [doc] --approve <actor>
uv run novera lab problems / run <name> --problems a,b --scale 2 / list   # Portfolio Lab sandboxes
uv run novera lab run <name> ...   # sandbox at data/lab/<name>.duckdb; NOVERA_DB_PATH points the dashboard at it
uv run novera risk            # ad-hoc risk summary without persisting
uv run streamlit run src/novera/ui/app.py
uv run uvicorn novera.api.app:app --reload
```
If `uv run` fails with `ModuleNotFoundError: No module named 'novera'`, Python skipped the
editable `.pth` file because macOS flagged it hidden after a uv rebuild. Fix:
`uv sync --all-extras --reinstall-package novera`. Tests are immune (pytest `pythonpath`).

## Do not
- Do not read or use `../z-My_Tests` (owner's private brainstorming).
- Do not add an asset class or product outside the current phase without updating
  `docs/03-roadmap.md` first.
- Do not introduce a second source of truth for reference data or results.
