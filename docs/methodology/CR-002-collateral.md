# Collateral under the CSA  (ID: CR-002)

| Field | Value |
|---|---|
| Version | 1.1.0 |
| Owner | Counterparty Risk Methodology |
| Approval status | Draft |
| Code | `novera.counterparty_risk.exposure.collateral_balance` |
| Last validated | 2026-09-26 |

## Definition
Net collateral balance held from the counterparty along each path:

```
V_lag(t)  = V(t) − w · (V(t) − V(t_prev)),  w = min(MPoR / (t − t_prev), 1)
they(t)   = max(V_lag − threshold_they_post, 0), zero if below the MTA, rounded down
we(t)     = max(−V_lag − threshold_we_post, 0), same rules
balance   = they · (1 − haircut) + independent_amount − we · (1 − haircut)
exposure  = max(V − balance, 0);   negative exposure = max(balance − V, 0)
```

The margin period of risk (10 days) is applied by lagging the value linearly between grid
points, a documented approximation of the close-out delay. The independent amount is
the counterparty's posting to us. Uncollateralised netting sets use gross exposure.

## Consequence worth knowing
Collateral we have posted is at risk if the value turns in our favour before it is
returned, so collateralised exposure can exceed gross exposure on some paths. The
platform reports both.

## What-if
Stored path values allow any netting set to be re-collateralised under alternative
terms instantly (`csa_what_if`), without rerunning the simulation. The what-if is run with the
run's own initial margin (REG-004), so unchanged terms reproduce the reported profile.

## Initial margin
For collateralised sets the exposure is reduced by the initial margin as well as the variation
margin balance: `exposure = max(V − balance − IM(t), 0)`, with IM from REG-004 and aged across
the grid there. Uncollateralised sets carry neither. When the regulatory engine has not run on
the same run there is no IM, and the counterparty run says so in its warnings rather than
reporting a VM-only profile silently.

## Validation tests
`tests/test_counterparty.py::test_collateral_balance_rules`.
