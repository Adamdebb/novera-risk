# Funding valuation adjustment  (ID: CR-005)

| Field            | Value |
|------------------|-------|
| Version          | 1.1.0 |
| Owner            | Counterparty Risk Methodology |
| Approval status  | Draft |
| Code             | `novera.counterparty_risk.cva.fva_from_profile`, `netting_set_xva`, `cva_table` |
| Last validated   | 2026-10-14 |

## Definition
The cost of funding the positive exposure a counterparty does not collateralise (funding cost
adjustment, FCA) and the benefit of holding the negative exposure it leaves with us (funding
benefit adjustment, FBA), per netting set. Trades are priced clean on OIS curves (decision
35.4); the funding of what collateral does not cover is this adjustment, computed on the same
exposure profiles as CVA and DVA (CR-003). Headline XVA per netting set = CVA − DVA + FCA.

## Mathematical method
`FCA = s_f Σ_i EE(t_i) DF(t_i) Q_c(t_i) Q_o(t_i) Δt_i`, `FBA` the same on `ENE(t_i)`, `FVA = FCA −
FBA`. `EE` and `ENE` are the collateralised expected positive and negative exposure from the
exposure engine (CR-001, CR-002: variation margin with its margin period, thresholds and
initial margin), `DF` the reporting-currency OIS discount factor, `Q_c` and `Q_o` the survival
of the counterparty and of the firm (flat hazards as in CR-003), so funding stops at either
default, and `Δt_i` the grid spacing. XVA = CVA − DVA + FCA.

MVA (1.1.0, decision 39.2): `MVA = s_f Σ_i IM_posted(t_i) DF(t_i) Q_c(t_i) Q_o(t_i) Δt_i` funds the
initial margin we post (REG-004), aged with the portfolio. FCA then funds `ee_funding`, the
exposure without that margin (at a CCP the posted margin is part of the credit exposure,
CR-006). Headline XVA = CVA − DVA + FCA + MVA.

## Inputs
Exposure profiles of the run (stored with it); counterparty one-year PD from the reference data;
the firm's own spread `own_credit_spread_bp` and LGD as for DVA; the funding spread
`funding_spread_bp` (setting, 80bp by default).

## Assumptions
Every netting set is funded on its collateralised profile (decision 37.1): under a CSA with zero
thresholds only the margin-period gap is funded, an uncollateralised set funds all of it. One
flat funding spread for every currency and tenor (37.2). FBA and DVA both monetise the firm's own
credit, so FBA is reported but left out of XVA to avoid counting that benefit twice (37.3).

## Calibration
The funding spread is a setting, not a market factor: FVA moves with exposure, not with funding
markets, and has no sensitivity or VaR of its own.

## Limitations
No term structure or currency basis in the funding spread; no ColVA (collateral remuneration differing from OIS); FVA is not hedged or limited and
does not enter market-risk VaR. The FCA/FBA split follows one accounting choice among several.

## Validation tests
`tests/test_counterparty.py::test_fva_formula_and_xva`: FCA and FBA reproduce the closed form on a
flat profile, scale with the spread and vanish at zero spread; XVA = CVA − DVA + FCA; collateral
lowers FCA. `tests/test_api.py`: the CSA what-if returns XVA on both sets of terms, and making a
netting set uncollateralised raises its FCA.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-10-14 | FCA and FBA on the collateralised profiles, headline XVA = CVA − DVA + FCA (decision 37) | Novera |
| 1.1.0 | 2026-10-14 | MVA on the initial margin we post; XVA = CVA − DVA + FCA + MVA (decision 39.2) | Novera |
