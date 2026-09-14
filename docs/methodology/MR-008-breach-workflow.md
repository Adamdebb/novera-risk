# Breach workflow and temporary limit increases  (ID: MR-008)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Control |
| Approval status | Draft |
| Code | `novera.limits.workflow` |
| Last validated | 2026-09-14 |

## Lifecycle
```
OPEN --acknowledge--> ACKNOWLEDGED --escalate--> ESCALATED --close--> CLOSED
  \\-------------------escalate / auto-escalate--------^        ^
   \\----------------------------------------close--------------/
```
- **Raised** by the end-of-day run when a limit's status is BREACH and no open breach
  exists for that limit. One open breach per limit.
- **Updated** on each later run that still breaches: consecutive days, latest and peak
  utilisation. When the run shows the limit back within threshold the breach is flagged
  "within limit, close pending" and stays open until a person closes it.
- **Auto-escalation** by the monitor: an OPEN breach seen again on a second business day
  (nobody acknowledged), or an ACKNOWLEDGED breach on its third consecutive breaching
  run. Target by level: firm to Board Risk Committee, business to CRO, desk and book to
  Head of Market Risk, counterparty to Head of Counterparty Risk.
- **Close** needs a reason. RISK_REDUCED only when the latest run is within limit;
  TEMPORARY_INCREASE_APPROVED only when an approved increase is in force; FALSE_POSITIVE
  needs an explanatory comment; LIMIT_RETIRED is free-form.

## Temporary limit increases (approval matrix)
| Limit level | May approve |
|---|---|
| Firm, business, legal entity | CRO |
| Desk, book, trader, asset class, currency, product | Head of Market Risk or CRO |
| Counterparty | Head of Counterparty Risk or CRO |
| Any increase above +25% of the base amount | CRO only |

The requester cannot approve their own request. Maximum duration 45 calendar days.
Approved increases replace the limit amount in monitoring while in force (the run
records the base amount and the increase id) and expire automatically.

## Audit
Every transition writes a breach action and an audit event with actor, comment, run id
and, where relevant, escalation target or close reason. Actors are named but not
authenticated until roles arrive in Phase 5.

## Validation tests
`tests/test_breach_workflow.py`.
