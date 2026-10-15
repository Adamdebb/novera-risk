# wraquant, `wraquant.risk.stress`

Source: https://wraquant.readthedocs.io/en/stable/_modules/wraquant/risk/stress.html (v1.2.0
docs page; the repository and its licence were not inspected).

## What it does
A returns-based toolkit: additive and volatility-scaling shocks, spot shocks, a single-factor
sensitivity ladder, historical replay of built-in crisis windows, a Monte Carlo reverse stress
test, a joint shock (vol, mean, correlation), correlation blending toward 1, a liquidation cost
estimate, and a library of seven named scenarios. It works on return series and weights, not on
a revalued trade-level portfolio.

## Ideas worth taking
- **Correlation breakdown as its own scenario.** Blend the correlation matrix toward 1 by a
  stated fraction and report the change in portfolio risk. Novera's composites move levels only.
- **Joint shock with a dependence leg.** Vol, level and correlation moved together, not one at a time.
- **Marginal stress contribution.** Split a scenario loss by position. Novera attributes VaR but
  could do the same for a scenario.
- **Liquidity cost from days to liquidate.** Square-root volume impact, with days to liquidate
  at a fraction of average daily volume. Addresses the "no liquidity horizon" assumption in MR-005.
- **Reverse stress test question:** what share of simulated paths breaches a target loss, and
  what is the worst among them.

## Where it would land in Novera
`novera.risk.stress` (correlation and joint scenarios), `novera.risk.liquidity` (liquidation
cost), `novera.risk.monte_carlo` (path search for the reverse test). Each needs its own MR record.

## What we do not take
- **The reverse test as written.** It assumes normal returns, which understates fat tails; its
  own docstring says so.
- **`scenario_library`.** In the docs page, only the equity shock feeds the stressed return;
  its credit, rate and correlation parameters are stored but unused. A named library whose
  parameters do nothing is the failure to avoid. Novera's scenarios revalue the full snapshot.
- **`marginal_stress_contribution` percentages** are unstable when the total loss is near zero.
- **Its crisis windows and shock sizes.** Novera calibrates from stored history (MD-001).
