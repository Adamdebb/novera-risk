# The end-of-day run, step by step

`novera run eod` executes one function, `run_eod` in `src/novera/workflows/eod.py`. This
document describes what that function does, in order, what each step reads and writes, and
what the run guarantees. It is the reference for the "skeleton" of the platform; the maths
of each step lives in its methodology record (`docs/methodology/`).

## How a run starts

| Entry point | When | Notes |
|---|---|---|
| `uv run novera run eod [--business-date] [--fund]` | on demand | Business date defaults to the latest market snapshot |
| `novera schedule` / `novera schedule --once` | daily at the configured time | Advances the simulated world one business day when the latest day already has a run, then runs EOD with three attempts and a RUN_FAILED alert on the last failure (OPS-001) |
| `POST /admin/rerun`, Admin page, `novera rerun` | on demand | Re-runs one stage of a stored run into a new RERUN run (OPS-002); not a full run |

## The run record

Before any number is computed the run gets a `RunRecord` with:

- `run_id` (sortable, `run_` plus a timestamp), `run_type` EOD, the business date;
- the `portfolio_snapshot_id` and `market_snapshot_id` it priced on, and the previous
  market snapshot id for P&L explain (all content hashes);
- the model version of every engine (`MODEL_VERSIONS`), the configuration (`EODConfig`) and a
  `config_hash` of both;
- `status`, the data-quality `verdict`, a `summary` of headline numbers and `timings` per step.

Every result table is keyed by `run_id` and is never edited afterwards (rule 5). Running the
same snapshots with the same code and configuration reproduces the same numbers; the re-run
tests check this by recomputing a stage and comparing it with the stored table.

## The steps

Each step is timed and its duration stored on the run. Durations below are from the demo
bank run of 2026-09-15 (1,515 trades, 1,494 risk factors, 1,305 days of history, one worker
per core).

| # | Step (timing key) | What it does | Reads | Writes | Record | Demo |
|---|---|---|---|---|---|---|
| 1 | `load` | Picks the market snapshot on or before the business date and the portfolio snapshot on or before it; loads the organisation, the risk-factor universe, the full market history, the limits in force and the approved temporary increases; expires increases past their end date; applies market-data proxies to stale or missing factors, keeping the raw snapshot | snapshots, history, limits, increases | `md_proxies`; INCREASE_EXPIRED audit events | MD-002, MR-008 | 0.4s |
| 2 | `valuation` | Prices every trade on the proxied market in reporting currency and builds the revaluation `Portfolio` | portfolio, market | `valuation` | PR-001 to PR-016 | 0.7s |
| 3 | `dq_pre` | Data-quality findings on the raw market (missing and stale factors and the trades that depend on them), on the proxies applied, on trades (invalid, dangling references, dead) and on valuation (unpriced) | raw market, proxies, trades, valuation | `dq_findings` (at persist) | DQ-001 | 0.0s |
| 4 | `sensitivities` | Bump-and-revalue ladders: DV01 by node, CS01, deltas, vegas per surface and swaption cube | portfolio | `sensitivities` | MR-001 | 4.9s |
| 5 | `var` | Historical simulation, 99% one-day VaR and 97.5% ES over the 500-day window, full revaluation of every trade on every scenario | portfolio, history | `var_summary`, `var_contributions`, `var_scenarios`, Parquet P&L matrix | MR-002, MR-003 | 38.9s |
| 6 | `var_challenger` | Delta-gamma-vega VaR on the same scenarios from the sensitivities | sensitivities, history | `var_summary` row, `var_contributions_challenger`, Parquet | MR-004 | 0.2s |
| 7 | `monte_carlo` | Monte Carlo VaR on the sensitivity approximation from the window's covariance | sensitivities, history | `var_summary` row, `var_contributions_monte_carlo` | MR-010 | 0.8s |
| 8 | `backtest` | Static backtest of the scenario vector (250 test days against a 250-day lookback) and the live backtest across stored EOD runs | VaR vector, earlier runs | `backtest_summary`, `backtest_series`, `backtest_live_series` | MR-011 | 0.0s |
| 9 | `stress` | The stress library under full revaluation: twelve hypothetical scenarios and the two stylised episodes | portfolio, history | `stress`, `stress_summary` | MR-005 | 2.2s |
| 10 | `limits` (first pass) | Utilisation of every limit against valuation, sensitivities, VaR and stress, with approved increases as the effective amounts; limit types fed by the counterparty and fund engines are deferred to step 18 | limits, steps 2 to 9 | `limits` | MR-006 | 3.6s |
| 11 | `concentration` | Concentration by dimension, top positions and tenor; liquidity horizons and the liquidity-adjusted VaR; fund look-through | valuation, VaR contributions, sensitivities | `concentration*`, `liquidity_*`, `lookthrough_*`, `risk_flags` | MR-012 to MR-014 | 0.1s |
| 12 | `pnl` | Day-on-day P&L explain as a full-revaluation waterfall from the previous market and portfolio, with a Greeks-based challenger; a data-quality post-check flags trades whose challenger misses beyond tolerance | previous snapshots, steps 2 and 4 | `pnl_steps`, `pnl_by_trade`, `pnl_challenger` | MR-007, DQ-001 | 8.6s |
| 13 | verdict and workflow | Verdict from the findings (RED on any CRITICAL, AMBER on any MAJOR or MINOR, GREEN otherwise); DQ_FINDING, LIMIT_BREACH and LIMIT_WARNING audit events; breach synchronisation for the first-pass limits: raise, update, auto-escalate, flag back-within-limit | steps 3 and 10 | breaches, breach actions, audit events | DQ-001, MR-008 | in `limits` |
| 14 | `persist` | Writes the run record and every table above, plus the Parquet matrices under `data/runs/<run_id>/` | | all of the above | | 0.4s |
| 15 | `regulatory` (bank) | FRTB SA and IMA (with the P&L attribution inputs), SA-CCR, SIMM, BA-CVA and the cash ladder, computed from the stored valuation and sensitivities of this run | stored run | `reg_*` | REG records | 3.5s |
| 16 | `counterparty` | Monte Carlo exposure paths per netting set, CSA collateral with the margin period, EE, PFE, CVA and DVA, wrong-way flags | stored run, stress table | `cp_*`, Parquet grid | CR-001 to CR-004 | 197s |
| 17 | `fund` (fund face) | Leverage, prime-broker margin, factor exposures, redemption stress, strategy attribution, crowding | stored run | `fund_*` | HF records | |
| 18 | limits second pass | Re-monitors every limit with the counterparty PFE and fund metrics now available, replaces the `limits` table, and synchronises breaches for the deferred limit types only | steps 10, 16, 17 | `limits`, breaches, audit events | MR-006, MR-008 | |
| 19 | `alerts` | Builds RUN_SUMMARY, RUN_VERDICT, NEW_BREACH, AUTO_ESCALATION and BACK_WITHIN_LIMIT alerts from the run and its breach outcome, stores every alert, and delivers those at or above the configured severity through Slack or email when a channel is configured, de-duplicating by subject and date | run, breach outcome | `alert` rows | OPS-001 | 4.9s |
| 20 | final save | Writes the run record again with the engine summaries and every timing | | `risk_run` | | |

The demo bank run takes about four and a half minutes, of which the counterparty engine is
three. With the exposure engine off (`NOVERA_EXPOSURE_ENABLED=false`) a run is about a minute.

## What the run guarantees

- **It stores what it computed, never what a person changed.** Breach actions, limit
  increases and overrides are separate, audited events by named actors.
- **The verdict does not block.** A RED or AMBER verdict says whether the numbers can be
  trusted; the run still completes and stores everything, so the morning starts from a full
  picture and a list of problems (decision 5.x).
- **The market it priced on is reproducible.** The raw snapshot and the proxy actions are both
  stored; `load_run_market` rebuilds the priced snapshot, which is how re-runs and the
  regulatory and counterparty engines see exactly what valuation saw.
- **Runs are immutable; re-runs are new runs.** A partial re-run (OPS-002) copies the parent's
  tables under a new RERUN id and replaces one stage. It never touches breaches, alerts or
  the parent.
- **The live backtest, the scheduler and `latest` look only at EOD runs**, so re-runs and
  ad-hoc runs never change the regulatory record or the next day's starting point.

## Failure behaviour

An exception anywhere propagates out of `run_eod`. From the scheduler this means up to three
attempts with a growing pause, a job record with the traceback, and a RUN_FAILED alert. Two
consequences worth knowing:

- A failure before step 14 leaves no run record and no tables: the day is simply missing
  until the next attempt.
- The run record is marked COMPLETED at step 14, before the regulatory, counterparty and
  fund engines run. A failure in steps 15 to 19 therefore leaves a COMPLETED run whose
  summary lacks those engines' blocks and whose `reg_*`, `cp_*` or `fund_*` tables are
  absent. The dashboard shows the gap on the Capital and Counterparty pages, and the stage
  can be re-run from the Admin page. Marking such a run PARTIAL is a candidate improvement
  (decision log, second look).

## What is not in the run

Sign-off (OPS-003) happens after the run, not inside it: results are visible as soon as they persist, and
the Sign-off page tracks whether the required metrics have been signed and the run released.
No intraday or incremental run:
every run is a full pass. Named real crises are catalogued but not replayed (Stress library).
A re-run recomputes one stage and lists, but does not recompute, its dependents.

## Where to look

- Code: `workflows/eod.py` (`run_eod`, `EODConfig`, `MODEL_VERSIONS`, the `persist_*` helpers),
  `workflows/rerun.py`, `workflows/scheduler.py`, `workflows/alerts.py`, `workflows/runs.py`.
- Stored shape: `storage/duckdb_repository.py` (`save_run`, `save_run_frame`, `copy_run_frames`).
- Tests: `tests/test_workflows.py` (run record, verdict, determinism, re-runs),
  `tests/test_operations.py` (scheduler, alerts), `tests/test_api.py` (every table through the API).
