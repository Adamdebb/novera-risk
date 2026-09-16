"""Sign-off of a run's headline metrics before release (OPS-003).

A policy names the metrics that must be signed before a run counts as released. Named actors
sign or reject each metric on a run; the value they saw is frozen with the signature; every
action is an audit event. A run is RELEASED when every required metric is SIGNED, BLOCKED
while any required metric is REJECTED, PENDING otherwise (NO_POLICY when nothing is required).
The release row records who completed the release and when; rejecting a required metric
withdraws it. Nothing here changes a stored
result: sign-off is a judgement recorded next to the run, never an edit of it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from novera.limits.workflow import WorkflowError
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import AuditEvent, RunRecord

MODEL_VERSION = "1.0.0"
RECORD = "OPS-003"
SIGNABLE_RUN_TYPES = ("EOD", "RERUN")


@dataclass(frozen=True)
class Metric:
    metric_id: str
    title: str
    description: str
    record: str
    signer: str
    """Role expected to sign; informational until authentication exists."""
    default_required: bool
    keys: tuple[str, ...]
    """Run-summary keys frozen with the signature, so the record shows what was signed."""
    faces: tuple[str, ...] = ("bank", "fund")

    def value(self, summary: dict[str, Any]) -> dict[str, Any]:
        return {k: summary.get(k) for k in self.keys}

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "title": self.title,
            "description": self.description,
            "record": self.record,
            "signer": self.signer,
            "default_required": self.default_required,
            "keys": list(self.keys),
            "faces": list(self.faces),
        }


METRICS: tuple[Metric, ...] = (
    Metric(
        "DATA_QUALITY",
        "Data-quality verdict",
        "The run's verdict and findings. Signing a RED verdict is an override and needs a comment (DQ-001).",
        "DQ-001",
        "Head of Market Risk Control",
        True,
        ("dq_verdict", "dq_findings", "md_proxies"),
    ),
    Metric(
        "VAR",
        "VaR and expected shortfall",
        "Historical VaR and ES with the challenger and Monte Carlo figures.",
        "MR-002",
        "Head of Market Risk",
        True,
        ("var", "es", "challenger_var", "monte_carlo_var", "var_scenario_date"),
    ),
    Metric(
        "STRESS",
        "Stress results",
        "Worst scenario in the stress library.",
        "MR-005",
        "Head of Market Risk",
        True,
        ("worst_stress_name", "worst_stress"),
    ),
    Metric(
        "LIMITS",
        "Limit utilisation and breaches",
        "Limits monitored, breaches and warnings, and the breach workflow outcome of the run.",
        "MR-006",
        "Head of Market Risk",
        True,
        ("limits_monitored", "breaches", "warnings", "breaches_raised", "breaches_auto_escalated"),
    ),
    Metric(
        "PNL",
        "P&L explain",
        "Day-on-day P&L and its waterfall.",
        "MR-007",
        "Head of Product Control",
        True,
        ("pnl_total", "pnl_steps"),
    ),
    Metric(
        "BACKTEST",
        "Backtest",
        "Traffic-light zone and exceptions of the static backtest.",
        "MR-011",
        "Head of Model Validation",
        False,
        ("backtest_zone", "backtest_exceptions", "backtest_days"),
    ),
    Metric(
        "CONCENTRATION",
        "Concentration and liquidity",
        "Liquidity-adjusted VaR and the concentration and liquidity flags.",
        "MR-012",
        "Head of Market Risk",
        False,
        ("liquidity_adjusted_var", "concentration_flags", "liquidity_flags"),
    ),
    Metric(
        "COUNTERPARTY",
        "Counterparty exposure and CVA",
        "Exposure, CVA and DVA totals, largest PFE and wrong-way flags.",
        "CR-001",
        "Head of Counterparty Risk",
        True,
        ("counterparty",),
    ),
    Metric(
        "CAPITAL",
        "Regulatory capital",
        "FRTB, SA-CCR, SIMM and BA-CVA figures of the run.",
        "REG-001",
        "Head of Regulatory Reporting",
        False,
        ("regulatory",),
        faces=("bank",),
    ),
    Metric(
        "FUND",
        "Fund measures",
        "Leverage, margin usage and prime-broker concentration.",
        "HF-001",
        "Chief Risk Officer",
        True,
        ("fund",),
        faces=("fund",),
    ),
)
METRIC_BY_ID: dict[str, Metric] = {m.metric_id: m for m in METRICS}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _face(repo: DuckDBRepository, run: RunRecord) -> str:
    org = repo.load_organisation(run.config.get("firm_id", "GMB"))
    return "fund" if org.firm.firm_type == "HEDGE_FUND" else "bank"


# --- policy -----------------------------------------------------------------------------


def policy(repo: DuckDBRepository) -> dict[str, Any]:
    """The metrics that must be signed: the stored policy, or the defaults when none is stored."""
    rows = repo.load_signoff_policy()
    stored = {r["metric_id"]: r for r in rows}
    metrics = []
    for m in METRICS:
        r = stored.get(m.metric_id)
        metrics.append(
            {
                **m.to_dict(),
                "required": bool(r["required"]) if r else m.default_required,
                "updated_at": r["updated_at"] if r else None,
                "updated_by": r["actor"] if r else None,
            }
        )
    return {
        "record": RECORD,
        "source": "stored" if rows else "defaults",
        "metrics": metrics,
        "required": [x["metric_id"] for x in metrics if x["required"]],
    }


def set_policy(repo: DuckDBRepository, actor: str, required: list[str], comment: str = "") -> dict[str, Any]:
    """Replace the set of required metrics. Every metric id must exist; the change is audited
    with the sets before and after."""
    if not actor or not actor.strip():
        raise WorkflowError("actor is required")
    unknown = sorted(set(required) - set(METRIC_BY_ID))
    if unknown:
        raise WorkflowError(f"unknown metrics: {', '.join(unknown)}")
    before = policy(repo)["required"]
    at = _now()
    repo.init_schema()
    repo.save_signoff_policy(
        [
            {"metric_id": m.metric_id, "required": m.metric_id in required, "actor": actor, "updated_at": at}
            for m in METRICS
        ]
    )
    repo.save_audit_events(
        [
            AuditEvent.now(
                actor,
                "SIGNOFF_POLICY_CHANGED",
                "signoff_policy",
                before=sorted(before),
                after=sorted(required),
                comment=comment,
            )
        ]
    )
    return policy(repo)


# --- status -----------------------------------------------------------------------------


def status(repo: DuckDBRepository, run: RunRecord) -> dict[str, Any]:
    """Every metric of the run's face with its policy flag, its current sign-off and the
    frozen or live value, plus the run's release status."""
    face = _face(repo, run)
    pol = {m["metric_id"]: m for m in policy(repo)["metrics"]}
    signed = {s["metric_id"]: s for s in repo.load_signoffs(run.run_id)}
    release = repo.load_release(run.run_id)
    metrics = []
    for m in METRICS:
        if face not in m.faces:
            continue
        s = signed.get(m.metric_id)
        metrics.append(
            {
                "metric_id": m.metric_id,
                "title": m.title,
                "description": m.description,
                "record": m.record,
                "signer": m.signer,
                "required": pol[m.metric_id]["required"],
                "status": s["status"] if s else "PENDING",
                "actor": s["actor"] if s else None,
                "at": s["at"] if s else None,
                "comment": s["comment"] if s else None,
                "override": bool(s["override"]) if s else False,
                "value": json.loads(s["value"]) if s and s.get("value") else m.value(run.summary),
            }
        )
    required = [x for x in metrics if x["required"]]
    if not required:
        release_status = "NO_POLICY"
    elif any(x["status"] == "REJECTED" for x in required):
        release_status = "BLOCKED"
    elif all(x["status"] == "SIGNED" for x in required):
        release_status = "RELEASED"
    else:
        release_status = "PENDING"
    last = max((x for x in required if x["status"] == "SIGNED"), key=lambda x: x["at"] or "", default=None)
    released_at = (
        release["released_at"] if release else (last["at"] if release_status == "RELEASED" and last else None)
    )
    released_by = (
        release["released_by"]
        if release
        else (last["actor"] if release_status == "RELEASED" and last else None)
    )
    return {
        "record": RECORD,
        "run_id": run.run_id,
        "run_type": run.run_type,
        "business_date": run.business_date.isoformat(),
        "verdict": run.verdict,
        "face": face,
        "release_status": release_status,
        "released_at": released_at,
        "released_by": released_by,
        "override": bool(release["override"]) if release else any(x["override"] for x in required),
        "required_total": len(required),
        "signed_required": sum(1 for x in required if x["status"] == "SIGNED"),
        "pending": [x["metric_id"] for x in required if x["status"] != "SIGNED"],
        "metrics": metrics,
    }


def queue(repo: DuckDBRepository, limit: int = 20) -> list[dict[str, Any]]:
    """Release status of the most recent completed EOD runs, oldest pending first."""
    out = []
    for run in repo.list_runs(run_type="EOD", limit=limit):
        if run.status != "COMPLETED":
            continue
        st = status(repo, run)
        out.append(
            {
                "run_id": run.run_id,
                "business_date": st["business_date"],
                "verdict": run.verdict,
                "release_status": st["release_status"],
                "signed_required": st["signed_required"],
                "required_total": st["required_total"],
                "pending": st["pending"],
                "released_by": st["released_by"],
                "released_at": st["released_at"],
            }
        )
    return out


# --- actions ----------------------------------------------------------------------------


def _check_run(run: RunRecord) -> None:
    if run.status != "COMPLETED":
        raise WorkflowError(f"run {run.run_id} is {run.status}; only completed runs can be signed off")
    if run.run_type not in SIGNABLE_RUN_TYPES:
        raise WorkflowError(f"run type {run.run_type} is not subject to sign-off")


def _metric_for(repo: DuckDBRepository, run: RunRecord, metric_id: str) -> Metric:
    m = METRIC_BY_ID.get(metric_id)
    if m is None:
        raise WorkflowError(f"unknown metric {metric_id!r}; choose from {', '.join(METRIC_BY_ID)}")
    face = _face(repo, run)
    if face not in m.faces:
        raise WorkflowError(f"metric {metric_id} does not apply to the {face} face")
    return m


def sign(
    repo: DuckDBRepository, run: RunRecord, metric_id: str, actor: str, comment: str = ""
) -> dict[str, Any]:
    """Sign one metric. Allowed from PENDING or REJECTED. On a RED verdict the data-quality
    metric can only be signed with a comment, and the signature is flagged as an override.
    Releases the run when every required metric is signed."""
    _check_run(run)
    if not actor or not actor.strip():
        raise WorkflowError("actor is required")
    m = _metric_for(repo, run, metric_id)
    current = {s["metric_id"]: s for s in repo.load_signoffs(run.run_id)}.get(metric_id)
    if current and current["status"] == "SIGNED":
        raise WorkflowError(f"{metric_id} on {run.run_id} is already signed by {current['actor']}")
    override = run.verdict == "RED" and metric_id == "DATA_QUALITY"
    if override and not comment.strip():
        raise WorkflowError(
            "the verdict is RED: signing the data-quality metric is an override and needs a comment"
        )
    repo.init_schema()
    at = _now()
    repo.save_signoff(
        {
            "run_id": run.run_id,
            "metric_id": metric_id,
            "status": "SIGNED",
            "actor": actor,
            "at": at,
            "comment": comment,
            "value": json.dumps(m.value(run.summary), default=str),
            "override": override,
        }
    )
    events = [
        AuditEvent.now(
            actor,
            "METRIC_SIGNED",
            run.run_id,
            metric_id=metric_id,
            comment=comment,
            override=override,
            value=m.value(run.summary),
        )
    ]
    st = status(repo, run)
    if st["release_status"] == "RELEASED" and repo.load_release(run.run_id) is None:
        signed_override = any(x["override"] for x in st["metrics"] if x["status"] == "SIGNED")
        repo.save_release(
            {
                "run_id": run.run_id,
                "released_at": at,
                "released_by": actor,
                "override": signed_override,
                "metrics": json.dumps(
                    [
                        {k: x[k] for k in ("metric_id", "actor", "at", "override")}
                        for x in st["metrics"]
                        if x["required"]
                    ]
                ),
            }
        )
        events.append(
            AuditEvent.now(
                actor,
                "RUN_RELEASED",
                run.run_id,
                verdict=run.verdict,
                override=signed_override,
                metrics=[x["metric_id"] for x in st["metrics"] if x["required"]],
            )
        )
        st = status(repo, run)
    repo.save_audit_events(events)
    return st


def reject(
    repo: DuckDBRepository, run: RunRecord, metric_id: str, actor: str, comment: str
) -> dict[str, Any]:
    """Reject one metric, or withdraw a signature. A comment is mandatory. Rejecting a required
    metric on a released run withdraws the release."""
    _check_run(run)
    if not actor or not actor.strip():
        raise WorkflowError("actor is required")
    if not comment or not comment.strip():
        raise WorkflowError("a rejection needs a comment saying what is wrong")
    m = _metric_for(repo, run, metric_id)
    current = {s["metric_id"]: s for s in repo.load_signoffs(run.run_id)}.get(metric_id)
    if current and current["status"] == "REJECTED":
        raise WorkflowError(f"{metric_id} on {run.run_id} is already rejected by {current['actor']}")
    repo.init_schema()
    repo.save_signoff(
        {
            "run_id": run.run_id,
            "metric_id": metric_id,
            "status": "REJECTED",
            "actor": actor,
            "at": _now(),
            "comment": comment,
            "value": json.dumps(m.value(run.summary), default=str),
            "override": False,
        }
    )
    events = [
        AuditEvent.now(
            actor,
            "METRIC_REJECTED",
            run.run_id,
            metric_id=metric_id,
            comment=comment,
            withdrawn_signature=bool(current and current["status"] == "SIGNED"),
        )
    ]
    required = policy(repo)["required"]
    if metric_id in required and repo.load_release(run.run_id) is not None:
        repo.delete_release(run.run_id)
        events.append(
            AuditEvent.now(actor, "RUN_RELEASE_WITHDRAWN", run.run_id, metric_id=metric_id, comment=comment)
        )
    repo.save_audit_events(events)
    return status(repo, run)
