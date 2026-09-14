# Fund exposures and leverage  (ID: HF-001)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Fund Risk |
| Approval status | Draft |
| Code | `novera.fund.engine.delta_equivalent`, `novera.fund.modules.exposures` |
| Last validated | 2026-09-14 |

## Definition
Delta-equivalent exposure per position in the fund currency: equity, commodity and
crypto deltas per +1% times 100; FX delta likewise for FX products only (on other products
it is translation); bonds, swaps and CDS indices at signed notional (receive-fixed and
sold protection are long). Long, short, gross and net by strategy and asset class,
gross and net leverage as multiples of NAV.
