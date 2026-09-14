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

Risk engine status: bump-and-reprice sensitivities (DV01 ladders, CS01, FX/equity/
commodity/crypto deltas, vega, gamma, theta), 99% 1-day historical VaR by full
revaluation over 500 days with 97.5% ES and a delta-gamma-vega challenger, a stress
library of twelve hypothetical scenarios plus the two stylised historical episodes
(MR-001 to MR-005). Full-revaluation VaR runs in about 40 seconds on the 1,515-trade
bank using a forked process pool.

Limits: 79 seeded limits across firm, business, desk and counterparty levels (VaR, ES,
stress loss, DV01 ladders and buckets, CS01, deltas, vega, concentration shares,
counterparty exposure) with utilisation and status (MR-006). On the demo date four
breach and fifteen warn, matching the planted problems.

Still to do in this phase: P&L attribution, concentration and liquidity measures, Monte
Carlo VaR, VaR backtesting (Kupiec, Christoffersen), daily risk pack, morning dashboard
with drill-down.

## Phase 3 — Production workflow and trust (complete)

- [x] Governed EOD run: run id, snapshot ids, model versions, config hash, timings,
      immutable result tables and Parquet scenario matrices, audit events
- [x] Data-quality checks and verdict (DQ-001): stale and missing factors with the trades
      they affect, invalid and dangling trades, unpriced and dead trades, unexplained P&L
- [x] Daily P&L explain: full-revaluation waterfall with a sensitivity challenger (MR-007)
- [x] Read API (FastAPI) and Streamlit morning dashboard with drill-down, VaR, stress,
      limits, P&L, data quality, runs and audit, all from the stored run
- [x] Run comparison (any two runs side by side): headline, VaR by group, limit moves, trade moves
- [x] Breach workflow (MR-008): raise, acknowledge, escalate, auto-escalate, close with reasons;
      temporary limit increases with an approval matrix, expiry and effective amounts in monitoring;
      write endpoints on the API and actions in the dashboard; `novera breach` CLI
- [x] Second simulated business day (new trades, one concentration swap unwound) so the
      day-on-day story is real: three breaches auto-escalate, EM FX breaches on a new trade
- [x] Risk Copilot (AI-001), pulled forward from Phase 7: tool-calling over the stored run with
      a what-if engine, stored answers with tool calls and run ids, Claude Opus 5 or a scripted
      provider without credentials; API, `novera ask` and a chat page
- [x] Scheduler and alerts (OPS-001): `novera schedule` with retries, job log, day advance
      by historical bootstrap; alerts stored always, delivered by Slack webhook or email
- [x] Independent-challenger reconciliation (MR-009): simulated official feed with four
      planted differences, gap attributed to scope, market data, pricing model, methodology
- [x] Real market-data adapters (MD-001): FRED, Yahoo Finance, Coinbase via `novera fetch`,
      provenance recorded, named crisis windows become stress scenarios when covered

Phase 3 is complete. Deferred from Phase 2 and still open: Monte Carlo VaR, VaR
backtesting (Kupiec, Christoffersen), concentration and liquidity measures, risk pack export.

## Phase 4 — Counterparty risk (next)

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
