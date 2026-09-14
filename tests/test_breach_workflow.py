"""Breach lifecycle, auto-escalation, close rules and the approval matrix."""
from datetime import date, timedelta

import pandas as pd
import pytest

from novera.domain import HierarchyLevel, Limit, LimitScope, LimitType
from novera.domain.breaches import BreachStatus, IncreaseStatus
from novera.limits import (
    WorkflowError,
    acknowledge,
    close,
    decide_increase,
    effective_limits,
    escalate,
    expire_increases,
    request_increase,
    sync_breaches,
)
from novera.limits.monitoring import COLUMNS
from novera.storage.duckdb_repository import DuckDBRepository

D1, D2, D3 = date(2026, 9, 11), date(2026, 9, 14), date(2026, 9, 15)


def _limit(lid, level=HierarchyLevel.DESK, amount=100.0, owner="Head of USD Rates"):
    return Limit(limit_id=lid, limit_type=LimitType.DV01, scope=LimitScope(level=level, entity_id="USD_RATES"),
                 amount=amount, owner=owner, effective_from=date(2026, 1, 1))


def _table(rows):
    out = pd.DataFrame(rows, columns=COLUMNS)
    return out


def _row(lid, util, level="DESK"):
    return {"limit_id": lid, "limit_type": "DV01", "level": level, "entity_id": "USD_RATES", "filters": "",
            "amount": 100.0, "base_amount": 100.0, "increase_id": None, "current": util * 100, "utilisation": util,
            "status": "BREACH" if util >= 1 else ("WARNING" if util >= 0.8 else "OK"), "warning_threshold": 0.8,
            "owner": "Head of USD Rates", "trades_in_scope": 3}


@pytest.fixture
def repo(tmp_path):
    with DuckDBRepository(tmp_path / "wf.duckdb") as r:
        r.init_schema()
        r.save_limits([_limit("L1"), _limit("L2"), _limit("FIRM_VAR", HierarchyLevel.FIRM, owner="CRO")])
        yield r


def test_raise_update_and_auto_escalate(repo):
    out = sync_breaches(repo, "run1", D1, _table([_row("L1", 1.2), _row("L2", 0.5)]))
    assert len(out.raised) == 1 and out.raised[0].status is BreachStatus.OPEN
    b = repo.load_breaches(open_only=True)[0]
    assert b.limit_id == "L1" and b.consecutive_days == 1
    # Same run again is idempotent.
    assert sync_breaches(repo, "run1", D1, _table([_row("L1", 1.2)])).updated == []
    # Next day, still breaching and nobody acknowledged: auto-escalate.
    out = sync_breaches(repo, "run2", D2, _table([_row("L1", 1.3)]))
    assert len(out.auto_escalated) == 1
    b = repo.load_breach(b.breach_id)
    assert b.status is BreachStatus.ESCALATED and b.escalated_to == "Head of Market Risk"
    assert b.consecutive_days == 2 and b.peak_utilisation == pytest.approx(1.3)
    kinds = {a.action.value for a in repo.load_breach_actions(b.breach_id)}
    assert {"RAISED", "UPDATED", "AUTO_ESCALATED"} <= kinds
    events = repo.load_audit_events(subject=b.breach_id)
    assert "BREACH_AUTO_ESCALATED" in set(events["event_type"])


def test_acknowledged_breach_escalates_later(repo):
    sync_breaches(repo, "run1", D1, _table([_row("L1", 1.1)]))
    b = repo.load_breaches(open_only=True)[0]
    acknowledge(repo, b.breach_id, "Head of USD Rates", "reducing")
    sync_breaches(repo, "run2", D2, _table([_row("L1", 1.1)]))
    assert repo.load_breach(b.breach_id).status is BreachStatus.ACKNOWLEDGED
    sync_breaches(repo, "run3", D3, _table([_row("L1", 1.1)]))
    assert repo.load_breach(b.breach_id).status is BreachStatus.ESCALATED
    with pytest.raises(WorkflowError):
        acknowledge(repo, b.breach_id, "x")  # only OPEN can be acknowledged


def test_close_rules(repo):
    sync_breaches(repo, "run1", D1, _table([_row("L1", 1.1)]))
    b = repo.load_breaches(open_only=True)[0]
    with pytest.raises(WorkflowError, match="still breached"):
        close(repo, b.breach_id, "Head of USD Rates", "RISK_REDUCED")
    with pytest.raises(WorkflowError, match="no approved temporary increase"):
        close(repo, b.breach_id, "Head of USD Rates", "TEMPORARY_INCREASE_APPROVED")
    with pytest.raises(WorkflowError, match="explanatory comment"):
        close(repo, b.breach_id, "Head of USD Rates", "FALSE_POSITIVE", "typo")
    # Back within limit on the next run: RISK_REDUCED becomes allowed.
    out = sync_breaches(repo, "run2", D2, _table([_row("L1", 0.7)]))
    assert len(out.back_within_limit) == 1
    closed, _ = close(repo, b.breach_id, "Head of USD Rates", "RISK_REDUCED", "position cut")
    assert closed.status is BreachStatus.CLOSED and closed.close_reason.value == "RISK_REDUCED"
    assert repo.load_breaches(open_only=True) == []
    with pytest.raises(WorkflowError, match="already closed"):
        close(repo, b.breach_id, "x", "RISK_REDUCED")
    # A fresh breach on the same limit later gets a new breach id.
    out = sync_breaches(repo, "run3", D3, _table([_row("L1", 1.05)]))
    assert out.raised and out.raised[0].breach_id != b.breach_id


def test_escalate_manually(repo):
    sync_breaches(repo, "run1", D1, _table([_row("L1", 1.1)]))
    b = repo.load_breaches(open_only=True)[0]
    e, _ = escalate(repo, b.breach_id, "Head of USD Rates", "CRO", "needs a decision")
    assert e.status is BreachStatus.ESCALATED and e.escalated_to == "CRO"
    with pytest.raises(WorkflowError):
        escalate(repo, b.breach_id, "x")


def test_increase_approval_matrix(repo):
    sync_breaches(repo, "run1", D1, _table([_row("L1", 1.1)]))
    b = repo.load_breaches(open_only=True)[0]
    inc, _ = request_increase(repo, "L1", 120.0, D1 + timedelta(days=30), "Head of USD Rates",
                              "novation pipeline, unwinds scheduled", effective_from=D1, breach_id=b.breach_id)
    assert inc.status is IncreaseStatus.REQUESTED and not inc.needs_cro()
    with pytest.raises(WorkflowError, match="own increase"):
        decide_increase(repo, inc.increase_id, "Head of USD Rates", True)
    with pytest.raises(WorkflowError, match="cannot approve"):
        decide_increase(repo, inc.increase_id, "Head of Energy", True)
    ok, _ = decide_increase(repo, inc.increase_id, "Head of Market Risk", True, "agreed for 30 days")
    assert ok.status is IncreaseStatus.APPROVED
    # Effective limit is raised while in force, and the breach can be closed against it.
    limits, ids = effective_limits(repo.load_limits(), repo.load_increases(status="APPROVED"), D2)
    assert next(lim for lim in limits if lim.limit_id == "L1").amount == 120.0 and ids["L1"] == inc.increase_id
    limits, ids = effective_limits(repo.load_limits(), repo.load_increases(status="APPROVED"), D1 + timedelta(days=60))
    assert next(lim for lim in limits if lim.limit_id == "L1").amount == 100.0 and ids == {}
    closed, _ = close(repo, b.breach_id, "Head of USD Rates", "TEMPORARY_INCREASE_APPROVED", "", on=D2)
    assert closed.status is BreachStatus.CLOSED
    # Expiry.
    ev = expire_increases(repo, D1 + timedelta(days=60))
    assert ev and repo.load_increase(inc.increase_id).status is IncreaseStatus.EXPIRED


def test_large_increase_needs_cro_and_firm_level_needs_cro(repo):
    big, _ = request_increase(repo, "L1", 140.0, D1 + timedelta(days=10), "Head of USD Rates", "big", effective_from=D1)
    assert big.needs_cro()
    with pytest.raises(WorkflowError, match="needs the CRO"):
        decide_increase(repo, big.increase_id, "Head of Market Risk", True)
    assert decide_increase(repo, big.increase_id, "CRO", True)[0].status is IncreaseStatus.APPROVED
    firm, _ = request_increase(repo, "FIRM_VAR", 110.0, D1 + timedelta(days=10), "Head of Market Risk", "firm",
                               effective_from=D1)
    with pytest.raises(WorkflowError):
        decide_increase(repo, firm.increase_id, "Head of Market Risk", True)
    rejected, _ = decide_increase(repo, firm.increase_id, "CRO", False, "no")
    assert rejected.status is IncreaseStatus.REJECTED
    with pytest.raises(WorkflowError, match="limited to"):
        request_increase(repo, "L1", 110.0, D1 + timedelta(days=90), "x", "too long", effective_from=D1)
    with pytest.raises(WorkflowError, match="exceed"):
        request_increase(repo, "L1", 90.0, D1 + timedelta(days=9), "x", "not an increase", effective_from=D1)
