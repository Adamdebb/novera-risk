# Stressed Monte Carlo on fat-tailed PCA scores  (ID: MR-022)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.stress_monte_carlo` on `novera.risk.pca_space`; EOD step `pca_stress` (re-runnable stage `stress_monte_carlo`); `GET /runs/{run_id}/stress-monte-carlo`; Stress page, "Stressed Monte Carlo" |
| Last validated | 2026-10-17 |

## Definition
Ten-day paths of the whole market drawn in the PCA space of the stressed regime, valued on the book's
sensitivities, for the firm and every desk. Two versions run on the same random numbers: Gaussian
scores, and Student-t scores. The gap between them is the cost of the fat tail. For information:
no limit reads it.

## Mathematical method
The PCA space, its component count and its tail check are those of MR-021.

**Scores.** z ~ N(0, I_k). Gaussian: s = z. Fat-tailed: s = z · √((ν − 2) / χ²_ν) with one χ²_ν per
path and ν = 4. This is a multivariate Student-t with unit variance in every component, so the
covariance is the stressed history's, and the extremes of all components arrive together (a common
scale per path); independent fat-tailed scores would give a portfolio sum closer to normal.

**Paths.** x = σ ⊙ (W s); price-like factors are floored at −95%; the sensitivity factors' moves y
give P&L = Σ lin_j y_j + Σ quad_j y_j² per scope (MR-010 delta-gamma-vega, aggregated).

**Outputs** per scope and distribution, as losses (positive) except mean and worst, which are P&L:
VaR 99%, expected shortfall 97.5%, VaR 99.9%, and `tail_ratio` = VaR 99.9% of the version over the
Gaussian version of the same scope. The firm's loss quantiles (50% to 99.99%) for both versions.
For every stress-loss limit: the share of paths that lose at least the limit, per version, and the
return period of the fat-tailed version in years (ten trading days per path).

Defaults: 10,000 paths, ν = 4, seed 50, horizon 10 days (`PATHS`, `DEGREES_OF_FREEDOM`, `SEED`).

## Inputs
As MR-021, plus the desk of each trade for the scopes.

## Assumptions
- Stressed correlations; dependence between components comes only from the common scale.
- Degrees of freedom of 4 are set, not fitted; the owner can change them.
- The path P&L is the sensitivity approximation, not full revaluation.

## Limitations
- **Truncated space.** Components beyond k are dropped; MR-021's tail check is the guard, and the
  idiosyncratic residual is not simulated, so single-name tails are understated.
- **Quadratic P&L.** Exotics, barriers and smile are only as good as the sensitivities.
- **Sampling.** 10,000 paths leave the 99.99% quantile with about one path beyond it; read it as
  indicative.
- **Return periods** treat each path as an independent ten-day window.

## Validation tests
`tests/test_reverse_stress.py`: fat-tailed scores have unit variance and heavier tails; the run is
reproducible; the fat tail's VaR 99.9% exceeds the Gaussian one; quantiles increase; the share of
paths losing a limit is higher under the fat tail. `tests/test_workflows.py`: stored and re-run.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-10-17 | Gaussian and fat-tailed stressed paths on PCA scores (decision 50) | Novera |
