# Fund look-through  (ID: MR-014)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.lookthrough.look_through` |
| Last validated | 2026-09-14 |

## Definition
For every live ETF or mutual-fund position, the exposure to each constituent in reporting
currency: `shares × units_i × price_i × FX`. Per constituent, direct exposure (cash equity,
index futures at multiplier × level, commodity futures at contract size × front price, crypto
spot) is compared with exposure held via funds; `via_funds_share = |via| / |total|`. A flag is
raised when at least 25% of a constituent's exposure, and at least 10m of it, comes through funds.

## Inputs
Portfolio snapshot and the run's priced market snapshot (MD-002 proxies included).

## Assumptions and limitations
Direct exposure counts linear cash and futures positions only (options are excluded from
the direct side, as their delta is already in the sensitivities). Mutual-fund cash sleeves
are reported separately and not attributed to any constituent.

## Validation tests
`test_fund_lookthrough_pricing`: direct versus via-fund split and the 25% flag on a
constructed portfolio.
