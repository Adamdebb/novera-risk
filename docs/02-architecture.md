# Architecture

## Shape

A modular monolith in Python. One package, strict internal boundaries, no microservices
until a customer forces it. The UI is a thin client over a typed API so it can be replaced.

```
                     UI (Streamlit now, React later)
                                 |
                          API (FastAPI, typed)
                                 |
       +-------------------------+---------------------------+
       |                         |                           |
   WORKFLOWS               NOVERA ANALYST (ai/)          REPORTING
   EOD pipeline            tools -> api/services       risk packs
   run registry            never computes              exports
   audit events                  |
       |                         |
       +----------- SERVICES / ENGINE --------------------------+
       |  pricing  ->  sensitivities  ->  aggregation           |
       |  var / stress / pnl_attribution / concentration        |
       |  counterparty_risk (netting, collateral, exposure)     |
       |  limits (utilisation, breach lifecycle)                |
       |  data_quality (trust checks)                           |
       +--------------------------------------------------------+
                                 |
                       DOMAIN MODELS (pydantic)
       instruments, trades, organisation, counterparties, limits
                                 |
                          STORAGE (repository layer)
                DuckDB + Parquet now; Postgres for transactional later
                                 |
                          SIMULATION
        organisation, trade, market-data and scenario generators
```

## Data flow of one end-of-day run

```
portfolio snapshot ---+
market snapshot ------+--> run(run_id, config hash, model versions)
history, limits ------+          |
                                 v
   load (+ proxies) -> valuation -> data-quality checks -> sensitivities
        -> VaR (historical, delta-gamma-vega challenger, Monte Carlo) -> backtest
        -> stress -> limits (first pass) -> concentration, liquidity, look-through
        -> P&L explain (+ challenger) -> verdict, audit events, breach sync -> persist
        -> regulatory (bank) / counterparty / fund -> limits (second pass, deferred types)
        -> alerts -> final save
```

Step by step, with what each stage reads and writes: `docs/07-eod-workflow.md`.

## Boundaries that must hold

| Boundary | Rule | Why |
|----------|------|-----|
| UI to engine | UI calls API only | Streamlit to React swap stays cheap |
| API to client | Response model on every route, problem+json errors with codes, schema committed at `docs/api/openapi.json` | A client generates types from git and branches on error codes, not messages |
| Engine to storage | All SQL in `storage/` | DuckDB to Postgres swap stays cheap |
| AI to engine | Tools call services; AI never computes | Auditability, model risk |
| Pricing to risk | Pricers return PV and cashflows only | Sensitivities are computed uniformly by bump-and-reprice or analytic hooks |
| Domain to everything | Domain models have no I/O | Testable, reusable in simulators |

## Storage split

- **Analytical store**: DuckDB over Parquet. Market-data history, run results, scenario
  P&L vectors. Append-heavy, columnar, scanned in bulk.
- **Transactional store**: DuckDB for now, Postgres when multi-user. Trades, limits,
  breaches, approvals, audit events, run registry.
- One `Repository` protocol; concrete implementations per backend.

## Identifiers

- `portfolio_snapshot_id`: content hash of the trade set as of a business date.
- `market_snapshot_id`: content hash of the market-data set as of a timestamp.
- `run_id`: ULID. Run record stores both snapshot IDs, model versions, config hash.
- Every result row carries `run_id`. Every AI answer cites `run_id`s.

## Geography

A second hierarchy beside the organisation: currency pair -> currency -> country -> region
(decision round 44). The static table holds 8 regions, the 254 ISO 3166 territories that have a
currency plus six monetary unions, and the 153 ISO 4217 currencies in circulation. Each currency
has a liquidity score (the BIS FX turnover rank, 1 = USD); a pair attaches to its less liquid
currency. `scripts/build_geography.py` generates the table from the Unicode CLDR data;
`novera simulate` and `novera reference seed` store it (`ref_region`, `ref_country`,
`ref_currency`, `ref_currency_pair`). Reports attach it at read time: every stored run, old or
new, can be shown by region, country, risk currency or currency pair.

## Risk-factor model

Every instrument maps to risk factors in a shared universe (curve nodes, FX pairs, equity
names and indices, credit indices, commodity curves, crypto pairs, vol surface points).
Sensitivities, VaR and stress all key off the same factor IDs, so results reconcile.

## Renaming the platform

The brand is `settings.platform_name`. The package name is `novera`. Run
`python scripts/rename_platform.py <NewName>` to rename package, imports, env prefix and
docs. See the script for the rules that keep this safe.
