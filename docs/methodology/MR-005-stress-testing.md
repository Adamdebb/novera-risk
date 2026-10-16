# Stress testing  (ID: MR-005)

| Field | Value |
|---|---|
| Version | 1.6.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.stress`, `novera.risk.stress_catalogue`, `novera.risk.stress_definitions` |
| Last validated | 2026-10-16 |

## Definition
Instantaneous shock scenarios applied to the snapshot with full revaluation. Two kinds:
hypothetical (rule-based shocks on factor families) and historical (the observed move of
every factor across a named episode window in the history).

## Library
Since round 46 the library is a stored, versioned set of definitions with a goal each (OPS-005):
LIMIT scenarios feed the stress-loss limits, INFORMATION scenarios are reported only. Until a
firm stores its own library the built-ins below run, all with goal LIMIT, which is what the
engine always ran.

Since round 47 a hypothetical scenario may be **consistent**: every factor its rules leave out
moves with its conditional expectation under stressed correlations (MR-018). The eight market
shocks and composites of the built-ins are consistent from version 2; the six curve and basis
sensitivities stay standalone.

Built-ins: USD rates +100bp; global rates +100bp; USD steepener and flattener; EURIBOR basis +20bp
(6M over ESTR +20bp, 3M +15bp, ESTR unchanged: interbank stress); USD funding squeeze (every
cross-currency basis −25bp, EUR, JPY and CHF −40bp); EUR/USD −10%;
equity −20% with vol +15; credit +150bp; oil −30%; BTC −50%; vol +10 points; global
risk-off (composite); inflation shock (composite). Historical: the simulator's stylised
inflation bear, risk-off crash and rates shock, on the windows stored with the history (SIM-001).

### Categories on the "Stress library" page
`GET /reference/stress-library` and the dashboard page list every scenario in three
categories with the shocks it applies:

1. **Hypothetical** (14 built-in, plus any the firm approves): the rules of the approved
   versions, with their goal, version, tags, severity and review date, shown in bp for rates and
   spreads, vol points for surfaces and per cent for everything else, with the curve nodes a
   rule is limited to. Sizes are assumptions in the range regulators and desks use; none is
   measured from a real market move.
2. **Stylised historical episodes** (3, in the daily run): the simulator's windows, shown as
   the realised move of fourteen headline factors (S&P 500, Euro Stoxx, USD 2Y and 10Y, EUR
   10Y, EUR/USD, USD/JPY, CDX IG and HY, SPX 3M vol, USD 1Y10Y swaption vol, Brent, gold,
   Bitcoin) between the window's first and last day.
3. **Named real crises** (14 since round 47): 2008 GFC, 2011 euro sovereign, 2013 taper
   tantrum, 2015 franc de-peg, 2015 China, 2016 Brexit, 2018 Volmageddon, 2020 COVID, 2020
   negative oil, 2022 rates shock, 2022 gilt and LDI crisis, 2023 banking stress, 2024 yen
   carry unwind and 2025 tariff shock. Each carries a status:
   - NOT_COVERED: the stored history does not reach the window.
   - SYNTHETIC_WINDOW: the history covers the window, but no factor on those dates was fetched
     from a real source.
   - PARTLY_REAL or REAL: from the provenance table. A window is a real replay only at REAL; the
     page says so rather than showing synthetic moves over a real calendar as if they were the
     event.
   - CALIBRATED: the crisis's replay (approximate headline moves, every other factor under
     stressed correlations, MR-018) has been approved and runs daily.

## Prior art
Ideas for reverse stress testing, correlation breakdown and liquidity-adjusted stress are
reviewed in `docs/references/` (wraquant, torchquantlib, Hurlin et al. 2026, van Oers 2017).
They are references only: none is used by the code, and none is adopted until it has its own
methodology record and tests.

## Assumptions
Shocks are simultaneous and instantaneous; no liquidity horizon; no rebalancing. Vol
shocks are absolute vol points applied to every surface node. A rate shock on `IR:EUR:` moves
ESTR and, through it, every EURIBOR projection curve; only the basis scenario moves EURIBOR
against ESTR. A standalone scenario leaves every unnamed factor unchanged. A consistent scenario
(MR-018) fills them with stressed correlations, the tenor and cross-currency basis included.

## Validation tests
`test_stress_library_runs_and_makes_sense`, `test_historical_episode_scenarios`,
`test_stress_library_catalogue` (categories, units and window statuses); the route and its
schema in `tests/test_api.py`, the page in `tests/test_ui.py`. The stored library reproduces
the built-in results exactly (`tests/test_stress_setup.py`, OPS-005).

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-09-14 | Hypothetical library and stylised episodes | Novera |
| 1.1.0 | 2026-09-15 | Stress library catalogue: categories, shocks and crisis-window status (decision 22.1); scenario results unchanged | Novera |
| 1.2.0 | 2026-10-14 | EURIBOR basis +20bp scenario (decision 33) | Novera |
| 1.3.0 | 2026-10-14 | USD funding squeeze scenario on the cross-currency basis (decision 35) | Novera |
| 1.6.0 | 2026-10-16 | Consistent scenarios (every factor moves), PCA shape rules and plausibility (MR-018); eight built-ins consistent from version 2; 14 named crises, with calibrated replays drafted (decision 47) | Novera |
| 1.5.0 | 2026-10-16 | Scenarios read from the stored, approved library with a goal each; limits read the LIMIT set; built-in results unchanged (decision 46, OPS-005) | Novera |
| 1.4.0 | 2026-10-15 | Episode windows read from storage instead of counted back from the end of the history, which slid a day for each day appended; inflation bear added (decision 42) | Novera |
