"""The stress setup: the firm's library of stress scenarios, versioned and approved, and which of
them feed the stress-loss limits (OPS-005, decision round 46).

- **Defaults.** Until the library is stored, the built-ins run (``built_in_definitions``): the
  14 hypothetical rule sets, all feeding limits. The stylised episodes ran too until round 47c.
- **Seeding.** The first write stores the built-ins as version 1, APPROVED, with the writer as
  the person who adopted them, and starts their review clock.
- **Four eyes.** Every change is a DRAFT version. A different person, the Head of Market Risk
  or the CRO, approves it (the previous approved version becomes SUPERSEDED) or rejects it.
  Only APPROVED versions run. A scenario has at most one open draft.
- **Reviews.** An approved version is due for review three months of business dates after its
  approval or last review. A run whose business date is past a scenario's due date still runs
  it, and its data-quality report carries a MINOR finding naming it (AMBER verdict).
- **Runs record what they ran.** Each EOD run stores the definitions it used, with the dates it
  replayed for historical ones, so a later change never alters how a stored run is read or
  re-run (OPS-002).

Business dates come from the stored market history: the review clock runs on the same calendar
as the runs it is checked against.
"""

from __future__ import annotations

import calendar
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

from novera.limits.workflow import WorkflowError
from novera.market_data.history import MarketHistory
from novera.risk.stress_definitions import (
    GOALS,
    KINDS,
    RECORD,
    SEVERITIES,
    TAGS,
    ScenarioDefinition,
    built_in_definitions,
    category_of,
    episode_windows,
    validate,
)
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import AuditEvent

SUBJECT = "stress_setup"
REVIEW_MONTHS = 3
SEED_PROPOSER = "built-in library"
_NOT_PROPOSED = {
    # set by the workflow, never by the proposer; a row copied from the setup carries them
    "version",
    "position",
    "resolved_window",
    "status",
    "proposed_by",
    "proposed_at",
    "decided_by",
    "decided_at",
    "note",
    "review_due",
    "reviewed_by",
    "reviewed_at",
    "review_overdue",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def business_date(repo: DuckDBRepository) -> date:
    """The latest business date of the stored history, the clock reviews run on."""
    rng = repo.market_history_range()
    return rng[1] if rng else datetime.now(UTC).date()


def _windows(repo: DuckDBRepository) -> dict[str, tuple[date, date]]:
    stored = repo.load_market_episodes()
    if stored:
        return episode_windows(None, stored)
    rng = repo.market_history_range()
    if rng is None:
        return {}
    # Old databases: the core episodes counted back from the end of the history need the dates.
    hist = MarketHistory.from_long(repo.load_market_history(factor_ids=["EQIDX:SPX"]))
    return episode_windows(hist, None)


def defaults(repo: DuckDBRepository) -> list[ScenarioDefinition]:
    return built_in_definitions(list(_windows(repo)))


def is_stored(repo: DuckDBRepository) -> bool:
    return bool(repo.load_stress_scenarios())


def versions(repo: DuckDBRepository) -> list[dict[str, Any]]:
    """Every stored version, oldest first, with its definition parsed."""
    return [
        {**r, "definition": ScenarioDefinition.from_dict(r["definition"])}
        for r in repo.load_stress_scenarios()
    ]


def active(repo: DuckDBRepository) -> list[dict[str, Any]]:
    """The approved version of each scenario in run order; the built-ins when nothing is stored."""
    rows = versions(repo)
    if not rows:
        return [
            {
                "definition": d,
                "status": "APPROVED",
                "version": 1,
                "proposed_by": SEED_PROPOSER,
                "review_due": None,
            }
            for d in defaults(repo)
        ]
    approved: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["status"] == "APPROVED":
            approved[r["scenario_id"]] = r  # versions are in ascending order: the latest wins
    return sorted(approved.values(), key=lambda r: (r["definition"].position, r["scenario_id"]))


def definitions_for_run(repo: DuckDBRepository) -> list[dict[str, Any]]:
    """What an EOD run produces: the approved, enabled definitions, each carrying its review
    due date, as dicts for the run's config."""
    out = []
    for r in active(repo):
        d: ScenarioDefinition = r["definition"]
        if d.enabled:
            due = r.get("review_due")
            out.append({**d.to_dict(), "review_due": due.isoformat() if isinstance(due, date) else due})
    return out


def _row(r: dict[str, Any], today: date) -> dict[str, Any]:
    d: ScenarioDefinition = r["definition"]
    due = r.get("review_due")
    due_d = due if isinstance(due, date) else (date.fromisoformat(due) if due else None)
    return {
        **d.to_dict(),
        "status": r["status"],
        "proposed_by": r.get("proposed_by"),
        "proposed_at": r.get("proposed_at"),
        "decided_by": r.get("decided_by"),
        "decided_at": r.get("decided_at"),
        "note": r.get("note"),
        "review_due": due_d.isoformat() if due_d else None,
        "reviewed_by": r.get("reviewed_by"),
        "reviewed_at": r.get("reviewed_at"),
        "review_overdue": bool(due_d and due_d < today),
        "category": category_of(d),
    }


def setup(repo: DuckDBRepository) -> dict[str, Any]:
    """The library as the Admin page shows it: the approved scenarios in run order, the open
    drafts, the decided history, and the options a proposal may use."""
    today = business_date(repo)
    stored = versions(repo)
    act = [_row(r, today) for r in active(repo)]
    drafts = [_row(r, today) for r in stored if r["status"] == "DRAFT"]
    history = [_row(r, today) for r in stored if r["status"] in ("REJECTED", "SUPERSEDED")]
    running = [a for a in act if a["enabled"]]
    rng = repo.market_history_range()
    return {
        "record": RECORD,
        "source": "stored" if stored else "defaults",
        "business_date": today.isoformat(),
        "review_months": REVIEW_MONTHS,
        "scenarios": act,
        "drafts": drafts,
        "history": history,
        "summary": {
            "scenarios": len(act),
            "in_daily_run": len(running),
            "limit": sum(1 for a in running if a["goal"] == "LIMIT"),
            "information": sum(1 for a in running if a["goal"] == "INFORMATION"),
            "drafts": len(drafts),
            "review_overdue": sum(1 for a in act if a["review_overdue"]),
        },
        "options": {
            "goals": list(GOALS),
            "kinds": list(KINDS),
            "severities": list(SEVERITIES),
            "tags": list(TAGS),
            "episodes": list(_windows(repo)),
            "covariances": ["STRESSED", "SIMILAR", "RECENT"],
        },
        "history_start": rng[0].isoformat() if rng else None,
        "history_end": rng[1].isoformat() if rng else None,
    }


def _audit(repo: DuckDBRepository, actor: str, event: str, **payload: Any) -> None:
    repo.save_audit_events([AuditEvent.now(actor, event, SUBJECT, **payload)])


def seed(repo: DuckDBRepository, actor: str, note: str = "", implicit: bool = False) -> bool:
    """Store the built-ins, APPROVED at their built-in version, adopted by ``actor``; False when
    the library is stored already. Their review clock starts on today's business date. An
    implicit seed (the first proposal or review stores the library) records that no person
    approved the built-ins: ``decided_by`` names the write that stored them."""
    if not actor or not actor.strip():
        raise WorkflowError("actor is required")
    repo.init_schema()
    if is_stored(repo):
        return False
    at, due = _now(), add_months(business_date(repo), REVIEW_MONTHS)
    defs = defaults(repo)
    repo.save_stress_scenarios(
        [
            {
                "scenario_id": d.scenario_id,
                "version": d.version,
                "status": "APPROVED",
                "definition": d.to_dict(),
                "proposed_by": SEED_PROPOSER,
                "proposed_at": at,
                "decided_by": f"stored on a first write by {actor}" if implicit else actor,
                "decided_at": at,
                "note": note or "built-in library adopted",
                "review_due": due,
            }
            for d in defs
        ]
    )
    _audit(repo, actor, "STRESS_LIBRARY_SEEDED", scenarios=[d.scenario_id for d in defs], note=note)
    return True


def _latest(repo: DuckDBRepository, scenario_id: str) -> list[dict[str, Any]]:
    return [r for r in versions(repo) if r["scenario_id"] == scenario_id]


def current_goal(repo: DuckDBRepository, scenario_id: str) -> str | None:
    """The goal of the approved version, None for a scenario the library does not have."""
    r = next((a for a in active(repo) if a["definition"].scenario_id == scenario_id), None)
    return r["definition"].goal if r else None


def propose(
    repo: DuckDBRepository, actor: str, definition: dict[str, Any], note: str = "", area: str | None = None
) -> dict[str, Any]:
    """A new scenario or a new version of one, as a DRAFT. ``area`` is the desk or business a
    desk head writes for (set by the caller from the user's scope). Validated against the risk
    factors and the stored history."""
    if not actor or not actor.strip():
        raise WorkflowError("actor is required")
    seed(repo, actor, implicit=True)
    try:
        d = ScenarioDefinition.from_dict({k: v for k, v in definition.items() if k not in _NOT_PROPOSED})
    except (TypeError, ValueError, KeyError) as e:
        raise WorkflowError(f"invalid scenario: {e}") from e
    rows = _latest(repo, d.scenario_id)
    if any(r["status"] == "DRAFT" for r in rows):
        raise WorkflowError(f"{d.scenario_id} already has an open draft; decide it first")
    approved = [r for r in rows if r["status"] == "APPROVED"]
    prev = approved[-1]["definition"] if approved else None
    position = (
        prev.position if prev else max((a["definition"].position for a in active(repo)), default=-1) + 1
    )
    d = replace(
        d,
        version=max((r["version"] for r in rows), default=0) + 1,
        source=prev.source if prev else (d.source if d.source in ("USER", "CALIBRATED") else "USER"),
        position=position,
        area=area if area is not None else d.area,
    )
    rng = repo.market_history_range()
    factor_ids = [f.factor_id for f in repo.load_risk_factors()]
    errors = validate(d, factor_ids, list(_windows(repo)), rng)
    if errors:
        raise WorkflowError("; ".join(errors))
    row = {
        "scenario_id": d.scenario_id,
        "version": d.version,
        "status": "DRAFT",
        "definition": d.to_dict(),
        "proposed_by": actor,
        "proposed_at": _now(),
        "note": note,
    }
    repo.save_stress_scenarios([row])
    _audit(
        repo,
        actor,
        "STRESS_SCENARIO_PROPOSED",
        scenario_id=d.scenario_id,
        version=d.version,
        goal=d.goal,
        enabled=d.enabled,
        previous_version=approved[-1]["version"] if approved else None,
        note=note,
    )
    return _row({**row, "definition": d}, business_date(repo))


def decide(
    repo: DuckDBRepository, actor: str, scenario_id: str, version: int, approve: bool, note: str = ""
) -> dict[str, Any]:
    """Approve or reject a draft. The proposer may not decide their own draft (four eyes)."""
    if not actor or not actor.strip():
        raise WorkflowError("actor is required")
    rows = _latest(repo, scenario_id)
    draft = next((r for r in rows if r["version"] == int(version)), None)
    if draft is None:
        raise WorkflowError(f"no version {version} of {scenario_id}")
    if draft["status"] != "DRAFT":
        raise WorkflowError(f"{scenario_id} v{version} is {draft['status']}, not a draft")
    if draft["proposed_by"] == actor:
        raise WorkflowError("four eyes: the proposer may not decide their own draft")
    at = _now()
    out = {
        **draft,
        "definition": draft["definition"].to_dict(),
        "status": "APPROVED" if approve else "REJECTED",
        "decided_by": actor,
        "decided_at": at,
        "note": note or draft.get("note"),
    }
    writes = []
    if approve:
        out["review_due"] = add_months(business_date(repo), REVIEW_MONTHS)
        writes += [
            {**r, "definition": r["definition"].to_dict(), "status": "SUPERSEDED"}
            for r in rows
            if r["status"] == "APPROVED"
        ]
    repo.save_stress_scenarios([*writes, out])
    _audit(
        repo,
        actor,
        "STRESS_SCENARIO_APPROVED" if approve else "STRESS_SCENARIO_REJECTED",
        scenario_id=scenario_id,
        version=int(version),
        proposed_by=draft["proposed_by"],
        goal=draft["definition"].goal,
        superseded=[r["version"] for r in writes],
        note=note,
    )
    return _row({**out, "definition": draft["definition"]}, business_date(repo))


def review(repo: DuckDBRepository, actor: str, scenario_id: str, note: str = "") -> dict[str, Any]:
    """Confirm the approved version still fits; its next review is due three months on."""
    if not actor or not actor.strip():
        raise WorkflowError("actor is required")
    seed(repo, actor, implicit=True)
    rows = [r for r in _latest(repo, scenario_id) if r["status"] == "APPROVED"]
    if not rows:
        raise WorkflowError(f"{scenario_id} has no approved version to review")
    r = rows[-1]
    today = business_date(repo)
    out = {
        **r,
        "definition": r["definition"].to_dict(),
        "reviewed_by": actor,
        "reviewed_at": _now(),
        "review_due": add_months(today, REVIEW_MONTHS),
    }
    repo.save_stress_scenarios([out])
    _audit(
        repo,
        actor,
        "STRESS_SCENARIO_REVIEWED",
        scenario_id=scenario_id,
        version=r["version"],
        previous_due=str(r.get("review_due")),
        review_due=out["review_due"].isoformat(),
        note=note,
    )
    return _row({**out, "definition": r["definition"]}, today)


def _content(d: ScenarioDefinition) -> dict[str, Any]:
    """What a definition shocks and how it is governed, without its place in the store."""
    out = d.to_dict()
    for k in ("version", "position", "resolved_window", "source", "area"):
        out.pop(k, None)
    return out


def upgrade_built_ins(repo: DuckDBRepository, actor: str, note: str = "") -> list[dict[str, Any]]:
    """Propose, as drafts, the built-ins whose code version differs from the approved version a
    stored library holds (the consistent market shocks of round 47, for example). A library
    that is not stored runs the code's built-ins already; nothing is proposed then."""
    if not is_stored(repo):
        return []
    approved = {r["definition"].scenario_id: r["definition"] for r in active(repo)}
    out = []
    for d in defaults(repo):
        cur = approved.get(d.scenario_id)
        if cur is None or cur.source != "BUILT_IN" or _content(cur) == _content(d):
            continue
        if any(r["status"] == "DRAFT" for r in _latest(repo, d.scenario_id)):
            continue
        out.append(
            propose(
                repo,
                actor,
                d.to_dict(),
                note or "built-in library update: the scenario now moves every factor (MR-018)",
            )
        )
    # Built-ins the code no longer ships (the stylised episodes since round 47c): retire them.
    current = {d.scenario_id for d in defaults(repo)}
    for sid, cur in approved.items():
        if cur.source != "BUILT_IN" or sid in current or not cur.enabled:
            continue
        if any(r["status"] == "DRAFT" for r in _latest(repo, sid)):
            continue
        retired = {k: v for k, v in cur.to_dict().items() if k not in _NOT_PROPOSED}
        out.append(
            propose(
                repo,
                actor,
                {**retired, "enabled": False},
                note or "built-in library update: no longer a built-in scenario; retire it",
            )
        )
    return out


def draft_crises(repo: DuckDBRepository, actor: str) -> list[dict[str, Any]]:
    """Enter every calibrated crisis table the library does not hold yet as an INFORMATION
    draft from ``actor``, marked approximate (decision 47.3); the approver verifies each."""
    from novera.risk.crisis_tables import CRISIS_TABLES, VERIFY_NOTE

    have = {r["scenario_id"] for r in repo.load_stress_scenarios()}
    return [
        propose(repo, actor, c.definition(), VERIFY_NOTE) for c in CRISIS_TABLES if c.scenario_id not in have
    ]


def plausibility(repo: DuckDBRepository) -> dict[str, Any]:
    """How extreme each approved scenario and each draft is (MR-018): z-score, exceedance,
    correlation z-score, anchor detail, and the risk ruler that places scenarios and the stored
    stress episodes on the headline basket under stressed correlations. For the approver; it
    changes nothing a run computes (decision 47.4)."""
    from novera.risk import scenario_covariance as sc_cov
    from novera.risk.scenarios import episode_shocks
    from novera.risk.stress_definitions import Expander

    msnaps = repo.list_market_snapshots()
    if not msnaps:
        return {"available": False, "reason": "no market snapshot stored"}
    base = repo.load_market_snapshot(msnaps[-1][0])
    history = MarketHistory.from_long(repo.load_market_history())
    universe = {f.factor_id: f for f in repo.load_risk_factors()}
    windows = _windows(repo)
    ex = Expander(history, base, universe, windows)

    def facts(d: ScenarioDefinition) -> dict[str, Any] | None:
        if d.kind != "HYPOTHETICAL":
            return None
        try:
            _, info = ex.explain(d)
        except ValueError as e:
            return {"error": str(e)}
        keep = (
            "z_score",
            "exceedance",
            "correlation_z",
            "volatility_z",
            "ruler_z",
            "covariance",
            "horizon_days",
            "filled",
            "anchor_detail",
            "pca",
        )
        return {k: info.get(k) for k in keep}

    scenarios = {r["definition"].scenario_id: facts(r["definition"]) for r in active(repo)}
    drafts = {
        f"{r['scenario_id']}:{r['version']}": facts(r["definition"])
        for r in versions(repo)
        if r["status"] == "DRAFT"
    }
    ms = ex.moves(sc_cov.DEFAULT_HORIZON)
    stressed = sc_cov.regime("STRESSED", history, ms, windows)
    ruler = []
    for eid, (s, e) in windows.items():
        if s < base.as_of:
            z = sc_cov.basket_z(episode_shocks(history, s, min(e, base.as_of), universe), ms, stressed)
            ruler.append(
                {"scenario_id": eid, "name": eid.replace("_", " ").title(), "kind": "EPISODE", "ruler_z": z}
            )
    for r in active(repo):
        d = r["definition"]
        f = scenarios.get(d.scenario_id) or {}
        if f.get("ruler_z") is not None:
            ruler.append(
                {"scenario_id": d.scenario_id, "name": d.name, "kind": d.source, "ruler_z": f["ruler_z"]}
            )
    ruler.sort(key=lambda x: x["ruler_z"] or 0.0)
    return {
        "available": True,
        "as_of": base.as_of.isoformat(),
        "horizon_days": sc_cov.DEFAULT_HORIZON,
        "method": "STRESSED",
        "basket": [f for f, _ in sc_cov.HEADLINE_FACTORS],
        "ruler": ruler,
        "scenarios": scenarios,
        "drafts": drafts,
    }
