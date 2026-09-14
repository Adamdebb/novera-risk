"""Breach lifecycle and temporary limit increases. Methodology record MR-008.

All state changes go through this module so every transition is validated and produces
an audit event. Actors are named, not authenticated (Phase 5 adds roles).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pandas as pd

from novera.domain.breaches import (
    APPROVERS_BY_LEVEL,
    Breach,
    BreachAction,
    BreachActionType,
    BreachStatus,
    CloseReason,
    IncreaseStatus,
    LimitIncrease,
)
from novera.domain.enums import HierarchyLevel
from novera.domain.limits import Limit
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import AuditEvent, new_run_id

MODEL_VERSION = "1.0.0"

AUTO_ESCALATE_UNACKNOWLEDGED_AFTER = 1  # runs: breach seen again and nobody acknowledged
AUTO_ESCALATE_ACKNOWLEDGED_AFTER = 3  # consecutive breaching runs while acknowledged
ESCALATION_TARGET = {
    "FIRM": "Board Risk Committee",
    "BUSINESS": "CRO",
    "DESK": "Head of Market Risk",
    "BOOK": "Head of Market Risk",
    "COUNTERPARTY": "Head of Counterparty Risk",
}


def _finite(x) -> float:
    """Utilisation as a float; NaN (limit could not be measured) becomes 0."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v != v else v


class WorkflowError(ValueError):
    """A transition or approval that the rules do not allow."""


# --- effective limits ------------------------------------------------------------------


def effective_limits(
    limits: list[Limit], increases: list[LimitIncrease], on: date
) -> tuple[list[Limit], dict[str, str]]:
    """Replace amounts with approved increases in force on ``on``. Returns the limits and a
    map limit_id -> increase_id for the ones that were raised."""
    active: dict[str, LimitIncrease] = {}
    for inc in increases:
        if inc.in_force(on) and (
            inc.limit_id not in active or inc.new_amount > active[inc.limit_id].new_amount
        ):
            active[inc.limit_id] = inc
    out: list[Limit] = []
    for lim in limits:
        inc = active.get(lim.limit_id)
        out.append(lim.model_copy(update={"amount": inc.new_amount}) if inc else lim)
    return out, {k: v.increase_id for k, v in active.items()}


def expire_increases(repo: DuckDBRepository, on: date) -> list[AuditEvent]:
    events: list[AuditEvent] = []
    for inc in repo.load_increases(status="APPROVED"):
        if inc.expires_on < on:
            inc.status = IncreaseStatus.EXPIRED
            repo.save_increase(inc)
            events.append(
                AuditEvent.now("limit-monitor", "INCREASE_EXPIRED", inc.increase_id, limit_id=inc.limit_id)
            )
    return events


# --- breach sync after a run -------------------------------------------------------------


@dataclass
class SyncOutcome:
    raised: list[Breach] = field(default_factory=list)
    updated: list[Breach] = field(default_factory=list)
    auto_escalated: list[Breach] = field(default_factory=list)
    back_within_limit: list[Breach] = field(default_factory=list)
    events: list[AuditEvent] = field(default_factory=list)


def sync_breaches(
    repo: DuckDBRepository, run_id: str, business_date: date, limit_table: pd.DataFrame
) -> SyncOutcome:
    """Raise, update and auto-escalate breaches from a run's limit table."""
    out = SyncOutcome()
    if limit_table.empty:
        return out
    open_by_limit = {b.limit_id: b for b in repo.load_breaches(open_only=True)}
    breached = limit_table[limit_table["status"] == "BREACH"]
    for _, r in breached.iterrows():
        util = _finite(r["utilisation"])
        b = open_by_limit.get(r["limit_id"])
        if b is None:
            b = Breach(
                breach_id=new_run_id("brc"),
                limit_id=r["limit_id"],
                limit_type=r["limit_type"],
                level=r["level"],
                entity_id=r["entity_id"],
                owner=r["owner"],
                first_run_id=run_id,
                latest_run_id=run_id,
                first_date=business_date,
                latest_date=business_date,
                first_utilisation=util,
                latest_utilisation=util,
                peak_utilisation=util,
            )
            repo.save_breach(b)
            _record(
                repo,
                out.events,
                b,
                "limit-monitor",
                BreachActionType.RAISED,
                run_id=run_id,
                comment=f"utilisation {util:.0%}",
            )
            out.raised.append(b)
            continue
        if b.latest_run_id == run_id or business_date < b.latest_date:
            continue  # same run, or a backfill of an earlier date: never rewind breach state
        if b.latest_date < business_date:
            b.consecutive_days += 1
        b.latest_run_id, b.latest_date = run_id, business_date
        b.latest_utilisation, b.peak_utilisation = util, max(b.peak_utilisation, util)
        b.within_limit_on_latest_run = False
        _record(
            repo,
            out.events,
            b,
            "limit-monitor",
            BreachActionType.UPDATED,
            run_id=run_id,
            comment=f"still breaching, utilisation {util:.0%}, day {b.consecutive_days}",
        )
        out.updated.append(b)
        target = ESCALATION_TARGET.get(b.level, "Head of Market Risk")
        if b.status is BreachStatus.OPEN and b.consecutive_days > AUTO_ESCALATE_UNACKNOWLEDGED_AFTER:
            _escalate(
                repo,
                out.events,
                b,
                "limit-monitor",
                target,
                run_id,
                auto=True,
                comment=f"not acknowledged after {b.consecutive_days} breaching runs",
            )
            out.auto_escalated.append(b)
        elif b.status is BreachStatus.ACKNOWLEDGED and b.consecutive_days >= AUTO_ESCALATE_ACKNOWLEDGED_AFTER:
            _escalate(
                repo,
                out.events,
                b,
                "limit-monitor",
                target,
                run_id,
                auto=True,
                comment=f"acknowledged but breaching for {b.consecutive_days} consecutive runs",
            )
            out.auto_escalated.append(b)
        repo.save_breach(b)
    # Open breaches whose limit is no longer breached on this run.
    breached_ids = set(breached["limit_id"])
    for lid, b in open_by_limit.items():
        if lid in breached_ids or lid not in set(limit_table["limit_id"]) or business_date < b.latest_date:
            continue
        row = limit_table[limit_table["limit_id"] == lid].iloc[0]
        b.within_limit_on_latest_run = True
        b.latest_run_id, b.latest_date, b.latest_utilisation = (
            run_id,
            business_date,
            _finite(row["utilisation"]),
        )
        repo.save_breach(b)
        _record(
            repo,
            out.events,
            b,
            "limit-monitor",
            BreachActionType.UPDATED,
            run_id=run_id,
            comment=f"back within limit at {float(row['utilisation']):.0%}; close pending",
        )
        out.back_within_limit.append(b)
    _persist(repo, out.events)  # idempotent: the run may store the same event ids again
    return out


# --- actions --------------------------------------------------------------------------------


def _record(
    repo,
    events,
    b: Breach,
    actor: str,
    action: BreachActionType,
    comment: str = "",
    run_id=None,
    escalated_to=None,
    close_reason=None,
) -> BreachAction:
    a = BreachAction(
        action_id=new_run_id("act"),
        breach_id=b.breach_id,
        actor=actor,
        action=action,
        comment=comment,
        run_id=run_id,
        escalated_to=escalated_to,
        close_reason=close_reason,
    )
    repo.save_breach_action(a)
    events.append(
        AuditEvent.now(
            actor,
            f"BREACH_{action.value}",
            b.breach_id,
            limit_id=b.limit_id,
            comment=comment,
            escalated_to=escalated_to,
            close_reason=close_reason,
        )
    )
    return a


def _escalate(repo, events, b: Breach, actor: str, to: str, run_id=None, auto=False, comment="") -> None:
    b.status = BreachStatus.ESCALATED
    b.escalated_to = to
    _record(
        repo,
        events,
        b,
        actor,
        BreachActionType.AUTO_ESCALATED if auto else BreachActionType.ESCALATED,
        comment=comment,
        run_id=run_id,
        escalated_to=to,
    )


def acknowledge(
    repo: DuckDBRepository, breach_id: str, actor: str, comment: str = ""
) -> tuple[Breach, list[AuditEvent]]:
    b = repo.load_breach(breach_id)
    if b.status is not BreachStatus.OPEN:
        raise WorkflowError(f"breach is {b.status.value}; only OPEN breaches can be acknowledged")
    if not actor.strip():
        raise WorkflowError("actor is required")
    events: list[AuditEvent] = []
    b.status, b.acknowledged_by = BreachStatus.ACKNOWLEDGED, actor
    _record(repo, events, b, actor, BreachActionType.ACKNOWLEDGED, comment)
    repo.save_breach(b)
    _persist(repo, events)
    return b, events


def escalate(
    repo: DuckDBRepository, breach_id: str, actor: str, to: str | None = None, comment: str = ""
) -> tuple[Breach, list[AuditEvent]]:
    b = repo.load_breach(breach_id)
    if b.status not in (BreachStatus.OPEN, BreachStatus.ACKNOWLEDGED):
        raise WorkflowError(f"breach is {b.status.value}; cannot escalate")
    events: list[AuditEvent] = []
    _escalate(
        repo, events, b, actor, to or ESCALATION_TARGET.get(b.level, "Head of Market Risk"), comment=comment
    )
    repo.save_breach(b)
    _persist(repo, events)
    return b, events


def comment(repo: DuckDBRepository, breach_id: str, actor: str, text: str) -> tuple[Breach, list[AuditEvent]]:
    b = repo.load_breach(breach_id)
    if not text.strip():
        raise WorkflowError("comment text is required")
    events: list[AuditEvent] = []
    _record(repo, events, b, actor, BreachActionType.COMMENT, text)
    _persist(repo, events)
    return b, events


def close(
    repo: DuckDBRepository,
    breach_id: str,
    actor: str,
    reason: CloseReason | str,
    comment: str = "",
    on: date | None = None,
) -> tuple[Breach, list[AuditEvent]]:
    b = repo.load_breach(breach_id)
    reason = CloseReason(reason)
    if b.status is BreachStatus.CLOSED:
        raise WorkflowError("breach is already closed")
    on = on or b.latest_date
    if reason is CloseReason.RISK_REDUCED and not b.within_limit_on_latest_run:
        raise WorkflowError("cannot close as RISK_REDUCED: the limit is still breached on the latest run")
    if reason is CloseReason.TEMPORARY_INCREASE_APPROVED:
        covering = [i for i in repo.load_increases(status="APPROVED", limit_id=b.limit_id) if i.in_force(on)]
        if not covering:
            raise WorkflowError("no approved temporary increase is in force for this limit")
    if reason is CloseReason.FALSE_POSITIVE and len(comment.strip()) < 10:
        raise WorkflowError("closing as FALSE_POSITIVE requires an explanatory comment")
    events: list[AuditEvent] = []
    b.status, b.closed_by, b.close_reason, b.closed_at = BreachStatus.CLOSED, actor, reason, datetime.now(UTC)
    _record(repo, events, b, actor, BreachActionType.CLOSED, comment, close_reason=reason)
    repo.save_breach(b)
    _persist(repo, events)
    return b, events


# --- temporary increases ------------------------------------------------------------------


def request_increase(
    repo: DuckDBRepository,
    limit_id: str,
    new_amount: float,
    expires_on: date,
    requested_by: str,
    rationale: str,
    effective_from: date | None = None,
    breach_id: str | None = None,
) -> tuple[LimitIncrease, list[AuditEvent]]:
    limits = {lim.limit_id: lim for lim in repo.load_limits()}
    if limit_id not in limits:
        raise WorkflowError(f"unknown limit {limit_id}")
    base = limits[limit_id]
    effective_from = effective_from or date.today()
    try:
        inc = LimitIncrease(
            increase_id=new_run_id("inc"),
            limit_id=limit_id,
            base_amount=base.amount,
            new_amount=new_amount,
            effective_from=effective_from,
            expires_on=expires_on,
            requested_by=requested_by,
            rationale=rationale,
            breach_id=breach_id,
        )
    except ValueError as e:
        raise WorkflowError(str(e)) from e
    repo.save_increase(inc)
    events = [
        AuditEvent.now(
            requested_by,
            "INCREASE_REQUESTED",
            inc.increase_id,
            limit_id=limit_id,
            base_amount=base.amount,
            new_amount=new_amount,
            expires_on=str(expires_on),
            needs_cro=inc.needs_cro(),
        )
    ]
    _persist(repo, events)
    return inc, events


def allowed_approvers(inc: LimitIncrease, limit: Limit) -> tuple[str, ...]:
    if inc.needs_cro():
        return ("CRO",)
    return APPROVERS_BY_LEVEL.get(limit.scope.level, ("CRO",))


def decide_increase(
    repo: DuckDBRepository, increase_id: str, approver: str, approve: bool, comment: str = ""
) -> tuple[LimitIncrease, list[AuditEvent]]:
    inc = repo.load_increase(increase_id)
    if inc.status is not IncreaseStatus.REQUESTED:
        raise WorkflowError(f"increase is {inc.status.value}; only REQUESTED increases can be decided")
    if approver.strip().lower() == inc.requested_by.strip().lower():
        raise WorkflowError("the requester cannot approve their own increase")
    limit = next((lim for lim in repo.load_limits() if lim.limit_id == inc.limit_id), None)
    if limit is None:
        raise WorkflowError(f"limit {inc.limit_id} no longer exists")
    allowed = allowed_approvers(inc, limit)
    if approve and approver not in allowed:
        raise WorkflowError(
            f"{approver} cannot approve this increase; allowed: {', '.join(allowed)}"
            + (" (increase above 25% needs the CRO)" if inc.needs_cro() else "")
        )
    inc.status = IncreaseStatus.APPROVED if approve else IncreaseStatus.REJECTED
    inc.decided_by, inc.decided_at, inc.decision_comment = approver, datetime.now(UTC), comment
    repo.save_increase(inc)
    events = [
        AuditEvent.now(
            approver,
            "INCREASE_APPROVED" if approve else "INCREASE_REJECTED",
            inc.increase_id,
            limit_id=inc.limit_id,
            new_amount=inc.new_amount,
            comment=comment,
        )
    ]
    _persist(repo, events)
    return inc, events


def cancel_increase(
    repo: DuckDBRepository, increase_id: str, actor: str
) -> tuple[LimitIncrease, list[AuditEvent]]:
    inc = repo.load_increase(increase_id)
    if inc.status not in (IncreaseStatus.REQUESTED, IncreaseStatus.APPROVED):
        raise WorkflowError(f"increase is {inc.status.value}; cannot cancel")
    inc.status = IncreaseStatus.CANCELLED
    inc.decided_by, inc.decided_at = actor, datetime.now(UTC)
    repo.save_increase(inc)
    events = [AuditEvent.now(actor, "INCREASE_CANCELLED", inc.increase_id, limit_id=inc.limit_id)]
    _persist(repo, events)
    return inc, events


def _persist(repo: DuckDBRepository, events: list[AuditEvent]) -> None:
    if events:
        repo.save_audit_events(events)


_ = HierarchyLevel  # re-exported for type hints in callers
