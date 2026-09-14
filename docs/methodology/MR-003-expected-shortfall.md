# Expected shortfall  (ID: MR-003)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.var.tail_measures` |
| Last validated | 2026-09-14 |

## Definition
97.5% one-day expected shortfall on the same scenario set as MR-002: the mean loss over
the scenarios at or beyond the 2.5% P&L quantile. ES contributions are each trade's mean
P&L across those tail scenarios, which sums exactly to the total.

## Validation tests
Covered by `test_historical_var_properties`.
