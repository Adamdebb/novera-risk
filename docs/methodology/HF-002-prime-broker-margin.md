# Prime-broker margin replication  (ID: HF-002)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Fund Risk |
| Approval status | Draft |
| Code | `novera.fund.modules.pb_margin` |
| Last validated | 2026-09-14 |

## Definition
Per prime broker and asset class: margin rate per product from a broker-specific schedule
on |delta-equivalent exposure|, a crowding surcharge on names with score above 0.7, a
25% netting benefit on the offsetting share of long and short exposure within the asset
class and broker. Listed positions are routed to brokers by exchange. Reported: margin
by broker with shares, margin over NAV, unencumbered cash (15% of NAV), excess equity.

## Limitations
Schedules and routing are synthetic. No cross-asset portfolio margining, no funding rates.
