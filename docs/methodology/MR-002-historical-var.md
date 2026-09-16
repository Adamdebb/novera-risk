# Historical-simulation VaR, full revaluation  (ID: MR-002)

| Field | Value |
|---|---|
| Version | 1.1.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.var.historical_var` |
| Last validated | 2026-09-16 |

## Definition
One-day 99% Value at Risk from an equally weighted window of 500 historical daily factor
moves applied to today's snapshot, with every trade fully repriced under every scenario.
The confidence level and the window are those of the measure in the firm's VaR setup
(OPS-004); the defaults above are what the platform produces until a setup is saved. The
same code with decaying weights is MR-015 and over a fixed window MR-016.

## Mathematical method
For scenario `s`: ABSOLUTE factors (zero rates, credit spreads, swaption SABR rho) shift by
their observed one-day difference, RELATIVE factors scale by their observed one-day return. P&L_s = Σ_trades (PV_s − PV_0) in
reporting currency. VaR = −Q_{1%}(P&L) with linear interpolation between order statistics.
Ten-day VaR is reported by square-root-of-time scaling.

## Contributions
Component VaR allocates the total using the mean trade P&L across the three scenarios
around the VaR quantile, rescaled to sum exactly to VaR. Standalone VaR per group is the
quantile of the group's own P&L.

## Assumptions
Stationarity over two years; equal weights; no ageing of trades within the horizon.

## Limitations
Synthetic history in the demo. Interpolated quantile is not the Basel "5th worst" but is
within one observation of it. No absolute-versus-relative choice per factor beyond the
universe definition.

## Validation tests
`test_historical_var_properties`: shape, positivity, ES ≥ VaR, exact additivity of
contributions, sub-additivity of standalone group VaR. `test_var_measures_share_matrices_and_validate`:
the default setup reproduces this record's figures.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-09-14 | Historical simulation, full revaluation, 99% over 500 days | Novera |
| 1.1.0 | 2026-09-16 | Confidence and window from the VaR setup; shared scenario builder with MR-015 and MR-016 | Novera |
