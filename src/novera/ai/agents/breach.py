"""Breach investigation agent (AI-002).

Evidence gathered from stored runs, deterministically:
- the breach, its limit and workflow history;
- utilisation on every run since the breach opened;
- which trades carry the measure on the latest run (contributions in the limit's own
  measure, with desk, book, trader, counterparty and trade date);
- what changed since the first breaching run: trades booked or removed in scope;
- a remediation sized by the engine: how much has to come off to be back inside the limit
  and inside the warning threshold, and which contributors would do it.
The provider drafts the note from that evidence; the note is attached to the breach as a
comment by the actor ``breach-investigator`` so it lives in the workflow audit trail.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from novera.ai.agents.base import Agent, AgentNote, _m
from novera.domain.limits import Limit, LimitType
from novera.limits.monitoring import _SENS_MEASURE, _measure_for_underlying, _sens_in_scope, trades_in_scope
from novera.limits.workflow import comment
from novera.storage.duckdb_repository import DuckDBRepository


def _contributions(limit: Limit, repo: DuckDBRepository, run_id: str) -> tuple[pd.DataFrame, str]:
    """Signed contribution per trade to the limit's measure on one run, with descriptors."""
    val = repo.load_run_frame(run_id, "valuation")
    ids = trades_in_scope(limit, val)
    lt = limit.limit_type
    if lt in (LimitType.VAR, LimitType.EXPECTED_SHORTFALL):
        c = repo.load_run_frame(run_id, "var_contributions")
        c = c[c["trade_id"].isin(ids)]
        part = c.groupby("trade_id")["component_var"].sum().rename("value")
        measure = "component VaR"
    elif lt is LimitType.STRESS_LOSS:
        st = repo.load_run_frame(run_id, "stress")
        st = st[st["trade_id"].isin(ids)]
        worst = st.groupby("scenario_id")["pnl"].sum().idxmin() if len(st) else None
        part = (-st[st["scenario_id"] == worst].groupby("trade_id")["pnl"].sum()).rename("value")
        measure = f"loss under {worst}"
    elif lt in _SENS_MEASURE or lt is LimitType.CONCENTRATION:
        sens = repo.load_run_frame(run_id, "sensitivities")
        if lt is LimitType.CONCENTRATION:
            measure = (
                "DV01" if limit.scope.tenor_bucket else _measure_for_underlying(limit.scope.risk_factor or "")
            )
        else:
            measure = _SENS_MEASURE[lt]
            if lt is LimitType.COMMODITY_DELTA and limit.scope.risk_factor in ("BTC", "ETH"):
                measure = "CRYPTO_DELTA"
        s = _sens_in_scope(limit, sens, ids, measure)
        part = s.groupby("trade_id")["value"].sum()
    elif lt is LimitType.COUNTERPARTY_EXPOSURE:
        v = val[val["counterparty_id"] == limit.scope.entity_id]
        part = v.set_index("trade_id")["pv"].clip(lower=0.0).rename("value")
        measure = "positive PV facing the counterparty"
    else:
        part = pd.Series(dtype=float, name="value")
        measure = lt.value
    keys = val.set_index("trade_id")[
        ["desk_id", "book_id", "trader_id", "counterparty_id", "product_type", "pv"]
    ]
    out = part.to_frame("value").join(keys, how="left")
    return out.sort_values("value", key=lambda s: s.abs(), ascending=False), measure


class BreachInvestigator(Agent):
    kind = "BREACH_INVESTIGATION"
    instructions = (
        "Write an investigation note for this limit breach: what breached and by how much, since when "
        "and how it evolved, which trades carry it (name trade ids, desks and counterparties), what "
        "changed since the breach opened, the remediation the engine sized, and the workflow status. "
        "End with two or three recommended actions. Use the numbers in the evidence only."
    )

    def gather(self, breach_id: str = "", **_: Any) -> dict[str, Any]:
        with DuckDBRepository(self.db_path, read_only=True) as repo:
            b = repo.load_breach(breach_id)
            limits = {lim.limit_id: lim for lim in repo.load_limits()}
            limit = limits.get(b.limit_id)
            actions = repo.load_breach_actions(breach_id)
            runs = [
                r
                for r in repo.list_runs(run_type="EOD", limit=200)
                if b.first_date <= r.business_date <= b.latest_date
            ]
            runs = sorted({r.business_date: r for r in runs}.values(), key=lambda r: r.business_date)
            history = []
            for r in runs:
                lt = repo.load_run_frame(r.run_id, "limits")
                row = lt[lt["limit_id"] == b.limit_id]
                if len(row):
                    history.append(
                        {
                            "business_date": str(r.business_date),
                            "run_id": r.run_id,
                            "utilisation": round(float(row.iloc[0]["utilisation"]), 3),
                            "current_m": _m(row.iloc[0]["current"]),
                            "amount_m": _m(row.iloc[0]["amount"]),
                            "status": row.iloc[0]["status"],
                        }
                    )
            latest_run = b.latest_run_id
            first_run = b.first_run_id
            contributions: list[dict[str, Any]] = []
            measure = ""
            remediation: dict[str, Any] = {}
            changed: dict[str, Any] = {}
            if limit is not None:
                contrib, measure = _contributions(limit, repo, latest_run)
                lt_latest = repo.load_run_frame(latest_run, "limits")
                row = lt_latest[lt_latest["limit_id"] == b.limit_id].iloc[0] if len(lt_latest) else None
                total = float(contrib["value"].sum()) if len(contrib) else 0.0
                for tid, r in contrib.head(8).iterrows():
                    contributions.append(
                        {
                            "trade_id": tid,
                            "desk_id": r["desk_id"],
                            "book_id": r["book_id"],
                            "trader_id": r["trader_id"],
                            "counterparty_id": r["counterparty_id"],
                            "product_type": r["product_type"],
                            "contribution": round(float(r["value"]), 2),
                            "share_of_total": round(float(r["value"]) / total, 3) if total else None,
                        }
                    )
                if row is not None and limit.limit_type is not LimitType.CONCENTRATION:
                    current, amount = float(row["current"]), float(row["amount"])
                    warn = amount * float(row["warning_threshold"])
                    sign = 1.0 if total >= 0 else -1.0
                    to_limit, to_warning = max(current - amount, 0.0), max(current - warn, 0.0)
                    picks, cut = [], 0.0
                    for tid, r in contrib.iterrows():
                        if sign * float(r["value"]) <= 0:
                            continue
                        picks.append(tid)
                        cut += abs(float(r["value"]))
                        if cut >= to_warning:
                            break
                    remediation = {
                        "measure": measure,
                        "current": round(current, 2),
                        "limit": round(amount, 2),
                        "reduce_to_limit": round(to_limit, 2),
                        "reduce_to_warning": round(to_warning, 2),
                        "unwind_candidates": picks[:6],
                        "unwind_candidates_cover": round(cut, 2),
                    }
                elif row is not None:
                    remediation = {
                        "measure": f"share of {measure} on {limit.scope.tenor_bucket or limit.scope.risk_factor}",
                        "current_share": round(float(row["current"]), 3),
                        "limit_share": round(float(row["amount"]), 3),
                        "unwind_candidates": [c["trade_id"] for c in contributions[:4]],
                    }
                if first_run != latest_run:
                    try:
                        v_first = repo.load_run_frame(first_run, "valuation")
                        v_last = repo.load_run_frame(latest_run, "valuation")
                        ids_first = set(trades_in_scope(limit, v_first))
                        ids_last = set(trades_in_scope(limit, v_last))
                        first_contrib, _ = _contributions(limit, repo, first_run)
                        top_ids = [c["trade_id"] for c in contributions]
                        changed = {
                            "trades_added_in_scope": sorted(ids_last - ids_first)[:10],
                            "trades_removed_from_scope": sorted(ids_first - ids_last)[:10],
                            "top_contributors_first_run": {
                                t: round(float(first_contrib["value"].get(t, 0.0)), 2) for t in top_ids
                            },
                        }
                    except Exception as e:  # noqa: BLE001
                        changed = {"error": str(e)}
            increases = [
                {
                    "increase_id": i.increase_id,
                    "status": i.status.value,
                    "new_amount": i.new_amount,
                    "expires_on": str(i.expires_on),
                    "requested_by": i.requested_by,
                }
                for i in repo.load_increases(limit_id=b.limit_id)
            ]
        return {
            "breach": {
                "breach_id": b.breach_id,
                "limit_id": b.limit_id,
                "limit_type": b.limit_type,
                "level": b.level,
                "entity_id": b.entity_id,
                "owner": b.owner,
                "status": b.status.value,
                "first_date": str(b.first_date),
                "latest_date": str(b.latest_date),
                "consecutive_days": b.consecutive_days,
                "first_utilisation": round(b.first_utilisation, 3),
                "latest_utilisation": round(b.latest_utilisation, 3),
                "peak_utilisation": round(b.peak_utilisation, 3),
                "escalated_to": b.escalated_to,
                "acknowledged_by": b.acknowledged_by,
            },
            "limit": {
                "rationale": limit.rationale if limit else "",
                "scope": limit.scope.model_dump() if limit else {},
                "warning_threshold": limit.warning_threshold if limit else None,
            },
            "run_id": latest_run,
            "measure": measure,
            "utilisation_history": history,
            "top_contributors": contributions,
            "changed_since_open": changed,
            "remediation": remediation,
            "workflow_actions": [
                {
                    "at": a.at.isoformat(),
                    "actor": a.actor,
                    "action": a.action.value,
                    "comment": a.comment[:200],
                }
                for a in actions
            ],
            "increase_requests": increases,
        }


def investigate_breach(
    db_path: str, breach_id: str, attach: bool = True, provider=None, persist: bool = True
) -> AgentNote:
    agent = BreachInvestigator(db_path, provider)
    note = agent.run(breach_id, persist=persist, breach_id=breach_id)
    if attach and persist:
        with DuckDBRepository(db_path) as repo:
            comment(repo, breach_id, "breach-investigator", f"[{note.note_id}] " + note.text[:3800])
            note.status = "ATTACHED"
            repo.update_agent_note_status(note.note_id, "ATTACHED")
    return note
