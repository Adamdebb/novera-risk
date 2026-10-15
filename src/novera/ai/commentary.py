"""The morning commentary drafted automatically after an end-of-day run (decision round 38).

Called once the run is final and its database connection closed: the Analyst opens its own
connections, which DuckDB refuses to a process still holding the database read-write. The
configured LLM chain drafts it within ``commentary_timeout_seconds``; if every provider fails or
time runs out, the scripted provider writes it from the same stored numbers, so a commentary
always exists. The step never changes the run: a failure is returned for the job's notes and
written to the audit trail. ``commentary_auto`` switches it off. Record AI-001.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, TypeVar

import duckdb

from novera.config import Settings, get_settings
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import AuditEvent

AUTO_SESSION = "auto-commentary"  # session id marking the drafts made by this step
WRITE_RETRY_SECONDS = 10.0  # how long a write waits out the read-only connections of a late LLM draft

T = TypeVar("T")


def draft_after_run(db_path: str, run_id: str, settings: Settings | None = None) -> dict[str, Any]:
    """Draft and store the commentary for ``run_id``; returns what happened, never raises."""
    from novera.ai.analyst import Analyst
    from novera.ai.provider import ScriptedProvider

    settings = settings or get_settings()
    if not settings.commentary_auto:
        return {"status": "OFF"}
    db_path = str(db_path)
    error: str | None = None
    try:
        analyst = Analyst(db_path, settings=settings)
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(analyst.commentary, run_id, False)  # stored below, only if in time
        try:
            ans = future.result(timeout=settings.commentary_timeout_seconds)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)  # a late LLM answer is discarded
    except FutureTimeout:
        ans, error = None, f"LLM draft exceeded {settings.commentary_timeout_seconds:g}s"
    except Exception as e:  # noqa: BLE001 - every provider failed, or the run cannot be read
        ans, error = None, f"{type(e).__name__}: {e}"[:300]
    fallback = ans is None
    try:
        if fallback:
            analyst = Analyst(db_path, provider=ScriptedProvider(), settings=settings)
            ans = analyst.commentary(run_id, False)
        ans.session_id = AUTO_SESSION
        _with_retry(lambda: analyst.store(ans))
    except Exception as e:  # noqa: BLE001 - recorded, the run is unaffected
        msg = f"{type(e).__name__}: {e}"[:300]
        _audit(db_path, run_id, "COMMENTARY_FAILED", error=msg, llm_error=error)
        return {"status": "FAILED", "error": msg, "llm_error": error}
    _audit(db_path, run_id, "COMMENTARY_DRAFTED", answer_id=ans.answer_id, provider=ans.provider,
           fallback=fallback, llm_error=error)  # fmt: skip
    return {
        "status": "DRAFTED",
        "answer_id": ans.answer_id,
        "provider": ans.provider,
        "model": ans.model,
        "fallback": fallback,
        "llm_error": error,
    }


def note_for_job(outcome: dict[str, Any]) -> str:
    """One line for a job's notes."""
    if outcome.get("status") == "DRAFTED":
        how = "scripted fallback" if outcome.get("fallback") else outcome.get("provider")
        extra = f" ({outcome['llm_error']})" if outcome.get("llm_error") else ""
        return f"morning commentary drafted by {how}{extra}: {outcome['answer_id']}"
    if outcome.get("status") == "OFF":
        return "morning commentary not drafted: commentary_auto is off"
    return f"morning commentary failed: {outcome.get('error')}"


def _audit(db_path: str, run_id: str, kind: str, **payload: Any) -> None:
    try:

        def write() -> None:
            with DuckDBRepository(db_path) as repo:
                repo.save_audit_events([AuditEvent.now("analyst", kind, run_id, **payload)])

        _with_retry(write)
    except Exception:  # noqa: BLE001 - the audit write must not take the caller down
        pass


def _with_retry(write: Callable[[], T]) -> T:
    """Run a read-write step, retrying while DuckDB refuses it.

    A timed-out LLM draft keeps running in its abandoned thread, and its tools open read-only
    connections; DuckDB refuses a read-write connection in the same process while one is open.
    Those connections are short-lived, so the write is retried with a growing pause until it
    gets through or ``WRITE_RETRY_SECONDS`` have passed."""
    deadline = time.monotonic() + WRITE_RETRY_SECONDS
    pause = 0.05
    while True:
        try:
            return write()
        except duckdb.ConnectionException:
            if time.monotonic() + pause > deadline:
                raise
            time.sleep(pause)
            pause = min(pause * 2, 1.0)
