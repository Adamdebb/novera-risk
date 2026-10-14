"""Stuck runs and scheduler jobs are swept (decision round 36, OPS-001)."""

import os
import socket
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

import pytest
from typer.testing import CliRunner

from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import RunRecord
from novera.workflows.scheduler import run_once
from novera.workflows.sweep import MAX_AGE, owner, sweep_stuck

HOST = socket.gethostname()


@pytest.fixture()
def dead_pid() -> int:
    """The id of a process that has already exited."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


@pytest.fixture()
def repo():
    r = DuckDBRepository(":memory:")
    r.init_schema()
    yield r
    r.close()


def _run(run_id: str, own: dict | None, started: datetime, status: str = "RUNNING") -> RunRecord:
    run = RunRecord(run_id, "EOD", date(2026, 9, 15), "pf", "mkt", None, "USD", {}, {}, started_at=started)
    run.status = status
    if own is not None:
        run.summary["owner"] = own
    return run


def test_sweep_closes_dead_runs_and_spares_live_ones(repo, dead_pid) -> None:
    """A RUNNING run whose process died on this machine is FAILED at once; one owned by a live
    process (here, the sweeping process itself) is left alone however old; one from another
    machine or with no owner waits for MAX_AGE; finished runs are never read."""
    now = datetime.now(UTC)
    old, recent = now - MAX_AGE - timedelta(minutes=1), now - timedelta(minutes=5)
    cases = {
        "dead_here": _run("dead_here", {"host": HOST, "pid": dead_pid}, recent),
        "live_here": _run("live_here", owner(), old),
        "elsewhere_recent": _run("elsewhere_recent", {"host": "other-host", "pid": 1}, recent),
        "elsewhere_old": _run("elsewhere_old", {"host": "other-host", "pid": 1}, old),
        "no_owner_recent": _run("no_owner_recent", None, recent),
        "no_owner_old": _run("no_owner_old", None, old),
        "done": _run("done", {"host": HOST, "pid": dead_pid}, old, status="COMPLETED"),
    }
    for r in cases.values():
        repo.save_run(r)
    res = sweep_stuck(repo, now=now)
    assert set(res.runs) == {"dead_here", "elsewhere_old", "no_owner_old"}
    assert f"process {dead_pid}" in res.runs["dead_here"]
    for rid in res.runs:
        stored = repo.load_run(rid)
        assert stored.status == "FAILED" and stored.summary["abandoned"] == res.runs[rid]
        assert stored.finished_at is not None
    for rid in ("live_here", "elsewhere_recent", "no_owner_recent"):
        assert repo.load_run(rid).status == "RUNNING"
    assert repo.load_run("done").status == "COMPLETED"
    ev = repo.load_audit_events()
    assert set(ev[ev["event_type"] == "RUN_ABANDONED"]["subject"]) == set(res.runs)
    assert sweep_stuck(repo, now=now).empty  # a second sweep finds nothing


def test_sweep_closes_jobs_with_their_runs(repo, dead_pid) -> None:
    """A job is closed when its own process died, or when the run it launched was abandoned."""
    now = datetime.now(UTC)
    repo.save_run(_run("r1", {"host": HOST, "pid": dead_pid}, now))
    base = {"action": "EOD", "business_date": "2026-09-15", "attempts": 1, "error": None, "notes": []}
    repo.save_job({**base, "job_id": "j_dead", "started_at": now.isoformat(), "run_id": None,
                   "status": "RUNNING", "owner": {"host": HOST, "pid": dead_pid}})  # fmt: skip
    repo.save_job({**base, "job_id": "j_linked", "started_at": now.isoformat(), "run_id": "r1",
                   "status": "RUNNING", "owner": {"host": "other-host", "pid": 1}})  # fmt: skip
    repo.save_job({**base, "job_id": "j_live", "started_at": now.isoformat(), "run_id": None,
                   "status": "RUNNING", "owner": owner()})  # fmt: skip
    res = sweep_stuck(repo, now=now)
    assert set(res.jobs) == {"j_dead", "j_linked"} and "with its run r1" in res.jobs["j_linked"]
    jobs = {j["job_id"]: j for j in repo.load_jobs()}
    assert jobs["j_dead"]["status"] == "FAILED" and jobs["j_dead"]["finished_at"]
    assert jobs["j_live"]["status"] == "RUNNING"


def test_jobs_are_saved_running_with_their_owner(repo) -> None:
    """A scheduler job is stored RUNNING as it starts (so a killed one is visible and swept)
    and carries this process as its owner; here it fails at once (no market data)."""
    seen: list[dict] = []
    original = repo.save_job
    repo.save_job = lambda d: (seen.append(dict(d)), original(d))[1]  # type: ignore[method-assign]
    job = run_once(repo, advance=False, max_attempts=1, backoff_seconds=0)
    assert seen[0]["status"] == "RUNNING" and seen[0]["owner"] == {"host": HOST, "pid": os.getpid()}
    assert job.status == "FAILED" and seen[-1]["status"] == "FAILED"


def test_cli_sweep_reports_what_it_changed(tmp_path, dead_pid, monkeypatch) -> None:
    from novera.cli import app
    from novera.config import get_settings

    db = tmp_path / "s.duckdb"
    with DuckDBRepository(db) as r:
        r.init_schema()
        r.save_run(_run("stuck", {"host": HOST, "pid": dead_pid}, datetime.now(UTC)))
    monkeypatch.setenv("NOVERA_DB_PATH", str(db))
    get_settings.cache_clear()
    try:
        out = CliRunner().invoke(app, ["run", "sweep"])
        assert out.exit_code == 0 and "run stuck marked FAILED" in out.output
        out = CliRunner().invoke(app, ["run", "sweep"])
        assert "nothing stuck" in out.output
    finally:
        get_settings.cache_clear()
