# Interest-rate swap valuation  (ID: PR-002)

| Field | Value |
|---|---|
| Version | 2.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.rates.price_interest_rate_swap`, `MarketSnapshot.projection_curve` |
| Last validated | 2026-10-14 |

## Definition
Multi-curve valuation of a fixed-for-floating swap. Cash flows are discounted on the
currency's `IR:{CCY}` curve, which is the overnight-index (OIS) curve: SOFR, ESTR, SONIA.
Floating fixings are projected off the index's own curve. For an overnight index that is the
discount curve itself; for a term-rate index (EURIBOR-6M, EURIBOR-3M) it is the discount curve
plus the index's tenor basis `IRBASIS:{CCY}:{INDEX_TENOR}:{TENOR}` (decision 33.1).

## Mathematical method
Projection curve `P`: zero rates `z_P(t) = z_D(t) + b(t)`, with `z_D` the discount zero curve
and `b` the basis spread, interpolated linearly in tenor onto the discount nodes and flat beyond
the first and last basis node. Interpolation of each curve is then linear in `r × t` as for the
discount curve.

Fixed leg `N c Σ τ_i DF(t_i)`; floating leg `N Σ (f_i + s) τ_i DF(t_i)` with simple forwards
off the projection curve `f_i = (P(t_{i-1})/P(t_i) − 1)/τ_i`. Receive-fixed PV = fixed − float.
Par rate = float / annuity, the annuity discounted on `DF`.

## Inputs
`IR:{CCY}:*` discount curve; `IRBASIS:EUR:6M:*` (1Y, 2Y, 5Y, 10Y, 30Y) for EURIBOR-6M swaps.
The simulated history of the basis is described in SIM-001.

## Assumptions
No stored fixings: the current floating period is projected from the curve as of today
(documented approximation). No calendar adjustment. Discounting on the OIS curve stands for
CSA discounting under a cash-collateral CSA in the currency of the swap.

## Limitations
No CSA-specific discounting (non-cash or cross-currency collateral), no amortisation. The basis
is a zero spread, not bootstrapped from basis-swap quotes; with real data the 6M and 3M curves
should be stripped from ESTR/EURIBOR basis swaps. If a snapshot carries no basis for a term-rate
index (a real-data snapshot today), the swap is projected off the discount curve and
`details.tenor_basis_applied` is 0, so the fallback is visible. On the test swap that fallback
misses by notional × basis × annuity, 19% of PV with a 15bp basis.

## Sensitivities
DV01 bumps a discount-curve node with the projection curve riding on it, so a EURIBOR swap's
DV01 is its whole rate risk. BASIS01 bumps one basis node with ESTR held (MR-001).

## Validation tests
`test_par_swap_has_zero_pv_and_matches_quantlib`: NPV and fair rate versus `ql.VanillaSwap` on a
matching discount curve (USD SOFR, single curve by construction); par swap re-prices to zero;
payer = −receiver.
`test_eur_swap_dual_curve_matches_quantlib`: a 7-year EUR receiver against QuantLib with ESTR
discounting and an IborIndex projecting off ESTR + 15bp: NPV within 2,000 on 100m and fair rate
within 0.2bp; the single-curve fallback is flagged and misses by notional × basis × annuity.
`test_tenor_basis_moves_only_term_rate_indices`: the projection curve is discount plus the
interpolated basis; SOFR and an index without nodes project off the discount curve.

## Change history
| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-14 | Single curve per currency; the EUR gap measured at 0.53% of PV (decision 18.3) |
| 2.0.0 | 2026-10-14 | Multi-curve: OIS discounting, EURIBOR projected off ESTR plus a simulated tenor basis (decision 33) |
