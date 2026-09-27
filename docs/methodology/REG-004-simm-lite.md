# SIMM-lite initial margin  (ID: REG-004)

| Field | Value |
|---|---|
| Version | 1.1.0 |
| Owner | Counterparty Risk Methodology |
| Approval status | Draft |
| Code | `novera.regulatory.simm` |
| Last validated | 2026-09-26 |

## Definition
Initial margin per bilateral netting set with the structure of the ISDA SIMM: weighted
sensitivities per risk class (rates by tenor and currency, credit qualifying by index,
equity by bucket, FX, commodity by bucket, vega), bucket aggregation with correlations,
cross-bucket aggregation with gamma, and a simple sum over risk classes. Parameters are
approximate published-style values; this is not the licensed calibration and must not be
used for margin calls.

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
- One margin amount per netting set, treated as received from the counterparty. Margin **we**
  post is not modelled, so it neither reduces their exposure to us nor feeds DVA, and its gap
  risk on their default is not captured.
- No in-scope test: IM is applied to every netting set with a CSA. Real IM requires the AANA
  test and is only exchanged above a group-level threshold, so coverage here is wider than the
  rules would give.
- Approximate published-style parameters, not the licensed ISDA calibration.

## Validation tests
`tests/test_regulatory.py::test_regulatory_run_end_to_end` (IM positive; the same paths without
it give exposure at least as high everywhere and strictly higher somewhere; the margin never
exceeds its t0 value and decays by the end of the grid),
`::test_csa_what_if_baseline_ties_to_the_reported_profile`,
`::test_counterparty_run_warns_when_initial_margin_is_absent`.
