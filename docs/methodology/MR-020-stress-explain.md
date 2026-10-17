# Stress explain and summary of significant risks  (ID: MR-020)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.stress_explain`; EOD step `stress_explain` (re-runnable, OPS-002); `GET /runs/{run_id}/stress-explain`; Stress page, "Explain the stress result" |
| Last validated | 2026-10-17 |

## Definition
Takes every stress scenario's loss apart so that a risk manager can say where it comes from.
Nothing here changes a stress figure or feeds a limit; it explains the figures MR-005 and MR-018
produce.

1. **P&L by factor family**, split into delta, gamma, vega, higher-order and cross-family terms.
2. **The top trades** of each scenario.
3. **The hedge-failure line**: the basis family on its own.
4. **A summary of significant risks**: VaR beside the worst stress, per desk and per position.

It follows the stress framework (`docs/08-stress-framework.md`, part C).

## Mathematical method
**Families.** A factor belongs to the family of its identifier prefix: RATES (IR), BASIS (IRBASIS,
XCCY), CREDIT (CDS), FX, EQUITY (EQ, EQIDX), COMMODITY (CMD), CRYPTO, VOLATILITY (VOL) and
RATES_VOL (SWVOL, SWRHO, SWNU).

For a scenario with shocks x and a trade i:

- A_{i,F} is the trade's P&L when only the shocks of family F are applied, in full revaluation.
- T_{i,F}^{delta}, T_{i,F}^{gamma}, T_{i,F}^{vega} are the family's Taylor terms from the stored
  sensitivities, computed as the challenger VaR does (MR-001, MR-010):
  delta·x/bump, ½·gamma·(x/bump)², vega·Δvol/bump. Theta is not part of a scenario.
- higher order = A_{i,F} − (T^{delta} + T^{gamma} + T^{vega}).
- cross-family = S_i − Σ_F A_{i,F}, where S_i is the trade's P&L under all the shocks together.

The columns then add up exactly: Σ_F (delta + gamma + vega + higher order) + cross-family = S_i,
for each trade, so for each desk and the firm. The test checks this against the stored stress P&L.

**Reading the terms.** Higher order holds what the sensitivities do not carry: the SABR smile,
convexity beyond the 1% gamma bump, exotic payoffs and the second-order cross terms inside one
family. Cross-family is the interaction between families, for example a rate move on an option
whose spot also moves.

**Top trades.** The ten trades with the lowest P&L in each scenario, with desk, asset class and
product.

**Hedge failure.** The BASIS family's A: tenor basis (EURIBOR, TIIE, JIBAR over their overnight
curves) and cross-currency basis. A hedge whose legs differ only by a basis leaves exactly this
residual. It is reported for every scenario, whatever the sign; the run summary names the worst
(`hedge_failure`, `hedge_failure_scenario`).

**Summary of significant risks.** From the headline VaR measure's component VaR and the LIMIT
stress scenarios:

| Row | VaR | Stress |
|---|---|---|
| FIRM | headline VaR (a loss, positive) | the firm's worst LIMIT scenario (P&L, negative) |
| DESK | component VaR (the desks add up to the firm) | the desk's own worst scenario, and its P&L in the firm's worst scenario (these add up to the firm) |
| POSITION | the trade's component VaR | the trade's own worst scenario, and its P&L in the firm's worst scenario |

Significance (decision 49.3): a desk is flagged at 10% or more of the firm's VaR or of its worst
stress loss; a position is kept when it carries at least 2% of either, the 30 largest by worst
stress. Constants `DESK_SHARE`, `POSITION_SHARE` and `MAX_POSITIONS`.

## Inputs
Portfolio and base market, sensitivities, every stress result with its shocks (MR-005, MR-018),
the headline VaR result, the valuation table.

## Assumptions
- A factor's family is fixed by its prefix; a new prefix lands in OTHER until mapped.
- The Taylor terms use the same bumps as the sensitivities (1bp, 1%, one vol point).
- The explain covers every scenario of the run, LIMIT and INFORMATION; the summary of significant
  risks reads only the LIMIT set, as the stress-loss limits do.

## Limitations
- **Cross-family is one number.** It does not say which two families interact; a pairwise split
  would cost one more revaluation per pair.
- **Higher order mixes causes.** Smile, exotics and convexity are not separated.
- **Basis is the only hedge-failure definition.** A hedge that fails for another reason (a single
  name against an index, an equity against a future) shows up in its families and in cross-family,
  not on the hedge-failure line.
- **Component VaR can be negative** for a desk that hedges the book; its share is negative then.
- **Positions are capped** at 30, so a long tail of 2% positions is cut off.
- **Cost.** One full revaluation per family and scenario: about 10 s on the bank and 7 s on the
  fund for the demo library.

## Validation tests
`tests/test_stress_explain.py`:
- the terms add up to each scenario's full-revaluation loss, per row, per desk and in total;
- a family's total equals a direct revaluation of its shocks alone;
- the Taylor terms equal the challenger approximation (`taylor_pnl_matrix`);
- vega appears only in a volatility scenario, and a one-family scenario has no other family;
- top trades are the worst, in order, at most ten;
- the hedge-failure line equals the basis shocks revalued, with no cross-family term;
- the desks' VaR and their P&L in the firm's worst scenario add up to the firm; positions are
  significant and capped; with no LIMIT scenario the summary is empty.

`tests/test_workflows.py`: the EOD stores the three tables, the family totals add up to the stored
scenario totals, and a re-run of the stage reproduces them exactly.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-10-17 | Family, term, top-trade, hedge-failure and significant-risk explain every EOD (decision 49) | Novera |
