# Expected shortfall  (ID: MR-003)

| Field | Value |
|---|---|
| Version | 1.1.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.var.tail_measures` |
| Last validated | 2026-09-16 |

## Definition
97.5% one-day expected shortfall on the same scenario set as MR-002: the mean loss over
the scenarios at or beyond the 2.5% P&L quantile. ES contributions are each trade's mean
P&L across those tail scenarios, which sums exactly to the total.

When the scenarios carry decaying weights (MR-015) the tail is cut at the weighted quantile
and ES is the weighted mean loss of the tail scenarios; contributions are the weighted mean
of each trade's tail P&L, which again sums exactly to the total. An ES row of the VaR setup
(OPS-004) sets its own confidence (97.5% for the bank defaults, 95% in the hedge-fund
template) and feeds expected-shortfall limits when its goal is LIMIT.

## Validation tests
Covered by `test_historical_var_properties` and `test_weighted_tail_measures_and_fixed_window`.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-09-14 | ES 97.5% on the VaR scenario set | Novera |
| 1.1.0 | 2026-09-16 | Weighted tail; confidence from the VaR setup | Novera |
