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

All Phase 2 items are delivered (some in the Phase 3 work stream): P&L attribution
(MR-007), concentration (MR-012), liquidity (MR-013), Monte Carlo VaR (MR-010), VaR
backtesting (MR-011), the daily risk pack (RP-001) and the morning dashboard.

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

Phase 3 is complete, and the Phase 2 leftovers are closed: Monte Carlo VaR on the
delta-gamma-vega expansion (MR-010), static and live VaR backtesting with Kupiec,
Christoffersen and the Basel zone (MR-011), concentration (MR-012) and liquidity (MR-013)
measures with flags, and the daily risk pack in HTML, PDF and Excel (RP-001).

## Phase 4 — Counterparty risk (delivered, initial margin deferred to Phase 5)

- [x] Netting sets and CSAs in the reference data since Phase 1; counterparty hierarchy
- [x] Exposure engine (CR-001): 1,000 paths, 12 dates, full revaluation of every bilateral
      trade with ageing; EE, EPE, EEPE, PFE95/99 per netting set and counterparty
- [x] Collateral (CR-002): threshold, MTA, independent amount, rounding, haircut, 10-day
      margin period; gross and collateralised profiles; instant CSA what-if on stored paths
- [x] CVA and DVA (CR-003) from internal PD and own spread; bilateral CVA
- [x] Wrong-way risk indicator (CR-004) with the planted sovereign and corporate cases
- [x] Counterparty limits now on peak PFE95 after collateral; watchlist flag; current
      exposure under every stress scenario
- [x] Counterparty page, API, Copilot tool, `novera run counterparty`, EOD integration
- [ ] Initial margin (SIMM-lite) and CCP exposure: Phase 5

## Phase 5 — Segment modules (delivered)

- **Bank (IB)**: FRTB standardised approach with delta, vega, curvature, DRC and crypto
  (REG-001); FRTB internal models with liquidity horizons, NMRF, the backtesting
  multiplier and the P&L attribution test (REG-002); SA-CCR EAD and RWA (REG-003);
  SIMM-lite initial margin feeding the exposure engine (REG-004); BA-CVA capital
  (REG-005); funding cash ladder (REG-006); capital attributed to desks; Capital page,
  API, Copilot tool, `novera run regulatory`.
- **Hedge fund (HF)**: a second simulated organisation, Meridian Multi-Strategy Fund, with
  strategies, prime brokers, a NAV and an investor register (`novera simulate --template
  hedge_fund`, separate database, firm selector in the dashboard); exposures and leverage
  (HF-001), prime-broker margin replication (HF-002), factor betas (HF-003), redemption
  stress (HF-004), strategy attribution (HF-005), crowding (HF-006); fund limits on
  leverage, margin usage and broker concentration; Fund page, API, Copilot tool.
- Not done: IPV as a separate module (covered by the challenger), ICAAP and ILAAP
  document generation, CCP exposure.

## Phase 6 — Instrument breadth (delivered 2026-09-14)

Repos and reverse repos, interest-rate futures, European swaptions (Bachelier on a normal
vol cube), single-name CDS, commodity options (Black 76 on commodity vol surfaces), ETFs
and mutual funds priced by look-through with a direct-versus-via-fund report, equity
barrier and digital options (closed form, QuantLib-benchmarked), and market-data proxies
with an audit trail (interpolate, roll, re-level). Nine new bank books; the fund holds
the new products in its strategies. Records PR-010 to PR-016, MR-014, MD-002. Deferred:
strike concentration reporting and volatility calibration tools.

| Asset class | Product | Listed/OTC | Pricer | Record |
|---|---|---|---|---|
| Rates | Repo / reverse repo | OTC | Cash leg off the zero curve | PR-010 |
| Rates | Interest-rate future | Listed | Curve forward, no convexity | PR-011 |
| Rates | European swaption | OTC | Bachelier on a normal vol cube | PR-012 |
| Credit | Single-name CDS | OTC | Flat hazard (shared with PR-008) | PR-013 |
| Commodities | Commodity option | Listed | Black 76 on the curve price | PR-014 |
| Equity | ETF, mutual fund | Listed / transfer agent | NAV by look-through | PR-015 |
| Equity | Barrier and digital options | OTC | Reiner–Rubinstein, cash-or-nothing | PR-016 |

## Phase 7 — AI and automation (delivered 2026-09-15)

Risk Copilot with tool calling and commentary (Phase 3), plus four agents that gather
evidence deterministically and draft from it: breach investigation (note attached to
the breach with contributors, changes and an engine-sized remediation), scenario
suggestion (proposals sized by the history, run through the engine), model-validation
report drafting (from the methodology records and live evidence), ISDA/CSA document
ingestion with a review-and-approve step. Portfolio Lab: choose a template, plant a
subset of problems at a chosen size, run into a sandbox, see what was detected, from
the dashboard or `novera lab`. MCP server over stdio exposing the same tools to any MCP
client, read-only by default and audited. Records AI-002, AI-003, AI-004, LAB-001.

## Between phases — platform contract (delivered 2026-09-15)
- [x] API contract for a future React client: a response model on every route, RFC 9457
      problem documents with stable codes, one `ApiError` hierarchy shared by the HTTP and
      in-process clients, CORS setting, risk-pack download endpoint, committed OpenAPI file
      with a staleness test (decision log round 14)
- [x] Reference data page: organisation tree (by business or legal entity) and counterparty
      tree (netting sets, CSA terms) with a filter; `GET /reference/counterparties` (round 15)
- [ ] React dashboard: screen by screen, each once its API response has stopped changing
      for a full phase; morning overview and limits first

## Injected problems in the simulated portfolio

USD 10Y DV01 concentration, illiquid Brent position, BTC convexity, counterparty near
limit, wrong-way exposure, stale EUR vol surface, missing USD curve node, trades with
invalid fields, unexplained P&L on a handful of positions, an intentional VaR limit breach.
