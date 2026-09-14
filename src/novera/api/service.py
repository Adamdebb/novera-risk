"""Read-side service over stored runs. The API and the UI both go through this class, so
no screen ever computes a number itself (ADR 0004). Every answer carries the run id."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import RunRecord

HIERARCHY = ["firm_id", "business_id", "desk_id", "book_id", "trader_id", "trade_id"]
DIMENSIONS = HIERARCHY + ["legal_entity_id", "asset_class", "product_type", "currency", "counterparty_id"]


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out = df.copy()
    for c in out.columns:
        if str(out[c].dtype).startswith("datetime") or out[c].map(lambda x: hasattr(x, "isoformat")).any():
            out[c] = out[c].map(lambda x: x.isoformat() if hasattr(x, "isoformat") else x)
    return out.astype(object).where(pd.notna(out), None).to_dict(orient="records")


@dataclass
class RunNotFoundError(Exception):
    run_id: str


class RiskService:
    def __init__(self, repo: DuckDBRepository) -> None:
        self.repo = repo

    # --- runs ----------------------------------------------------------------------
    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return [self._run_dict(r) for r in self.repo.list_runs(limit=limit)]

    def resolve(self, run_id: str | None) -> RunRecord:
        if run_id in (None, "", "latest"):
            r = self.repo.latest_run()
            if r is None:
                raise RunNotFoundError("latest")
            return r
        try:
            return self.repo.load_run(run_id)
        except KeyError as e:
            raise RunNotFoundError(run_id) from e

    @staticmethod
    def _run_dict(r: RunRecord) -> dict[str, Any]:
        return {
            "run_id": r.run_id,
            "run_type": r.run_type,
            "business_date": r.business_date.isoformat(),
            "portfolio_snapshot_id": r.portfolio_snapshot_id,
            "market_snapshot_id": r.market_snapshot_id,
            "previous_market_snapshot_id": r.previous_market_snapshot_id,
            "reporting_currency": r.reporting_currency,
            "model_versions": r.model_versions,
            "config_hash": r.config_hash,
            "status": r.status,
            "verdict": r.verdict,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "summary": r.summary,
            "timings": r.timings,
        }

    def summary(self, run_id: str | None = None) -> dict[str, Any]:
        r = self.resolve(run_id)
        d = self._run_dict(r)
        d["var_by_asset_class"] = self.var_by("asset_class", r.run_id)
        d["top_breaches"] = self.limits(r.run_id, status="BREACH")[:10]
        d["dq_findings"] = self.dq(r.run_id)
        d["pnl_steps"] = r.summary.get("pnl_steps") or {}
        return d

    # --- frames --------------------------------------------------------------------
    def _frame(self, run_id: str | None, name: str) -> tuple[RunRecord, pd.DataFrame]:
        r = self.resolve(run_id)
        return r, self.repo.load_run_frame(r.run_id, name)

    def valuation(self, run_id: str | None = None, **filters: str) -> pd.DataFrame:
        _, v = self._frame(run_id, "valuation")
        for k, val in filters.items():
            if val and k in v.columns:
                v = v[v[k] == val]
        return v

    def positions(
        self, run_id: str | None = None, by: str = "desk_id", **filters: str
    ) -> list[dict[str, Any]]:
        v = self.valuation(run_id, **filters)
        if v.empty:
            return []
        g = (
            v.groupby(by, dropna=False)
            .agg(
                pv=("pv", "sum"), trades=("trade_id", "count"), unpriced=("error", lambda s: s.notna().sum())
            )
            .reset_index()
        )
        return _records(g.sort_values("pv", ascending=False))

    def var_by(
        self,
        by: str = "asset_class",
        run_id: str | None = None,
        method: str = "historical_full_revaluation",
        **filters: str,
    ) -> list[dict[str, Any]]:
        r = self.resolve(run_id)
        name = (
            "var_contributions" if method == "historical_full_revaluation" else "var_contributions_challenger"
        )
        contrib = self.repo.load_run_frame(r.run_id, name)
        v = self.valuation(r.run_id, **filters)
        if contrib.empty or v.empty:
            return []
        m = contrib.merge(
            v[["trade_id", *[c for c in DIMENSIONS if c in v.columns and c != "trade_id"]]],
            on="trade_id",
            how="inner",
        )
        g = (
            m.groupby(by, dropna=False)
            .agg(
                component_var=("var_contribution", "sum"),
                component_es=("es_contribution", "sum"),
                trades=("trade_id", "count"),
            )
            .reset_index()
        )
        return _records(g.sort_values("component_var", ascending=False))

    def var_summary(self, run_id: str | None = None) -> list[dict[str, Any]]:
        _, s = self._frame(run_id, "var_summary")
        return _records(s)

    def var_scenarios(self, run_id: str | None = None) -> list[dict[str, Any]]:
        _, s = self._frame(run_id, "var_scenarios")
        return _records(s.sort_values("scenario_date"))

    def sensitivities(
        self,
        run_id: str | None = None,
        measure: str = "DV01",
        by: str = "desk_id",
        bucket_by: str = "bucket",
        **filters: str,
    ) -> list[dict[str, Any]]:
        r, s = self._frame(run_id, "sensitivities")
        v = self.valuation(r.run_id, **filters)
        if s.empty or v.empty:
            return []
        s = s[s["measure"] == measure].merge(
            v[["trade_id", *[c for c in DIMENSIONS if c in v.columns and c != "trade_id"]]], on="trade_id"
        )
        keys = [by, "underlying"] + ([bucket_by] if bucket_by and measure == "DV01" else [])
        g = s.groupby(keys, dropna=False)["value"].sum().reset_index()
        return _records(g)

    def stress(
        self, run_id: str | None = None, by: str | None = "asset_class", **filters: str
    ) -> list[dict[str, Any]]:
        r, summary = self._frame(run_id, "stress_summary")
        if summary.empty:
            return []
        if not by:
            return _records(summary.sort_values("total"))
        rows = self.repo.load_run_frame(r.run_id, "stress")
        v = self.valuation(r.run_id, **filters)
        m = rows.merge(v[["trade_id", by]], on="trade_id", how="inner")
        piv = m.pivot_table(index="scenario_id", columns=by, values="pnl", aggfunc="sum", fill_value=0.0)
        piv["total"] = piv.sum(axis=1)
        piv = piv.join(summary.set_index("scenario_id")[["name", "kind", "description"]]).reset_index()
        return _records(piv.sort_values("total"))

    def limits(
        self, run_id: str | None = None, status: str | None = None, level: str | None = None
    ) -> list[dict]:
        _, lt = self._frame(run_id, "limits")
        if lt.empty:
            return []
        if status:
            lt = lt[lt["status"] == status]
        if level:
            lt = lt[lt["level"] == level]
        return _records(lt)

    def dq(self, run_id: str | None = None) -> list[dict[str, Any]]:
        _, d = self._frame(run_id, "dq_findings")
        return _records(d)

    def pnl(self, run_id: str | None = None, by: str | None = None, **filters: str) -> dict[str, Any]:
        r, steps = self._frame(run_id, "pnl_steps")
        out: dict[str, Any] = {
            "run_id": r.run_id,
            "steps": _records(steps),
            "total": r.summary.get("pnl_total"),
        }
        if by:
            bt = self.repo.load_run_frame(r.run_id, "pnl_by_trade")
            v = self.valuation(r.run_id, **filters)
            m = bt.merge(v[["trade_id", by]], on="trade_id", how="inner")
            piv = m.pivot_table(index=by, columns="step", values="pnl", aggfunc="sum", fill_value=0.0)
            piv["TOTAL"] = piv.sum(axis=1)
            out["by"] = _records(piv.reset_index().sort_values("TOTAL"))
        ch = self.repo.load_run_frame(r.run_id, "pnl_challenger")
        if not ch.empty:
            ch = ch.dropna()
            out["challenger"] = {
                "predicted": float(ch["predicted"].sum()),
                "actual": float(ch["actual"].sum()),
                "residual": float(ch["residual"].sum()),
                "worst_residuals": _records(
                    ch.reindex(ch["residual"].abs().sort_values(ascending=False).index).head(10)
                ),
            }
        return out

    def trade(self, trade_id: str, run_id: str | None = None) -> dict[str, Any]:
        r, v = self._frame(run_id, "valuation")
        row = v[v["trade_id"] == trade_id]
        if row.empty:
            raise KeyError(trade_id)
        s = self.repo.load_run_frame(r.run_id, "sensitivities")
        st = self.repo.load_run_frame(r.run_id, "stress")
        snap = self.repo.load_portfolio_snapshot(r.portfolio_snapshot_id)
        t = next((x for x in snap.trades if x.trade_id == trade_id), None)
        return {
            "run_id": r.run_id,
            "valuation": _records(row)[0],
            "trade": t.model_dump(mode="json") if t else None,
            "sensitivities": _records(s[s["trade_id"] == trade_id]),
            "stress": _records(st[st["trade_id"] == trade_id]),
        }

    def audit(self, subject: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return _records(self.repo.load_audit_events(subject, limit))

    def organisation(self, firm_id: str = "GMB") -> dict[str, Any]:
        return self.repo.load_organisation(firm_id).model_dump(mode="json")
