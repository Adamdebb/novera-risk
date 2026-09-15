# Equity barrier and digital option valuation  (ID: PR-016)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.breadth.price_equity_exotic`, `novera.pricing.exotic_formulas` |
| Last validated | 2026-09-14 |

## Definition
Black–Scholes with continuous rate `r = −ln DF(T)/T` from the zero curve, zero dividend
yield and implied vol from the underlying's surface at `K/F`.

- **Barrier** (up/down, in/out; continuously monitored): Reiner–Rubinstein (1991) in Haug's
  A–F decomposition, with a rebate paid at expiry for knock-outs never hit and at hit for
  knock-ins that expire unexercised. An option already through its barrier is a vanilla
  (knock-in) or worth the rebate (knock-out).
- **Cash-or-nothing digital**: `payout × DF × Φ(±d₂)`.

PV = ±contracts × multiplier × unit value.

## Inputs
Spot (share or index level), zero curve, vol surface; strike, barrier, barrier type, rebate,
payout and expiry from the instrument.

## Assumptions and limitations
Continuous monitoring (discrete monitoring would need the Broadie–Glasserman–Kou shift);
flat vol per option (no local or stochastic vol, so barrier risk near the barrier is
understated); no dividends. These are exotic products for capital purposes: FRTB SA applies
the residual risk add-on (REG-001).

## Validation tests
`test_barrier_and_digital_formulas_match_quantlib`: all sixteen barrier/type/strike cases with
and without rebate agree with `ql.AnalyticBarrierEngine` to 1e-8; digitals with
`ql.AnalyticEuropeanEngine`; in-out parity. `test_equity_exotic_pricer_uses_surface_and_curve`:
pricer wiring, knock-out below vanilla, knocked-out option worth the rebate.
