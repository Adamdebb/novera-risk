# European swaption valuation  (ID: PR-012)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.breadth.price_swaption`, `novera.pricing.exotic_formulas.bachelier_price` |
| Last validated | 2026-09-14 |

## Definition
Normal (Bachelier) model on the forward swap rate `S` with annuity `A = Σ τ_i DF(t_i)` over the
underlying swap's fixed schedule (30/360, semi-annual by default). With normal vol `σ_N`
(basis points per year, from the `SWVOL:{CCY}:{EXPIRY}:{TENOR}` cube, bilinear in expiry and
tenor), `d = (S − K)/(σ_N √T)`:

- payer: `A [(S − K) Φ(d) + σ_N √T φ(d)]`
- receiver: `A [(K − S) Φ(−d) + σ_N √T φ(d)]`

PV = ±notional × unit value (BUY = long the option). Rate delta `A Φ(d)` (payer) and vega
per basis point `A √T φ(d) / 10⁴` are reported in `details`; the risk engine bumps and
re-prices for the stored measures (VEGA rows on the `SWVOL:` family use a 1bp bump).

## Inputs
Zero curve of the currency (single curve: forwards and discounting off the same curve);
swaption vol cube; expiry, tenor, strike and payer/receiver from the instrument.

## Assumptions and limitations
Cash and physical settlement are priced identically (no cash-settlement annuity
convention). The cube is flat outside its grid. No smile in strike: the cube is quoted
at the money and used for every strike. Single-curve forwards (no OIS/IBOR basis).

## Validation tests
`test_swaption_matches_quantlib_bachelier`: within 0.2% of `ql.BachelierSwaptionEngine` on the
same discount curve and schedule; payer minus receiver equals `A (S − K)`; bought swaptions
have positive vega. `test_bachelier_matches_quantlib`: the formula reproduces
`ql.bachelierBlackFormula` to machine precision.
