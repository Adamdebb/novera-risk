# SIMM-lite initial margin  (ID: REG-004)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Counterparty Risk Methodology |
| Approval status | Draft |
| Code | `novera.regulatory.simm` |
| Last validated | 2026-09-14 |

## Definition
Initial margin per bilateral netting set with the structure of the ISDA SIMM: weighted
sensitivities per risk class (rates by tenor and currency, credit qualifying by index,
equity by bucket, FX, commodity by bucket, vega), bucket aggregation with correlations,
cross-bucket aggregation with gamma, and a simple sum over risk classes. Parameters are
approximate published-style values; this is not the licensed calibration and must not be
used for margin calls.

## Use
The margin enters the exposure engine as collateral held from the counterparty
(exposure = max(V − VM balance − IM, 0)) for collateralised sets, and is reported next
to variation margin on the Capital page.

## Validation tests
`tests/test_regulatory.py` (IM positive, reduces collateralised exposure).
