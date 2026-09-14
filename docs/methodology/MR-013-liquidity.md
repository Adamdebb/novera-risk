# Liquidity  (ID: MR-013)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.liquidity` |
| Last validated | 2026-09-14 |

## Definition
Days to liquidate = |position| / (20% participation × average daily volume), with volume
and bid-ask assumptions per product (synthetic in the demo, thin for far-dated Brent and
index options). Positions are bucketed at 1, 5 and 10 days by |PV|. Bid-ask cost is half
the spread on notional for linear OTC products and on |PV| otherwise.

Liquidity-adjusted VaR = VaR × √max(horizon, 1) + bid-ask cost, where the horizon is the
|PV|-weighted days to liquidate (a simplified Bangia et al. adjustment).

## Limitations
Assumptions are not market data; the module demonstrates the measure, not calibrated
liquidity risk. No market-impact model beyond participation.

## Validation tests
`tests/test_measures.py::test_liquidity_measures`.
