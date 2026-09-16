# Model inventory and appropriateness  (ID: MV-001)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.catalogue.PRODUCT_CATALOGUE`, `model_inventory`, `render_model_inventory` |
| Last validated | 2026-09-15 |

## Definition
One row per product the platform prices, stating the model used, the model a trading desk
or a validation team would expect for that product, where the model used departs from it,
a three-level appropriateness rating, and how the implementation is checked. The inventory
answers two different questions that a benchmark alone cannot: *is the pricer implemented
correctly* (the validation column) and *is it the right model for the product* (the market
standard, simplifications and rating columns).

## Method
The inventory is reference data held in code next to the pricer catalogue, so the model
name, version and methodology record can never disagree with what the pricer writes on
valuation rows (`tests/test_api.py::test_product_catalogue_matches_pricers_and_methodology`).
The table below is rendered from that code by `scripts/export_model_inventory.py`; the
same test fails when the committed table is stale.

Rating scale:

| Rating | Meaning |
|---|---|
| Market standard | The model is what the market uses for the product; residual differences are conventions (calendars, quotation) with no material effect on value or risk. |
| Acceptable simplification | A documented departure from the standard whose error is small for the book as simulated, and which the methodology record states. |
| Known weakness | A departure that can misstate value or risk for part of the book. The gap is measured where a reference implementation exists; an upgrade is on the roadmap. |

A rating is a methodology judgment recorded here, not a validation opinion: the owner sets
it, the simplifications column says exactly where the departure is, and the Analyst and
agents may quote it but never change it (governance rule: AI does not act as model
validation).

## Inventory

<!-- inventory:start -->
| Product | Model used | Market standard | Simplifications | Rating | Validation |
|---|---|---|---|---|---|
| **Government bond** (Rates) | Discounting off the government curve · `bond_discounting` v1.0.0 · PR-001 | Discounting off a bootstrapped government curve per issuer, ACT/ACT day count, holiday calendars. | Government-to-swap spread is one constant per currency rather than a curve; ACT/ACT approximated as ACT/365.25; no holiday calendars; no inflation-linked or callable bonds. | Acceptable simplification | QuantLib benchmark: ql.FixedRateBond dirty price within 0.02% (tests/test_pricing.py::test_bond_matches_quantlib). |
| **Interest-rate swap** (Rates) | Single-curve discounting and forwarding · `swap_single_curve` v1.0.0 · PR-002 | Multi-curve: OIS discounting (SOFR, ESTR, SONIA) with forwards projected off the index's own curve; CSA discounting for bilateral trades. | One curve per currency for discounting and projection. Exact for the USD SOFR and GBP SONIA swaps in the simulated book, where the index is the OIS rate. EUR swaps reference EURIBOR-6M and ignore the ESTR/EURIBOR basis; the error is measured, not assumed (see validation). No stored fixings, no CSA discounting, no amortisation. | Known weakness | QuantLib benchmark: ql.VanillaSwap NPV within 2,000 on 100m and fair rate within 0.2bp (test_par_swap_has_zero_pv_and_matches_quantlib). Measured gap: single-curve PV of an in-the-money EUR swap against QuantLib with ESTR discounting and EURIBOR projection, bounded at 1% of PV (test_eur_swap_single_curve_gap_is_measured). |
| **Repo and reverse repo** (Rates) | Cash leg off the zero curve · `repo_cash_leg` v1.0.0 · PR-010 | Cash leg discounted off a repo curve (general collateral, with specials); collateral repriced with its haircut. | Cash leg off the currency zero curve, no GC/special spread. Collateral is not repriced inside the market-risk PV; its requirement is recorded for the counterparty and liquidity views. | Acceptable simplification | Analytic identities: zero PV at the curve-implied fair rate, DV01 sign, collateral requirement N/(1-h) (test_repo_zero_at_fair_rate_and_dv01_sign). |
| **Interest-rate future** (Rates) | Curve forward, no convexity adjustment · `ir_future_forward` v1.0.0 · PR-011 | Curve forward plus a futures-to-forward convexity adjustment (Hull-White or the exchange convention), IMM calendar. | No convexity adjustment (a few basis points at long expiries); IMM dates approximated as the third Friday without a holiday calendar. | Acceptable simplification | Analytic identities: price reproduces the curve forward, tick-value identity, sign under a rate rise (test_ir_future_price_from_curve). |
| **European swaption** (Rates) | Bachelier on a normal SABR smile cube · `swaption_bachelier` v1.1.0 · PR-012 | Bachelier (normal) on a SABR-calibrated smile cube, OIS discounting, cash-settlement annuity convention where applicable. | Normal SABR with beta fixed at zero: rho and nu per expiry and tenor, alpha implied from the at-the-money cube; single-curve forwards; cash and physical settlement priced alike; the vega rows bump the at-the-money cube with rho and nu held, so smile-parameter risk appears in full-revaluation VaR only. | Known weakness | QuantLib benchmark: ql.BachelierSwaptionEngine within 0.2% and ql.bachelierBlackFormula (test_swaption_matches_quantlib_bachelier, test_bachelier_matches_quantlib); the smile against ql.SabrSmileSection in normal vol within 1% across strikes (test_normal_sabr_matches_quantlib); the pricer reads the smile vol at the strike (test_swaption_prices_off_the_sabr_smile); payer minus receiver equals A(S-K). |
| **FX spot** (FX) | Mark to market at spot · `fx_spot_mtm` v1.0.0 · PR-003 | Mark to market at spot. | Unsettled spot is not discounted to its T+2 settlement. | Market standard | Analytic identities: spot mark, settled trade returns zero (test_fx_forward_cip_and_spot). |
| **FX forward** (FX) | Covered interest parity · `fx_forward_cip` v1.0.0 · PR-003 | Forward from spot and quoted forward points, which embed the cross-currency basis; discounted at the collateral currency curve. | Covered interest parity on the two zero curves; no cross-currency basis, so forwards sit on the interest differential alone. | Acceptable simplification | Analytic identities: CIP reproduced, sign of the forward points matches the rate differential (test_fx_forward_cip_and_spot). |
| **FX vanilla option** (FX) | Garman-Kohlhagen · `fx_option_garman_kohlhagen` v1.0.0 · PR-004 | Garman-Kohlhagen on a delta-quoted smile (at-the-money, risk reversal, butterfly) with premium-currency delta conventions. | Smile read at moneyness K/F rather than at delta; no premium-adjusted delta convention; forward from CIP without cross-currency basis. | Market standard | QuantLib benchmark: ql.blackFormula to 1e-10; put-call parity (test_fx_option_matches_quantlib_black_and_parity). |
| **Cash equity** (Equity) | Mark to market · `equity_mtm` v1.0.0 · PR-005 | Mark to market at the close. | None. | Market standard | Analytic identity: shares times spot (test_equity_cash_future_option). |
| **Equity index future** (Equity) | Cost of carry · `index_future_carry` v1.0.0 · PR-005 | Mark to the listed contract price, or cost of carry with financing and the index dividend yield. | Carry at the zero rate with no dividend yield and no futures basis; the fair value overstates the forward by the dividend yield. | Acceptable simplification | Analytic identity: (F - K) with F = S/DF(T) (test_equity_cash_future_option). |
| **Equity vanilla option** (Equity) | Black-Scholes · `equity_option_black_scholes` v1.0.0 · PR-006 | Black-Scholes on the dividend-adjusted forward with the smile at K/F; American exercise for listed single names (binomial tree or Bjerksund-Stensland). | No dividends; European exercise for listed options that are American (small for the short-dated, out-of-the-money book simulated). | Acceptable simplification | QuantLib benchmark: ql.blackFormula to 1e-10; put-call parity (test_equity_cash_future_option). |
| **ETF** (Equity) | NAV by look-through to constituents · `fund_lookthrough` v1.0.0 · PR-015 | Mark to the listed ETF price; look-through to constituents for risk and concentration. | Look-through NAV with a constant tracking spread stands in for the listed price; static basket, no rebalancing or corporate actions. | Acceptable simplification | Analytic identities: NAV from constituents, tracking spread applied, constituent factors in the dependency map (test_fund_lookthrough_pricing). |
| **Mutual fund** (Equity) | NAV by look-through to constituents · `fund_lookthrough` v1.0.0 · PR-015 | Latest published NAV; look-through to constituents for risk. | NAV recomputed continuously from a static basket although the fund deals once a day with notice; cash sleeve as a constant. | Acceptable simplification | Analytic identities: NAV from constituents plus cash sleeve (test_fund_lookthrough_pricing). |
| **Equity barrier and digital option** (Equity) | Reiner-Rubinstein barriers, cash-or-nothing digitals · `exotic_closed_form_bs` v1.0.0 · PR-016 | Local or stochastic volatility with discrete barrier monitoring, priced by PDE or Monte Carlo; digitals as tight call spreads on the smile. | Closed-form Black-Scholes with one vol per option and continuous monitoring; barrier risk near the barrier is understated; no dividends. Carries the FRTB residual risk add-on for that reason. | Known weakness | QuantLib benchmark: ql.AnalyticBarrierEngine to 1e-8 on sixteen barrier cases with and without rebate; digitals against ql.AnalyticEuropeanEngine (test_barrier_and_digital_formulas_match_quantlib). |
| **CDS index** (Credit) | Flat hazard rate (ISDA standard model, simplified) · `cds_flat_hazard` v1.0.0 · PR-008 | ISDA standard model: hazard curve bootstrapped from the quoted term structure, IMM dates, upfront and running-coupon conventions. | Flat hazard from the credit triangle; quarterly periods from the first of the month rather than IMM dates; no upfront. Suitable for risk, not settlement. | Acceptable simplification | QuantLib benchmark: ql.MidPointCdsEngine on a flat hazard within 5% (test_cds_index_matches_quantlib_within_tolerance); PV rises with spread for a protection buyer. |
| **Single-name CDS** (Credit) | Flat hazard rate (shared with the index model) · `cds_flat_hazard` v1.0.0 · PR-013 | ISDA standard model with a bootstrapped hazard curve per entity and the entity's recovery assumption. | Same flat-hazard model as the index; one par spread per entity, no term structure of hazard; no upfront. | Acceptable simplification | Shares the benchmarked index legs; identities on model id, spread pass-through and sign (test_single_name_cds_and_index_share_the_model). |
| **Commodity future** (Commodities) | Mark off the futures curve · `commodity_future_curve` v1.0.0 · PR-007 | Mark to the listed contract price. | Linear interpolation on the tenor curve between listed contracts when the expiry sits between curve points. | Market standard | Analytic identity: contracts times size times (F - K) off the curve (test_commodity_future_off_curve). |
| **Commodity option** (Commodities) | Black 76 on the curve price · `commodity_option_black76` v1.0.0 · PR-014 | Black 76 on the futures price with the contract's own smile; American exercise for options on futures. | European exercise; the surface is quoted on the equity moneyness grid rather than per contract. | Acceptable simplification | QuantLib benchmark: ql.blackFormula; put-call parity C - P = DF(F - K) (test_commodity_option_black76_and_parity). |
| **Crypto spot (BTC, ETH)** (Digital assets) | Mark to market · `crypto_mtm` v1.0.0 · PR-009 | Mark to market at the venue or composite price. | One USD price per asset; no venue basis. | Market standard | Analytic identity: units times spot (test_crypto_spot). |

19 products: 5 market standard, 11 acceptable simplification, 3 known weakness.
<!-- inventory:end -->

## Measured gaps
Where a market-standard reference exists in QuantLib, the error of the simplification is
measured by a test rather than assumed:

| Product | Gap measured | Result | Test |
|---|---|---|---|
| Interest-rate swap (EUR) | Single-curve PV versus ESTR discounting with EURIBOR-6M projection, 15bp basis, on a 7-year 100m receiver swap 90bp in the money | Dual-curve PV 4,435,683 against single-curve 4,412,333: gap 23,350, 0.53% of PV, 2.3bp of notional | `tests/test_pricing.py::test_eur_swap_single_curve_gap_is_measured` (bound: between 0.05% and 1% of PV) |

USD and GBP swaps in the simulated book reference SOFR and SONIA, where the index is the
discount rate, so the single-curve model is exact for them. The gap therefore applies to
the EUR book only.

## Inputs
The pricer catalogue (`PRODUCT_CATALOGUE`), each product's methodology record PR-001 to
PR-016, and the benchmark tests in `tests/test_pricing.py` and `tests/test_breadth.py`.

## Limitations
The market standard named for each product is the common practice of a sell-side desk in
the major currencies; a desk with a different mandate (for instance a hedge fund marking
listed instruments to screen prices) may hold a different standard. Ratings are not
weighted by exposure: a known weakness on a product with no positions has no effect on the
run, and the product hierarchy in the trade extract is the place to check that. The
inventory covers pricing models only; risk methods carry their limitations in their own
MR, CR, REG, HF and DQ records.

## Validation tests
`tests/test_api.py::test_product_catalogue_matches_pricers_and_methodology`: every product
has an inventory entry with all four columns filled, a rating from the scale above, a
methodology record that exists, and a committed table equal to the code's rendering.
`tests/test_pricing.py::test_eur_swap_single_curve_gap_is_measured`: the measured gap.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-09-15 | First inventory: 19 products, 5 market standard, 11 acceptable simplification, 3 known weakness | Market Risk Methodology |
