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
No multi-curve, no CSA discounting, no amortisation. The single curve is exact for the USD
SOFR and GBP SONIA swaps in the simulated book, where the index is the discount rate. EUR
swaps reference EURIBOR-6M and ignore the ESTR/EURIBOR basis: measured at 0.53% of PV
(2.3bp of notional) on a 7-year receiver swap 90bp in the money with a 15bp basis. Rated
*known weakness* in MV-001; dual-curve EUR is the planned upgrade.

## Validation tests
`test_par_swap_has_zero_pv_and_matches_quantlib`: NPV and fair rate versus
`ql.VanillaSwap` on a matching discount curve; par swap re-prices to zero; payer = −receiver.
`test_eur_swap_single_curve_gap_is_measured`: the same EUR trade in QuantLib with ESTR
discounting and EURIBOR projection; the single-curve error is bounded between 0.05% and 1%
of PV, so the gap is measured rather than assumed.
