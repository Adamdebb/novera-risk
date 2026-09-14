# CVA and DVA  (ID: CR-003)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Counterparty Risk Methodology |
| Approval status | Draft |
| Code | `novera.counterparty_risk.cva` |
| Last validated | 2026-09-14 |

## Definition
Unilateral CVA per netting set on the collateralised expected exposure:
`CVA = LGD × Σ_i EE(t_i) DF(t_i) (Q(t_{i−1}) − Q(t_i))`, with a flat hazard
`λ = −ln(1 − PD_1y)` from the counterparty's internal one-year PD (by rating in the
reference data) and LGD 60%. DVA mirrors it on expected negative exposure with the
firm's own spread (`λ_own = s / (1 − R)`, 80bp by default). Bilateral CVA = CVA − DVA.
CVA on gross exposure is also reported to show the value of collateral.

## Limitations
Independence between exposure and default (wrong-way risk is reported separately, CR-004,
not priced in). Flat hazard, no market-implied curves. Discounting on the reporting
currency zero curve.

## Validation tests
`tests/test_counterparty.py::test_cva_formula_and_hazards`.
