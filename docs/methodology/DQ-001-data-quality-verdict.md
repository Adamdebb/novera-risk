# Data-quality checks and run verdict  (ID: DQ-001)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Control |
| Approval status | Draft |
| Code | `novera.data_quality.checks` |
| Last validated | 2026-09-14 |

## Checks

| Code | Severity | What it catches |
|---|---|---|
| MD_MISSING_FACTOR | MAJOR if trades depend on it, else MINOR | factor in the universe absent from the snapshot |
| MD_STALE_FACTOR | MAJOR if trades depend on it, else MINOR | factor observed before the business date |
| TRADE_INVALID | MAJOR | trade flagged invalid upstream |
| TRADE_UNKNOWN_BOOK / _COUNTERPARTY / _NETTING_SET | MAJOR | dangling reference-data keys |
| VAL_UNPRICED | CRITICAL above 2% of trades, else MAJOR | pricer failures |
| VAL_DEAD_TRADES | MINOR | matured, expired or settled trades still in the feed |
| PNL_UNEXPLAINED | MINOR | trades whose Greeks-based P&L misses full revaluation beyond tolerance |

## Verdict
RED if any CRITICAL finding; AMBER if any MAJOR or MINOR; GREEN otherwise. The run
completes regardless; RED results should not be published without an override event.

## Validation tests
`tests/test_workflows.py::test_run_record_and_verdict`.
