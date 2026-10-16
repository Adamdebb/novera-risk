"""Who may do what (ADR 0006). The workflows keep their own rules (the approval matrix, never
the requester, a breach's state machine); this layer adds the role and scope a write needs, and
the actor it records is always the signed-in user's name.

Scope of a limit: a desk head answers for the limits of their desks (desk, book and trader
limits), a business head for their business and its desks; the Head of Market Risk oversees
every limit but counterparty ones, which the Head of Counterparty Risk oversees; the CRO
oversees all. A firm-level owner (the fund's CFO) answers for the limits they own.
"""

from __future__ import annotations

from enum import StrEnum

from novera.api.errors import ForbiddenError
from novera.domain.enums import HierarchyLevel
from novera.domain.limits import Limit
from novera.domain.organisation import Organisation
from novera.security.users import Role, User


class Action(StrEnum):
    RUN = "run the end-of-day"
    RERUN = "re-run a stage"
    SIGN_METRIC = "sign or reject a metric"
    SIGNOFF_POLICY = "change the sign-off policy"
    VAR_SETUP = "change the VaR setup"
    STRESS_PROPOSE = "propose a stress scenario"
    STRESS_DECIDE = "approve or reject a stress scenario"
    STRESS_REVIEW = "review a stress scenario"
    LIMIT_RECALIBRATE = "propose a limit recalibration"
    LIMIT_DRAFT_DECIDE = "decide a limit draft"
    BREACH_ACKNOWLEDGE = "acknowledge this breach"
    BREACH_COMMENT = "comment on this breach"
    BREACH_ESCALATE = "escalate this breach"
    BREACH_CLOSE = "close this breach"
    INCREASE_REQUEST = "request an increase of this limit"
    INCREASE_DECIDE = "decide a limit increase"
    INCREASE_CANCEL = "cancel this increase"
    AGENT_RUN = "run an agent"
    CSA_DECIDE = "approve or reject a CSA proposal"
    LAB_RUN = "run a Portfolio Lab sandbox"
    ANALYST = "ask the Analyst"
    REPORT = "build a risk pack"


_OPERATORS = {Role.RISK_CONTROL, Role.HEAD_OF_MARKET_RISK, Role.CRO}
_HEADS_OF_RISK = {Role.HEAD_OF_MARKET_RISK, Role.HEAD_OF_COUNTERPARTY_RISK, Role.CRO}
_APPROVERS = _HEADS_OF_RISK  # the approval matrix then picks among them by limit level
_EVERYONE = set(Role)

FIRM_WIDE: dict[Action, set[Role]] = {
    Action.RUN: _OPERATORS,
    Action.RERUN: _OPERATORS,
    Action.SIGNOFF_POLICY: {Role.HEAD_OF_MARKET_RISK, Role.CRO},
    Action.VAR_SETUP: {Role.HEAD_OF_MARKET_RISK, Role.CRO},
    Action.STRESS_PROPOSE: {Role.RISK_CONTROL, *_HEADS_OF_RISK},
    Action.STRESS_DECIDE: {Role.HEAD_OF_MARKET_RISK, Role.CRO},
    Action.STRESS_REVIEW: {Role.HEAD_OF_MARKET_RISK, Role.CRO},
    Action.LIMIT_RECALIBRATE: {Role.HEAD_OF_MARKET_RISK, Role.CRO},
    Action.LIMIT_DRAFT_DECIDE: _APPROVERS,
    Action.INCREASE_DECIDE: _APPROVERS,
    Action.AGENT_RUN: {Role.RISK_CONTROL, *_HEADS_OF_RISK},
    Action.CSA_DECIDE: {Role.HEAD_OF_COUNTERPARTY_RISK, Role.CRO},
    Action.LAB_RUN: _OPERATORS,
    Action.ANALYST: _EVERYONE,
    Action.REPORT: _EVERYONE,
}


def _denied(user: User, action: Action, **context: str) -> ForbiddenError:
    return ForbiddenError(
        f"{user.name} ({user.role.value}) may not {action.value}",
        context={"user_id": user.user_id, "action": action.name, **context},
    )


def require(user: User, action: Action) -> None:
    """A firm-wide action: the role alone decides."""
    if user.role not in FIRM_WIDE.get(action, set()):
        raise _denied(user, action)


def _desks_of(limit: Limit, org: Organisation | None) -> set[str]:
    level, entity = limit.scope.level, limit.scope.entity_id
    if level is HierarchyLevel.DESK:
        return {entity}
    if org is None:
        return set()
    if level is HierarchyLevel.BOOK:
        return {b.desk_id for b in org.books if b.book_id == entity}
    if level is HierarchyLevel.TRADER:
        return {t.desk_id for t in org.traders if t.trader_id == entity}
    if level is HierarchyLevel.BUSINESS:
        return {d.desk_id for d in org.desks if d.business_id == entity}
    return set()


def oversees(user: User, limit: Limit) -> bool:
    """A head of risk over this kind of limit."""
    counterparty = limit.scope.level is HierarchyLevel.COUNTERPARTY
    return user.role is Role.CRO or user.role is (
        Role.HEAD_OF_COUNTERPARTY_RISK if counterparty else Role.HEAD_OF_MARKET_RISK
    )


def answers_for(user: User, limit: Limit, org: Organisation | None) -> bool:
    """The limit's owner, a head over its desk or business, or a head of risk over it."""
    if user.role is Role.VIEWER:
        return False
    if limit.owner == user.name or oversees(user, limit):
        return True
    if user.role is Role.BUSINESS_HEAD and limit.scope.level is HierarchyLevel.BUSINESS:
        return limit.scope.entity_id in user.businesses
    if user.role in (Role.BUSINESS_HEAD, Role.DESK_HEAD):
        return bool(_desks_of(limit, org) & set(user.desks))
    return False


def require_on_limit(user: User, action: Action, limit: Limit, org: Organisation | None) -> None:
    """A breach or increase action on ``limit``."""
    if action in (Action.BREACH_ACKNOWLEDGE, Action.INCREASE_REQUEST):
        ok = answers_for(user, limit, org)
    elif action is Action.BREACH_COMMENT:
        ok = user.role is Role.RISK_CONTROL or answers_for(user, limit, org)
    elif action in (Action.BREACH_ESCALATE, Action.BREACH_CLOSE):
        ok = user.role is Role.RISK_CONTROL or oversees(user, limit)
    else:
        raise ValueError(f"{action.name} is not a limit action")
    if not ok:
        raise _denied(user, action, limit_id=limit.limit_id)


def require_signer(user: User, signer: str, metric_id: str) -> None:
    """A run metric is signed by the role it names (OPS-003), or by the CRO."""
    if user.name != signer and user.role is not Role.CRO:
        raise _denied(user, Action.SIGN_METRIC, metric_id=metric_id, signer=signer)


def require_stress_proposal(user: User, goal: str, current_goal: str | None) -> str | None:
    """Who may propose a stress scenario (decision 46.2): the risk function, any goal; a desk or
    business head, for information only and never a change to a scenario that feeds limits.
    Returns the area a head writes for (their desks or business), None for the risk function."""
    if user.role in FIRM_WIDE[Action.STRESS_PROPOSE]:
        return None
    if user.role in (Role.DESK_HEAD, Role.BUSINESS_HEAD):
        if goal != "INFORMATION":
            raise _denied(
                user, Action.STRESS_PROPOSE, reason="desk and business heads propose INFORMATION scenarios"
            )
        if current_goal == "LIMIT":
            raise _denied(
                user, Action.STRESS_PROPOSE, reason="the scenario feeds limits; the risk function changes it"
            )
        area = user.businesses if user.role is Role.BUSINESS_HEAD and user.businesses else user.desks
        return ",".join(area) if area else None
    raise _denied(user, Action.STRESS_PROPOSE)
