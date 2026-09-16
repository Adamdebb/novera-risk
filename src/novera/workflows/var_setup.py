"""The VaR setup an administrator maintains: which VaR measures the firm produces daily,
which feed limits and which are for information (OPS-004).

The setup is stored once per database (so once per firm face); until one is saved the
defaults apply, which reproduce what the platform produced before the setup existed. A
change replaces the whole matrix, names an actor, is validated against the stored history
(a fixed window must lie inside it) and is audited with the measure ids before and after.
Every EOD run records the measures it produced in its config, so a later change of the
setup never alters how a stored run is read or re-run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from novera.limits.workflow import WorkflowError
from novera.market_data.history import MarketHistory
from novera.risk.var import VaRConfig
from novera.risk.var_measures import (
    COMPUTES,
    DEFAULT_MEASURES,
    GOALS,
    METRICS,
    RECORD,
    SHOCKS,
    VaRMeasure,
    most_volatile_year,
    templates,
    validate_measures,
)
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import AuditEvent

SUBJECT = "var_setup"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def stored_measures(repo: DuckDBRepository) -> list[VaRMeasure]:
    return [VaRMeasure.from_dict(r) for r in repo.load_var_setup()]


def measures_for_run(repo: DuckDBRepository) -> list[VaRMeasure]:
    """The measures an EOD run produces: the enabled rows of the stored setup, or the
    defaults when none is stored."""
    stored = stored_measures(repo)
    return [m for m in stored if m.enabled] if stored else list(DEFAULT_MEASURES)


def _stress_window(repo: DuckDBRepository) -> tuple | None:
    """The most volatile year of the equity index in the stored history, for the bank
    template's stressed VaR; reads one factor's series only."""
    rng = repo.market_history_range()
    if rng is None:
        return None
    hist = MarketHistory.from_long(repo.load_market_history(factor_ids=["EQIDX:SPX"]))
    return most_volatile_year(hist) if len(hist.wide) else None


def setup(repo: DuckDBRepository, base: VaRConfig | None = None) -> dict[str, Any]:
    """The setup as the Admin page shows it: every measure described, the source (stored or
    defaults), the templates a firm can start from, the options per column and the range of
    the stored history that fixed windows must respect."""
    base = base or VaRConfig()
    rows = repo.load_var_setup()
    measures = [VaRMeasure.from_dict(r) for r in rows] if rows else list(DEFAULT_MEASURES)
    rng = repo.market_history_range()
    stress = _stress_window(repo) if rng else None
    described = []
    for m, r in zip(measures, rows or [None] * len(measures), strict=True):
        d = m.describe(base)
        if not m.window_years and not m.fixed_window:
            d["window_years"] = round(base.window_days / 250, 2)  # resolved for display
        d["updated_at"] = r["updated_at"] if r else None
        d["updated_by"] = r["actor"] if r else None
        described.append(d)
    return {
        "record": RECORD,
        "source": "stored" if rows else "defaults",
        "measures": described,
        "headline": next(
            (
                d["measure_id"]
                for d in described
                if d["enabled"] and d["goal"] == "LIMIT" and d["metric"] == "VAR"
            ),
            next((d["measure_id"] for d in described if d["enabled"] and d["metric"] == "VAR"), None),
        ),
        "templates": {name: [m.describe(base) for m in ms] for name, ms in templates(stress).items()},
        "options": {
            "goals": list(GOALS),
            "metrics": list(METRICS),
            "shocks": list(SHOCKS),
            "computes": list(COMPUTES),
        },
        "history_start": rng[0].isoformat() if rng else None,
        "history_end": rng[1].isoformat() if rng else None,
        "stress_window": [d.isoformat() for d in stress] if stress else None,
        "base_window_days": base.window_days,
    }


def set_setup(
    repo: DuckDBRepository, actor: str, measures: list[dict[str, Any]], comment: str = ""
) -> dict[str, Any]:
    """Replace the setup. Every row is validated (``validate_measures``) against the stored
    history; the change is audited with the measure ids before and after."""
    if not actor or not actor.strip():
        raise WorkflowError("actor is required")
    if not measures:
        raise WorkflowError("the setup needs at least one measure")
    try:
        parsed = [VaRMeasure.from_dict(m) for m in measures]
    except (TypeError, ValueError) as e:
        raise WorkflowError(f"invalid measure: {e}") from e
    rng = repo.market_history_range()
    errors = validate_measures(parsed, rng[0] if rng else None, rng[1] if rng else None)
    if errors:
        raise WorkflowError("; ".join(errors))
    before = [m.measure_id for m in stored_measures(repo)] or [m.measure_id for m in DEFAULT_MEASURES]
    at = _now()
    repo.init_schema()
    repo.save_var_setup(
        [{**m.to_dict(), "position": i, "actor": actor, "updated_at": at} for i, m in enumerate(parsed)]
    )
    repo.save_audit_events(
        [
            AuditEvent.now(
                actor,
                "VAR_SETUP_CHANGED",
                SUBJECT,
                before=before,
                after=[m.measure_id for m in parsed],
                limits={m.limit_type: m.measure_id for m in parsed if m.limit_type and m.enabled},
                comment=comment,
            )
        ]
    )
    return setup(repo)


def apply_template(repo: DuckDBRepository, name: str, actor: str, comment: str = "") -> dict[str, Any]:
    stress = _stress_window(repo)
    tpl = templates(stress)
    if name not in tpl:
        raise WorkflowError(f"unknown template {name!r}; one of {', '.join(tpl)}")
    return set_setup(repo, actor, [m.to_dict() for m in tpl[name]], comment or f"template {name}")
