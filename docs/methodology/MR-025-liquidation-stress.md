# Concentration and liquidation stress  (ID: MR-025)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.liquidation_stress`; `novera.workflows.liquidation_stress`; EOD step `liquidation_stress` (re-runnable stage of the same name); `GET /runs/{run_id}/liquidation-stress`; Stress page, "Concentration and liquidation stress" |
| Last validated | 2026-10-17 |

## Definition
A stress scenario prices the book at screen after the move. A large position cannot be sold at screen.
It takes days to exit, the market keeps moving while it is sold, and selling it moves the price. This
measure adds both effects to every stress scenario of the run:

- **Slow-exit add-on:** a position slower to exit than the scenario's 10-day horizon carries its loss for
  longer.
- **Exit cost:** the gap between screen price and liquidation value.
- **Concentration add-on:** the exit cost of each market less the same cost computed trade by trade.
  Positions in one name, contract month or strike compete for the same volume.
- **Sector shocks:** the names of a sector move together beyond their market.

For information: no limit reads it (promotion is round 58).

## Mathematical method

**Markets.** Trades that compete for one volume form a market:

| Kind | Products | Key |
|---|---|---|
| NAME | cash equity, ETF, mutual fund, government bond, single-name CDS, CDS index, crypto | ticker / fund code / issuer and maturity month / entity / family and series / symbol |
| DELIVERY | equity index, rate and commodity futures | underlying and contract month |
| STRIKE | equity, commodity and FX options, equity exotics, swaptions | underlying, expiry month, strike bucket (5% in log strike; 25bp for swaptions) |
| OTC | swaps, FX forwards and spot, repo | the trade itself |

An OTC trade is its own market: it is unwound with a dealer, not through one order book, and the MR-013
volume is the capacity of one unwind (decision 53.5). Offsetting trades net within a market. The share to
exit is n_m = |Σ e_i| / Σ|e_i| over the trades' signed delta-equivalent exposures e_i (HF-001).

**Stressed exit horizon.**

  H_m = n_m · Σ_{i∈m} q_i / (p · s · ADV_i)

q_i is the trade's position, ADV_i its MR-013 average daily volume, s the stressed volume share and p
the participation (both from the default row of the MR-023 action rules the run recorded). The trade
view uses each trade on its own: H_i = q_i / (p · s · ADV_i).

**Slow-exit add-on.** Each trade's scenario P&L is scaled by its market's factor:

  λ_m = √(clip(H_m, 10, 120) / 10)

- **adjusted P&L** = Σ λ_m(i) · P&L_i
- **horizon add-on** = adjusted P&L − plain P&L

Positions that exit within 10 days are unchanged; 120 days (FRTB's longest liquidity horizon) caps the
factor at √12. A hedge in a liquid market is not scaled, so the add-on carries the basis between an
illiquid position and its liquid hedge.

**Exit cost.** For each market:

  cost_m = m · n_m · Σ_{i∈m} c_i + Y · σ_m · √(n_m · Σ_{i∈m} q_i / (s · ADV_i))

- c_i is the trade's MR-013 half-spread cost, m the stressed spread multiple and Y the impact
  coefficient (the action rule).
- σ_m is the daily stressed P&L volatility of the market's net exposure under the full stressed
  covariance of the PCA space: σ_m² = gᵀ D V Λ Vᵀ D g / h, where
  - g is the market's P&L per unit factor move (from the sensitivities);
  - D is the diagonal of factor sigmas;
  - V Λ Vᵀ is the stressed correlation matrix with every component, so single-name risk counts;
  - h is the space's horizon.
- The trade view applies the same formula to each trade alone and sums. The **concentration add-on** is
  cost_m less the trade view: positive when trades pile into one volume, negative when offsets net.
- A market's cost is shared among its trades in proportion to their trade-view cost, for the desk view.

**Liquidation loss** of a scenario = adjusted P&L − Σ cost_m (the cost of exiting the whole book).

**Sector shocks.** Equity and CDS sectors held through two or more names:

1. **Residual history.** For each name, the overlapping 10-day residual is
   - equities: the name's return less its regional index (SPX, Euro Stoxx 50, FTSE 100, Nikkei 225);
   - CDS: its spread change less the average of all single names.
2. **Shock size.** The sector residual is the names' average. Its 1st and 99th percentiles over the stored
   history are the two candidate shocks.
3. **Loss.** Each candidate shock is applied to every name of the sector, revalued in full and
   liquidity-adjusted. The worse direction is kept.

**Groupings reported.** Issuers (every market of one name: cash, options, CDS, bonds), sectors, markets by
kind (strike buckets and contract months among them), the scenarios, their desks and, per scenario, the ten
positions whose slow exit adds most.

## Inputs
The run's portfolio, sensitivities, valuation, MR-013 liquidity per trade, every stress result, the LIMIT
set, the PCA space of MR-021 (its sigmas and full eigen-decomposition), the stored history, the default
action rule the run recorded, and the equity and CDS sector reference data.

## Assumptions
- **Owner's choices (decision round 53):**
  - the √(H/10) scaling above 10 days;
  - square-root impact per pooled market, with the concentration add-on against the trade view;
  - issuer, sector, strike and delivery-month groupings;
  - every scenario, for information.
- Execution limits are the MR-023 defaults (participation 20%, stressed volume 50% of normal, spread × 2,
  Y = 1), and MR-013 volumes and spreads are synthetic.
- **Exit cost is scenario-independent.** It uses the stressed-regime volatility, not the scenario's move.
- Sector residuals use a beta of one to the regional index.

## Limitations
- **OTC is not pooled.** Concentration in one OTC curve (many swaps in one currency and tenor) shows only
  through the trade view; a dealer-capacity or DV01-based market would pool it.
- **One volume per product.** MR-013 volumes are per product, not per name or contract month, so two names
  of one product differ only through position size.
- **No management action is assumed.** The add-on assumes the whole net position is exited at the stated
  participation; the managed paths of MR-023 show how a desk's rules change the exit.
- **The impact is linear in the sensitivities.** Option gamma during the exit is ignored.
- **Sector shocks cover two or more names.** A single name is issuer risk, reported by the jump to default
  of MR-019.

## Validation tests
`tests/test_liquidation_stress.py`:
- markets pool what shares one volume (name, contract month, strike bucket) and keep OTC trades apart;
- the daily volatility equals the direct stressed covariance;
- a name starved of volume exits in more than 10 days, its loss scales by √(H/10) exactly, its market is
  slower than its trades, and a same-direction pooled exit costs at least the trades one by one;
- markets within 10 days do not scale, the desks add up to the firm, and the tables hold together.

`tests/test_workflows.py`: stored every EOD and the stage re-runs exactly. `tests/test_api.py`: the
endpoint.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-10-17 | Exit horizons per pooled market, exit cost and concentration add-on, liquidity-adjusted stress losses and sector shocks (decision round 53) | Novera |
