"""Sweep runs and scheduler jobs left RUNNING by a process that died (decision round 36, OPS-001).

Every run and job records the process that owns it (host and process id). A RUNNING one is
abandoned when that process is no longer alive on this machine, or, when it was started on
another machine or predates owners, once it is older than ``MAX_AGE``. A live run is never
touched, including one in the sweeping process itself (the API serves requests on threads).
An abandoned run or job is marked FAILED with the reason in its summary or error and a
RUN_ABANDONED audit event; a job is also closed when the run it launched was abandoned.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import AuditEvent

MAX_AGE = timedelta(hours=2)
ACTOR = "run-sweeper"


def owner() -> dict[str, Any]:
    """The current process, as recorded on a run's summary or a job's payload."""
    return {"host": socket.gethostname(), "pid": os.getpid()}


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, owned by another user
        return True
    return True


def abandoned_reason(own: dict[str, Any] | None, started_at: datetime, now: datetime) -> str | None:
    """Why a RUNNING record is abandoned, or None while it may still be live."""
    if own and own.get("host") == socket.gethostname():
        pid = int(own.get("pid", -1))
        if pid == os.getpid() or process_alive(pid):
            return None
        return f"abandoned: process {pid} on {own['host']} is no longer running"
    # DuckDB hands TIMESTAMP columns back as naive local time; astimezone reads a naive value
    # as local, so both stored runs and ISO job timestamps compare in UTC.
    started = started_at.astimezone(UTC)
    if now - started > MAX_AGE:
        where = f"started on {own['host']}" if own else "with no recorded owner"
        return f"abandoned: RUNNING for more than {MAX_AGE} and {where}"
    return None


@dataclass
class SweepResult:
    runs: dict[str, str] = field(default_factory=dict)  # run_id -> reason
    jobs: dict[str, str] = field(default_factory=dict)  # job_id -> reason

    @property
    def empty(self) -> bool:
        return not self.runs and not self.jobs


def sweep_stuck(repo: DuckDBRepository, actor: str = ACTOR, now: datetime | None = None) -> SweepResult:
    """Mark every abandoned RUNNING run and job FAILED. Safe to call at the start of any run."""
    now = now or datetime.now(UTC)
    res = SweepResult()
    events: list[AuditEvent] = []
    for run in repo.list_running_runs():
        reason = abandoned_reason(run.summary.get("owner"), run.started_at, now)
        if reason is None:
            continue
        run.summary["abandoned"] = reason
        run.finish("FAILED")
        repo.save_run(run)
        res.runs[run.run_id] = reason
        events.append(AuditEvent.now(actor, "RUN_ABANDONED", run.run_id, reason=reason))
    for job in repo.load_jobs(limit=1000):
        if job.get("status") != "RUNNING":
            continue
        started = datetime.fromisoformat(job["started_at"])
        reason = abandoned_reason(job.get("owner"), started, now)
        if reason is None and job.get("run_id") in res.runs:
            reason = f"abandoned with its run {job['run_id']}"
        if reason is None:
            continue
        job.update(status="FAILED", error=reason, finished_at=now.isoformat())
        repo.save_job(job)
        res.jobs[job["job_id"]] = reason
        events.append(AuditEvent.now(actor, "RUN_ABANDONED", job["job_id"], reason=reason))
    if events:
        repo.save_audit_events(events)
    return res
