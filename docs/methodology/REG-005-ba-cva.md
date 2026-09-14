# BA-CVA capital  (ID: REG-005)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Regulatory Capital Methodology |
| Approval status | Draft |
| Code | `novera.regulatory.cva_capital` |
| Last validated | 2026-09-14 |

## Definition
Reduced basic approach: `K = 2.33 × sqrt((ρ Σ SCVA_c)² + (1 − ρ²) Σ SCVA_c²)`, ρ = 0.5,
`SCVA_c = RW_c × M_c × EAD_c × DF_c / 1.4`, `DF = (1 − e^{−0.05M}) / (0.05M)`, risk weights
by sector (sovereign, financial, corporate) and investment-grade status, EAD from SA-CCR.

## Validation tests
`tests/test_regulatory.py::test_regulatory_run_end_to_end`.
