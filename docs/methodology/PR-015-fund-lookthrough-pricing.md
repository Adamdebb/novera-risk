# ETF and mutual-fund valuation by look-through  (ID: PR-015)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.breadth.price_etf`, `price_mutual_fund`, `fund_nav` |
| Last validated | 2026-09-14 |

## Definition
NAV per share = `Σ units_i × price_i × FX_i→fund ccy (+ cash per share)`, over the fund's
basket of equities, indices, commodities (front-month curve price) or crypto. ETF price =
NAV × (1 + tracking spread); mutual funds deal at NAV. PV = shares × price.

## Inputs
Constituent prices and FX from the snapshot; basket units, currency, tracking spread and
cash sleeve from the instrument (baskets are synthetic and sized so the basket is worth the
reference NAV at reference levels).

## Assumptions and limitations
Baskets are static (no rebalancing, no corporate actions); the ETF premium/discount is a
constant tracking spread rather than a market-driven basis; mutual funds are priced
continuously although they deal once a day with notice. Because the fund depends on its
constituents' factors, every risk measure sees through it automatically (MR-014 makes that
explicit).

## Validation tests
`test_fund_lookthrough_pricing`: NAV identity for ETF and fund, tracking spread applied,
cash sleeve added, constituent factors in the dependency map.
