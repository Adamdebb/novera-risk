# Stress testing  (ID: MR-005)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.stress` |
| Last validated | 2026-09-14 |

## Definition
Instantaneous shock scenarios applied to the snapshot with full revaluation. Two kinds:
hypothetical (rule-based shocks on factor families) and historical (the observed move of
every factor across a named episode window in the history).

## Library
USD rates +100bp; global rates +100bp; USD steepener and flattener; EUR/USD −10%;
equity −20% with vol +15; credit +150bp; oil −30%; BTC −50%; vol +10 points; global
risk-off (composite); inflation shock (composite). Historical: the simulator's stylised
risk-off crash and rates shock.

## Assumptions
Shocks are simultaneous and instantaneous; no liquidity horizon; no rebalancing. Vol
shocks are absolute vol points applied to every surface node.

## Validation tests
`test_stress_library_runs_and_makes_sense`, `test_historical_episode_scenarios`.
