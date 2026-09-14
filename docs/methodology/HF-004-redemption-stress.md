# Redemption stress  (ID: HF-004)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Fund Risk |
| Approval status | Draft |
| Code | `novera.fund.modules.redemption_stress` |
| Last validated | 2026-09-14 |

## Definition
For each scenario (share redeemed, market haircut, investor types), each investor's
request is capped by its gate, placed on its next dealing date after notice, and the
cumulative cash due is compared with what can be liquidated by then: positions with days
to liquidate (MR-013) within the window, after the haircut, scaled to NAV, plus the
unencumbered cash buffer. Outputs coverage, shortfall and gated amounts per dealing date.
Scenarios: normal quarter (5%), stressed quarter (20%, 10% haircut), fund-of-funds run
(60% of FOF, 15%), severe (35%, 20%). Locked-up investors are excluded.
