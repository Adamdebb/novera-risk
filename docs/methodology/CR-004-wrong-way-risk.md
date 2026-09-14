# Wrong-way risk indicator  (ID: CR-004)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Counterparty Risk Methodology |
| Approval status | Draft |
| Code | `novera.counterparty_risk.cva.wrong_way_indicators` |
| Last validated | 2026-09-14 |

## Definition
For each netting set, the correlation across simulated paths between gross exposure and
a credit-deterioration proxy for the counterparty, evaluated at each grid date in the
first year; the strongest value is reported with its date. A netting set is flagged when
the correlation exceeds 0.5 and carries exposure (below that, the common market factor
explains most of the co-movement in the simulation).

| Counterparty type | Proxy (rise = deterioration) |
|---|---|
| Sovereign | its currency against USD (e.g. USD/ARS) |
| Corporate below A | HY CDS index of its region |
| Bank, dealer | IG CDS index of its region |
| Fund, asset manager | equity index, sign reversed |

## Limitations
An indicator, not a model of dependence: it flags where exposure and credit move
together in the simulation, it does not adjust CVA. Proxies are assigned by type and
country, not per name.

## Validation tests
`tests/test_counterparty.py::test_run_counterparty_end_to_end` (proxies for the planted
sovereign and corporate cases).
