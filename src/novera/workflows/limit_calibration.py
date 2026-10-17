"""Seeding and recalibrating limits from a target utilisation (decision round 40, MR-017).

- ``seed_limits``: when a firm is created. A day-1 EOD runs on a throwaway copy of the database
  (alerts off), its limit table gives each limit's measure, and the limits are set APPROVED from
  their targets. The real day-1 run then reproduces the same measures, so every limit opens at its
  target and the story limits at their designed utilisation.
- ``propose_recalibration``: after a methodology change. DRAFT versions from a stored run,
  effective the next business day, a limit in breach left alone.
- ``decide_draft``: the limit's approver (``APPROVERS_BY_LEVEL``, never the requester) approves or
  rejects; an approved draft ends the old version the day before and becomes current, the old one
  kept in the history. A limit with an open breach is refused: that is an increase request.

None of this runs from the EOD or the scheduler: positions changing never moves a limit.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from novera.domain.breaches import APPROVERS_BY_LEVEL
from novera.domain.limits import Limit, LimitStatus, LimitType
from novera.limits.calibration import Proposal, propose
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import AuditEvent, new_run_id


def _next_business_day(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _rationale(p: Proposal, source: str) -> str:
    kind = "story utilisation" if p.story else "target"
    note = "" if p.reason in ("story target", "type target") else f", {p.reason}"
    return f"calibrated to {kind} {p.target:.0%} on {source} (measure {p.current:,.4g}{note})"


def seed_limits(
    db_path: str | Path, firm_id: str = "GMB", actor: str = "limit-calibration"
) -> list[Proposal]:
    """Set every limit from its target on a day-1 dry run (see module docstring)."""
    from novera.workflows.eod import EODConfig, run_eod

    tmp = Path(tempfile.mkdtemp(prefix="novera-calibration-"))
    try:
        copy = tmp / "calibration.duckdb"
        shutil.copyfile(db_path, copy)
        with DuckDBRepository(copy) as repo:
            day1 = min(d for _, d, *_ in repo.list_portfolio_snapshots())
            res = run_eod(
                repo,
                EODConfig(
                    firm_id=firm_id, actor=actor, alerts=False, factor_stress=False, stress_explain=False
                ),
                day1,
                runs_dir=tmp / "runs",
            )
            measured = repo.load_run_frame(res.run.run_id, "limits")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    with DuckDBRepository(db_path) as repo:
        proposals = propose(repo.load_limits(), measured, guard_breaches=False)
        source = f"the day-1 dry run of {day1}"
        repo.save_limits(
            [
                p.limit.model_copy(
                    update={
                        "amount": p.new_amount,
                        "appetite": p.limit.appetite or p.limit.amount,  # the designed amount, kept (40.6)
                        "rationale": _rationale(p, source),
                    }
                )
                for p in proposals
                if p.new_amount
            ]  # fmt: skip
        )
        repo.save_audit_events(
            [
                AuditEvent.now(
                    actor,
                    "LIMITS_CALIBRATED",
                    firm_id,
                    business_date=str(day1),
                    calibrated=sum(1 for p in proposals if p.new_amount),
                    left=sum(1 for p in proposals if not p.new_amount),
                )
            ]  # fmt: skip
        )
    return proposals


def propose_recalibration(
    repo: DuckDBRepository, run_id: str, actor: str, limit_types: set[LimitType] | None = None
) -> tuple[list[dict[str, Any]], list[Proposal]]:
    """DRAFT versions from a stored run's measures; returns (drafts, proposals left alone)."""
    if not actor.strip():
        raise ValueError("name the actor: every recalibration is audited")
    run = repo.load_run(run_id)
    measured = repo.load_run_frame(run_id, "limits")
    limits = [
        lim
        for lim in repo.load_limits(on=run.business_date)
        if not limit_types or lim.limit_type in limit_types
    ]
    pending = {d["limit_id"]: d for d in repo.load_limit_drafts(status="PENDING")}
    effective = _next_business_day(run.business_date)
    drafts, left, events = [], [], []
    for p in propose(limits, measured, guard_breaches=True):
        if not p.new_amount or abs(p.new_amount - p.limit.amount) <= 0.01 * p.limit.amount:
            left.append(p if p.new_amount is None else replace(p, reason="already at target"))
            continue
        if p.limit.limit_id in pending:  # a newer proposal replaces an undecided one
            old = pending[p.limit.limit_id] | {"status": "WITHDRAWN"}
            repo.save_limit_draft(old)
        new = p.limit.model_copy(
            update={"amount": p.new_amount, "status": LimitStatus.DRAFT, "effective_from": effective,
                    "effective_to": None, "approver": "", "rationale": _rationale(p, f"run {run_id}")}
        )  # fmt: skip
        d = {
            "draft_id": new_run_id("lcal"),
            "limit_id": p.limit.limit_id,
            "status": "PENDING",
            "requested_by": actor.strip(),
            "requested_at": datetime.now(UTC).isoformat(),
            "reference_run": run_id,
            "current": p.current,
            "target": p.target,
            "story": p.story,
            "old_amount": p.limit.amount,
            "new_amount": p.new_amount,
            "effective_from": effective.isoformat(),
            "limit": new.model_dump(mode="json"),
        }
        repo.save_limit_draft(d)
        drafts.append(d)
        events.append(
            AuditEvent.now(
                actor.strip(),
                "LIMIT_RECALIBRATION_PROPOSED",
                p.limit.limit_id,
                draft_id=d["draft_id"],
                old=p.limit.amount,
                new=p.new_amount,
                run_id=run_id,
            )
        )
    if events:
        repo.save_audit_events(events)
    return drafts, left


def decide_draft(
    repo: DuckDBRepository, draft_id: str, approver: str, approve: bool, comment: str = ""
) -> dict[str, Any]:
    d = next((x for x in repo.load_limit_drafts() if x["draft_id"] == draft_id), None)
    if d is None:
        raise KeyError(f"no limit draft {draft_id}")
    if d["status"] != "PENDING":
        raise ValueError(f"draft {draft_id} is {d['status']}, not PENDING")
    if approver.strip().lower() == d["requested_by"].strip().lower():
        raise ValueError("the person who proposed a recalibration cannot decide it")
    current = next((lim for lim in repo.load_limits() if lim.limit_id == d["limit_id"]), None)
    if current is None:
        raise KeyError(f"limit {d['limit_id']} no longer exists")
    allowed = APPROVERS_BY_LEVEL.get(current.scope.level, ("CRO",))
    if approve and approver not in allowed:
        raise ValueError(
            f"{approver} cannot approve a {current.scope.level.value} limit; allowed: {', '.join(allowed)}"
        )
    if approve and repo.load_breaches(open_only=True, limit_id=d["limit_id"]):
        raise ValueError(f"{d['limit_id']} has an open breach: raise it through an increase request")
    if approve:
        new = Limit(**d["limit"]).model_copy(update={"status": LimitStatus.APPROVED, "approver": approver})
        old = current.model_copy(update={"effective_to": new.effective_from - timedelta(days=1)})
        repo.supersede_limit(old, new)
    d = d | {"status": "APPROVED" if approve else "REJECTED", "decided_by": approver,
             "decided_at": datetime.now(UTC).isoformat(), "comment": comment}  # fmt: skip
    repo.save_limit_draft(d)
    repo.save_audit_events(
        [
            AuditEvent.now(
                approver,
                "LIMIT_RECALIBRATION_" + ("APPROVED" if approve else "REJECTED"),
                d["limit_id"],
                draft_id=draft_id,
                old=d["old_amount"],
                new=d["new_amount"],
                comment=comment,
            )
        ]  # fmt: skip
    )
    return d
