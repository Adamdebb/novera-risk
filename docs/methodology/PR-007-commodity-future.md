# Commodity future valuation  (ID: PR-007)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.commodity.price_commodity_future` |
| Last validated | 2026-09-14 |

## Definition
Contracts × contract size × `(F(T) − K)` with `F(T)` linearly interpolated on the commodity
futures curve `CMD:{CODE}:*` by time to expiry.

## Validation tests
`test_commodity_future_off_curve`.
