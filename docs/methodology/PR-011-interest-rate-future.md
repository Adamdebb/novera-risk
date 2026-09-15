# Interest-rate future valuation  (ID: PR-011)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.breadth.price_interest_rate_future` |
| Last validated | 2026-09-14 |

## Definition
Three-month money-market future quoted `100 − 100 × F`, with `F` the simple forward rate over
`[T_expiry, T_expiry + 0.25]` from the zero curve: `F = (DF(T₁)/DF(T₂) − 1)/0.25`. The
position is daily-settled, so PV is the variation margin
`contracts × notional × 0.25/100 × (price − trade price)`; one basis point is worth
`notional × 0.25 × 0.0001` per contract.

## Inputs
Zero curve of the contract currency; IMM expiry, notional and tenor from the instrument.

## Assumptions and limitations
No futures-to-forward convexity adjustment (a few basis points at long expiries), no
calendar for IMM dates beyond the third Friday, and the underlying index is taken to be the
curve's floating index.

## Validation tests
`test_ir_future_price_from_curve`: price reproduces the curve forward; a long future loses
when rates rise; PV equals the tick-value identity.
