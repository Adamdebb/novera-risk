# Interest-rate future valuation  (ID: PR-011)

| Field | Value |
|---|---|
| Version | 1.1.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.breadth.price_interest_rate_future` |
| Last validated | 2026-10-14 |

## Definition
Three-month money-market future quoted `100 − 100 × F`, with `F` the simple forward rate over
`[T_expiry, T_expiry + 0.25]` off the contract index's projection curve (PR-002):
`F = (P(T₁)/P(T₂) − 1)/0.25`. A SOFR or SONIA future projects off the discount curve; a
EURIBOR-3M future off ESTR plus the `IRBASIS:EUR:3M:` basis (decision 33.2). The
position is daily-settled, so PV is the variation margin
`contracts × notional × 0.25/100 × (price − trade price)`; one basis point is worth
`notional × 0.25 × 0.0001` per contract.

## Inputs
Discount curve of the contract currency and, for EURIBOR-3M, the 3M tenor basis; IMM expiry,
notional and tenor from the instrument.

## Assumptions and limitations
No futures-to-forward convexity adjustment (a few basis points at long expiries), no
calendar for IMM dates beyond the third Friday. Without a 3M basis in the snapshot a EURIBOR
future falls back to the discount curve and `details.tenor_basis_applied` is 0.

## Validation tests
`test_ir_future_price_from_curve`: price reproduces the curve forward; a long future loses
when rates rise; PV equals the tick-value identity. `test_euribor_future_projects_off_the_3m_basis`:
+10bp of 3M basis lowers the price by 0.10 (within compounding), the 6M basis leaves it alone
and a SOFR future ignores both.

## Change history
| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-14 | Curve forward off the single zero curve |
| 1.1.0 | 2026-10-14 | Forward off the index's projection curve: EURIBOR-3M over ESTR (decision 33.2) |
