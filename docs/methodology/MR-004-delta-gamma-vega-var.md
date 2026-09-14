# Delta-gamma-vega VaR (challenger)  (ID: MR-004)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.var.taylor_var` |
| Last validated | 2026-09-14 |

## Definition
The same historical scenarios as MR-002, pushed through a second-order Taylor expansion
of the sensitivities table instead of full repricing. Exists to challenge the primary
measure: where the two disagree, the difference is attributable to convexity, cross
effects and surface shape that the sensitivities do not capture.

## Mathematical method
`P&L_s ≈ Σ_f δ_f · u_f,s + ½ Σ_f γ_f · u_f,s² + Σ_u ν_u · Δσ_u,s / 0.01`, where
`u_f,s = shock_f,s / bump_f` is the scenario move in bump units, γ is the reported GAMMA
(already a full ±1% second difference) and Δσ is the mean ATM vol-point change.

## Limitations
No cross-gamma; commodity curves use the mean node return; DV01 ladder ignores intra-node
shape. Expected to under-state VaR on option-heavy books.

## Validation tests
`test_taylor_var_is_close_but_not_equal`: scenario P&L correlation above 0.9 with the
primary measure and VaR within ±50%.
