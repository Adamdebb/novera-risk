# FRTB standardised approach  (ID: REG-001)

| Field | Value |
|---|---|
| Version | 1.2.0 |
| Owner | Regulatory Capital Methodology |
| Approval status | Draft |
| Code | `novera.regulatory.frtb_sa` |
| Last validated | 2026-10-14 |

## Definition
Sensitivities-based method (MAR21): delta, vega and curvature charges per risk class
(GIRR, CSR non-securitisation, equity, FX, commodity), bucket aggregation with prescribed
risk weights and correlations, the maximum over the low, medium and high correlation
scenarios, plus a default risk charge and a crypto exposure charge. Inputs are the
platform's stored sensitivities (MR-001).

## Parameters and mappings
- GIRR: vertices 0.25y to 30y with the 2019 risk weights (1.7% down to 1.1%), 7Y node
  split to 5y and 10y, tenor correlation `max(e^{−0.03|t−s|/min(t,s)}, 0.4)`, 50% across
  currencies. Each curve of a currency is its own set of risk factors (MAR21.8): ESTR and
  EURIBOR-6M and -3M for EUR. The platform bumps EURIBOR as ESTR plus basis, so the EURIBOR
  curve's sensitivity is BASIS01 and ESTR's is DV01 less BASIS01, vertex by vertex; two curves
  of one currency correlate at 99.9% times the tenor correlation (MAR21.46). An outright
  EURIBOR swap is therefore charged as one curve, not twice, and a pure basis position only on
  the 0.1% decorrelation. The cross-currency basis against USD is one more GIRR factor per
  currency, with no term structure: XCCY01 summed over tenors, risk weight 1.6% and no
  correlation with the curve factors, as read from MAR21; both numbers are to be checked against
  the text before relying on them. GIRR vega from the swaption cube.
- CSR: CDS indices mapped to IG (5%) and HY (12%) buckets, 35% within, 5% across.
- Equity: developed indices 15%, large caps 25%; correlations 75% and 25%; 15% across.
  Vega on equity surfaces with RW 55%·√(20/10); curvature from stored gamma on short
  optionality only.
- FX: 15%, liquid pairs 15%/√2, correlation 60%; vega RW capped at 100%.
- Commodity: energy 30%, natural gas 45%, precious and base metals 20%.
- DRC: index protection as JTD from CS01 (5y duration), IG 3%, HY 15%, hedge benefit ratio.
- Crypto: capital equal to exposure (BCBS group 2).

## Simplifications
Curvature approximated from gamma rather than full ±RW repricing; no residual risk
add-on (no exotics); CSR buckets by index quality only; equity buckets by developed
market only; no GIRR inflation. The desk attribution scales standalone
charges to the firm total.

## Validation tests
`tests/test_regulatory.py::test_frtb_building_blocks`, `test_frtb_sa_scales_with_sensitivities`,
`test_tenor_basis_is_a_second_curve_in_girr_and_simm`.

## Change history
| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-14 | Initial model |
| 1.1.0 | 2026-10-14 | EURIBOR curves as GIRR risk factors next to ESTR (decision 33) |
| 1.2.0 | 2026-10-14 | Cross-currency basis as a GIRR risk factor per currency (decision 35); `test_cross_currency_basis_is_its_own_girr_and_simm_factor` |
