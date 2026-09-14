# Daily P&L explain  (ID: MR-007)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.pnl_attribution.explain_pnl` |
| Last validated | 2026-09-14 |

## Definition
Attribution of the change in portfolio value from the previous business date to today.

**Official (full-revaluation waterfall).** For trades present on both days, starting
from yesterday's PV on yesterday's market:
1. CARRY: roll the valuation date to today, market unchanged.
2. RATES, CREDIT, FX, EQUITY, COMMODITY, DIGITAL_ASSET, VOLATILITY: replace that group's
   factors with today's values, one group at a time, and reprice. Each step is the
   difference from the previous step, so the steps telescope exactly.
3. DATA: the difference between the last stepped snapshot and today's official snapshot.
   Non-zero when factors present yesterday are missing or stale today (for example a
   missing curve node), so data problems appear as an explicit residual.
4. NEW_TRADES: today's PV of trades booked today, less cash paid at inception (price for
   cash products and premiums for options; zero for swaps, forwards, futures and CDS).
5. DEAD_TRADES: yesterday's PV of trades that matured, expired or settled today, to zero.

**Challenger (sensitivity-based).** Yesterday's sensitivities (MR-001) times today's
factor moves, plus theta, per trade. The residual against the official trade-level
P&L is the "unexplained" reported to the data-quality module (`PNL_UNEXPLAINED`).

## Assumptions
Sequential ordering allocates cross effects to the later group. Settlement of dead trades
at yesterday's PV. Vanished trades (in yesterday's feed, absent today) are reported as a
note, not attributed.

## Limitations
No intraday trades or amendments (versions) yet. Ordering of groups is fixed.

## Validation tests
`tests/test_workflows.py::test_pnl_waterfall_is_exact`: steps sum to the total; the DATA
step is non-zero when a node is missing; challenger correlation above 0.8.
