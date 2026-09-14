# Factor exposures  (ID: HF-003)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Fund Risk |
| Approval status | Draft |
| Code | `novera.fund.modules.factor_betas` |
| Last validated | 2026-09-14 |

## Definition
Ordinary least squares of each strategy's scenario P&L series (today's book under each
historical daily move, MR-002) on standardised factor moves for the same dates: US
equity, USD 10Y rate, HY credit spread, EUR/USD, Brent, Bitcoin, SPX implied vol. Betas
are P&L per one-standard-deviation factor move, also as % of NAV, with t-statistics, R²
and residual volatility.

## Limitations
Hypothetical, static-book betas; collinear factors share loadings.

## Validation tests
`tests/test_fund.py::test_factor_betas_recover_a_known_loading`.
