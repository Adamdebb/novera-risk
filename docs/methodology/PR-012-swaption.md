# European swaption valuation  (ID: PR-012)

| Field | Value |
|---|---|
| Version | 1.1.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.breadth.price_swaption`, `novera.pricing.exotic_formulas.bachelier_price`, `novera.market_data.sabr` |
| Last validated | 2026-09-15 |

## Definition
Normal (Bachelier) model on the forward swap rate `S` with annuity `A = Σ τ_i DF(t_i)` over the
underlying swap's fixed schedule (30/360, semi-annual by default). With normal vol `σ_N`
(basis points per year, read at the strike from the smile below), `d = (S − K)/(σ_N √T)`:

- payer: `A [(S − K) Φ(d) + σ_N √T φ(d)]`
- receiver: `A [(K − S) Φ(−d) + σ_N √T φ(d)]`

PV = ±notional × unit value (BUY = long the option). Rate delta `A Φ(d)` (payer) and vega
per basis point `A √T φ(d) / 10⁴` are reported in `details`; the risk engine bumps and
re-prices for the stored measures (VEGA rows on the `SWVOL:` family use a 1bp bump).

### Smile
The at-the-money normal vol `σ_ATM` comes from the `SWVOL:{CCY}:{EXPIRY}:{TENOR}` cube,
bilinear in expiry and tenor, flat outside. The strike dependence is normal SABR with
`β = 0` (Hagan et al. 2002, normal-vol expansion), which handles low and negative forwards
without a shift. With `ρ` and `ν` from the `SWRHO:` and `SWNU:` cubes (same grid, same
interpolation):

- `σ_N(K) = α · z / x(z) · [1 + (2 − 3ρ²) ν² T / 24]`, `z = (ν/α)(S − K)`,
  `x(z) = ln[(√(1 − 2ρz + z²) + z − ρ) / (1 − ρ)]`
- `α = σ_ATM / [1 + (2 − 3ρ²) ν² T / 24]`, so the cube's ATM quote is reproduced exactly and
  stays the vega instrument; `ρ` sets the skew (negative: low strikes richer), `ν` the curvature.

A snapshot without `SWRHO:`/`SWNU:` factors prices every strike at `σ_ATM`, as version 1.0.0
did. `details` carries `normal_vol_bp` (the vol used), `atm_vol_bp`, `sabr_rho`, `sabr_nu` and
`sabr_alpha_bp`.

## Inputs
Zero curve of the currency (single curve: forwards and discounting off the same curve);
swaption vol cube and, when stored, the SABR rho and nu cubes; expiry, tenor, strike and
payer/receiver from the instrument.

## Assumptions and limitations
Cash and physical settlement are priced identically (no cash-settlement annuity
convention). The cubes are flat outside their grid. `β` is fixed at zero rather than
calibrated; `ρ` and `ν` are simulated, not calibrated to broker smile quotes. The
sensitivities bump the ATM cube with `ρ` and `ν` held, so smile-parameter risk appears in
full-revaluation VaR (where `SWRHO:` shocks are absolute and `SWNU:` relative) but not in the
VEGA rows or the delta-gamma-vega challenger. Single-curve forwards (no OIS/IBOR basis).

## Validation tests
`test_swaption_matches_quantlib_bachelier`: within 0.2% of `ql.BachelierSwaptionEngine` on the
same discount curve and schedule; payer minus receiver equals `A (S − K)`; bought swaptions
have positive vega. `test_bachelier_matches_quantlib`: the formula reproduces
`ql.bachelierBlackFormula` to machine precision. `test_normal_sabr_matches_quantlib`: the
smile agrees with `ql.SabrSmileSection` in normal vol within 1% across strikes from 200bp
below to 200bp above the forward, reproduces `α` from the ATM vol, is symmetric at `ρ = 0`
and skews the right way for `ρ < 0`. `test_swaption_prices_off_the_sabr_smile`: the pricer
reads the smile vol at the strike, agrees with the flat cube at the money, and prices as
Bachelier at that vol. `test_swaption_smile_factors`: simulated `ρ` and `ν` stay in bounds and
produce a downward skew.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-09-14 | Bachelier on the ATM cube | Novera |
| 1.1.0 | 2026-09-15 | Normal SABR smile in strike (decision 21.1) | Novera |
