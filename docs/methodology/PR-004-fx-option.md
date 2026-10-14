# FX vanilla option valuation  (ID: PR-004)

| Field | Value |
|---|---|
| Version | 2.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.fx.price_fx_option` |
| Last validated | 2026-10-14 |

## Definition
Garman–Kohlhagen expressed as Black on the market forward of PR-003 (USD-collateral curves,
so the cross-currency basis of both currencies is in it), discounted on the trade's collateral
curve for the quote currency (decision 35). Volatility read from the pair's surface at
moneyness `K/F` and time to expiry.

## Validation tests
`test_fx_option_matches_quantlib_black_and_parity`: unit price equals `ql.blackFormula` to
1e-10; put–call parity holds; short position is the negative of long.

## Change history
| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-14 | Black on the CIP forward |
| 2.0.0 | 2026-10-14 | Basis-consistent forward, discounting on the trade's CSA (decision 35) |
