# Limit utilisation and breach status  (ID: MR-006)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Control |
| Approval status | Draft |
| Code | `novera.limits.monitoring.monitor` |
| Last validated | 2026-09-14 |

## Definition
For each approved limit effective on the business date, the current value of its measure
over the trades in scope, the utilisation (current / amount) and a status.

| Limit type | Measure |
|---|---|
| VAR, EXPECTED_SHORTFALL | standalone historical VaR / ES of the scope (MR-002, MR-003) |
| STRESS_LOSS | worst loss across the stress library for the scope (MR-005) |
| DV01, CS01, FX/EQUITY/COMMODITY_DELTA, VEGA, GAMMA | absolute net sensitivity (MR-001), filtered by currency, tenor bucket or underlying |
| CONCENTRATION | share of the scope's absolute sensitivity carried by one tenor bucket or underlying |
| COUNTERPARTY_EXPOSURE | positive net PV facing the counterparty, before collateral (replaced by the exposure engine in Phase 4) |

## Status
BREACH when utilisation ≥ 100%; WARNING when ≥ the limit's warning threshold (default 80%);
OK otherwise; NO_DATA when the measure could not be computed.

## Scope
A hierarchy node (firm, business, desk, book, trader, legal entity, asset class,
counterparty) with optional currency and asset-class filters on the trade set.

## Limitations
Standalone VaR ignores diversification with the rest of the firm by design. Concentration
uses net exposures per bucket, so offsetting positions within a bucket are not visible.

## Validation tests
`tests/test_limits.py`.
