"""In-process scheduler: fire the EOD run at a wall-clock time each business day, retry on
failure, alert when it still fails, and optionally advance the simulated world first.

No external infrastructure. ``run_once`` is the unit the scheduler loop and the CLI share.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from novera.config import Settings, get_settings
from novera.simulation.advance import advance_business_day
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.alerts import channels_from_settings, dispatch, run_failed_alert
from novera.workflows.eod import EODConfig, run_eod
from novera.workflows.runs import AuditEvent, new_run_id


@dataclass
class JobRecord:
    job_id: str
    started_at: datetime
    action: str  # EOD, ADVANCE_AND_EOD
    business_date: date | None = None
    run_id: str | None = None
    status: str = "RUNNING"  # RUNNING, COMPLETED, PARTIAL, FAILED, SKIPPED
    attempts: int = 0
    error: str | None = None
    finished_at: datetime | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "started_at": self.started_at.isoformat(),
            "action": self.action,
            "business_date": self.business_date.isoformat() if self.business_date else None,
            "run_id": self.run_id,
            "status": self.status,
            "attempts": self.attempts,
            "error": self.error,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "notes": self.notes,
        }


def run_once(
    repo: DuckDBRepository,
    advance: bool,
    cfg: EODConfig | None = None,
    max_attempts: int = 3,
    backoff_seconds: float = 5.0,
    settings: Settings | None = None,
) -> JobRecord:
    """One scheduled execution: advance the world if asked and the latest day already has a
    completed run, then run EOD with retries. Failures are recorded and alerted."""
    settings = settings or get_settings()
    job = JobRecord(new_run_id("job"), datetime.now(UTC), "ADVANCE_AND_EOD" if advance else "EOD")
    repo.init_schema()
    try:
        msnaps = repo.list_market_snapshots()
        if not msnaps:
            raise RuntimeError("no market data stored; run `novera simulate` first")
        latest_date = msnaps[-1][1]
        latest_run = repo.latest_run()
        if advance and latest_run is not None and latest_run.business_date >= latest_date:
            adv = advance_business_day(repo, cfg.firm_id if cfg else "GMB")
            job.notes.append(
                f"advanced to {adv.business_date} (bootstrap of {adv.bootstrap_from}); "
                + "; ".join(adv.changes)
            )
            latest_date = adv.business_date
        job.business_date = latest_date
        if latest_run is not None and latest_run.business_date == latest_date and not advance:
            job.status, job.notes = (
                "SKIPPED",
                job.notes + [f"run {latest_run.run_id} already covers {latest_date}"],
            )
            job.finished_at = datetime.now(UTC)
            repo.save_job(job.to_dict())
            return job
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            job.attempts = attempt
            try:
                res = run_eod(repo, cfg or EODConfig(), latest_date)
                job.run_id, job.status = res.run.run_id, res.run.status  # COMPLETED or PARTIAL
                last_error = None
                break
            except Exception as e:  # noqa: BLE001 - retried, then alerted
                last_error = e
                job.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
                if attempt < max_attempts:
                    time.sleep(backoff_seconds * attempt)
        if last_error is not None:
            job.status = "FAILED"
            dispatch(
                repo,
                [run_failed_alert(latest_date, str(last_error), job.attempts)],
                channels_from_settings(settings),
            )
    except Exception as e:  # noqa: BLE001
        job.status, job.error = "FAILED", f"{type(e).__name__}: {e}"
    job.finished_at = datetime.now(UTC)
    repo.save_job(job.to_dict())
    repo.save_audit_events(
        [
            AuditEvent.now(
                "scheduler",
                f"JOB_{job.status}",
                job.job_id,
                action=job.action,
                business_date=str(job.business_date),
                run_id=job.run_id,
                attempts=job.attempts,
            )
        ]
    )
    return job


def run_manual(
    repo: DuckDBRepository,
    actor: str,
    reason: str = "",
    business_date: date | None = None,
    advance: bool = False,
    cfg: EODConfig | None = None,
) -> JobRecord:
    """One full end-of-day run launched by a named person from the Admin page or the API
    (OPS-001). Same pipeline as the scheduler, one attempt, no RUN_FAILED alert: the person
    launching it sees the outcome. Recorded as a job (action MANUAL_EOD) and an audit event.

    ``business_date`` defaults to the latest market snapshot. With ``advance`` the simulated
    world (bank or fund) moves one business day first, as the scheduler does, but only when
    the latest day already has a completed run; otherwise the run covers that latest day and
    says so."""
    if not actor.strip():
        raise ValueError("name the actor: every manual run is audited")
    cfg = replace(cfg or EODConfig(), actor=actor.strip())
    action = "MANUAL_ADVANCE_AND_EOD" if advance else "MANUAL_EOD"
    job = JobRecord(new_run_id("job"), datetime.now(UTC), action)
    job.notes.append(f"launched by {cfg.actor}" + (f": {reason.strip()}" if reason.strip() else ""))
    repo.init_schema()
    try:
        msnaps = repo.list_market_snapshots()
        if not msnaps:
            raise RuntimeError("no market data stored; run `novera simulate` first")
        latest_date = msnaps[-1][1]
        latest_run = repo.latest_run()
        if advance:
            if latest_run is not None and latest_run.business_date >= latest_date:
                adv = advance_business_day(repo, cfg.firm_id)
                job.notes.append(
                    f"advanced to {adv.business_date} (bootstrap of {adv.bootstrap_from}); "
                    + "; ".join(adv.changes)
                )
                business_date = adv.business_date
            else:
                job.notes.append(f"{latest_date} has no completed run yet, so it is run instead of advancing")
                business_date = latest_date
        job.business_date = business_date or latest_date
        job.attempts = 1
        res = run_eod(repo, cfg, job.business_date)
        job.run_id, job.status = res.run.run_id, res.run.status  # COMPLETED or PARTIAL
        job.business_date = res.run.business_date
    except Exception as e:  # noqa: BLE001 - recorded on the job, shown to the person who launched it
        job.status = "FAILED"
        job.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
    job.finished_at = datetime.now(UTC)
    repo.save_job(job.to_dict())
    repo.save_audit_events(
        [
            AuditEvent.now(
                cfg.actor,
                f"JOB_{job.status}",
                job.job_id,
                action=job.action,
                business_date=str(job.business_date),
                run_id=job.run_id,
                attempts=job.attempts,
                reason=reason.strip(),
                source="manual",
            )
        ]
    )
    return job


def next_fire_time(now: datetime, hhmm: str) -> datetime:
    """Next weekday at HH:MM local time strictly after ``now``."""
    h, m = (int(x) for x in hhmm.split(":"))
    candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() > 4:
        candidate += timedelta(days=1)
    return candidate


def serve(
    db_path: str,
    hhmm: str,
    advance: bool,
    cfg: EODConfig | None = None,
    iterations: int | None = None,
    sleep: Any = time.sleep,
    clock: Any = None,
) -> Iterator[JobRecord]:
    """Blocking generator: waits for the next firing time, runs one job, yields it.
    ``iterations`` caps the number of firings (tests); ``clock`` and ``sleep`` are
    injectable so the loop can be driven without waiting."""
    clock = clock or (lambda: datetime.now().astimezone())
    fired = 0
    while iterations is None or fired < iterations:
        now = clock()
        at = next_fire_time(now, hhmm)
        sleep(max((at - now).total_seconds(), 0))
        with DuckDBRepository(db_path) as repo:
            yield run_once(repo, advance, cfg)
        fired += 1
