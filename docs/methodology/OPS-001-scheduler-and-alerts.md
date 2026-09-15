# Scheduler and alerts  (ID: OPS-001)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Control |
| Approval status | Draft |
| Code | `novera.workflows.scheduler`, `novera.workflows.alerts`, `novera.simulation.advance` |
| Last validated | 2026-09-14 |

## Scheduler
`novera schedule` runs in-process: it waits for the configured wall-clock time on business
days (weekends skipped), then executes one job. A job advances the simulated world by a
business day when the latest day already has a completed run (optional), runs the EOD
pipeline with up to three attempts and increasing backoff, records the attempt in the
job log with its run id or error, raises a CRITICAL alert if all attempts fail, and writes
an audit event. `--once` runs a single job immediately.

## Day advance (simulation only)
The next day's market is a historical bootstrap: one past day's factor moves, chosen by a
seed derived from the date, applied to the latest snapshot (absolute for rates and spreads,
relative otherwise). Factors missing from the latest snapshot are restored from history.
The portfolio gains about 3% new business struck at fair value. Earlier snapshots and runs
are untouched, so history never changes under a stored run.

## Alerts
| Kind | Severity | Recipients |
|---|---|---|
| NEW_BREACH | CRITICAL | limit owner |
| AUTO_ESCALATION | CRITICAL | escalation target, owner |
| BACK_WITHIN_LIMIT | INFO | owner |
| RUN_VERDICT (AMBER / RED) | WARNING / CRITICAL | Market Risk Control |
| RUN_SUMMARY | INFO | Market Risk |
| RUN_FAILED | CRITICAL | Risk IT, Market Risk Control |

Every alert is stored. Delivery goes through configured channels only (Slack incoming
webhook, SMTP email), for WARNING and above. Duplicates (same kind, subject and business
date) are suppressed. Delivery failures are recorded per channel, never raised.

## Testing a channel
`novera alert test` sends one WARNING alert of kind CHANNEL_TEST through every configured
channel and prints the delivery outcome per channel; the alert is stored with its
deliveries like any other, so the test leaves a record. The email channel uses STARTTLS
and a login only when `NOVERA_SMTP_USER` and `NOVERA_SMTP_PASSWORD` are set; without them
it speaks plain SMTP, which is what a local relay or a development sink expects.

## Validation tests
`tests/test_operations.py`: alert derivation, partial delivery, suppression, failed-run
alert, weekend skipping, advance-and-run, skip when already run, injected clock loop,
email channel message and TLS/login behaviour, `alert test` without a channel.
