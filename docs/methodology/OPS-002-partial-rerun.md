# Partial re-run of an EOD stage  (ID: OPS-002)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Control |
| Approval status | Draft |
| Code | `novera.workflows.rerun`, `DuckDBRepository.copy_run_frames`, `GET /admin/rerun/options`, `POST /admin/rerun`, `novera rerun` |
| Last validated | 2026-09-15 |

## Definition
An administrator re-runs one stage of the end-of-day workflow on a stored run, for example
after a market-data correction, a pricer fix or a limit change, without re-running the whole
day and without editing the stored run. The result is a new run of type RERUN.

## Method
1. The parent must be a COMPLETED run. The new run carries the parent's business date,
   portfolio and market snapshot ids, reporting currency and configuration, plus a `rerun`
   block (parent run id, stage, actor, reason), so its config hash differs from the parent's.
   Model versions are those of the code that runs.
2. Every stored result table of the parent (`run_*`) and its Parquet matrices are copied under
   the new run id (`copy_run_frames`). The parent's rows are never touched (rule 5).
3. The stage is recomputed from the parent's snapshots: the priced market is rebuilt with the
   proxies the parent applied (`load_run_market`). Upstream results the stage needs are
   recomputed in memory; because the engine is deterministic they reproduce the stored
   numbers, which the tests check by comparing the re-run's stress table with the parent's.
4. Only the stage's tables are replaced on the new run. Stages downstream of it keep the
   parent's copies and are listed under `stale_stages`.
5. The run summary is the parent's with the stage's keys refreshed; `changed` records every
   summary key whose value moved, before and after. Audit events RERUN_STARTED and
   RERUN_FINISHED name the actor and reason.

## Stages and what they replace
| Stage | Replaces | Not recomputed |
|---|---|---|
| valuation | valuation | everything downstream |
| sensitivities | sensitivities | var, limits, concentration, regulatory, fund |
| var | var_summary, contributions (three methods), var_scenarios, Parquet matrices | backtest, limits, concentration, regulatory, fund |
| backtest | backtest_summary, backtest_series, backtest_live_series | none |
| stress | stress, stress_summary | limits, counterparty |
| limits | limits | none |
| concentration | concentration, liquidity and look-through tables, risk_flags | none |
| pnl | pnl_steps, pnl_by_trade, pnl_challenger | none |
| regulatory (bank) | reg_* | counterparty |
| counterparty | cp_* | limits |
| fund (fund face) | fund_* | limits |

## What a re-run never does
It never raises, escalates or closes a breach (the limits stage monitors and stores the
table only), never dispatches alerts, never becomes the latest EOD run (`latest_run` and the
scheduler look at EOD runs only), and never counts in the live backtest. Data quality is not
recomputed: the verdict is the parent's.

## Assumptions and limitations
Limit increases are those approved at the time of the re-run, not at the time of the parent
run. A stage's dependents are not recomputed automatically; re-run them one at a time, or run
a full EOD, when a change is meant to flow through. No sign-off step yet.

## Validation tests
`test_partial_rerun_of_a_stage`: new RERUN run, parent untouched, breaches untouched, every
table copied, the stress and limits stages reproduce the parent's numbers exactly, audit
events, unknown stage and wrong face rejected. `test_admin_rerun_endpoints`: the route, the
422 for an unknown stage, the run listing and the options history. The page renders in
`tests/test_ui.py`.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-09-15 | Partial re-run of a single stage, Admin page (decision 23.1) | Novera |
