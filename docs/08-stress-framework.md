# Stress framework

The plan for rebuilding stress testing (MR-005) into a framework for a bank and a fund. It
was agreed in round 45 of the decision log on 2026-10-16. Each later round builds one part, with
its own methodology record, tests and question round.

## Why rebuild

The current module revalues the book in full under 14 rule-based scenarios and 3 stylised
episodes, and catalogues 7 named crises. Measured against current practice it has ten gaps:

1. **Scenarios live in code.** There is no versioned store, no owner, narrative or review
   date, and no split between scenarios that feed limits and scenarios that inform.
2. **Factors a rule does not name stay at zero.** "Equity −20%" leaves rates, FX and credit
   where they are.
3. **No plausibility measure.** Nothing says how extreme a scenario is next to past crises.
4. **No reverse stress test.**
5. **No structured sensitivity grids:** no curve shapes, no vol skew or term structure, no
   spot × vol ladders, no standalone shock per factor.
6. **Historical windows replay over one horizon only.** There is no worst-window search and no
   rescaling to today's volatility.
7. **Stress P&L stops at P&L.** It does not flow into margin calls, collateral, liquidation
   cost, funding, counterparty losses, CVA, capital or fund survival.
8. **Hedge-failure loss (basis) is not reported on its own.**
9. **Every scenario is an instantaneous jump.** There are no paths, no hedging or liquidation
   during the stress, no crowded unwind and no margin spiral.
10. **Limits read only the worst loss across the library.** There is no review cycle, and
    scenarios are never compared with what markets later did.

## Sources

The design draws on Golub (ed.), *BlackRock's Guide to Fixed-Income Risk Management* (2024),
chapters 3 to 8, 16 and 19; Golub and Crum on the 2007–2009 credit crisis (2009); the
RiskMetrics *Risk Management: A Practical Guide* (1999); Crouhy, Galai and Mark, *The Essentials
of Risk Management* (2005), chapter 7; Winston, *Buy Side Risk Management* (Morgan Stanley,
2006); the GARP Buy Side Risk Managers Forum principles (2015); the 2013 market-risk disclosures
of Goldman Sachs and Deutsche Bank; van Greuning and Brajovic Bratanovic, *Analyzing Banking
Risk* (World Bank, 2009); and two papers on causal networks for stress testing (Gao, Mishra
and Ramazzotti 2017; Rojas and Dias 2019).

The older sources predate the GFC, the euro crisis, the 2015 franc de-peg, COVID, the 2022
rates and gilt (LDI) crises, the 2023 bank failures and the 2024 yen-carry unwind, so the crisis
library is built from recent events, not from them. Some of their market facts no longer hold.
For example, the 1999 guide calls the stock–bond correlation positive; it was negative from
about 2000 to 2021 and turned positive again in 2022. As with `docs/references/`, no shock size
and no result comes from a source. The engine computes every figure, and every calibration is
either measured on the stored history or set by the owner and recorded as an assumption.

Ideas considered and left out:
- Causal Bayesian networks as the scenario engine: shown only on toy or quarterly credit data.
- Text-mined geopolitical attention indices: they need data the platform does not have.
- Structural macro models (DSGE, VAR).
- GPU revaluation.

## Architecture

One engine serves both firms. A bank layer and a fund layer sit on top of it.

### A. Scenario store and stress setup
- **Scenarios are stored records.** Each scenario lives in the database with an id, a version,
  family tags (a scenario can carry several), a narrative and catalyst, its policy variables,
  a severity tier, an owner, an approval status and a review date. Approval uses the role
  matrix and the sign-off workflow.
- **A stress setup**, modelled on the VaR setup (OPS-004), marks each scenario LIMIT or
  INFORMATION. Only LIMIT scenarios feed stress-loss limits.
- **Run cadence:** a core set daily; the extended library and reverse stress weekly; macro
  narratives monthly or quarterly; ad hoc runs after a market event.
- **The current library migrates unchanged.** The first round must reproduce today's stored
  stress results exactly.

### B. Scenario generation
1. **Standalone and sensitivity shocks.**
   - Every factor is shocked alone in both directions. The size comes from a chosen rule:
     the 99% 10-day move, the worst move in the stored history, or k standard deviations of a
     stressed window.
   - Losses are ranked by factor. The delta-gamma-vega approximation screens all factors and
     full revaluation confirms the worst.
   - Sliding P&L scales from −3σ to +3σ for the key factors.
   - Single-name jump to default.
   - A stress envelope of standard shocks per category.
   - The same machinery supplies the stressed scenario for non-modellable risk factors in
     REG-002.
2. **PCA shapes.**
   - Level, slope and curvature per currency curve, and a global rates component.
   - Contango and backwardation shapes for commodity curves.
   - Level, skew and term-structure shapes for vol surfaces.
   - Each shape is sized in standard deviations, so it carries a likelihood. Components are
     estimated on a stressed window as well as the recent one, and their signs are aligned
     from one date to the next.
3. **Historical.**
   - Windows replayed over 1, 5, 10, 20 and 60 days as well as the full window.
   - A search for the window that would hurt today's book most.
   - Raw and volatility-rescaled versions.
   - Factors that did not exist at the time are filled from the factors that did.
4. **Hypothetical, market-driven.**
   - The scenario sets 5 to 12 policy variables. Every other factor gets its conditional
     expectation given those shocks under a chosen covariance (recent, regime-weighted, or
     weighted toward similar past episodes). Owners may override the correlation between two
     factor blocks.
   - The engine reports the scenario z-score (Mahalanobis distance ÷ √n), its place on a
     "risk ruler" of past crises, an empirical exceedance probability, and a correlation
     z-score and z-score delta per policy variable. These flag a shock with the wrong sign or
     size.
5. **Named crises as calibrated replays.**
   - Each crisis is a table of headline moves the owner approves, expanded to all factors as
     in item 4.
   - It carries the status CALIBRATED and becomes REAL only when fetched history covers the
     window.
6. **Reverse stress.**
   - Starts from a threshold: a limit, a NAV drawdown, margin capacity or a capital ratio.
   - The engine searches PCA space for the most plausible scenario that reaches it. In that
     space plausibility is the Euclidean norm of the standardised scores. It uses the
     sensitivity approximation to search and full revaluation to confirm.
   - It returns several distinct solutions with their near neighbours, and tests that no
     reduction step smooths away the tail.
7. **Automated discovery.** The scenario agent (AI-002) proposes candidates from exposures,
   recent moves and the worst windows; the engine sizes and runs them (ADR 0003).

### C. Explaining the result
- P&L by factor family, split into delta, gamma, vega, cross terms and the full-revaluation
  residual.
- The top trades.
- Hedge-failure (basis) loss on its own line.
- A summary-of-significant-risks page: VaR and the worst stress per desk and per position.

### D. The chain from loss to survival
For each scenario, the engine computes the consequences of the loss as well as the loss:
- variation margin and initial margin (SIMM-lite and CCP margin) under the stressed market;
- collateral calls and haircut shocks;
- liquidation cost under stressed volume and bid-ask spread, with each position's shock scaled
  by √ of its liquidation horizon;
- the gap between the screen price and the liquidation value of large positions;
- concentration add-ons by issuer, sector, strike and delivery month;
- funding through the cash ladder (REG-006) under three scenarios: normal, firm-specific and
  market-wide.

Each figure is reported before and after management actions.

### E. Paths and management actions
- **Paths.** Three ways to make one: a historical episode replayed day by day; a hypothetical
  end point given a shape (gap then drift, a V, or a slow bleed) through the simulator's
  bridge; and stressed Monte Carlo paths from fat-tailed PCA scores.
- **Each day** the engine revalues the book and adds carry and theta, then processes margin
  calls, collateral, funding, redemptions on dealing dates, and stop-loss and limit triggers.
- **Management actions follow owner rules**: cut gross exposure, hedge with listed
  instruments, close option positions. Each action is bounded by participation × stressed
  volume, costs square-root market impact, and its hedge is shocked by the same scenario.
- **The liquidity spiral** (margin call → sale → price impact → more margin) iterates to a
  fixed point with a capped number of rounds.
- **Outputs**, for the frozen and the managed book:
  - drawdown and the worst day;
  - cumulative cash out;
  - the day of the first breach;
  - time to flat;
  - days the firm survives.

### F. Counterparty and interconnectedness
- **Inside the scenario:** exposure and CVA, with stressed collateral haircuts and a stressed
  margin period of risk.
- **Largest counterparty default:** the five largest single defaults and one joint default,
  each counting the cost of replacing the hedges.
- **Wrong-way defaults:** a counterparty whose credit proxy deteriorates in the scenario
  defaults first.
- **CCP stress:** the two largest clearing members default; we lose our default-fund
  contribution, pay assessments and post procyclical margin.
- **For the fund:** a prime broker fails, trapping margin and assets; a prime broker withdraws
  financing; concentration across prime brokers.
- **Contagion** is modelled as defaults of sector and region clusters, because the
  counterparties' exposures to each other are not observable.

### G. Crowding and fast unwind
- **Fast-unwind scenarios:**
  - crowded longs fall and crowded shorts squeeze, scaled by crowding score × peer selling ÷
    volume;
  - factor crashes on the HF-003 factors (momentum, value);
  - short squeezes with borrow recall.
- **Templates** after the August 2007 quant quake, the January 2021 short squeeze, the
  March 2021 Archegos unwind and the August 2024 yen-carry unwind.
- **Peer selling** shrinks our exit capacity through the crowded exit horizon (HF-006).

### H. Firm layers
- **Bank.** A resources table per legal entity, set by the owner in Admin: CET1, RWA outside
  market risk, and high-quality liquid assets.
  - Stressed CET1 ratio and stressed net liquidity, each against red/amber/green levels.
  - A global-market-shock scenario combined with the largest counterparty default.
  - Stressed FRTB capital.
  - A link to recovery indicators.
- **Fund.**
  - Loss as a share of NAV, and drawdown against stop-loss levels.
  - Margin-to-equity under stress.
  - Prime-broker terms tightened.
  - Redemptions arriving with the market stress (HF-004 tied to the scenario).
  - Days the fund survives, and the probability of hitting a drawdown barrier.
  - Strategy packs: macro, rates relative value, volatility, credit, equity long/short,
    commodities, multi-strategy pods.
  - Investor-grade scenario reports.

### I. Limits and validation
- **New scenarios start as INFORMATION.** The owner promotes a scenario to LIMIT; a limit
  recalibration draft follows (MR-017), and the demo's planted breaches stay
  unchanged until then.
- **Each scenario is checked against later market moves.** After a market event, the engine
  measures which scenario came closest to what happened.
- **Every new scenario is placed on the risk ruler.**
- **Each part has its own methodology record and tests.**

## Order of work

| Round | Part | Full demo rebuild |
|---|---|---|
| 46 | Scenario store and stress setup; current library migrated unchanged | No |
| 47 | Conditional expansion, PCA shapes, plausibility measures, calibrated named crises | No |
| 48 | Standalone factor stress, sliding scales, jump to default, multi-horizon replays, worst-window search (done 2026-10-17, MR-019) | No |
| 49 | Explain: factor families, Greeks, hedge-failure line, summary of significant risks (done 2026-10-17, MR-020) | No |
| 50 | Reverse stress in PCA space, PCA Monte Carlo (done 2026-10-17, MR-021, MR-022) | No |
| 51 | Path engine with management actions, hedging and liquidation | No |
| 52 | Margin, collateral and funding chain with the liquidity spiral | No |
| 53 | Concentration and liquidation stress | No |
| 54 | Counterparty, largest counterparty default, CCP and prime-broker stress | No |
| 55 | Crowding and fast-unwind family | No |
| 56 | Fund layer | No |
| 57 | Bank layer and resources table | Possibly (new reference data) |
| 58 | Promotion to limits and recalibration; checks against later market moves | Yes |

A full demo rebuild runs only when the simulated world changes (round 40b); every round up to 57 is checked with
targeted tests and, if needed, a single-stage re-run (OPS-002).
