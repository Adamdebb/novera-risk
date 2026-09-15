# Single-name CDS valuation  (ID: PR-013)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.breadth.price_cds_single_name` (legs from `novera.pricing.credit.cds_legs`) |
| Last validated | 2026-09-14 |

## Definition
Same flat-hazard model as the index (PR-008): `λ = s/(1 − R)` from the entity's par spread
`CDS:{ENTITY}` (basis points), protection and premium legs with accrual on default, buyer of
protection PV = protection − premium. Jump-to-default `(1 − R) × notional` is reported in
`details` for the default-risk views.

## Inputs
Zero curve of the contract currency; entity spread; coupon (100bp investment grade,
500bp high yield), recovery and maturity from the instrument.

## Assumptions and limitations
As PR-008: no term structure of hazard, no ISDA standard model, quarterly periods from the
first of the month. Single names share the index driver in the simulated market with an
idiosyncratic component so index hedges leave basis.

## Validation tests
`test_single_name_cds_and_index_share_the_model`: model id and spread pass-through; a
protection buyer gains when the spread widens; PV positive at a coupon below the spread.
