# van Oers (2017), reverse stress testing: a framework to identify stress scenarios

Source: https://thesis.eur.nl/pub/43410/Oers-van.pdf, Erasmus School of Economics master thesis,
13 November 2017. Only the abstract and table of contents were read, not the chapters.

## What it does
A quantitative framework on a bank portfolio: select risk factors, estimate their marginal and
joint distributions including dependence (copula), estimate how the factors drive the
occurrence of default, then run a grid search to find the set of scenarios that exhaust the
capital buffer. Extensions examined scenario reduction and clustering to cut computation, and
mixed-frequency data (MIDAS). The thesis reports that reduction did not yet work efficiently
and that, with MIDAS, extreme scenarios were smoothed out so none were identified.

## Ideas worth taking
- **A set of scenarios, not one.** The output is a family of breaching scenarios usable as early
  warning signs or as inputs to a conventional stress test.
- **Dependence modelled explicitly** rather than assumed normal.
- **Scenario clustering** to present a handful of distinct stories instead of thousands of
  breaching points.
- **Cautionary result.** Choices made to limit the scenario count can smooth away the extremes
  that matter. Any reduction step in Novera needs a test that the tail survives.

## Where it would land in Novera
Reverse stress mode in `novera.risk.stress` (set output and clustering), and the dependence
model alongside `novera.risk.monte_carlo`. The thesis's default link is credit; the market-risk
version would use a limit or loss threshold instead.

## What we do not take
- **Its default model and bank-capital framing.**
- **MIDAS and scenario reduction** as designed there, given its own negative findings.
- **Any figure or calibration.** To be read chapter by chapter before design work.
