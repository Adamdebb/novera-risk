"""Stress paths with management actions (MR-023, decision round 51): the owner's action rules, kept
in the database and edited from the Admin page, and the EOD stage that walks every path frozen and
managed. A run records the rules it applied, so a re-run of the stage reproduces it whatever the
rules say later."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from novera.domain.limits import Limit
from novera.limits.workflow import WorkflowError
from novera.market_data.history import MarketHistory
from novera.risk.pca_space import HORIZON as PCA_HORIZON
from novera.risk.pca_space import PCASpace, QuadBook, build_space, quad_book
from novera.risk.revaluation import Portfolio
from novera.risk.reverse_stress import targets_from_limits
from novera.risk.stress_paths import (
    DEFAULT_RULES,
    DEFAULT_SCOPE,
    RECORD,
    ActionRule,
    StressPathsResult,
    run_stress_paths,
    validate_rules,
)
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import AuditEvent

STRESS_PATH_TABLES = (
    "stress_paths",
    "stress_path_days",
    "stress_path_desks",
    "stress_path_actions",
    "stress_path_parameters",
)
SUBJECT = "stress_action_rules"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _desks(repo: DuckDBRepository) -> list[str]:
    out: set[str] = set()
    for fid in repo.firm_ids():
        out |= {d.desk_id for d in repo.load_organisation(fid).desks}
    return sorted(out)


def stored_rules(repo: DuckDBRepository) -> list[ActionRule]:
    return [ActionRule.from_dict(r["rule"]) for r in repo.load_stress_action_rules()]


def rules_for_run(repo: DuckDBRepository) -> list[ActionRule]:
    """The rules an EOD run applies: the stored set, or the defaults when none is stored."""
    return stored_rules(repo) or list(DEFAULT_RULES)


def setup(repo: DuckDBRepository) -> dict[str, Any]:
    """The rule set as the Admin page shows it: the default row first, then any desk overrides,
    with the desks a row may name and the field descriptions."""
    rows = repo.load_stress_action_rules()
    by_scope = {r["scope"]: r for r in rows}
    rules = rules_for_run(repo)
    rules = sorted(rules, key=lambda r: (r.scope != DEFAULT_SCOPE, r.scope))
    return {
        "record": RECORD,
        "source": "stored" if rows else "defaults",
        "rules": [
            {
                **r.to_dict(),
                "updated_at": by_scope.get(r.scope, {}).get("updated_at"),
                "updated_by": by_scope.get(r.scope, {}).get("actor"),
            }
            for r in rules
        ],
        "desks": _desks(repo),
        "fields": FIELD_HELP,
    }


FIELD_HELP = {
    "scope": f"{DEFAULT_SCOPE} for every desk, or a desk id whose row replaces the default for that desk",
    "enabled": "False: the desk never acts in a path (its frozen and managed books are the same)",
    "stop_loss_var_multiple": "The desk acts from the day after its path loss reaches this multiple of its "
    "1-day VaR; every desk acts once the firm's path loss reaches the firm's stress-loss limit",
    "cut_fraction": "Share of every position sold once the desk acts (0.5 cuts gross by half)",
    "close_short_options": "Buy back short option positions in full",
    "hedge_delta": "Hedge the remaining delta every day with listed futures",
    "participation": "Largest share of a day's stressed volume the desk may trade",
    "stressed_volume_share": "Stressed volume as a share of normal volume",
    "stressed_spread_multiple": "Stressed bid-ask spread as a multiple of the normal one",
    "impact_coefficient": "Y in the square-root impact cost Y·σ·√(q/V) per unit traded",
}


def set_rules(
    repo: DuckDBRepository, actor: str, rules: list[dict[str, Any]], comment: str = ""
) -> dict[str, Any]:
    """Replace the rule set; validated and audited with the rules before and after."""
    if not actor or not actor.strip():
        raise WorkflowError("actor is required")
    try:
        parsed = [ActionRule.from_dict(dict(r)) for r in rules]
    except (TypeError, ValueError) as e:
        raise WorkflowError(f"invalid action rule: {e}") from e
    errors = validate_rules(parsed, _desks(repo))
    if errors:
        raise WorkflowError("; ".join(errors))
    before = [r.to_dict() for r in rules_for_run(repo)]
    at = _now()
    repo.init_schema()
    repo.save_stress_action_rules(
        [{"scope": r.scope, "rule": r.to_dict(), "actor": actor, "updated_at": at} for r in parsed]
    )
    repo.save_audit_events(
        [
            AuditEvent.now(
                actor,
                "STRESS_ACTIONS_CHANGED",
                SUBJECT,
                before=before,
                after=[r.to_dict() for r in parsed],
                comment=comment,
            )
        ]
    )
    return setup(repo)


# --- the stage -----------------------------------------------------------------------------------
def run_stage(
    pf: Portfolio,
    sens: pd.DataFrame,
    history: MarketHistory,
    valuation: pd.DataFrame,
    liquidity_by_trade: pd.DataFrame,
    var_result: Any,
    limits: list[Limit],
    limit_results: list[Any],
    episodes: dict | None,
    rules: list[dict[str, Any]],
    book: QuadBook | None = None,
    space: PCASpace | None = None,
    workers: int | None = None,
) -> StressPathsResult:
    """The stage from a run's inputs; reuses the PCA space of the reverse stress when given."""
    if book is None:
        book = quad_book(pf, sens, list(pf.base.values))
    if space is None:
        space = build_space(pf, history, book, episodes, list(pf.priced_ids), PCA_HORIZON)
    desk_var = var_result.by(valuation, "desk_id").set_index("desk_id")["standalone_var"].to_dict()
    return run_stress_paths(
        pf,
        book,
        space,
        sens,
        history,
        valuation,
        liquidity_by_trade,
        {str(k): float(v) for k, v in desk_var.items()},
        targets_from_limits(limits, valuation),
        limit_results,
        [ActionRule.from_dict(dict(r)) for r in rules],
        workers=workers,
    )


def persist_stress_paths(repo, rid: str, res: StressPathsResult) -> None:
    repo.save_run_frame(rid, "stress_paths", res.summary_table)
    repo.save_run_frame(rid, "stress_path_days", res.days)
    repo.save_run_frame(rid, "stress_path_desks", res.desks)
    repo.save_run_frame(rid, "stress_path_actions", res.actions)
    params = {k: v for k, v in res.parameters.items() if k != "rules"}
    params["rules_json"] = json.dumps(res.parameters.get("rules", []))
    repo.save_run_frame(rid, "stress_path_parameters", pd.DataFrame([params]))
