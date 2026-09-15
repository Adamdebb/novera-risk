# Portfolio Lab  (ID: LAB-001)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Risk Technology |
| Approval status | Draft |
| Code | `novera.lab.run`, `novera.simulation.trades` (`BANK_PROBLEMS`, `FUND_PROBLEMS`), `novera.simulation.market_data` (`MARKET_PROBLEMS`) |
| Last validated | 2026-09-15 |

## Definition
A lab is a sandbox database `data/lab/<name>.duckdb`. The chosen template (bank or fund)
is simulated with a subset of the planted problems, each scaled by one multiplier, plus
optional market-data problems; the governed EOD runs on it unchanged (SIM-001, MR-001 to
MR-014, DQ-001). Detection is then read from the stored run with fixed rules per problem
(`PROBLEM_CATALOGUE`): limits that went to WARNING or BREACH, data-quality findings on the
planted trades or subjects, risk flags naming the underlying, liquidation horizons over ten
days, wrong-way flags when the counterparty engine ran. Limits that stayed inside are
reported with their utilisation so a miss is explained, not hidden. Spec, injections,
detections and summary are stored in the sandbox (`lab` table).

## Limitations
Limits are the production calibration; the lab does not recalibrate them to the sandbox
book, so small books under-utilise limits. Wrong-way problems need the counterparty engine,
which the lab runs with 200 paths when enabled.

## Validation tests
`test_problem_catalogue_covers_every_plantable_problem`, `test_lab_spec_validation`,
`test_lab_runs_and_detects_selected_problems` (selective planting, scaling, detection,
sandbox isolation under a temporary data directory).
