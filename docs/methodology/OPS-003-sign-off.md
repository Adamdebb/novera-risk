# Sign-off and release  (ID: OPS-003)

| Field | Value |
|---|---|
| Version | 1.1.0 |
| Owner | Market Risk Control |
| Approval status | Draft |
| Code | `novera.workflows.signoff`, `GET /runs/{run_id}/signoff`, `POST .../signoff/{metric}/sign` and `/reject`, `GET/POST /admin/signoff/policy`, `GET /signoff/queue`, `novera signoff` |
| Last validated | 2026-09-27 |

## Definition
After the end-of-day run has stored its results, named people sign each metric that the
firm's policy requires before the run counts as released. Sign-off is a judgement recorded
next to the run; it never edits a stored result (rule 5).

## Metrics
| Id | Title | Record | Expected signer | Required by default | Face |
|---|---|---|---|---|---|
| DATA_QUALITY | Data-quality verdict | DQ-001 | Head of Market Risk Control | yes | both |
| VAR | VaR and expected shortfall | MR-002 | Head of Market Risk | yes | both |
| STRESS | Stress results | MR-005 | Head of Market Risk | yes | both |
| LIMITS | Limit utilisation and breaches | MR-006 | Head of Market Risk | yes | both |
| PNL | P&L explain | MR-007 | Head of Product Control | yes | both |
| BACKTEST | Backtest | MR-011 | Head of Model Validation | no | both |
| CONCENTRATION | Concentration and liquidity | MR-012 | Head of Market Risk | no | both |
| COUNTERPARTY | Counterparty exposure and CVA | CR-001 | Head of Counterparty Risk | yes | both |
| CAPITAL | Regulatory capital | REG-001 | Head of Regulatory Reporting | no | bank |
| FUND | Fund measures | HF-001 | Chief Risk Officer | yes | fund |

Each metric names the run-summary keys it covers; those values are frozen with the signature
so the record shows what was signed, even if a later re-run shows different numbers.

## Policy
The Admin page (and `POST /admin/signoff/policy`) sets which metrics are required. Until a
policy is saved the defaults above apply. A change replaces the whole set, names an actor,
and is audited (SIGNOFF_POLICY_CHANGED with the sets before and after). It applies to every
run not yet released, including past ones: release status is computed from the current
policy, never stored as a snapshot.

## Rules
- Only COMPLETED and PARTIAL runs of type EOD or RERUN can be signed. Metrics that do not apply
  to the firm's face are rejected.
- On a PARTIAL run a metric whose stage failed is UNAVAILABLE, with the stage and its error:
  COUNTERPARTY reads the counterparty engine, CAPITAL the regulatory engine, FUND the fund
  engine, and LIMITS all three plus the second limit pass, because the counterparty and fund
  limits are measured there. An unavailable metric cannot be signed (it can be rejected), so a
  policy that requires it keeps the run PENDING. The re-run of the failed stage (OPS-002) is a
  new run, signed in its place.
- **Sign** needs an actor; allowed from PENDING or REJECTED; a second signature on a SIGNED
  metric is refused. On a RED verdict, signing DATA_QUALITY needs a comment and is flagged
  as an override (DQ-001: RED results are not published without an override event).
- **Reject** needs an actor and a comment; allowed from PENDING or SIGNED (withdrawing a
  signature); a second rejection is refused.
- **Release status** per run: NO_POLICY when nothing is required; BLOCKED while any required
  metric is REJECTED; RELEASED when every required metric is SIGNED; PENDING otherwise. The
  signature that completes the release writes the release row (who, when, whether any
  signature was an override) and a RUN_RELEASED event. Rejecting a required metric on a
  released run deletes the row and writes RUN_RELEASE_WITHDRAWN.
- Actors are named, not authenticated (decision 6.3); the expected signer is shown, not
  enforced.

## Where it shows
The "Sign-off" page: release status, every metric with its value, status, signer and comment,
a sign/reject form, the sign-off trail from the audit events, and the release queue of recent
EOD runs. Every page header shows the run's release status next to its verdict. The Admin
page holds the policy.

## Assumptions and limitations
No authentication, so segregation of duties (signer differs from the person who fixed the
data) is a convention. No reminders or deadlines. A policy change does not re-open a run whose
release row exists; it changes the computed status, and the row stays as the historical
record. Optional metrics can be signed but never affect release.

## Validation tests
`test_signoff_policy_release_and_override` (rules, frozen values, release and withdrawal, RED
override, policy change, audit events, the run untouched), `test_signoff_endpoints` (routes,
409 conflicts, policy narrowing releases a run), `test_a_failed_late_engine_leaves_a_partial_run`
(an unavailable metric cannot be signed; the re-run can), the page in `tests/test_ui.py`.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-09-16 | Sign-off metrics, policy, release status, page and Admin section (decision 24.1) | Novera |
| 1.1.0 | 2026-09-27 | PARTIAL runs signable; metrics of a failed stage UNAVAILABLE (decision 32.4) | Novera |
