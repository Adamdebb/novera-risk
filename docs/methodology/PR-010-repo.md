# Repo and reverse repo valuation  (ID: PR-010)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.breadth.price_repo` |
| Last validated | 2026-09-14 |

## Definition
The cash leg is marked against the zero curve. For a reverse repo (BUY: we lend cash `N`
against government-bond collateral) PV = `N (1 + r τ) DF(T_end) − N DF(T_start)`, where `r`
is the contractual repo rate, `τ` the term in the repo day count (ACT/360) and `T_start` is
zero once the cash has gone out. A repo (SELL) is the negative. PV is therefore zero when the
rate is struck at the curve-implied fair rate `(DF(T_start)/DF(T_end) − 1)/τ`, and the position
carries DV01 on the term only.

## Inputs
Zero curve of the cash currency; repo rate, dates, haircut and collateral id from the
instrument. `collateral_required = N / (1 − haircut)` is reported for exposure purposes.

## Assumptions and limitations
Collateral is not re-priced inside the market-risk PV: the collateral is the counterparty's
asset and its market risk sits with them; the platform records the collateral requirement for
the counterparty and liquidity views. No repo-specific curve (GC/special spread) and no
re-hypothecation modelling. Repos are excluded from SA-CCR as securities-financing
transactions.

## Validation tests
`test_repo_zero_at_fair_rate_and_dv01_sign`: PV vanishes at the fair rate; a reverse repo
loses when short rates rise; collateral requirement equals `N/(1−h)`.
