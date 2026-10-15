# torchquantlib, stress testing module

Source: https://torchquant.readthedocs.io/en/latest/_sources/torchquantlib.core.risk.market_risk.stress_testing.rst.txt
(docs page only; the repository and its licence were not inspected).

## What it does
One function, `perform_stress_test`: a holdings tensor and named scenarios giving a fractional
shock per asset in, a dollar impact per scenario out. The page documents no historical or
Monte Carlo method and does not explain the calculation beyond applying the shocks.

## Ideas worth taking
Almost none. The one point is the guidance that scenarios should be "realistic yet severe",
which Novera already records as an assumption in MR-005 (sizes are assumptions in the range
regulators and desks use).

## Where it would land in Novera
Nowhere. Novera's hypothetical scenarios already apply shocks by factor family with full
revaluation, which is stronger than shocking holdings by a flat percentage.

## What we do not take
- **Flat percentage shocks on holdings.** That ignores convexity, curve shape and basis, all of
  which Novera reprices.
- **Anything else from this module.** Kept in the notes only so the review is on record.
