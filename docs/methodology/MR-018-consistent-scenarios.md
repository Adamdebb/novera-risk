# Consistent scenarios and plausibility  (ID: MR-018)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.scenario_covariance`, `novera.risk.stress_definitions.Expander`, `novera.risk.crisis_tables`; `GET /admin/stress/plausibility`, `POST /admin/stress/upgrade`, `POST /admin/stress/crises`; `novera stress-setup plausibility | upgrade | crises` |
| Last validated | 2026-10-16 |

## Definition
A **consistent** stress scenario moves every risk factor, not only the ones its rules name. The
rules keep their exact shocks. Each rule nominates one representative factor, its **anchor**.
Every factor no rule names takes its expected move given the anchors' moves, under the
correlations of a chosen market regime. A scenario's **plausibility** says how extreme its
anchor moves are against those correlations, and where it sits among past stress episodes.

The method follows the market-driven scenarios of Golub, Greenberg and Ratcliffe
(*BlackRock's Guide to Fixed-Income Risk Management*, 2024, ch. 5), re-implemented from the
description (`docs/08-stress-framework.md`). No figure is taken from the source.

## Mathematical method
**Moves.** For a horizon of h business days (20 by default), the moves are every overlapping
h-day change in the stored history up to the valuation date, in shock units: absolute changes
for zero rates, basis, spreads and SABR rho, simple returns for prices and vols (MR-002).

**Regime weights** w_t over those moves:
- STRESSED (default): 1 on moves ending inside a stored stress episode, or on a day whose 20-day
  realised S&P 500 volatility is in the top fifth; 0 elsewhere. If fewer than 30 such days
  exist, every day counts.
- SIMILAR: (max(0, cos θ_t))⁴. θ_t is the angle between day t's anchor moves and the scenario's
  anchor shocks, each standardised by the anchor's volatility. If fewer than 30 effective days
  remain, it falls back to STRESSED.
- RECENT: 0.5^(age/250).

**Conditional expectation.** With Σ the weighted covariance of the moves, a the anchors and r
their shocks, every other factor o gets

    x_o = Σ_oa (Σ_aa + εI)⁻¹ r,   ε = 10⁻⁶ · trace(Σ_aa) / k

Only the k × k anchor block is inverted, so the full 1,584-factor covariance never needs to be,
even with fewer days than factors. Each filled move is capped at 1.5 times the factor's largest
historical h-day move. A relative shock is floored at −95%.

**Anchors.** Each rule's anchor is chosen in this order:
1. a headline factor the rule shocks (S&P 500, Euro Stoxx, USD 10Y and 2Y, EUR 10Y, EUR/USD,
   USD/JPY, CDX IG and HY, S&P 3M at-the-money vol, Brent, WTI, gold, Bitcoin, Ether);
2. otherwise the 10Y, 2Y, 5Y, 1Y or 1M node, the leading currency first;
3. otherwise a 3M at-the-money vol;
4. otherwise the most volatile factor the rule shocks.

A rule that names one factor with a zero move pins that factor. A calibrated crisis uses this
for "the 10Y did not move".

**PCA shapes.** A rule `{pca: prefix, component: k, sigmas: s}` adds s · √λ_k · v_k to the nodes
under the prefix. λ_k and v_k are the k-th eigenvalue and eigenvector of the nodes' weighted
h-day covariance: stressed weights, or recent ones when the scenario asks for RECENT. Nodes are
ordered by tenor, then strike. Signs are fixed so that a positive size does the following:

| Component | Name | A positive size |
|---|---|---|
| 1 | level | raises the sum of the nodes |
| 2 | slope | raises the long end against the short end |
| 3 | curvature | raises the belly against the wings |

**Plausibility** on the k anchors, under the scenario's regime:

| Measure | Formula | Meaning |
|---|---|---|
| Mahalanobis distance | MD = √(rᵀ Σ_aa⁻¹ r) | Distance of the anchor shocks from the regime's centre |
| Scenario z-score | Z = MD / √k | Severity, comparable across scenarios with different k |
| Volatility z-score | V = √(Σ z_i² / k), with z_i = r_i / σ_i | Severity if correlations were zero |
| Correlation z-score | C = Z / V | Above 1: the signs and relative sizes fight the correlations |
| Exceedance | share of historical h-day moves with a higher z-score | How often history was at least this extreme |
| z-delta of anchor i | Z − Z(r with r_i set to its expectation given the other anchors) | Which anchor makes the scenario implausible |

**Risk ruler.** The z-score of a scenario's shocks on the 14 headline factors, under stressed
correlations. Factors the scenario leaves alone count as zero moves. The stored episodes and
every approved scenario are placed on the same ruler.

## Inputs
- The stored history up to the valuation date and the stored episode windows (SIM-001).
- The universe's shock types.
- The definition's rules, `expand`, `covariance` and `horizon_days` (OPS-005).

## Built-ins and calibrated crises
**Built-ins (decision 47.1).** The eight market shocks and composites are consistent from
version 2:

| Consistent from version 2 | Standalone by design |
|---|---|
| EUR/USD −10%, equity crash with vol, credit +150bp, oil −30%, BTC −50%, vol +10, global risk-off, inflation shock | USD and global +100bp, steepener, flattener, EURIBOR basis, USD funding squeeze |

A library stored before round 47 receives the new versions as drafts (`novera stress-setup
upgrade`).

**Calibrated crises (decision 47.3).** The 14 named crises (2008 to 2025) each give approximate
moves of 5 to 14 headline factors over the crisis window (`crisis_tables.py`). Every other factor
is filled under stressed correlations, which also stands in for factors that did not trade then.
- They enter only as INFORMATION drafts from Risk Control, marked approximate.
- The approver verifies the figures before approving (four eyes, OPS-005).
- An approved crisis shows as CALIBRATED in the stress library, and as REAL once fetched history
  covers its window.

## Assumptions
- **Stressed correlations.** Correlations in a stress move the way they moved on the stored
  stress days. On the synthetic history those days come from the simulator's episodes and its
  volatility clustering, not from a real crisis.
- **Linear fills.** The conditional expectation is linear in the anchors: a scenario twice as
  large fills twice as much, until the cap binds.
- **Fixed covariance.** The exceedance probability scores historical moves against today's
  regime covariance, not against the covariance each historical day would have had.

## Limitations
- **Large anchors give large fills,** within the cap. "Credit +150bp" applies the shock to every
  CDS including investment grade; once consistent it implies the S&P 500 down by roughly a third
  on the demo history. Its z-score is the warning, not a cap.
- **No dedicated proxies.** Factors absent from the history before a date are filled by the same
  conditional expectation; there is no proxy model.
- **Ruler horizons differ.** Ruler entries mix horizons (an episode's full window against 20-day
  moves).
- **No hard rule (decision 47.4).** Plausibility informs the approver; nothing blocks or warns.
- **Approximate crisis figures.** They are rounded from published market moves and must be
  verified.

## Validation tests
`tests/test_stress_consistency.py`:
- rules kept exactly, fills linear and capped, standalone scenarios untouched;
- z-score proportional to size, correlation z-score above 1 for credit tightening in an equity
  crash, the z-delta naming the inconsistent anchor;
- the SIMILAR fallback;
- PCA order and signs;
- validity of every built-in and crisis table;
- crisis drafts and built-in upgrades through four eyes, and the plausibility view.

`tests/test_stress_setup.py`: a stage re-run reproduces consistent scenarios exactly, and
seeding does not change the results.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-10-16 | Conditional expansion, PCA shapes, plausibility and risk ruler, calibrated named crises (decision 47) | Novera |
