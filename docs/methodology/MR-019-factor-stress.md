# Factor stress, jump to default and worst windows  (ID: MR-019)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.factor_stress`; EOD step `factor_stress` (re-runnable, OPS-002); `GET /runs/{run_id}/factor-stress`; Stress page, "Factor stress" |
| Last validated | 2026-10-17 |

## Definition
Four views of today's book that the scenario library (MR-005, MR-018) does not give. All are for
information: none feeds a limit.

1. **Standalone shocks.** Each of the risk factors moves alone, down and up, in full
   revaluation.
2. **Sliding scales.** P&L of one factor from −3σ to +3σ.
3. **Jump to default.** Each corporate issuer defaults alone.
4. **Worst windows.** The stretches of the stored history that would hurt today's book most,
   over 1 to 60 days.

They follow the stress framework (`docs/08-stress-framework.md`, part B.1 and B.3), whose ideas
come from the RiskMetrics guide (1999), Crouhy, Galai and Mark (2005) and BlackRock (2024),
re-implemented from the descriptions. No figure is taken from a source.

## Mathematical method
**Moves.** For a factor f, the h-day moves m_{f,t} are every overlapping h-day change in the
stored history up to the valuation date, in shock units: absolute changes for rates, basis,
spreads and SABR rho, simple returns for prices and vols (MR-002).

**Standalone sizes (decision 48.1).** With h = 10:

  down_f = min(Q_{0.001}(m_f), 0),  up_f = max(Q_{0.999}(m_f), 0)

- Q is the empirical quantile over every stored day.
- Each direction is measured on its own, so an asymmetric factor keeps its asymmetry.
- A factor that never moved in a direction gets no shock in it.

Each shock is applied alone, by full revaluation of the trades that depend on the factor. The
P&L is stored by desk.

**Sliding scales (decision 48.4).**
- σ_f is the standard deviation of the 10-day moves.
- The P&L of factor f is revalued at k·σ_f for k = −3, −2.5, …, +3.
- Relative shocks are floored at −99%, so a price never crosses zero.
- Factors scaled: the headline basket of MR-018, then each desk's three worst standalone factors
  (by the worse direction).
- Stored for the firm and by desk.

**Jump to default (decision 48.2).** Each corporate issuer defaults alone, with no other market
move.
- **Issuers:** the single-name CDS reference entities and the stock tickers of the universe. An
  issuer with both is joined (BOEING with BA). Index CDS and sovereign issuers are out of scope.
- **CDS:** each live single-name CDS on the issuer settles, at its own recovery R:

    P&L = s · N · (1 − R) · FX − PV

  Here s is +1 for bought protection and −1 for sold, N is the notional, FX converts to the
  reporting currency, and PV is today's value of the trade (its mark-to-market is replaced by
  the settlement).
- **Stock:** the stock moves to 0.01% of today's price, a relative shock of −0.9999. Every trade
  that depends on the stock is revalued in full: cash equities, options, exotics, and ETFs and
  funds that hold it through their baskets. Zero itself would break the option pricers' log of
  spot.
- Stored by issuer and desk, with equity and CDS P&L apart.

**Worst windows.**
- For h in {1, 5, 10, 20, 60}, every overlapping h-day window of the stored history gives a
  scenario: its moves m_{·,t}.
- **RAW:** the moves as they happened.
- **VOL_RESCALED:** each factor's move times σ̂_f(today) / σ̂_f(window start).
  - σ̂ is the EWMA volatility of daily moves (λ = 0.94).
  - The ratio is clipped to [0.25, 4], so a factor that was nearly flat then does not explode
    today.
- **Screen:** the delta-gamma-vega approximation (MR-003) values every window.
- **Confirm:** the three worst windows that do not overlap (end dates at least h days apart)
  are revalued in full.
- Both figures are stored. The ranking within a horizon and version uses full revaluation.

## Inputs
Today's portfolio and market snapshot, the stored market history up to the business date, the
sensitivities of the run (for the screen), and the valuation table (for desks).

## Results on the demo (15 September 2026 bank, 14 September fund)
| | Bank | Fund |
|---|---|---|
| Worst standalone shock | S&P 500 −14.7%: −179m | NVDA −22.7%: −72m |
| Worst jump to default | MSFT: −93m (mostly options) | NVDA: −319m |
| Worst window | 60 days to 14 Oct 2024, rescaled: −758m | 60 days to 4 Mar 2025, raw: −239m |
| Run time | about 14 s | about 8 s |

The fund's NVDA default exceeds its 300m fund stress-loss limit on its own. It is information
only: no limit reads factor stress.

## Assumptions
- **The stored history is the stress history.** The sizes are the 99.9th percentile of the
  simulated five years. On the demo those years hold three stylised episodes, so the sizes
  carry their tails.
- **One factor at a time.** A standalone shock leaves every other factor unchanged. That is the
  point of the view, and it is why the view is not a scenario.
- **Default with no market move.** The default moves only the issuer's stock and CDS. Spread
  widening of peers, index CDS and the counterparty consequences come later (round 54).
- **Accrued premium ignored** on CDS settlement.

## Limitations
- **Overlapping windows.** Ten-day moves overlap. The 99.9th percentile of about 1,250 of them
  rests on a handful of independent observations.
- **Screen misses.** A window that the delta-gamma-vega screen ranks below the top three is not
  confirmed. Option-heavy books whose losses are convex can hide a worse window. The screen and
  full revaluation are stored side by side so the gap is visible.
- **No index-constituent default.** A name's default does not move the CDS indices it belongs
  to: constituents are not modelled.
- **Desks only.** The scales and windows are not broken down by book or legal entity.

## Validation tests
`tests/test_factor_stress.py`:
- sizes equal each direction's quantile of the 10-day moves and lie beyond the 99% move;
- standalone P&L equals a direct one-factor revaluation, split by desk;
- scales cover the headline basket and each desk's worst factor, the firm row is the sum of the
  desks, and zero sigma is zero P&L;
- issuers join CDS and stock and leave out indices;
- CDS settle at their own recovery, and a cash equity loses all but 0.01% of its value;
- worst windows are distinct, ranked, h days long and confirmed by full revaluation;
- the summary names the worst of each view.

`tests/test_workflows.py`: the EOD stores the four tables and the summary, and a re-run of the
stage reproduces them exactly.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-10-17 | Standalone shocks, sliding scales, jump to default and worst windows every EOD (decision 48) | Novera |
