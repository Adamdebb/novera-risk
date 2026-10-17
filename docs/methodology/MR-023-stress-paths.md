# Stress paths with management actions  (ID: MR-023)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.stress_paths`; `novera.workflows.stress_paths` (action rules, stage); EOD step `stress_paths` (re-runnable stage of the same name); `GET /runs/{run_id}/stress-paths`; `GET`/`POST /admin/stress/actions`; `novera stress-setup actions`; Stress page, "Stress paths with management actions"; Admin page, "Stress actions" |
| Last validated | 2026-10-17 |

## Definition
A stress scenario is an instantaneous jump. A real stress unfolds over days, and the firm reacts. This
measure walks the book through 20-business-day paths of the whole market twice: **frozen** (nobody acts)
and **managed** (each desk follows the owner's action rules). For each path and each book it reports the
drawdown, the worst day, the day of the first stress-loss limit breach, the day the desks start to act,
the day they finish (time to flat), what the actions cost and what the hedges earned. For information:
no limit reads it.

## Mathematical method

**Paths.** A path is the cumulative move x_t of every risk factor from today's close, t = 0 … 20, in
shock units (MR-018: absolute for rates and spreads, relative for prices and vols). Three sources:

1. **LIMIT scenarios in three shapes.** Each scenario of the run's LIMIT set, with end shock s, is
   spread over time as x_t = w(t)·s:
   - `GAP_DRIFT`: w(1) = 0.6, then linear to w(20) = 1;
   - `V`: linear to w(5) = 1, then back to w(20) = 0.4;
   - `SLOW_BLEED`: w(t) = t/20.
   The shapes carry no noise (decision 51.5).
2. **The worst historical window.** The history's overlapping 20-day windows are screened on the
   sensitivities for today's book; the five worst are revalued in full and the worst one is replayed
   close by close from its first day.
3. **The fat-tailed Monte Carlo tail.** 2,000 daily paths in the stressed PCA space of MR-021, with
   Student-t scores (ν = 4, one scale per path, as MR-022): the daily move is W·s_d·σ/√10 per factor,
   and daily moves are summed; price-like factors compound as log returns (x = e^Σ − 1), so a run of
   daily falls cannot pass −100%. The 100 worst on the sensitivities are revalued in full at their end;
   the 20 worst (1%) are kept.

Price-like factors are floored at −95% everywhere.

**Valuation.** Every trade is revalued in full at every close of every path (the base valuation's
pricers, collateral curves and fixings); a trade's daily P&L is the change in its full-revaluation P&L
plus its theta × 7/5 (calendar days per business day). The sensitivity approximation of each frozen
path's end is stored beside it (`taylor_end_pnl`) to show where the delta-gamma-vega model fails.

**Frozen book.** The sum of every trade's daily P&L.

**Managed book.** Each trade carries a held fraction h_i (1 today). Day t's P&L is
Σ h_i(t−1)·ΔV_i(t) + hedge P&L − costs.

- **Trigger.** At each close, a desk whose cumulative managed P&L is at or below −m × its standalone
  1-day VaR (the headline measure, m = `stop_loss_var_multiple`) is triggered; when the firm's
  cumulative managed P&L reaches its stress-loss limit, every desk is triggered. Orders decided at a
  close execute at the next close, so one day's move always hits the old position.
- **Cut.** Every position of a triggered desk is reduced towards (1 − `cut_fraction`) of its size;
  short option positions (options, swaptions and exotics sold) are bought back in full when
  `close_short_options` is set.
- **Volume.** A trade's daily reduction is at most participation × stressed volume ÷ position, with
  stressed volume = `stressed_volume_share` × the MR-013 average daily volume. Liquid positions are
  cut first by construction; a desk is done when every position sits at its target.
- **Cost.** Spread: Δh_i × the trade's MR-013 half-spread cost × `stressed_spread_multiple`, on every
  trade. Impact (square-root law): for each desk and day,
  Y · ‖Σ Δh_i·ℓ_i‖ · (Σ w_i√(q_i/V_i)) / (Σ Δh_i·σ_i), where ℓ_i is the trade's daily P&L loading on the
  PCA scores, σ_i = ‖ℓ_i‖ its daily stressed P&L volatility, w_i = Δh_i·σ_i, and q_i/V_i its share of
  stressed volume that day. Impact is charged on the desk's **net** risk sold, so a swap and its hedge
  cut together pay spread twice but impact only on what is left between them (decision 51.6).
- **Hedge.** From the day after the trigger, at every close, a triggered desk with `hedge_delta` holds
  listed futures that offset its current delta, computed at that close's moves as
  Σ h_i (lin_ij + 2·quad_ij·y_j) from the sensitivities, mapped to instruments:
  - rates in USD, EUR, GBP, JPY, AUD, CAD and CHF: a 2-year future for tenors up to 3 years, a
    10-year future beyond (other currencies have no listed hedge);
  - single stocks: the regional index future (USD: S&P 500; EUR and CHF: Euro Stoxx 50; GBP: FTSE 100;
    JPY: Nikkei 225); index exposure: its own future;
  - FX pairs, commodity curves and crypto: their own futures.
  The hedge is a position in the same path, so its basis to what it hedges (curve shape, single name
  against index) is gained or lost. A hedge trade is bounded by participation × stressed volume of the
  future and pays half the futures spread × the stressed multiple plus square-root impact.

**Outputs.** Per path and book: end P&L, maximum drawdown (from the running peak, starting at zero),
worst day and its P&L, first day a stress-loss limit (firm or desk, at the effective amount) is
breached and which, first trigger day and number of desks triggered, completion day (null while any
triggered desk is unfinished) and the unfinished desks, action cost and hedge P&L. Per day: P&L,
cumulative P&L, hedge P&L, cost and the share of gross daily risk still held. Per desk: the same for
the desk with its stop-loss. Per desk and action (CUT, CLOSE_SHORT_OPTIONS, HEDGE): first and last day,
amount traded and cost. The run summary names the path with the worst frozen drawdown and both
drawdowns.

## Inputs
The run's portfolio, market snapshot, sensitivities (lin, quad, theta), history, the LIMIT stress
results, the effective stress-loss limits, the headline VaR's standalone VaR per desk, the MR-013
volume and spread per trade, the PCA space of MR-021, and the action rules recorded in the run's
configuration (`stress_action_rules`).

## Assumptions
- **Owner's rules** (defaults, decision round 51): stop-loss 3× 1-day VaR, cut 50%, close short
  options, hedge delta; participation 20% of stressed volume at 50% of normal; spread × 2; Y = 1.
  The (default) row applies to every desk without its own row; Head of Market Risk or CRO changes
  them (role matrix `STRESS_ACTIONS`), audited as `STRESS_ACTIONS_CHANGED`.
- Volumes and spreads are the synthetic MR-013 assumptions; futures volumes (`HEDGE_VOLUME`) are set,
  not observed.
- Hedge ratios come from the sensitivities; the delta of a desk is aggregated per hedge instrument with
  a beta of one (no regression to the index).
- Theta accrues on calendar days at today's rate.

## Limitations
- **No margin, collateral or funding** (round 52), **no redemptions** (round 56) and no counterparty
  default (round 54): cash out and days of survival are not yet reported.
- **No feedback of our selling on prices** beyond the impact cost: the liquidity spiral is round 52.
- Positions are cut proportionally; a desk that would close its riskiest positions first, or net
  offsetting trades by compression, would do better.
- The impact of the net risk sold is measured in the PCA space, which carries no idiosyncratic risk.
- Vega and the credit, basis and EM-rates deltas are not hedged (no listed instrument in the mapping).
- The scenario shapes are deterministic; the Monte Carlo paths carry the randomness.
- Monte Carlo selection screens on the sensitivities before revaluing 5× the kept paths; a path the
  screen ranks low is never revalued.

## Validation tests
`tests/test_stress_paths.py`: shapes reach their points; the rules validate and a desk row replaces
the default; every frozen close equals the full revaluation of that day's moves plus theta and a
scenario path ends on the scenario's loss; disabled rules leave the managed book frozen; the held share
falls monotonically, never below the cut target, and costs are paid; a desk that only hedges keeps
less than half its frozen loss on a slow USD rates move; names hedge with the right index and a
currency without listed futures is not hedged; Monte Carlo compounds prices as log returns.
`tests/test_workflows.py`: stored every EOD with the rules recorded, and the stage re-runs exactly.
`tests/test_api.py`: endpoints, permissions, validation and the audit event.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-10-17 | Frozen and managed 20-day stress paths with stop-loss triggers, cuts, option buy-backs and futures hedges (decision 51) | Novera |
