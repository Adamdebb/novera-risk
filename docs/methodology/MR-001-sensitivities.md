# Sensitivities by bump-and-reprice  (ID: MR-001)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.sensitivities.compute_sensitivities` |
| Last validated | 2026-09-14 |

## Definition
First-order (and selected second-order) P&L sensitivities to each risk factor, computed by
shifting the factor in the market snapshot and repricing only the trades that depend on it.
All values are P&L in reporting currency for the stated move.

| Measure | Move | Scope |
|---|---|---|
| DV01 | +1bp on one zero-curve node | ladder per currency; parallel DV01 = sum of nodes |
| CS01 | +1bp on a CDS index spread | per index |
| FX_DELTA | +1% on an FX spot factor | per pair as quoted; includes translation of foreign PVs |
| EQ_DELTA | +1% on an equity or index spot | per name |
| CMD_DELTA | +1% on every node of a commodity curve | per commodity |
| CRYPTO_DELTA | +1% on a crypto spot | per symbol |
| VEGA | +1 vol point on every node of a surface | per underlying |
| GAMMA | PV(+1%) + PV(−1%) − 2·PV on a spot factor | options only |
| THETA | one calendar day forward, market unchanged | per trade |

## Assumptions
One-sided bumps for deltas (the bump is small relative to curvature except for options,
where GAMMA is reported separately). Dependency mapping is by product; a trade that
depends on a factor not in its map would show zero sensitivity — the tests guard this.

## Limitations
No cross-gamma. No vega by expiry bucket yet (surface-level only). Bumps are not
re-calibrated (a vol bump does not re-fit the smile; the swaption VEGA row bumps the ATM cube
with the SABR rho and nu held).

## Validation tests
`tests/test_risk.py::test_sensitivity_signs_and_structure`; stress-versus-DV01 linearity
check in `test_stress_library_runs_and_makes_sense`.
