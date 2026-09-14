# Independent challenger reconciliation  (ID: MR-009)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Control |
| Approval status | Draft |
| Code | `novera.reconciliation.reconcile` |
| Last validated | 2026-09-14 |

## Definition
Reconciles an external risk feed (per-trade PV and component VaR, with metadata) against
a Novera run for the same business date, and attributes the VaR gap to causes a risk
manager can act on. Nothing is estimated: each attribution step is a sum of stored
differences or a documented rerun of the engine.

## Method
Let `d_i = official contribution_i − Novera contribution_i`.

| Step | Cause | Amount | Test |
|---|---|---|---|
| 1 | SCOPE | Σ d_i over trades in one system only | outer join on trade id |
| 2 | MARKET_DATA | Σ d_i over trades whose official PV equals Novera's PV repriced on the previous day's market | reprice through the engine, tolerance max(1,000; 1e-4 relative) |
| 3 | PRICING_MODEL | Σ d_i over remaining trades whose PV differs beyond tolerance | grouped by product for the finding |
| 4 | METHODOLOGY | Σ d_i over common trades with matching PV | verified by rerunning Novera's VaR on that set with the feed's window |
| 5 | RESIDUAL | official total − Novera total − Σ steps | non-zero only if the feed's total is not the sum of its contributions |

Component VaR re-allocates when the trade set changes, so scope removals also move the
common trades' contributions; the rerun in step 4 separates the window effect from that
re-allocation and reports both.

## Simulated feed (demo)
`novera vendor-feed` derives an "official" feed from a run with four planted differences:
one book excluded, one asset class valued on the previous day's market, equity options
priced with flat ATM volatility, and a 250-day VaR window. The reconciliation is expected
to recover all four.

## Limitations
Cause detection is structural, not causal: a PV difference is labelled MARKET_DATA only
when the previous-day market reproduces it exactly. Other stale sources (intraday
snapshots, a different vendor curve) would show as PRICING_MODEL. Sensitivities are not
yet reconciled.

## Validation tests
`tests/test_operations.py::test_vendor_feed_and_reconciliation`: attribution sums to the
gap, scope and stale-market causes detected, an identical feed reconciles to zero.
