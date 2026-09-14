# Funding cash ladder  (ID: REG-006)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Treasury Risk |
| Approval status | Draft |
| Code | `novera.regulatory.cash_ladder` |
| Last validated | 2026-09-14 |

## Definition
Contractual cashflows of every live trade from the pricers (coupons, principals, swap
legs projected off the curve, forward settlements), bucketed at 1 week, 1, 3, 6 months,
1, 2 years and beyond, per currency and converted to the reporting currency, with
inflows, outflows, net and cumulative net. Options and futures carry no contractual
flows (daily settled or premium paid).

## Limitations
No behavioural assumptions, no funding of margin, no repo or deposit book yet.
