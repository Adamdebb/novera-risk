"""Role accounts (ADR 0006): one user per role the platform already names, seeded from the
organisation and its limits, so the audit trail keeps readable names ("CRO", "Head of Energy").

A user's ``name`` is the role string the workflows record and the approval matrix matches
(``APPROVERS_BY_LEVEL``); ``desks`` and ``businesses`` are the parts of the firm a desk or
business head answers for.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from novera.domain.enums import HierarchyLevel
from novera.domain.limits import Limit
from novera.domain.organisation import Organisation


class Role(StrEnum):
    CRO = "CRO"
    HEAD_OF_MARKET_RISK = "HEAD_OF_MARKET_RISK"
    HEAD_OF_COUNTERPARTY_RISK = "HEAD_OF_COUNTERPARTY_RISK"
    RISK_CONTROL = "RISK_CONTROL"
    BUSINESS_HEAD = "BUSINESS_HEAD"
    DESK_HEAD = "DESK_HEAD"
    LIMIT_OWNER = "LIMIT_OWNER"  # a firm-level owner outside the risk function, e.g. the CFO
    SIGNER = "SIGNER"  # signs the run metrics named after them, e.g. the Head of Product Control
    VIEWER = "VIEWER"


RISK_FUNCTION: dict[Role, str] = {
    Role.CRO: "CRO",
    Role.HEAD_OF_MARKET_RISK: "Head of Market Risk",
    Role.HEAD_OF_COUNTERPARTY_RISK: "Head of Counterparty Risk",
    Role.RISK_CONTROL: "Risk Control",
    Role.VIEWER: "Viewer",
}


class User(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    name: str
    role: Role
    desks: tuple[str, ...] = ()
    businesses: tuple[str, ...] = ()
    active: bool = True


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def build_users(org: Organisation, limits: list[Limit], signers: Iterable[str] = ()) -> list[User]:
    """The risk function, a head per business and desk, any other limit owner, each metric
    signer outside them (OPS-003), and a viewer."""
    users = {name: User(user_id=slug(name), name=name, role=role) for role, name in RISK_FUNCTION.items()}
    desk_heads: dict[str, list[str]] = {}
    for d in org.desks:
        if d.head:
            desk_heads.setdefault(d.head, []).append(d.desk_id)
    business_owner = {
        lim.scope.entity_id: lim.owner for lim in limits if lim.scope.level is HierarchyLevel.BUSINESS
    }
    for b in org.businesses:
        name = business_owner.get(b.business_id) or f"Head of {b.name}"
        desks = tuple(d.desk_id for d in org.desks if d.business_id == b.business_id)
        prev = users.get(name)
        if prev is not None and prev.role in (Role.BUSINESS_HEAD, Role.DESK_HEAD):
            # One person heads a business and its only desk ("Head of Digital Assets").
            users[name] = prev.model_copy(
                update={
                    "role": Role.BUSINESS_HEAD,
                    "desks": tuple(sorted({*prev.desks, *desks})),
                    "businesses": (*prev.businesses, b.business_id),
                }
            )
        elif prev is None:
            users[name] = User(
                user_id=slug(name),
                name=name,
                role=Role.BUSINESS_HEAD,
                desks=desks,
                businesses=(b.business_id,),
            )
    for name, desk_ids in desk_heads.items():
        prev = users.get(name)
        if prev is None:
            users[name] = User(user_id=slug(name), name=name, role=Role.DESK_HEAD, desks=tuple(desk_ids))
        elif prev.role is Role.BUSINESS_HEAD:
            users[name] = prev.model_copy(update={"desks": tuple(sorted({*prev.desks, *desk_ids}))})
    for lim in limits:
        if lim.owner not in users and lim.scope.level in (
            HierarchyLevel.FIRM,
            HierarchyLevel.LEGAL_ENTITY,
        ):
            users[lim.owner] = User(user_id=slug(lim.owner), name=lim.owner, role=Role.LIMIT_OWNER)
    for name in signers:
        if name not in users:
            users[name] = User(user_id=slug(name), name=name, role=Role.SIGNER)
    return sorted(users.values(), key=lambda u: (list(Role).index(u.role), u.name))


def seed_users(repo, firm_id: str | None = None) -> list[User]:
    """Store the role accounts of the database's firm; existing tokens stay valid."""
    from novera.workflows.signoff import METRICS

    firm = firm_id or repo.list_firm_ids()[0]
    users = build_users(repo.load_organisation(firm), repo.load_limits(), [m.signer for m in METRICS])
    repo.save_users(users)
    return users
