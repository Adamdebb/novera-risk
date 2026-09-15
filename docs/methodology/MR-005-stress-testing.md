# Stress testing  (ID: MR-005)

| Field | Value |
|---|---|
| Version | 1.1.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.stress`, `novera.risk.stress_catalogue` |
| Last validated | 2026-09-15 |

## Definition
Instantaneous shock scenarios applied to the snapshot with full revaluation. Two kinds:
hypothetical (rule-based shocks on factor families) and historical (the observed move of
every factor across a named episode window in the history).

## Library
USD rates +100bp; global rates +100bp; USD steepener and flattener; EUR/USD −10%;
equity −20% with vol +15; credit +150bp; oil −30%; BTC −50%; vol +10 points; global
risk-off (composite); inflation shock (composite). Historical: the simulator's stylised
risk-off crash and rates shock.

### Categories on the "Stress library" page
`GET /reference/stress-library` and the dashboard page list every scenario in three
categories with the shocks it applies:

1. **Hypothetical** (12, in the daily run): the rules above, shown in bp for rates and
   spreads, vol points for surfaces and per cent for everything else, with the curve nodes a
   rule is limited to. Sizes are assumptions in the range regulators and desks use; none is
   measured from a real market move.
2. **Stylised historical episodes** (2, in the daily run): the simulator's windows, shown as
   the realised move of fourteen headline factors (S&P 500, Euro Stoxx, USD 2Y and 10Y, EUR
   10Y, EUR/USD, USD/JPY, CDX IG and HY, SPX 3M vol, USD 1Y10Y swaption vol, Brent, gold,
   Bitcoin) between the window's first and last day.
3. **Named real crises** (7, not in the daily run): the windows in `market_data/crises.py`
   (2008 GFC, 2011 euro sovereign, 2015 China, 2016 Brexit, 2020 COVID, 2022 rates shock,
   2023 banking stress). Each carries a status: NOT_COVERED when the stored history does
   not reach the window, SYNTHETIC_WINDOW when it does but no factor on those dates was
   fetched from a real source, PARTLY_REAL or REAL from the provenance table. A window is
   only a real replay at REAL; the page says so rather than showing synthetic moves over a
   real calendar as if they were the event.

## Assumptions
Shocks are simultaneous and instantaneous; no liquidity horizon; no rebalancing. Vol
shocks are absolute vol points applied to every surface node.

## Validation tests
`test_stress_library_runs_and_makes_sense`, `test_historical_episode_scenarios`,
`test_stress_library_catalogue` (categories, units and window statuses); the route and its
schema in `tests/test_api.py`, the page in `tests/test_ui.py`.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-09-14 | Hypothetical library and stylised episodes | Novera |
| 1.1.0 | 2026-09-15 | Stress library catalogue: categories, shocks and crisis-window status (decision 22.1); scenario results unchanged | Novera |
