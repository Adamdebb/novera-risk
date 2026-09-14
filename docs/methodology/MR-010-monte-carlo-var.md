# Monte Carlo VaR (delta-gamma-vega)  (ID: MR-010)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.monte_carlo` |
| Last validated | 2026-09-14 |

## Definition
99% one-day VaR and 97.5% ES from 10,000 simulated one-day factor moves, valued through
the delta-gamma-vega expansion of the run's sensitivities (MR-004). Reported as a third
method next to historical full revaluation and its challenger.

## Method
Daily factor moves over the VaR window (MR-002 shock units) are centred; simulated moves
are `Xᶜᵀ z / √(n−1)` with `z ~ N(0, I_n)`, which reproduces the sample covariance exactly
without decomposing a 1,190 × 1,190 matrix. Seeded, so reruns reproduce. P&L per path per
trade from `taylor_pnl_matrix`; VaR, ES and contributions as in MR-002 and MR-003.

## Limitations
Gaussian tails: the simulated distribution has no fat tails, so Monte Carlo VaR sits below
historical VaR when the window contains a crisis (expected and visible in the demo).
Inherits the approximation limits of MR-004 on option books.

## Validation tests
`tests/test_measures.py::test_monte_carlo_reproduces_sample_covariance_and_is_seeded`.
