# Weighted historical VaR  (ID: MR-015)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.var.scenario_weights`, `tail_measures`, `VaRConfig.decay`; produced through `novera.risk.var_measures` |
| Last validated | 2026-09-16 |

## Definition
Historical-simulation VaR and expected shortfall (MR-002, MR-003) in which the scenarios
are not equally weighted: the newest daily move carries the largest weight and each older
day carries a factor lambda less. The measure reacts within days to a change in volatility,
which is why hedge funds and prime-broker margin desks use it, typically at 95% with lambda
0.94 over one year. It fits the Boudoukh, Richardson and Whitelaw weighted historical
simulation (1998), not the RiskMetrics parametric EWMA: the P&L of every scenario is still
computed by full revaluation (or the delta-gamma-vega expansion), only the tail is weighted.

## Mathematical method
Scenario `s` has age `a_s` (0 for the newest scenario date, 1 for the one before, and so
on). Its weight is

    w_s = lambda^a_s / sum_t lambda^a_t

so the weights sum to one and the effective sample is about `1 / (1 − lambda)` days (17 for
lambda 0.94). Sort the scenario P&Ls ascending and accumulate the weights `W_i`; VaR at
confidence `c` is minus the P&L at cumulative weight `1 − c`, interpolated linearly on the
grid `(W_i − w_i) / (1 − w_n)`, which for equal weights is exactly the linear
order-statistic interpolation of MR-002 (so a decay of None and uniform weights give the
same number). ES is the weighted mean loss of the
scenarios at or beyond the P&L at cumulative weight `1 − c_ES`. The VaR scenario date is the
scenario at which the cumulative weight first reaches `1 − c`.

Contributions: VaR from the three scenarios around that rank, rescaled to the total, as in
MR-002; ES contributions are the weighted mean of each trade's tail P&L, which sums exactly
to the ES. Standalone VaR of a group and the VaR read by a limit (`tail_measures` on the
group's P&L) recompute the weights from the scenario dates, so every figure of the run
uses the same weighting.

## Inputs
The scenario matrix of MR-002 over the measure's window; the decay lambda and confidence
from the VaR setup (OPS-004).

## Assumptions
The newest moves are the best predictor of tomorrow's distribution. Weights depend on the
order of scenario dates only, not on calendar gaps.

## Calibration
The hedge-fund template uses 95%, one year and lambda 0.94. A lambda of 0.97 halves the
reactivity; 0.99 approaches equal weights over a year.

## Limitations
The effective sample is small, so the VaR is noisy day to day and a single large move
raises it sharply and then fades. It forgets stress days quickly, which makes it a poor
basis for a 99% regulatory backtest (MR-011 runs on equally weighted windows of the
headline's own scenario vector). Monte Carlo (MR-010) does not support decay: its
covariance is equally weighted.

## Validation tests
`tests/test_risk.py::test_weighted_tail_measures_and_fixed_window`: weights sum to one and
decay geometrically from the newest date; the weighted result shares the P&L matrix of the
equal-weighted one; uniform weights reproduce the unweighted quantile within the
interpolation convention; all the weight on the worst scenario returns that scenario's loss;
contributions add up. `test_var_setup_drives_the_run_and_limits`: a run on the hedge-fund
style setup stores the weights and feeds the firm VaR limit with the weighted figure.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-09-16 | Weighted historical simulation as a measure of the VaR setup (decision 25.2) | Novera |
