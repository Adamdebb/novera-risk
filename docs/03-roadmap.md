# Roadmap

Each phase ends with something demonstrable. Later phases never start before the current
phase has tests, methodology records and a working screen.

## Phase 1 — Foundation (current)

- [x] Repo, tooling, CI, design docs, governance rules
- [x] Domain model: instruments, trades, organisation hierarchy, counterparties, netting sets, CSAs, limits
- [x] Simulated organisation "Global Macro Bank" with desks across all asset classes
- [x] Trade generator with deliberately injected problems (see below)
- [x] Market-data simulator: curves, FX, equities, vol surfaces with smile, credit, commodities, crypto, two stylised crisis episodes, planted stale surface and missing node
- [x] Repository layer on DuckDB; snapshots with content-hash IDs
- [x] CLI: `novera simulate`, `novera value`; `novera run eod` and `novera report` arrive with Phase 3

## Phase 2 — Market risk MVP

Instruments (one per asset class, ten products):

| Asset class    | Product                | Listed/OTC | Pricer                     |
|----------------|------------------------|------------|----------------------------|
| Rates          | Government bond        | Listed     | Discounting off curve      |
| Rates          | Interest-rate swap     | OTC        | Curve discounting/forwarding |
| FX             | FX spot and forward    | OTC        | Covered interest parity    |
| FX             | FX vanilla option      | OTC        | Garman–Kohlhagen           |
| Equity         | Cash equity            | Listed     | Mark to market             |
| Equity         | Equity index future    | Listed     | Cost of carry              |
| Equity         | Equity vanilla option  | Listed     | Black–Scholes              |
| Commodities    | Commodity future       | Listed     | Curve mark                 |
| Credit         | CDS index              | OTC        | ISDA standard model (simplified) |
| Digital assets | BTC / ETH spot         | Listed     | Mark to market             |

Status: all ten pricers implemented and benchmarked against QuantLib or closed form
(methodology records PR-001 to PR-009). Trades are struck at fair market on their trade
date from the simulated history, so P&L is genuine.

Risk: PV, clean and dirty P&L, DV01, CS01, FX delta, equity delta, commodity delta,
vega, gamma, theta, curve ladders, historical-simulation VaR and expected shortfall,
Monte Carlo VaR, historical and hypothetical stress, concentration, P&L attribution,
limit framework with breach workflow, daily risk pack, VaR backtesting (Kupiec,
Christoffersen). Morning dashboard with drill-down.

## Phase 3 — Production workflow and trust

Scheduled EOD pipeline, run comparison, data-quality controls ("can I trust today's
VaR"), independent-challenger reconciliation, audit trail, alerts and escalation,
real market-data adapters (FRED, Yahoo, crypto exchanges) so named historical
scenarios (2008, 2011, 2015, 2016, 2020, 2022, 2023) use real history.

## Phase 4 — Counterparty risk

Counterparty hierarchy, legal entities, agreements, netting sets, CSAs with thresholds,
MTAs and haircuts, current exposure, EE/EPE/PFE by Monte Carlo, collateral and margin
(VM, IM with SIMM-lite), counterparty limits, watchlist, wrong-way-risk indicators,
CVA/DVA. Start with FX forwards, swaps and repos.

## Phase 5 — Segment modules

- **IB**: FRTB SA and IMA (ES, P&L attribution test, NMRF), SA-CCR, RWA and capital
  attribution, regulatory evidence, ICAAP and ILAAP inputs, IPV (independent price
  verification), funding cash ladder.
- **HF**: prime-broker margin replication, gross/net and leverage, factor exposures,
  liquidity and days-to-liquidate, redemption stress, crowding, strategy attribution,
  investor reporting.

## Phase 6 — Instrument breadth

Repos and securities financing, swaptions and IR futures, commodity options, single-name
CDS, ETFs and mutual funds with look-through, exotics (barriers, digitals), strike
concentration reporting, volatility calibration tools.

## Phase 7 — AI and automation

Risk Copilot with tool calling over the API, daily commentary drafting, breach
investigation agent, scenario suggestion, document ingestion (ISDA/CSA to netting
sets), model-validation report drafting, MCP server exposing the same tools,
"Portfolio Lab" (choose an organisation template, inject problems, run, watch detection).

## Injected problems in the simulated portfolio

USD 10Y DV01 concentration, illiquid Brent position, BTC convexity, counterparty near
limit, wrong-way exposure, stale EUR vol surface, missing USD curve node, trades with
invalid fields, unexplained P&L on a handful of positions, an intentional VaR limit breach.
