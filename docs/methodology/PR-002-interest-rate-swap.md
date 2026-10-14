# Interest-rate swap valuation  (ID: PR-002)

| Field | Value |
|---|---|
| Version | 2.1.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.rates.price_interest_rate_swap`, `MarketSnapshot.projection_curve` |
| Last validated | 2026-10-14 |

## Definition
Multi-curve valuation of a fixed-for-floating swap. Cash flows are discounted on the
currency's `IR:{CCY}` curve, which is the overnight-index (OIS) curve: SOFR, ESTR, SONIA, TONA,
F-TIIE, CDI, ZARONIA. Floating fixings are projected off the index's own curve. For an overnight
index that is the discount curve itself; for a term-rate index (EURIBOR, TIIE-28D, JIBAR-3M) it
is the discount curve
plus the index's tenor basis `IRBASIS:{CCY}:{INDEX_TENOR}:{TENOR}` (decisions 33.1, 34.1).

| Index | Discount curve (`IR:`) | Projection | Fixed leg | Floating leg |
|---|---|---|---|---|
| USD-SOFR | SOFR | discount curve | annual ACT/360 | annual ACT/360 |
| EUR-EURIBOR-6M | ESTR | ESTR + `IRBASIS:EUR:6M` | annual 30/360 | semi-annual ACT/360 |
| GBP-SONIA | SONIA | discount curve | annual ACT/365 | annual ACT/365 |
| JPY-TONA | TONA | discount curve | annual ACT/365 | annual ACT/365 |
| MXN-TIIE-28D | F-TIIE | F-TIIE + `IRBASIS:MXN:28D` | every 28 days ACT/360 | every 28 days ACT/360 |
| BRL-CDI | CDI | discount curve | zero-coupon BUS/252, compounded | zero-coupon, CDI compounded |
| ZAR-JIBAR-3M | ZARONIA | ZARONIA + `IRBASIS:ZAR:3M` | quarterly ACT/365 | quarterly ACT/365 |

Conventions are set per index by the generator (`reference_levels.SWAP_CONVENTIONS`, decision
34.2); a TIIE swap runs a whole number of 28-day periods, 13 a year.

## Mathematical method
Projection curve `P`: zero rates `z_P(t) = z_D(t) + b(t)`, with `z_D` the discount zero curve
and `b` the basis spread, interpolated linearly in tenor onto the discount nodes and flat beyond
the first and last basis node. Interpolation of each curve is then linear in `r × t` as for the
discount curve.

Fixed leg `N c Σ τ_i DF(t_i)`; floating leg `N Σ (f_i + s) τ_i DF(t_i)` with simple forwards
off the projection curve `f_i = (P(t_{i-1})/P(t_i) − 1)/τ_i`. Receive-fixed PV = fixed − float.
Par rate = float / annuity, the annuity discounted on `DF`.

Zero-coupon (CDI): one period; fixed leg `N((1 + K)^τ − 1) DF(T)` with `τ` in BUS/252, CDI leg
`N(P(t_s)/P(T) − 1) DF(T)`; par rate `K = (1 + F / (N DF(T)))^{1/τ} − 1` for floating PV `F`.

Running period (decision 34.4): the swap carries the fixing of the period running on its
portfolio's business date (`InterestRateSwap.fixing`, set from the market history by
`simulation/fixings.py`). A term index pays its stored fixing `F` for the whole period, coupon
`N F τ_i`, with no projection. An overnight index compounds the stored realised rate `ρ` to the
fixing date `d_f` and projects the rest: `N(e^{ρ t(t_s, d_f)} · e^{r_0 t(d_f, today)} · P(0)/P(t_i) − 1)`,
the middle factor only when pricing after `d_f` (exposure paths). A period with no stored
fixing falls back to accruing the elapsed part at today's shortest projection rate `r_0`.

## Inputs
`IR:{CCY}:*` discount curve; `IRBASIS:EUR:6M:*`, `IRBASIS:MXN:28D:*` and `IRBASIS:ZAR:3M:*`
(1Y, 2Y, 5Y, 10Y, 30Y) for the term-rate swaps.
The simulated history of the basis is described in SIM-001.

## Assumptions
Fixings are read off the simulated history: the 1M node of each day's curve stands for the
overnight fixing and the projection curve on the period start for the term fixing. No holiday calendars: no date adjustment, and BUS/252 counts
weekdays rather than Brazilian business days. Discounting on the OIS curve stands for
CSA discounting under a cash-collateral CSA in the currency of the swap.

## Limitations
No CSA-specific discounting: every bilateral CSA in the simulated bank posts USD cash, so a
non-USD swap under it should discount on its curve adjusted by the cross-currency basis; that is
the next upgrade. No amortisation. The basis
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
`test_mxn_tiie_swap_is_dual_curve_on_28_day_periods`: a 5-year TIIE swap (65 periods of 28
days) against QuantLib with F-TIIE discounting and TIIE projection, NPV within 2,000 and fair
rate within 0.2bp. `test_running_period_accrues_at_the_short_rate`: a seasoned SOFR swap against
QuantLib given the fixing the fallback implies. `test_stored_fixings_remove_false_short_rate_risk`:
a stored fixing leaves only the unfixed days sensitive to the short end and a term coupon equal to
its fixing. `test_generator_attaches_the_running_fixing`. `test_brl_cdi_zero_coupon_swap`: a DI pre at par
is worth nothing and a seasoned one equals its closed form. `test_generated_swaps_follow_market_conventions`.

## Change history
| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-14 | Single curve per currency; the EUR gap measured at 0.53% of PV (decision 18.3) |
| 2.0.0 | 2026-10-14 | Multi-curve: OIS discounting, EURIBOR projected off ESTR plus a simulated tenor basis (decision 33) |
| 2.1.0 | 2026-10-14 | TIIE and JIBAR basis, market conventions per index, zero-coupon CDI swaps, fixings of the running period stored on the trade (decision 34) |
