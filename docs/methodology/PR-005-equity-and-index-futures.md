# Cash equity and index future valuation  (ID: PR-005)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.equity.price_cash_equity`, `price_equity_index_future` |
| Last validated | 2026-09-14 |

## Definition
Cash equity: signed shares × spot. Index future (daily settled): contracts × multiplier ×
`(F − K)` with `F = S / DF(T)` (cost of carry at the zero rate, no dividend yield).

## Limitations
No dividend yields yet (planned as a market-data factor). Futures basis not modelled.

## Validation tests
`test_equity_cash_future_option`.
