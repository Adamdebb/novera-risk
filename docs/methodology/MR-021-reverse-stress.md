# Reverse stress in PCA space  (ID: MR-021)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.pca_space`, `novera.risk.reverse_stress`, `novera.workflows.pca_stress`; EOD step `pca_stress` (re-runnable stage `reverse_stress`, OPS-002); `GET /runs/{run_id}/reverse-stress`; Stress page, "Reverse stress" |
| Last validated | 2026-10-17 |

## Definition
Forward stress asks what a scenario costs. Reverse stress asks which scenario costs a given amount and
how plausible it is. The targets are the effective stress-loss limits (MR-006): the firm's and every
desk's, with the amounts in force on the business date. For each target the engine returns up to three
different scenarios that lose exactly the limit, the most plausible first, and for each their near
neighbours, their drivers, their distance in sigmas and their probability. It is for information.

It follows the stress framework (`docs/08-stress-framework.md`, part B.6).

## Mathematical method
**Space (`pca_space`).** The overlapping 10-day moves of every factor up to the valuation date (shock
units, MR-002) are weighted by the stressed regime (MR-018), standardised by their weighted standard
deviation σ_f, and decomposed by principal components of the weighted correlation matrix. A scenario is
a score vector s, one unit-variance score per kept component:

  x_f = σ_f · Σ_c W_fc · s_c,   W = V_k · diag(√λ_k)

The Euclidean norm ‖s‖ is the scenario's plausibility, measured in standard deviations along its own
direction. The number of components k is the smallest that keeps 95% of the variance **and** reproduces
at least 90% of the summed P&L of the book's worst 1% of historical 10-day moves, growing by 5 at a
time up to 80. A reduction that smooths the tail away is therefore not accepted; the sizes tried are
stored (`pca_tail`).

**Book.** The sensitivities give P&L(x) = Σ_j lin_j y_j + Σ_j quad_j y_j², with y_j the move of
sensitivity factor j in units of its bump (delta, gamma, vega as in MR-010). It equals the challenger
VaR's approximation exactly (tested). In score space, loss(s) = −gᵀs − sᵀQs.

**Search.** For a target L:

  minimise ‖s‖²  subject to  loss(s) ≥ L,  ‖s‖ ≤ 15,  x_f ≥ −95% for every price-like factor

by sequential quadratic programming started on the linear solution. The price floors enter as cutting
planes: only the factors the solution violates are added, up to eight rounds. A target that no
scenario within these bounds reaches is reported UNREACHABLE.

**Distinct solutions.** Solution 2 must point at least 45° away from solution 1, solution 3 from both.
They are different stories, not the same story at a different size.

**Near neighbours.** Each solution is rotated by 15° towards the directions on which the loss curves
most (and the loss gradient's tangent), rescaled to the target again, and the ratio of norms is
reported. A ratio near 1.035 (= 1/cos 15°) means the book is linear there: the plausibility surface
is flat and the story is one of a family.

**Confirmation.** The search uses the sensitivities; full revaluation of the trades in scope confirms.
The solution's factor moves are revalued, then scaled along their ray (bracketing, then false position,
at most six revaluations) until the full-revaluation loss is within 2% of the target. The reported
distance is the **confirmed** scenario's: refined norm = norm × scale. A solution that does not
converge is UNCONFIRMED.

**Plausibility.** For a linear loss the half-space {loss ≥ L} is crossed with Gaussian probability
Φ(−σ) where σ is the refined norm; this is the `probability` column, over ten days. `exceedance` is the
share of historical 10-day moves whose projection on the solution's direction reached σ.
The stressed Monte Carlo (MR-022) gives the fat-tailed counterpart for the same targets.

**Drivers.** The eight sensitivity factors that contribute most of the loss in the sensitivity
approximation, and the move of each headline factor (MR-018) in the scenario.

## Inputs
Portfolio, base market, sensitivities, history up to the valuation date, stored stress episodes, the
effective limits, valuation (scope of each limit).

## Assumptions
- A scenario is a point in the standardised principal-component space; the Gaussian norm measures
  plausibility. Real tails are fatter (see MR-022).
- Moves are those of 10 days; the stressed regime sets the correlations.
- Price-like factors (spots, vols) cannot fall through −95%; rates and spreads are not floored.

## Limitations
- **Local solutions.** The search is local: a book with strong short gamma can have a better
  solution the search does not find. The angle constraint and the start on the linear solution
  mitigate this; they do not remove it.
- **UNREACHABLE is not an alarm.** It means the limit lies beyond 15 sigma or the price floors, for
  example a limit far above the desk's real exposure.
- **Scope is the limit's own scope.** The desk's loss is its trades' P&L alone; hedges in other desks
  are ignored, as in the stress-loss limit itself.
- **Neighbours are rotations.** They show fragility, not every alternative story.
- **Gaussian probability.** It understates tail risk; read it with the Monte Carlo probability.
- **Margin and capital ratio targets are not built.** They need the margin chain (round 52) and the
  bank layer (round 57).

## Validation tests
`tests/test_reverse_stress.py`:
- the quadratic book equals `taylor_pnl_matrix`;
- the space keeps 95% of the variance and the tail share, and projection is idempotent;
- the solution reaches the target exactly and no random direction reaches it on a shorter vector;
- a second solution points at least 45° from the first;
- full revaluation confirms each solution within 2% and the factor moves respect the price floors;
- a larger target is further away; an impossible target is UNREACHABLE; only stress-loss limits
  become targets.

`tests/test_workflows.py`: the EOD stores the tables and a re-run of the stage reproduces them.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-10-17 | Reverse stress on the stress-loss limits (decision 50) | Novera |
