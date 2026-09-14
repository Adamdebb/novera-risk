# FX vanilla option valuation  (ID: PR-004)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.fx.price_fx_option` |
| Last validated | 2026-09-14 |

## Definition
Garman–Kohlhagen expressed as Black on the CIP forward, discounted at the quote-currency
curve. Volatility read from the pair's surface at moneyness `K/F` and time to expiry.

## Validation tests
`test_fx_option_matches_quantlib_black_and_parity`: unit price equals `ql.blackFormula` to
1e-10; put–call parity holds; short position is the negative of long.
