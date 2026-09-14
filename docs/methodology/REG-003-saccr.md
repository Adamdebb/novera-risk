# SA-CCR  (ID: REG-003)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Regulatory Capital Methodology |
| Approval status | Draft |
| Code | `novera.regulatory.saccr` |
| Last validated | 2026-09-14 |

## Definition
Per bilateral netting set: `EAD = 1.4 × (RC + multiplier × AddOn)`. Replacement cost
`max(V − C, TH + MTA − NICA, 0)` for margined sets, `max(V − C, 0)` otherwise, with C
today from the CSA rules on the current value. Add-ons per asset class from supervisory
factors on adjusted notionals (supervisory duration for rates and credit, base-currency
notional for FX), supervisory deltas (±1 for linear trades, the Black delta for options)
and maturity factors (margined 1.5√(MPoR/250), unmargined √min(M,1)). Hedging sets:
currency with three maturity buckets and the 70%/30% correlations (rates), currency pair
(FX), index with 80% correlation (credit). Multiplier `min(1, 0.05 + 0.95 e^{(V−C)/(1.9·AddOn)})`.
Counterparty RWA from standardised risk weights by rating (banks and dealers on the bank
table); capital 8%.

## Limitations
Equity and commodity OTC derivatives are not in the simulated bilateral book (listed
products are exchange-cleared), so those asset classes carry no add-on yet. Effective
maturity for BA-CVA is a proxy (1y margined, 2.5y unmargined).

## Validation tests
`tests/test_regulatory.py::test_regulatory_run_end_to_end`.
