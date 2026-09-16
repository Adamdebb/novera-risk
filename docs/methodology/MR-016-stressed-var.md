# Stressed VaR  (ID: MR-016)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.var.scenario_shocks`, `VaRConfig.window_start` and `window_end`; produced through `novera.risk.var_measures`; `LimitType.STRESSED_VAR` |
| Last validated | 2026-09-16 |

## Definition
Value at Risk of today's portfolio over a fixed historical window chosen for its stress,
rather than the rolling window ending at the valuation date. The window does not move as
days pass, so the figure changes only with the book. Basel 2.5 introduced it as an add-on
to the market-risk capital; the bank template of the VaR setup produces it at 99% and
feeds it to stressed-VaR limits.

## Mathematical method
The scenario matrix is built exactly as in MR-002 from the daily factor moves inside the
window: every observation from the start date to the end date (inclusive) yields
`n − horizon` scenarios, each applied to today's snapshot and fully revalued (or pushed
through the sensitivities). VaR, ES, the VaR scenario date and the contributions follow
MR-002 and MR-003. A fixed window and decaying weights (MR-015) can be combined, in which
case the newest date of the window carries the largest weight.

## Inputs
The stored market history over the window; the confidence level and the dates from the VaR
setup (OPS-004). The dates must lie inside the stored history; the setup refuses them
otherwise.

## Assumptions
The chosen window is representative of the stress the firm wants to hold capital or limits
against. Today's risk-factor universe must be covered by the history over that window;
factors absent from the history are absent from the scenarios.

## Calibration
The bank template proposes the most volatile 250-day window of the equity index in the
stored history (`most_volatile_year`), which on the synthetic history is the year around
the stylised risk-off crash. With real history a firm would pick a named crisis, for example
2007-01-01 to 2009-12-31 as in the owner's matrix, once `novera fetch` covers those years.

## Limitations
No named real crisis is replayable until the stored history reaches back to it; the stress
library (MR-005) catalogues the named crises and their status. The Monte Carlo variant on a
fixed window draws from that window's covariance and inherits MR-010's Gaussian tails. The
backtest (MR-011) does not test stressed VaR: a fixed window is not a forecast.

## Validation tests
`tests/test_risk.py::test_weighted_tail_measures_and_fixed_window`: the fixed-window
scenarios are exactly the equal-weighted matrix's rows between the two dates; a window with
no observations is refused. `test_var_setup_drives_the_run_and_limits`: a LIMIT stressed-VaR
row is produced daily, recorded in the run and read by the limits.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-09-16 | Fixed-window VaR as a measure of the VaR setup (decision 25.3) | Novera |
