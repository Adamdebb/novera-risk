# Government bond valuation  (ID: PR-001)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.rates.price_government_bond` |
| Last validated | 2026-09-14 |

## Definition
Dirty present value of a fixed-coupon government bond by discounting remaining coupons and
principal off the currency zero curve shifted by a static government-to-swap spread.

## Mathematical method
`P_dirty = Σ_i c τ_i DF(t_i) + 100 DF(T)`, `DF(t) = exp(-(z(t) + s_govt) t)`, coupons on a
backward-generated schedule, no calendar adjustment. Accrued = coupon × elapsed fraction of
the current period (linear in days). Trade PV = signed face × P_dirty / 100.

## Inputs
Zero curve `IR:{CCY}:*`; `GOVT_SPREAD` constant per currency; instrument terms.

## Assumptions
Government spread constant across the curve; ACT/ACT approximated as ACT/365.25; no
holiday calendars; no optionality.

## Limitations
No inflation-linked or callable bonds. Spread is not yet a market-data factor.

## Validation tests
`tests/test_pricing.py::test_bond_matches_quantlib` against `ql.FixedRateBond` on the same
discount curve (dirty price within 0.02%).
