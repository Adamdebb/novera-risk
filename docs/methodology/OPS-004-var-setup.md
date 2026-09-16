# VaR setup  (ID: OPS-004)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Control |
| Approval status | Draft |
| Code | `novera.risk.var_measures`, `novera.workflows.var_setup`, `GET/POST /admin/var/setup`, `POST /admin/var/template`, `novera var-setup`; the "VaR measures" section of the Admin page |
| Last validated | 2026-09-16 |

## Definition
The VaR matrix a firm maintains: which VaR measures the end-of-day run produces, which of
them feed the limits and which are for information. A bank runs a 99% two-year historical
VaR for limits with a stressed VaR next to it and keeps ES, the challenger and Monte Carlo
for information; a hedge fund runs a 95% exponentially weighted one-year VaR for limits and
a 99% five-year VaR for information. Both are rows of the same matrix.

## A measure
| Column | Values | Meaning |
|---|---|---|
| goal | LIMIT, INFORMATION | LIMIT feeds the limits of the metric's type; INFORMATION is reported only |
| metric | VAR, ES, STRESSED_VAR | What the row's value is; an ES row sets the ES confidence, a stressed row needs a fixed window |
| confidence | 0.5 to 1 | 0.99 for 99% |
| shocks | HISTORICAL, HISTORICAL_WEIGHTED, MONTE_CARLO | Equal weights (MR-002), decaying weights (MR-015), Gaussian draws from the window's covariance (MR-010) |
| compute | FULL_REVALUATION, SENSITIVITY | Every trade repriced, or the delta-gamma-vega expansion (MR-004); Monte Carlo is sensitivity-only |
| window_years | years | Back from the valuation date at 250 business days a year; empty means the run's base window (500 days) |
| window_start, window_end | ISO dates | A fixed window inside the stored history (MR-016); the end may be empty to run to the valuation date |
| decay | 0 to 1 | Lambda of the weighted scenarios; required for HISTORICAL_WEIGHTED, refused otherwise |
| enabled | yes, no | Whether the run produces the row |

The measure id is derived from the parameters (`VAR_99_HS_FULL_2Y`, `ES_95_WHS_FULL_1Y_L94`,
`STRESSED_VAR_99_HS_FULL_20250401_20260331`), so two identical rows collide by construction.

## Rules
- At least one enabled VaR row: the LIMIT VaR row, else the first VaR row, is the run's
  headline VaR (`summary.var`, the backtest's series, the Overview card).
- At most one LIMIT row per metric. VaR limits read the LIMIT VaR row, expected-shortfall
  limits the LIMIT ES row, stressed-VaR limits the LIMIT stressed-VaR row (`LimitType.STRESSED_VAR`).
  A VaR or ES limit with no LIMIT row of its metric falls back to the headline's scenarios
  (its ES at the base 97.5%); a stressed-VaR limit with no row has no value.
- Fixed windows must lie inside the stored history; a weighted row needs a decay; Monte
  Carlo cannot be full revaluation; a stressed row needs a fixed window.
- Rows that share scenarios and compute share one P&L matrix: an ES row next to a VaR row
  costs nothing, the weighted 95% VaR and the plain 99% VaR on the same window share the
  full revaluation.
- The run records the measures it produced in its config (`var_measures`), so a later change
  of the setup does not alter how the run is read, and a partial re-run of the VaR stage
  (OPS-002) reproduces the run's own matrix.
- A change replaces the whole matrix, names an actor and is audited (VAR_SETUP_CHANGED with
  the ids before and after and the limit routing).

## Defaults and templates
Until a setup is saved the defaults reproduce what the platform produced before the setup
existed: 99% VaR and 97.5% ES on the base window, both feeding limits; the delta-gamma-vega
challenger and Monte Carlo for information. Two templates can be loaded on the Admin page or
with `novera var-setup template bank|hedge_fund`:

| Template | Goal | Measure |
|---|---|---|
| bank | LIMIT | VaR 99% · historical · full revaluation · 2Y |
| bank | LIMIT | Stressed VaR 99% · historical · full revaluation · most volatile year of the history |
| bank | INFORMATION | ES 97.5% · historical · full revaluation · 2Y |
| bank | INFORMATION | VaR 99% · historical · delta-gamma-vega · 2Y |
| bank | INFORMATION | VaR 99% · Monte Carlo · delta-gamma-vega · 2Y |
| hedge_fund | LIMIT | VaR 95% · weighted historical (λ 0.94) · full revaluation · 1Y |
| hedge_fund | INFORMATION | ES 95% · weighted historical (λ 0.94) · full revaluation · 1Y |
| hedge_fund | INFORMATION | VaR 99% · historical · full revaluation · 5Y |

## Where it shows
The run's `var_summary` table has one row per measure (goal, metric, label, value, VaR, ES,
window, decay, scenarios, seconds); `var_measure_contributions` and `var_measure_scenarios`
carry every measure's contributions and scenario P&L with its weight. The VaR page shows the
LIMIT measures first, then the INFORMATION ones, the component VaR of every measure by
group, and the scenario weights of a weighted measure. The Admin page holds the matrix with
the templates; the Overview card labels the headline's confidence.

## Assumptions and limitations
One setup per firm face (per database). Limit amounts are not recalibrated when the measure
behind them changes: switching the bank from 99% two-year to 95% weighted VaR leaves the
limit amounts where they were, so utilisations drop. Fixed windows are limited to the stored
history (five synthetic years in the demo). A Monte Carlo full-revaluation row is refused
rather than run for its cost.

## Validation tests
`test_var_measures_share_matrices_and_validate` (engine, sharing, routing, validation,
templates, round trip), `test_var_setup_drives_the_run_and_limits` (stored setup drives the
run, limits read the LIMIT rows, config records the matrix, re-run keeps it),
`test_var_setup_endpoints` (routes, 409 on invalid, template, per-measure reads), the VaR and
Admin pages in `tests/test_ui.py`.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-09-16 | VaR matrix with goals, templates, limit routing and the Admin section (decision 25.1) | Novera |
