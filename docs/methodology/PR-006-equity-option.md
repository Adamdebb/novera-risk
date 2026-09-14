# Equity vanilla option valuation  (ID: PR-006)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.equity.price_equity_option` |
| Last validated | 2026-09-14 |

## Definition
Black–Scholes on the forward `F = S / DF(T)`, European exercise, vol from the underlying's
surface at `K/F`. PV = signed contracts × multiplier × unit price.

## Limitations
No dividends, no American early exercise (listed single-name options are American in
reality; the difference is small for the short-dated, out-of-the-money book simulated).

## Validation tests
`test_equity_cash_future_option`: equals `ql.blackFormula` to 1e-10.
