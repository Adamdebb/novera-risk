# Interest-rate swap valuation  (ID: PR-002)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.rates.price_interest_rate_swap` |
| Last validated | 2026-09-14 |

## Definition
Single-curve valuation of a fixed-for-floating swap. Discounting and forward projection use
the same zero curve per currency.

## Mathematical method
Fixed leg `N c Σ τ_i DF(t_i)`; floating leg `N Σ (f_i + s) τ_i DF(t_i)` with simple forwards
`f_i = (DF(t_{i-1})/DF(t_i) − 1)/τ_i`. Receive-fixed PV = fixed − float. Par rate = float / annuity.

## Assumptions
No stored fixings: the current floating period is projected from the curve as of today
(documented approximation). Single curve, no OIS/IBOR basis. No calendar adjustment.

## Limitations
No multi-curve, no CSA discounting, no amortisation.

## Validation tests
`test_par_swap_has_zero_pv_and_matches_quantlib`: NPV and fair rate versus
`ql.VanillaSwap` on a matching discount curve; par swap re-prices to zero; payer = −receiver.
