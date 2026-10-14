# SIMM-lite initial margin  (ID: REG-004)

| Field | Value |
|---|---|
| Version | 1.4.0 |
| Owner | Counterparty Risk Methodology |
| Approval status | Draft |
| Code | `novera.regulatory.simm` |
| Last validated | 2026-10-14 |

## Definition
Initial margin per bilateral netting set with the structure of the ISDA SIMM: weighted
sensitivities per risk class (rates by tenor and currency, credit qualifying by index,
equity by bucket, FX, commodity by bucket, vega), bucket aggregation with correlations,
cross-bucket aggregation with gamma, and a simple sum over risk classes. Parameters are
approximate published-style values; this is not the licensed calibration and must not be
used for margin calls.

Rates are by currency and sub-curve: ESTR and EURIBOR-6M and -3M for EUR, at 99.3% between
sub-curves of one currency. As in FRTB SA (REG-001), a sub-curve's delta is BASIS01 and the
discount curve's is DV01 less BASIS01, because the platform's DV01 moves EURIBOR with ESTR.
The cross-currency basis is one more factor per currency (XCCY01 summed, weight 21 per bp, 20%
correlation with the curve factors: approximate, like the other parameters).

## Both directions and the threshold (1.4.0, decision 39.1)
`im_model` is the margin the counterparty would post on our exposure; `im_posted_model` ours on
theirs, the same model on the sign-flipped sensitivities (equal for linear risk, curvature differs).
Bilateral margin is exchanged only on a netting set with a CSA and only above `im_threshold` per
counterparty (50m in reporting currency, standing for the EUR 50m uncleared-margin threshold),
split across its sets pro rata: `im` received and `im_posted` posted. A CCP netting set receives
nothing and posts its full model margin, as a stand-in for the CCP's own model (CR-006). Posted
margin is segregated: it lowers the counterparty's exposure to us (ENE) and its funding is MVA
(CR-005).

## Use
The margin enters the exposure engine as collateral held from the counterparty
(exposure = max(V − VM balance − IM(t), 0)) for collateralised sets, and is reported next
to variation margin on the Capital page. CSA what-ifs are run with the same margin, so a
what-if on unchanged terms reproduces the reported profile.

## Ageing across the exposure grid (1.1.0)
The margin is computed on today's sensitivities but the exposure grid runs to the end of the
book, so a constant IM would overstate the collateral benefit at the long end and understate
far-dated EE, PFE and CVA. IM is therefore scaled at each grid date by the notional-duration
still outstanding in the netting set:

```
IM(t_i) = IM(t_0) × min( Σ_live(t_i) |N| · max(T_mat − t_i, 0) / Σ_live(t_0) |N| · (T_mat − t_0), 1 )
```

Trades without a maturity contribute their notional unscaled, and settled FX spot contributes
nothing, matching the ageing already applied to the exposure paths. This is a proxy, not a
recomputation: true IM at a future date needs sensitivities on the aged portfolio at each grid
point. Notional units are mixed across product types, so the factor is only meaningful as a
ratio within one netting set over time. `novera.counterparty_risk.exposure.im_scales`.

## Limitations
- Posted margin is assumed fully segregated (no loss on the counterparty's default).
- The threshold is per counterparty, not per consolidated group, and there is no AANA in-scope
  test: a small counterparty below the threshold exchanges nothing, as the rules would give.
- Approximate published-style parameters, not the licensed ISDA calibration.

## Validation tests
`tests/test_regulatory.py::test_regulatory_run_end_to_end` (IM positive; the same paths without
it give exposure at least as high everywhere and strictly higher somewhere; the margin never
exceeds its t0 value and decays by the end of the grid),
`::test_csa_what_if_baseline_ties_to_the_reported_profile`,
`::test_counterparty_run_warns_when_initial_margin_is_absent`,
`::test_tenor_basis_is_a_second_curve_in_girr_and_simm` (an outright EURIBOR position is
margined as before, not twice).
