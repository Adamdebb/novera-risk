# Scheduler and alerts  (ID: OPS-001)

| Field | Value |
|---|---|
| Version | 1.2.0 |
| Owner | Market Risk Control |
| Approval status | Draft |
| Code | `novera.workflows.scheduler`, `novera.workflows.alerts`, `novera.simulation.advance`, `GET /admin/run/options`, `POST /admin/run` |
| Last validated | 2026-09-27 |

## Scheduler
`novera schedule` runs in-process: it waits for the configured wall-clock time on business
days (weekends skipped), then executes one job. It serves one firm face per process: the
bank database by default, the fund database with `--fund`; run both for both. The firm id
is read from the database, so the job and the day advance use that firm's organisation. A job advances the simulated world by a
business day when the latest day already has a completed run (optional), runs the EOD
pipeline with up to three attempts and increasing backoff, records the attempt in the
job log with its run id or error, raises a CRITICAL alert if all attempts fail, and writes
an audit event. `--once` runs a single job immediately; it exits 0 when the job is COMPLETED
or SKIPPED, 2 when it is PARTIAL and 1 when it FAILED.

Only an exception retries. A run that stored its core results and then lost the regulatory,
counterparty or fund engine ends PARTIAL (docs/07-eod-workflow.md, "Failure behaviour"): the job
is PARTIAL too, is not retried (a retry is a whole new run of several minutes that would most
likely fail the same way), and a RUN_PARTIAL alert names the failed stage. The fix is a partial
re-run of that stage (OPS-002). A PARTIAL run covers its day, so the next scheduled job
advances past it.

## Manual run
The same pipeline can be launched by hand from the Admin page ("Runs", mode "Full end-of-day
run") or `POST /admin/run`, for the firm whose database the page is on (bank or fund). The
launcher names themselves and a reason; the business date defaults to the latest market
snapshot, and the day advance below can be requested for either firm. One attempt, no
RUN_FAILED alert: the
outcome is shown to the launcher, and a failure comes back as a FAILED job with its error
rather than an exception. The job is stored with action MANUAL_EOD (or
MANUAL_ADVANCE_AND_EOD) and an audit event under the launcher's name, so manual and
scheduled runs sit in one log. The run itself is an ordinary EOD run: it becomes the latest,
synchronises breaches and raises the alerts a scheduled run would. A partial re-run of one
stage (OPS-002) is the other mode of the same Admin configuration.

## Day advance (simulation only)
The next day's market is a historical bootstrap: one past day's factor moves, chosen by a
seed derived from the date, applied to the latest snapshot (absolute for rates and spreads,
relative otherwise). Factors missing from the latest snapshot are restored from history.
The portfolio gains about 3% new business struck at fair value, drawn from the firm's own
template (`face_of`): the bank's desk mix with its dealers and CCPs, or the fund's strategy
mix booked with its prime brokers. Earlier snapshots and runs are untouched, so history never
changes under a stored run.

## Alerts
| Kind | Severity | Recipients |
|---|---|---|
| NEW_BREACH | CRITICAL | limit owner |
| AUTO_ESCALATION | CRITICAL | escalation target, owner |
| BACK_WITHIN_LIMIT | INFO | owner |
| RUN_VERDICT (AMBER / RED) | WARNING / CRITICAL | Market Risk Control |
| RUN_PARTIAL | WARNING | Risk IT, Market Risk Control |
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
email channel message and TLS/login behaviour, `alert test` without a channel, manual run
(job, audit event, advance, failure recorded). `tests/test_workflows.py`:
`test_a_failed_late_engine_leaves_a_partial_run` (RUN_PARTIAL alert, no retry of a PARTIAL day). `tests/test_fund.py`: the fund advances with its
own template and prime brokers, then `run_once` runs the new day. `tests/test_api.py`: run options and request
validation. `tests/test_ui.py`: the Runs configuration renders both modes.
