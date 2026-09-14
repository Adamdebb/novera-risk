# Exposure simulation  (ID: CR-001)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Counterparty Risk Methodology |
| Approval status | Draft |
| Code | `novera.counterparty_risk.simulation`, `novera.counterparty_risk.exposure` |
| Last validated | 2026-09-14 |

## Definition
Monte Carlo profiles of the value of each bilateral netting set at twelve future dates
(1W, 2W, 1M, 3M, 6M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y, 15Y), 1,000 paths, full revaluation of
every trade on every path and date. Cleared and listed trades are excluded (variation
margin daily at the CCP or exchange).

## Factor model
Increments between grid dates are drawn from the sample covariance of daily factor moves
over the VaR window (500 days), scaled by the number of business days in the step:
Brownian motion with zero drift. Rates and credit spreads move additively (floored at
−1%); prices, levels and vols move in log space (vols floored at 3%). Draws are seeded.

## Ageing
Trades are repriced with the grid date as valuation date, so matured swaps and bonds,
settled forwards and expired options drop out of the netting set naturally. FX spot is
treated as settled after its settlement date.

## Outputs per netting set and date
Gross exposure `max(V, 0)` and collateralised exposure (CR-002): expected exposure (EE),
PFE at 95% and 99%, expected negative exposure (ENE) for DVA, mean value and mean
collateral. Per counterparty: EPE and effective EPE over the first year (time-weighted),
peak PFE and its date. PFE is summed across netting sets (conservative, no
diversification between agreements).

## Limitations
No stochastic vol or jumps; zero drift ignores forward curves; no CCP exposure; 1,000
paths give PFE99 with visible sampling error on small sets.

## Validation tests
`tests/test_counterparty.py`: seeding, floors, profile ordering, uncollateralised sets
equal gross, stored paths reload for what-ifs.
