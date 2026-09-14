# Novera — AI-native Market & Counterparty Risk Intelligence Platform

Working notes for AI-assisted development. Read fully before changing code.

## What this is
A demonstrable prototype of how a modern market-risk function operates: a deterministic
risk engine over a simulated global trading organisation, wrapped in workflow automation,
governance and an AI Risk Copilot. Target audiences: investment banks and hedge funds.
Read `docs/01-product-vision.md` for positioning and `docs/03-roadmap.md` for what is
in scope now. Do not build ahead of the current phase.

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
  market_data/         Snapshots, curves, surfaces, risk-factor universe
  pricing/             One pricer per product; returns PV and cashflows, nothing else
  risk/                sensitivities, var, stress, pnl_attribution, concentration, liquidity
  counterparty_risk/   netting, collateral, exposure (EE/PFE), cva (later)
  limits/              Limit definitions, utilisation, breach lifecycle, escalation
  data_quality/        Checks that answer "can I trust today's run?"
  workflows/           End-of-day pipeline, run registry, audit events
  storage/             Repository layer. All SQL lives here.
  reporting/           Risk packs, tables, exports
  api/                 FastAPI app and typed schemas
  ai/                  Risk Copilot: tools, prompts, provider adapter. Never computes.
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
uv run streamlit run src/novera/ui/app.py
uv run uvicorn novera.api.app:app --reload
```

## Do not
- Do not read or use `../z-My_Tests` (owner's private brainstorming).
- Do not add an asset class or product outside the current phase without updating
  `docs/03-roadmap.md` first.
- Do not introduce a second source of truth for reference data or results.
