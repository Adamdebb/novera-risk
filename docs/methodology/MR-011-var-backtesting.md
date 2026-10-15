# VaR backtesting  (ID: MR-011)

| Field | Value |
|---|---|
| Version | 1.1.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.backtest` |
| Last validated | 2026-10-15 |

## Definition
Three series are tested each run:
- **Static hypothetical**: today's portfolio, its P&L under each of the last 250 daily
  moves (the historical-VaR scenario vector), each compared with the 99% VaR from the
  preceding 250 moves of the same vector.
- **Live**: each stored run's actual P&L explain total against the previous run's VaR.
  Grows by one point per business day.
- **History, for information** (`STATIC_SENSITIVITY_HISTORY`, decision 42.3): today's
  portfolio on every daily move of the stored history (about 1,050 days after the 250-day
  lookback on the five-year demo), on the delta-gamma-vega P&L (MR-004) so it is cheap enough to
  run daily. It covers the three stress episodes. It never sets the FRTB IMA multiplier, which
  reads the 250-day static result.

## Tests
Kupiec proportion-of-failures (LR, χ²₁), Christoffersen independence (first-order
Markov LR, χ²₁), conditional coverage (sum, χ²₂), and the Basel traffic-light zone with
exceptions scaled to 250 days (green below 5, amber 5 to 9, red 10 or more).

## Limitations
The static test ignores portfolio ageing and trading; it validates the VaR model on the
current book, not the historical sequence of books. The live series is short until the
platform has run for a year. The history series uses sensitivities, not full revaluation:
on the options book its exceptions measure the VaR and the Taylor approximation together.

## Validation tests
`tests/test_measures.py::test_backtest_statistics`, `test_static_backtest_on_portfolio`;
`tests/test_workflows.py::test_results_persisted_and_reloadable` (the three series stored).

## Change history
| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-14 | Static hypothetical and live backtests |
| 1.1.0 | 2026-10-15 | History backtest for information (decision 42.3) |
