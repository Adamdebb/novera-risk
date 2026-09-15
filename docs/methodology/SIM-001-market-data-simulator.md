# Synthetic market-data simulator  (ID: SIM-001)

| Field            | Value |
|------------------|-------|
| Version          | 1.2.0 |
| Owner            | Market Risk Methodology |
| Approval status  | Draft |
| Code             | `novera.simulation.market_data` |
| Last validated   | 2026-09-15 |

## Definition
Produces a reproducible daily history for every factor in the risk-factor universe, plus
the business-date snapshot and the previous-day snapshot used by the platform. It exists
so the platform can be demonstrated offline with realistic cross-asset behaviour. It is
not a forecast and makes no claim about real markets.

## Mathematical method
- Global factor `g_t ~ N(0,1)` daily. Each factor's standardised shock is
  `z = beta * g + w * common_group + sqrt(1 - beta^2 - w^2) * eps`.
- Prices, spreads, vols: `log S_{t+1} = log S_t + sigma sqrt(dt) m_t z_t - sigma^2 dt / 2 + d_t`,
  where `m_t` is the episode vol multiplier and `d_t` the episode drift.
- Zero rates: `r_{t+1} = r_t + sigma_d m_t (level + 0.45 slope_load * s + 0.25 curv_load * c) + d_t - 0.003 (r_t - r_base)`.
- Spreads and ATM vols mean-revert in log space to their base levels.
- Vol surface node = ATM(T) * term(T) * (1 - skew * k * lm + smile * (k * lm)^2), where
  `lm = ln(K/F) / sqrt(T)`, `k = 2.5`; equity skew 0.30, FX skew 0.03, smile 0.10 / 0.12.
- EUR/GBP is derived from EUR/USD and GBP/USD so triangular consistency holds.
- Commodity curves: spot node times `(1 + (slope + tilt_t) * T)`.
- Swaption smile (normal SABR, `β = 0`): per currency one mean-reverting path `x_t`
  (long-run standard deviation 0.06) and one log path `y_t` (0.12). Node `ρ_t = clip(ρ_base(ccy,
  expiry) · skew_mult_t + x_t, −0.9, 0.9)` and `ν_t = clip(ν_base(expiry) · tilt(tenor) ·
  √vol_mult_t · e^{y_t}, 0.05, 1.5)`, so the skew steepens in the crash and the vol of vol
  rises in both episodes. `α` is not simulated: the pricer implies it from the ATM cube.

## Inputs
Base levels and vols in `simulation/reference_levels.py`; episodes in
`DEFAULT_EPISODES`; seed and horizon in `MarketSimConfig`. The default horizon is five years
(1,305 business days) built as two segments. The core segment is the last `core_years`
(three years, 783 days) and is drawn exactly as a three-year run would draw it, so the
episodes (crash starting 480 days before the end, rates shock 220 days before), the levels
on the business date and the VaR window are unchanged by the longer horizon, and the limit
calibration holds. The extension segment covers the earlier years: a second run of the same
model with seed `seed + 101` and no episodes, re-levelled so its last day equals the core's
first day (a shift for zero rates, a scale for everything else), with that overlap day then
dropped so the junction step is an ordinary daily move. Every risk-factor series is
re-levelled independently, which preserves the FX triangle and the curve shapes.

## Assumptions
Constant correlations except through the vol multiplier; lognormal returns; no jumps
outside the episodes; no holidays (weekdays only); no intraday data.

## Calibration
Annualised vols set to plausible 2024-2026 levels. Episode sizes chosen to be visibly
larger than typical daily noise (crash: equities -27%, vol x2.3; rates shock: +150bp short,
+90bp long). Post-episode decay of 80% of the vol move and 60% of the spread move over 60 days.

## Limitations
Synthetic history cannot be used to validate a VaR model against real events. Smile
shape is static outside episodes. Correlations across currencies' rate curves come only
through the global factor. Not suitable for any regulatory purpose.

## Validation tests
`tests/test_market_data.py`: reproducibility, positivity, skew sign, triangular FX
consistency, backwardation shape, drawdown and rates-shock visibility, storage round trip,
and that the extension leaves the core bit-identical and joins without a jump.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-09-14 | Initial model | Novera |
| 1.1.0 | 2026-09-15 | Default horizon three to five years as core plus extension segments (decision 20.1) | Novera |
| 1.2.0 | 2026-09-15 | Swaption SABR smile parameters, drawn after every earlier family (decision 21.1) | Novera |
