# FRTB internal models approach  (ID: REG-002)

| Field | Value |
|---|---|
| Version | 1.1.0 |
| Owner | Regulatory Capital Methodology |
| Approval status | Draft |
| Code | `novera.regulatory.frtb_ima` |
| Last validated | 2026-10-15 |

## Definition
`IMES = sqrt( Σ_j ( ES(Q_j) · sqrt((LH_j − LH_{j−1}) / 10) )² )` over liquidity horizons
10, 20, 40, 60 and 120 days, where `ES(Q_j)` is the 97.5% expected shortfall (10-day by
square root of time) of the P&L from modellable factors with horizon at least `LH_j`,
computed on the historical scenarios through the delta-gamma-vega mapping (MR-004).
Capital = multiplier × IMES + SES, multiplier 1.5 plus the backtesting add-on
(MAR32 table) from the static backtest exceptions (MR-011). Non-modellable risk factors
are those the data-quality module found stale or missing; SES is the ES of their P&L.

## P&L attribution test
Per desk on the scenario series: risk-theoretical P&L (sensitivity-based) against
hypothetical P&L (full revaluation). Spearman correlation and Kolmogorov–Smirnov with the
MAR32 zones (green at ρ ≥ 0.8 and KS ≤ 0.09, red at ρ < 0.7 or KS > 0.12).

## Limitations
The stressed-period scaling (ES_full / ES_reduced) is not applied. 10-day ES uses the
square root of time on one-day moves rather than overlapping ten-day returns. The same
historical window serves as both current and stress periods.

## Desk allocation
The scenario P&L is kept by desk for each liquidity-horizon subset. A desk carries its Euler
contribution to the capital, exact through the cascade, with incremental and standalone
capital beside it (REG-008). This replaces the standalone 10-day ES scaled to the total.

## Validation tests
`tests/test_regulatory.py`, `tests/test_capital_allocation.py::test_ima_euler_is_exact_and_matches_the_derivative_of_the_cascade`.

## Change history
| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-14 | Initial model |
| 1.1.0 | 2026-10-15 | Desk allocation by Euler contributions (decision 41, REG-008); capital unchanged |
