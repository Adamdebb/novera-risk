# CDS index valuation  (ID: PR-008)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.credit.price_cds_index` |
| Last validated | 2026-09-14 |

## Definition
Flat hazard rate from the credit triangle `λ = s / (1 − R)`. Protection leg
`(1 − R) Σ DF(t_mid,i) (Q(t_{i−1}) − Q(t_i))`; premium leg `c Σ τ_i (DF(t_i) Q(t_i) + ½ DF(t_mid,i) ΔQ_i)`
including accrual on default. Buyer of protection PV = protection − premium.

## Limitations
No ISDA standard-model bootstrapping, no upfront conventions, quarterly periods start on
the first of the current month rather than IMM dates. Suitable for risk, not for settlement.

## Validation tests
`test_cds_index_matches_quantlib_within_tolerance`: within 5% of `ql.MidPointCdsEngine`
with a flat hazard rate; PV increases with spread for a protection buyer.
