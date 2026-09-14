# Crowding  (ID: HF-006)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Fund Risk |
| Approval status | Draft |
| Code | `novera.fund.modules.crowding` |
| Last validated | 2026-09-14 |

## Definition
A crowding score in [0, 1] per underlying from a synthetic peer-ownership universe.
Reported: exposure-weighted fund score, share of gross in crowded names (score ≥ 0.7
and at least 3% of NAV), and a crowded exit horizon per position equal to days to
liquidate × (1 + 2 × score), the time to exit if peers sell at the same time.

## Limitations
Scores are assumptions; a real implementation would use 13F, prime-broker and
short-interest data.
