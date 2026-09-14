# FRTB standardised approach  (ID: REG-001)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Regulatory Capital Methodology |
| Approval status | Draft |
| Code | `novera.regulatory.frtb_sa` |
| Last validated | 2026-09-14 |

## Definition
Sensitivities-based method (MAR21): delta, vega and curvature charges per risk class
(GIRR, CSR non-securitisation, equity, FX, commodity), bucket aggregation with prescribed
risk weights and correlations, the maximum over the low, medium and high correlation
scenarios, plus a default risk charge and a crypto exposure charge. Inputs are the
platform's stored sensitivities (MR-001).

## Parameters and mappings
- GIRR: vertices 0.25y to 30y with the 2019 risk weights (1.7% down to 1.1%), 7Y node
  split to 5y and 10y, tenor correlation `max(e^{−0.03|t−s|/min(t,s)}, 0.4)`, 50% across
  currencies. No GIRR vega (no swaptions yet).
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
market only; no GIRR inflation or basis risk. The desk attribution scales standalone
charges to the firm total.

## Validation tests
`tests/test_regulatory.py::test_frtb_building_blocks`, `test_frtb_sa_scales_with_sensitivities`.
