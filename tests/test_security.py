"""Identity and permissions (ADR 0006): role accounts, tokens, the role matrix and its scope."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from novera.api.errors import ForbiddenError, UnauthenticatedError
from novera.domain.enums import HierarchyLevel
from novera.security import Action, Role, answers_for, build_users, issue, require, require_on_limit, user_for
from novera.security.permissions import require_signer
from novera.security.tokens import token_hash
from novera.simulation import build_counterparty_universe, build_global_macro_bank, build_limits
from novera.storage.duckdb_repository import DuckDBRepository


@pytest.fixture(scope="module")
def bank():
    org = build_global_macro_bank()
    limits = build_limits(org, build_counterparty_universe(org))
    users = {u.user_id: u for u in build_users(org, limits, ["Head of Product Control", "CRO"])}
    return org, limits, users


@pytest.fixture
def repo(tmp_path, bank):
    org, limits, users = bank
    with DuckDBRepository(tmp_path / "s.duckdb") as r:
        r.init_schema()
        r.save_organisation(org)
        r.save_limits(limits)
        r.save_users(list(users.values()))
        yield r


def test_role_accounts_come_from_the_organisation(bank):
    org, limits, users = bank
    by_role = {}
    for u in users.values():
        by_role.setdefault(u.role, []).append(u)
    assert {u.name for u in by_role[Role.DESK_HEAD]} | {u.name for u in by_role[Role.BUSINESS_HEAD]} >= {
        d.head for d in org.desks if d.head != "Head of Digital Assets"
    }
    assert users["head-of-macro"].role is Role.BUSINESS_HEAD and "EUR_RATES" in users["head-of-macro"].desks
    assert users["head-of-energy"].desks == ("ENERGY",)
    assert users["head-of-product-control"].role is Role.SIGNER  # a sign-off role outside the org
    assert users["cro"].role is Role.CRO and len([u for u in users.values() if u.name == "CRO"]) == 1
    assert len({u.user_id for u in users.values()}) == len(users)


def _limit(limits, level, entity=None, kind=None):
    return next(
        lim
        for lim in limits
        if lim.scope.level is level
        and (entity is None or lim.scope.entity_id == entity)
        and (kind is None or lim.limit_type.value == kind)
    )


def test_scope_desk_business_and_counterparty_limits(bank):
    org, limits, users = bank
    energy = _limit(limits, HierarchyLevel.DESK, "ENERGY")
    rates = _limit(limits, HierarchyLevel.DESK, "EUR_RATES")
    cpty = _limit(limits, HierarchyLevel.COUNTERPARTY)
    assert answers_for(users["head-of-energy"], energy, org)
    assert not answers_for(users["head-of-energy"], rates, org)
    assert answers_for(users["head-of-commodities"], energy, org)  # the business over the desk
    assert not answers_for(users["head-of-macro"], energy, org)
    assert answers_for(users["head-of-market-risk"], energy, org)
    assert not answers_for(users["head-of-market-risk"], cpty, org)  # counterparty limits: HCR
    assert answers_for(users["head-of-counterparty-risk"], cpty, org)
    assert not answers_for(users["viewer"], energy, org)
    require_on_limit(users["risk-control"], Action.BREACH_CLOSE, energy, org)
    with pytest.raises(ForbiddenError) as e:
        require_on_limit(users["head-of-energy"], Action.BREACH_CLOSE, energy, org)
    assert e.value.status == 403 and e.value.context["action"] == "BREACH_CLOSE"
    with pytest.raises(ForbiddenError):
        require_on_limit(users["head-of-market-risk"], Action.BREACH_ESCALATE, cpty, org)


def test_firm_wide_actions_and_signers(bank):
    _, _, users = bank
    require(users["risk-control"], Action.RUN)
    require(users["head-of-market-risk"], Action.VAR_SETUP)
    for who, action in (
        ("risk-control", Action.VAR_SETUP),
        ("head-of-energy", Action.RUN),
        ("viewer", Action.AGENT_RUN),
        ("head-of-market-risk", Action.CSA_DECIDE),
    ):
        with pytest.raises(ForbiddenError):
            require(users[who], action)
    require(users["viewer"], Action.ANALYST)
    # Stress scenarios (OPS-005): the risk function proposes any goal, desk heads information only.
    from novera.security.permissions import require_stress_proposal

    assert require_stress_proposal(users["risk-control"], "LIMIT", "LIMIT") is None
    assert require_stress_proposal(users["head-of-energy"], "INFORMATION", None) == "ENERGY"
    for who, goal, current in (
        ("head-of-energy", "LIMIT", None),
        ("head-of-energy", "INFORMATION", "LIMIT"),
        ("viewer", "INFORMATION", None),
    ):
        with pytest.raises(ForbiddenError):
            require_stress_proposal(users[who], goal, current)
    require(users["cro"], Action.STRESS_DECIDE)
    with pytest.raises(ForbiddenError):
        require(users["head-of-counterparty-risk"], Action.STRESS_DECIDE)
    require_signer(users["head-of-product-control"], "Head of Product Control", "PNL")
    require_signer(users["cro"], "Head of Product Control", "PNL")  # the CRO may sign any metric
    with pytest.raises(ForbiddenError):
        require_signer(users["head-of-market-risk"], "Head of Product Control", "PNL")


def test_tokens_are_stored_hashed_and_expire_or_revoke(repo):
    t = issue(repo, "head-of-energy", days=30, label="laptop")
    assert t.token.startswith("nvr_") and user_for(repo, t.token).name == "Head of Energy"
    row = repo.load_token(token_hash(t.token))
    assert row["token_hash"] != t.token and t.token not in str(repo.list_tokens())
    assert user_for(repo, None) is None and user_for(repo, "nvr_unknown") is None
    assert repo.revoke_token(t.token_id, datetime.now(UTC)) and user_for(repo, t.token) is None
    assert not repo.revoke_token(t.token_id, datetime.now(UTC))  # already revoked
    old = issue(repo, "viewer", days=1)
    repo._conn.execute(  # noqa: SLF001 - age the token past its expiry
        "UPDATE api_token SET expires_at = ? WHERE token_id = ?",
        [(datetime.now(UTC) - timedelta(minutes=1)).replace(tzinfo=None), old.token_id],
    )
    assert user_for(repo, old.token) is None
    with pytest.raises(KeyError):
        issue(repo, "nobody")
    with pytest.raises(ValueError):
        issue(repo, "viewer", days=0)


def test_local_client_writes_need_a_user(repo, tmp_path):
    from novera.api.client import LocalClient

    path = tmp_path / "s.duckdb"
    repo.close()  # the clients open their own connections
    with pytest.raises(UnauthenticatedError):
        LocalClient(path).apply_var_template("bank")
    with pytest.raises(ForbiddenError):
        LocalClient(path, "viewer").apply_var_template("bank")
    assert LocalClient(path, "viewer").me()["role"] == "VIEWER"


def test_cli_names_a_known_user_and_stamps_its_events(repo):
    import typer

    from novera.cli import _as
    from novera.workflows.runs import AUDIT_CHANNEL, AuditEvent, set_audit_channel

    assert _as(repo, "Head of Energy").user_id == "head-of-energy"
    assert _as(repo, "head-of-energy").name == "Head of Energy"
    with pytest.raises(typer.BadParameter):
        _as(repo, "Head of Desk")  # free text no longer passes
    try:
        set_audit_channel(channel="cli", os_user="tester")
        assert AuditEvent.now("CRO", "X", "s").payload["via"] == {"channel": "cli", "os_user": "tester"}
    finally:
        AUDIT_CHANNEL.clear()
    assert "via" not in AuditEvent.now("CRO", "X", "s").payload


def test_mcp_checks_the_same_tokens(repo, tmp_path):
    from novera.mcp_server import TokenVerifier, main

    t = issue(repo, "risk-control", days=1)
    path = str(tmp_path / "s.duckdb")
    repo.close()  # the verifier opens its own read-only connection
    v = TokenVerifier(path)
    ok = asyncio.run(v.verify_token(t.token))
    assert ok is not None and ok.client_id == "risk-control" and ok.scopes == ["RISK_CONTROL"]
    assert asyncio.run(v.verify_token("nvr_nope")) is None
    empty = tmp_path / "empty.duckdb"
    with DuckDBRepository(empty) as r:
        r.init_schema()
    with pytest.raises(SystemExit, match="users"):
        main(["--db", str(empty), "--transport", "streamable-http"])
