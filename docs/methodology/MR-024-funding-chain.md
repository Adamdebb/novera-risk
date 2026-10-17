# Margin, collateral and funding chain with the liquidity spiral  (ID: MR-024)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology, with Treasury |
| Approval status | Draft |
| Code | `novera.risk.funding_chain`; `novera.risk.stress_paths` (the managed walk calls it, `_Seller`); `novera.workflows.stress_paths` (`funding_inputs`, funding rules); EOD step `stress_paths` (re-runnable stage of the same name); `GET /runs/{run_id}/stress-liquidity`; `GET`/`POST /admin/stress/funding`; `novera stress-setup funding`; Stress page, "Margin, collateral and funding on the stress paths"; Admin page, "Stress funding" |
| Last validated | 2026-10-17 |

## Definition
A stress loss hurts twice: as P&L, and as cash the firm must find at once. This measure follows the cash
on the stress paths of MR-023: what the firm pays and receives at every close (variation margin,
collateral, initial margin, haircuts, contractual flows), what it can still raise (its liquidity), and on
which day it would run out. Each of the 20-day paths is walked, frozen and managed, under three funding
scenarios set by the owner: **normal**, **firm-specific** and **market-wide**. When the managed book runs
short it sells, and the selling moves prices against what it still holds: the **liquidity spiral**. Every
stress scenario is also read as an instantaneous call on liquidity before any action. For information: no
limit reads it.

## Mathematical method

**Margin regime of a trade.**

| Regime | Trades | Cash at each close |
|---|---|---|
| VM | cleared at LCH or ICE; listed futures; listed options sold; securities sold short (the lender calls collateral daily) | the day's P&L |
| CSA | bilateral trades in a netting set with a CSA | the change in collateral (below) |
| FUNDED | securities owned (bonds, shares, funds, coins) | none: the P&L moves an asset |
| UNMARGINED | bilateral without a CSA, listed options bought, FX spot, repo | none |

**Collateral (CSA).** For a netting set of value V (the held trades' PV plus their path P&L), we post
max(−V − T_we·m, 0) and receive max(V − T_they, 0), where T are the CSA thresholds and m the scenario's
threshold multiplier. The collateral we post is securities at the CSA haircut h·k (k the scenario's
haircut multiplier), costing we_post·(1/(1 − h·k) − 1) of liquidity on top. Minimum transfer amounts,
rounding and independent amounts are not applied.

**Initial margin we post.** Today's SIMM-lite margin model for each exchanged set (REG-004: bilateral sets
with a CSA, both directions, and CCP sets) is scaled by the set's risk at the stressed point:

  IM_s(t) = IM_s(0) · σ_s(t)/σ_s(0),  σ_s(t) = ‖Σ_{i∈s} h_i ℓ_i(t)‖,  ℓ_i(t) = (lin_i + 2·quad_i ∘ y_t)·B

where ℓ_i is trade i's daily P&L loading on the PCA scores of MR-021 with its delta taken at the day's
sensitivity-factor moves y_t (so gamma raises margin and cuts lower it), h_i the share still held, and B the
map from scores to sensitivity rows. A CCP set is also scaled by a procyclical multiplier
clip(√EWMA_t / σ_s(0), 1, cap), where EWMA_t = λ·EWMA_{t−1} + (1 − λ)·P&L_{s,t}², seeded at σ_s(0)². Bilateral
margin above the threshold per counterparty is exchanged pro rata (as REG-004); CCP margin in full. Posted
margin is government bonds at the IM collateral haircut × k. Margin received is segregated and never
counts as liquidity. The listed hedge futures of MR-023 post their own margin (`HEDGE_MARGIN` × notional).

**Liquidity, bank.**

  L_t = cash buffer + cash_t + Δcollateral_t − ΔIM_t + HQLA_t − encumbrance_t

- cash_t: everything paid and received since today: VM, hedge P&L, contractual flows, sale proceeds, costs.
- HQLA_t: long government bonds at their path value × (1 − haircut × k). Level-1 currencies (USD, EUR,
  GBP, JPY, CHF, CAD, AUD) take `hqla_haircut_level1`, the others `hqla_haircut_other`.
- encumbrance_t: Σ cash borrowed in repo × (1 + its haircut + add-on) − Σ cash lent in reverse repo ×
  (1 + its haircut). The scenario's add-on applies to a repo from the day it rolls inside the horizon.
- Δcollateral and ΔIM are changes against today. Collateral already posted today is outside the buffer.

**Liquidity, fund.**

  L_t = NAV_t − M_t,  M_t = M_0 · p · Σ r_i|e_i(t)| / Σ r_i|e_i(0)|

- NAV_t is NAV plus the managed P&L.
- M_0 is today's prime-broker margin (HF-002), r_i each trade's PB rate (with the crowding surcharge), and
  p the scenario's PB margin multiplier.
- e_i is the delta-equivalent exposure: it moves one for one with P&L for cash equity, ETFs, crypto,
  futures and FX spot, and is held at its notional for the rest.
- All P&L is cash in the PB account. The 15% unencumbered cash is part of NAV − margin.

**Contractual flows.** Coupons, swap legs, premiums, protection and FX settlements falling inside the
horizon, on trades that are neither VM nor CSA (a payment on a margined trade comes back as collateral).
Principals are left out: repos roll, and a bond's principal turns an asset into cash.

**Funding scenarios** (`stress_funding_rule`, Admin → Stress funding). Each changes the terms from day one:

| Scenario | Threshold multiplier | Repo haircut add-on | Haircut multiplier | CCP procyclical | PB margin |
|---|---|---|---|---|---|
| NORMAL | 1 | 0 | 1 | yes | ×1 |
| FIRM_SPECIFIC | 0 (a downgrade removes our thresholds) | +5 points at each roll | 1 | yes | ×1 |
| MARKET_WIDE | 1 | 0 | 2 (CSA, IM collateral, HQLA) | yes | ×1 |

**The spiral (managed book).** At each close, after the desks' own actions, the walk computes L_t. If L_t
is below zero:

1. **Size the sale.** Probe a sale of 10% of every position, bounded by the day's stressed volume less what
   the desks already sold (MR-023). Measure the liquidity it raises before costs, g. Sell
   min(1.05·(−L_t)/g, 10) probes, still within the volume bound.
2. **Pay for it.** The sale pays the MR-023 cost (stressed half spread on every trade, square-root impact on
   the desk's net risk sold). Cost and impact are priced on the day's cumulative forced sale; each round
   pays the increment.
3. **Feel the impact.** A share `permanent_impact_share` of the impact stays in the price. It re-marks each
   held trade of the desk by I_d·σ_i/Σ_d(Δh·σ) per unit of position, which calls margin on VM trades, moves
   CSA sets and lowers HQLA.
4. **Repeat.** Recompute L_t, at most `spiral_max_rounds` rounds a close, until L_t ≥ −10⁻⁶ × today's
   liquidity, nothing more can be sold, or a sale raises nothing.

A close that still ends below zero is the day the firm fails (`survival_day`). The walk records it and
stops selling. The forced sales feed back into the desks' P&L and so into their stop-losses. The frozen
book never sells; it reports the first close its liquidity is below zero.

**Instantaneous calls.** Each stress scenario of the run (LIMIT and INFORMATION) is applied as one close
with h = 1, no contractual flows and no sale, under each funding scenario.

**Outputs.**
- Per path, book and funding scenario: today's liquidity, the lowest and its day, the end value, peak cash
  out, survival day, end-of-path variation margin, collateral, initial margin, HQLA and contractual flows,
  forced-sale days, share sold, cost, permanent impact, most spiral rounds, whether a shortfall stayed
  unresolved, and the end P&L including forced sales.
- Per close: each component, sale proceeds, costs, net liquidity, share sold, rounds.
- Per scenario and funding scenario, for the instantaneous calls: P&L, each component, the liquidity used
  and left.

The MR-023 tables are unchanged: their managed book follows the action rules only.

## Inputs
- From MR-023: the run's paths, the path engine, its full revaluation at every close, its costs and
  volume bounds, and the PCA space and sensitivity book.
- The netting sets and CSAs, CCP netting sets (CR-006), SIMM-lite margin per set (REG-004), live repos, and
  trade cashflows from the pricers.
- Fund face: NAV, the PB schedules and crowding scores (HF-002, HF-006), the delta-equivalent exposures.
- The funding rules recorded in the run's configuration (`stress_funding_rules`).

## Assumptions
- **Owner's choices (decision round 52):**
  - liquidity is book HQLA plus a set cash buffer (bank), NAV less PB margin (fund);
  - IM follows the stressed delta, CCP margin is procyclical, PB margin follows gross exposure;
  - the three funding scenarios are defaults the owner can edit;
  - the managed book sells to a fixed point, and the frozen book only reports.
- **Defaults** (Head of Market Risk or CRO edits them, role matrix `STRESS_FUNDING`, audited as
  `STRESS_FUNDING_CHANGED`):
  - cash buffer 1bn;
  - HQLA haircuts 2% (level 1) and 15% (other);
  - IM collateral haircut 2%;
  - permanent impact 50% of the square-root impact;
  - 10 spiral rounds;
  - λ = 0.94;
  - CCP margin cap 2× today's.
- Variation margin is cash and reusable; IM and CSA collateral we post are bonds from the HQLA.
- A forced sale is proportional across every position (MR-023's volume bound makes liquid positions finish
  first). Repo financing is held in place: cutting a repo raises its PV only.
- The SIMM-lite calibration is fixed over the horizon; margin moves with risk, not with a recalibration.

## Limitations
- **No balance sheet.** Unsecured funding, deposits and the funding of the bond inventory are not modelled.
  The bank's short bond positions are assumed covered outside the book, and HQLA nets repo borrowing
  against reverse repo. The resources table of round 57 replaces the cash buffer.
- **Exchange margin on the existing listed book is not modelled.** Listed futures pay variation margin, but
  no initial margin is held on them today; only the hedge futures post margin. The cash out on a listed
  book is understated.
- **The fund's three scenarios coincide until round 56.** The fund's CSAs have no thresholds or haircuts,
  so the firm-specific and market-wide scenarios differ from normal only through the PB margin multiplier,
  which is 1 until round 56 tightens prime-broker terms.
- **No redemptions** (round 56) and no counterparty default (round 54).
- **Collateral terms are simplified.** Minimum transfer amounts, rounding and independent amounts are left
  out. A downgrade does not trigger additional termination events.
- **Contractual flows are approximate.** The paths do not age the trades, so a coupon is not offset by the
  bond's price drop.
- **Permanent impact is a single number** per desk and close. Other firms selling the same positions
  (crowding) is round 55.

## Validation tests
`tests/test_funding_chain.py`:
- **Rules:** round trip and validation; the margin regimes.
- **Terms on day one:** no move under normal terms changes nothing; a downgrade calls the threshold with
  its haircut; market-wide haircuts cost what they should on posted collateral and HQLA.
- **One day's move:** a cleared loss is cash at once, a CSA set calls beyond its threshold, and a falling
  bond lowers HQLA net of haircut.
- **Initial margin:** posted IM doubles with the set's stressed risk, above the threshold pro rata; CCP
  margin is procyclical and capped.
- **Repo:** a repo takes the add-on on the day it rolls.
- **Spiral:** it reaches a fixed point, and permanent impact makes it sell more in as many or more rounds;
  a shortfall the market cannot absorb is the day the firm fails.
- **Fund:** liquidity is NAV less margin on the path.
- **On the MR-023 paths:** with ample liquidity every walk ends on the MR-023 P&L; with no buffer the
  frozen book runs short and the managed book sells and lasts at least as long.

`tests/test_workflows.py`: stored every EOD with the rules recorded, and the stage re-runs exactly.
`tests/test_api.py`: endpoints, permissions, validation and the audit event.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-10-17 | Margin, collateral and funding chain on the stress paths under three funding scenarios, with the liquidity spiral and instantaneous calls (decision round 52) | Novera |
