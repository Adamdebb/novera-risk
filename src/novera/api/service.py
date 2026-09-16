"""Read-side service over stored runs. The API and the UI both go through this class, so
no screen ever computes a number itself (ADR 0004). Every answer carries the run id."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from novera.api.errors import InvalidRequestError
from novera.api.errors import RunNotFoundError as _RunNotFoundError
from novera.domain.breaches import CloseReason
from novera.limits import workflow as wf
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import RunRecord

HIERARCHY = ["firm_id", "business_id", "desk_id", "book_id", "trader_id", "trade_id"]
VAR_CONTRIBUTION_FRAMES: dict[str, str] = {
    "historical_full_revaluation": "var_contributions",
    "delta_gamma_vega": "var_contributions_challenger",
    "monte_carlo_delta_gamma_vega": "var_contributions_monte_carlo",
}
"""Stored per-trade contribution frame for each VaR method the EOD run writes (MR-002, MR-004, MR-010)."""

DIMENSIONS = HIERARCHY + ["legal_entity_id", "asset_class", "product_type", "currency", "counterparty_id"]


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out = df.copy()
    for c in out.columns:
        if str(out[c].dtype).startswith("datetime") or out[c].map(lambda x: hasattr(x, "isoformat")).any():
            out[c] = out[c].map(lambda x: x.isoformat() if hasattr(x, "isoformat") else x)
    return out.astype(object).where(pd.notna(out), None).to_dict(orient="records")


RunNotFoundError = _RunNotFoundError  # re-exported: the service raises the API's own error


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
        if method not in VAR_CONTRIBUTION_FRAMES:
            raise InvalidRequestError(
                f"unknown VaR method {method!r}; one of {', '.join(VAR_CONTRIBUTION_FRAMES)}"
            )
        contrib = self.repo.load_run_frame(r.run_id, VAR_CONTRIBUTION_FRAMES[method])
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

    def organisation(self, firm_id: str | None = None) -> dict[str, Any]:
        """The firm's hierarchy. Without ``firm_id`` the single firm stored in this database."""
        if not firm_id:
            ids = self.repo.firm_ids()
            if not ids:
                raise KeyError("no organisation stored")
            firm_id = ids[0]
        return self.repo.load_organisation(firm_id).model_dump(mode="json")

    # --- trade extract ------------------------------------------------------------------------
    EXTRACT_DIMS = (
        "business_id",
        "desk_id",
        "book_id",
        "legal_entity_id",
        "trader_id",
        "asset_class",
        "product_type",
        "currency",
        "direction",
        "status",
        "clearing",
        "counterparty_id",
        "netting_set_id",
    )
    EXTRACT_LEAD = [
        "trade_id",
        "business_id",
        "desk_id",
        "book_id",
        "legal_entity_id",
        "trader_id",
        "counterparty_id",
        "netting_set_id",
        "clearing",
        "asset_class",
        "product_type",
        "instrument_id",
        "description",
        "currency",
        "direction",
        "swap_side",
        "quantity",
        "trade_price",
        "trade_date",
        "settlement_date",
        "maturity_date",
        "status",
        "source_system",
        "version",
        "pv_local",
        "fx_to_reporting",
        "pv",
        "model",
        "model_version",
        "note",
        "error",
    ]

    def _extract_frame(self, run_id: str | None) -> tuple[RunRecord, pd.DataFrame]:
        """Every trade of the run: valuation row joined with the booked trade and its
        instrument terms flattened into columns (a term a product lacks is empty)."""
        r, v = self._frame(run_id, "valuation")
        snap = self.repo.load_portfolio_snapshot(r.portfolio_snapshot_id)
        booked = []
        for tr in snap.trades:
            d = tr.model_dump(mode="json")
            ins = d.pop("instrument") or {}
            d.pop("validation_errors", None)
            d.pop("booked_at", None)
            for k in ("book_id", "trader_id", "counterparty_id", "quantity", "direction", "status"):
                d.pop(k, None)
            for k, val in ins.items():
                if k in ("currency", "product_type"):
                    continue
                d.setdefault(k, val)
            d["maturity_date"] = (
                ins.get("maturity_date")
                or ins.get("expiry_date")
                or ins.get("end_date")
                or d.get("settlement_date")
            )
            booked.append(d)
        b = pd.DataFrame(booked)
        m = v.merge(b, on="trade_id", how="left") if not b.empty else v
        lead = [c for c in self.EXTRACT_LEAD if c in m.columns]
        rest = sorted(c for c in m.columns if c not in lead)
        return r, m[lead + rest]

    def trade_extract_options(self, run_id: str | None = None) -> dict[str, Any]:
        r, m = self._extract_frame(run_id)
        dims = {
            d: sorted(str(x) for x in m[d].dropna().unique()) for d in self.EXTRACT_DIMS if d in m.columns
        }
        dates = {}
        for c in ("trade_date", "maturity_date"):
            s = m[c].dropna() if c in m.columns else pd.Series(dtype=str)
            dates[c] = {"min": str(s.min()) if len(s) else None, "max": str(s.max()) if len(s) else None}
        return {
            "run_id": r.run_id,
            "business_date": r.business_date.isoformat(),
            "dims": dims,
            "dates": dates,
        }

    def _extract_filtered(self, run_id: str | None, f: dict[str, Any]) -> tuple[RunRecord, pd.DataFrame]:
        r, m = self._extract_frame(run_id)
        for d in self.EXTRACT_DIMS:
            vals = f.get(d)
            if vals and d in m.columns:
                m = m[m[d].astype(str).isin([str(x) for x in vals])]
        for col, lo, hi in (
            ("trade_date", f.get("trade_date_from"), f.get("trade_date_to")),
            ("maturity_date", f.get("maturity_from"), f.get("maturity_to")),
        ):
            if col in m.columns:
                if lo:
                    m = m[m[col].fillna("") >= str(lo)]
                if hi:
                    m = m[m[col].fillna("9999") <= str(hi)]
        if f.get("min_abs_pv"):
            m = m[m["pv"].abs() >= float(f["min_abs_pv"])]
        if f.get("min_abs_quantity"):
            m = m[m["quantity"].abs() >= float(f["min_abs_quantity"])]
        if f.get("q"):
            needle = str(f["q"]).lower()
            hay = m[[c for c in ("trade_id", "instrument_id", "description") if c in m.columns]].astype(str)
            m = m[hay.apply(lambda row: needle in " ".join(row).lower(), axis=1)]
        if f.get("trade_ids"):
            wanted = {str(x).strip() for x in f["trade_ids"] if str(x).strip()}
            m = m[m["trade_id"].isin(wanted)]
        return r, m

    def trade_extract(self, run_id: str | None = None, **filters: Any) -> dict[str, Any]:
        """Trades of the run matching the filters, with valuation and instrument terms."""
        r, m = self._extract_filtered(run_id, filters)
        return {
            "run_id": r.run_id,
            "business_date": r.business_date.isoformat(),
            "reporting_currency": r.reporting_currency,
            "count": int(len(m)),
            "pv_total": float(m["pv"].fillna(0).sum()) if len(m) else 0.0,
            "columns": list(m.columns),
            "rows": _records(m),
        }

    def trade_extract_csv(self, run_id: str | None = None, **filters: Any) -> str:
        _, m = self._extract_filtered(run_id, filters)
        return m.to_csv(index=False)

    # --- limit management ---------------------------------------------------------------------
    LEVEL_RANK = {"firm_id": 0, "business_id": 1, "desk_id": 2, "book_id": 3, "counterparty_id": 4}

    def limit_hierarchy(self, run_id: str | None = None) -> dict[str, Any]:
        """Every limit definition placed in the firm hierarchy, with the selected run's
        utilisation and status beside it (NO_RUN when no run is stored)."""
        defs = self.repo.load_limits()
        ids = self.repo.firm_ids()
        org = self.repo.load_organisation(ids[0]) if ids else None
        businesses = {b.business_id: b.name for b in org.businesses} if org else {}
        desks = {d.desk_id: d for d in org.desks} if org else {}
        books = {b.book_id: b for b in org.books} if org else {}
        firm_name = org.firm.name if org else ""
        cps = {c.counterparty_id: c.name for c in self.repo.load_counterparties()}
        try:
            r = self.resolve(run_id)
            live = {row["limit_id"]: row for row in self.limits(r.run_id)}
            run_ref: dict[str, Any] = {"run_id": r.run_id, "business_date": r.business_date.isoformat()}
        except RunNotFoundError:
            live, run_ref = {}, {"run_id": None, "business_date": None}
        share_types = {"CONCENTRATION", "LEVERAGE", "MARGIN_USAGE", "PB_CONCENTRATION"}
        rows = []
        for lim in defs:
            level, entity = lim.scope.level.value, lim.scope.entity_id
            if level == "firm_id":
                path = [firm_name or entity]
            elif level == "business_id":
                path = [firm_name, businesses.get(entity, entity)]
            elif level == "desk_id":
                d = desks.get(entity)
                business = businesses.get(d.business_id, d.business_id) if d else "?"
                path = [firm_name, business, d.name if d else entity]
            elif level == "book_id":
                b = books.get(entity)
                d = desks.get(b.desk_id) if b else None
                business = businesses.get(d.business_id, "?") if d else "?"
                path = [firm_name, business, d.name if d else "?", b.name if b else entity]
            elif level == "counterparty_id":
                path = ["Counterparties", cps.get(entity, entity)]
            else:
                path = [level.replace("_id", ""), entity]
            scope = lim.scope.model_dump(exclude={"level", "entity_id"}, exclude_none=True)
            cur = live.get(lim.limit_id, {})
            rows.append(
                {
                    "limit_id": lim.limit_id,
                    "limit_type": lim.limit_type.value,
                    "level": level,
                    "level_rank": self.LEVEL_RANK.get(level, 9),
                    "entity_id": entity,
                    "node_name": path[-1],
                    "path": " › ".join(path),
                    "filters": ", ".join(f"{k}={v}" for k, v in scope.items()),
                    "unit": "share"
                    if lim.limit_type.value in share_types
                    else r.reporting_currency
                    if run_ref["run_id"]
                    else "reporting currency",
                    "amount": lim.amount,
                    "warning_threshold": lim.warning_threshold,
                    "owner": lim.owner,
                    "approver": lim.approver,
                    "approval_status": lim.status.value,
                    "effective_from": lim.effective_from.isoformat(),
                    "effective_to": lim.effective_to.isoformat() if lim.effective_to else None,
                    "rationale": lim.rationale,
                    "effective_amount": cur.get("amount", lim.amount),
                    "increase_id": cur.get("increase_id"),
                    "current": cur.get("current"),
                    "utilisation": cur.get("utilisation"),
                    "status": cur.get("status", "NO_RUN"),
                    "trades_in_scope": cur.get("trades_in_scope"),
                }
            )
        rows.sort(key=lambda x: (x["level_rank"], x["path"], x["limit_type"], x["limit_id"]))
        return {**run_ref, "rows": rows}

    def product_reference(self) -> dict[str, Any]:
        """What the platform prices, with which model and methodology record (from code)."""
        from novera.pricing.catalogue import product_reference

        return product_reference()

    def model_inventory(self) -> dict[str, Any]:
        """Model used, market standard, simplifications and rating per product (MV-001)."""
        from novera.pricing.catalogue import model_inventory

        return model_inventory()

    def measure_reference(self) -> dict[str, Any]:
        """Every risk measure by area, with its methodology record (from code)."""
        from novera.risk.catalogue import measure_reference

        return measure_reference()

    def risk_factor_reference(self) -> dict[str, Any]:
        """The stored risk-factor universe every measure keys off."""
        return {"factors": [f.model_dump(mode="json") for f in self.repo.load_risk_factors()]}

    def market_data_sources(self) -> dict[str, Any]:
        """Every stored risk factor with where its history comes from (real source or the
        simulator) and the free and paid sources that could replace it (MD-001)."""
        from novera.market_data.catalogue import market_data_sources

        return market_data_sources(self.repo.load_risk_factors(), self.repo.load_market_provenance())

    def signoff_status(self, run_id: str | None = None) -> dict[str, Any]:
        """Sign-off state of every metric of the run and its release status (OPS-003)."""
        from novera.workflows import signoff

        return signoff.status(self.repo, self.resolve(run_id))

    def signoff_policy(self) -> dict[str, Any]:
        """Which metrics must be signed before a run is released (OPS-003)."""
        from novera.workflows import signoff

        return signoff.policy(self.repo)

    def signoff_queue(self, limit: int = 20) -> list[dict[str, Any]]:
        """Release status of the recent completed EOD runs (OPS-003)."""
        from novera.workflows import signoff

        return signoff.queue(self.repo, limit)

    def rerun_options(self) -> dict[str, Any]:
        """The EOD stages an administrator can re-run on a stored run, and the re-runs made so
        far (OPS-002)."""
        from novera.workflows.rerun import RECORD, STAGES

        reruns = [
            {**self._run_dict(r), "rerun": r.summary.get("rerun", {})}
            for r in self.repo.list_runs(run_type="RERUN", limit=50)
        ]
        return {"record": RECORD, "stages": [s.to_dict() for s in STAGES], "reruns": reruns}

    def stress_library(self) -> dict[str, Any]:
        """The stress library by category with the shocks each scenario applies; historical
        windows show the realised moves of headline factors in the stored history (MR-005)."""
        from novera.market_data.history import MarketHistory
        from novera.risk.stress_catalogue import HEADLINE_FACTORS, stress_library

        universe = self.repo.load_risk_factors()
        rows = self.repo.load_market_history(factor_ids=[fid for fid, _ in HEADLINE_FACTORS])
        history = MarketHistory.from_long(rows) if len(rows) else None
        return stress_library(history, universe, self.repo.load_market_provenance())

    def counterparty_reference(self) -> dict[str, Any]:
        """Static counterparty reference data: counterparties, netting sets and CSA terms,
        independent of any run."""
        netting_sets, csas = self.repo.load_netting_sets()
        return {
            "counterparties": [c.model_dump(mode="json") for c in self.repo.load_counterparties()],
            "netting_sets": [n.model_dump(mode="json") for n in netting_sets],
            "csas": [c.model_dump(mode="json") for c in csas],
        }

    # --- breach workflow (reads) -------------------------------------------------------------
    def breaches(self, open_only: bool = True, limit_id: str | None = None) -> list[dict[str, Any]]:
        return [b.model_dump(mode="json") for b in self.repo.load_breaches(open_only, limit_id)]

    def breach(self, breach_id: str) -> dict[str, Any]:
        b = self.repo.load_breach(breach_id)
        d = b.model_dump(mode="json")
        d["actions"] = [a.model_dump(mode="json") for a in self.repo.load_breach_actions(breach_id)]
        d["increases"] = [i.model_dump(mode="json") for i in self.repo.load_increases(limit_id=b.limit_id)]
        return d

    def increases(self, status: str | None = None, limit_id: str | None = None) -> list[dict[str, Any]]:
        out = []
        limits = {lim.limit_id: lim for lim in self.repo.load_limits()}
        for i in self.repo.load_increases(status, limit_id):
            d = i.model_dump(mode="json")
            lim = limits.get(i.limit_id)
            d["allowed_approvers"] = list(wf.allowed_approvers(i, lim)) if lim else []
            d["increase_pct"] = i.increase_pct
            out.append(d)
        return out

    # --- run comparison ------------------------------------------------------------------------
    def compare(self, run_a: str | None, run_b: str | None, by: str = "asset_class") -> dict[str, Any]:
        """What changed between two runs: headline numbers, VaR by group, limit statuses,
        breaches, and the trades whose PV or VaR contribution moved most."""
        a, b = self.resolve(run_a), self.resolve(run_b)
        sa, sb = a.summary, b.summary
        keys = [
            "pv",
            "var",
            "es",
            "challenger_var",
            "worst_stress",
            "breaches",
            "warnings",
            "dq_findings",
            "pnl_total",
        ]
        headline = {
            k: {
                "a": sa.get(k),
                "b": sb.get(k),
                "change": (sb.get(k) or 0) - (sa.get(k) or 0) if sa.get(k) is not None else None,
            }
            for k in keys
        }
        va = (
            pd.DataFrame(self.var_by(by, a.run_id)).set_index(by)["component_var"]
            if self.var_by(by, a.run_id)
            else pd.Series(dtype=float)
        )
        vb = (
            pd.DataFrame(self.var_by(by, b.run_id)).set_index(by)["component_var"]
            if self.var_by(by, b.run_id)
            else pd.Series(dtype=float)
        )
        var_by = pd.DataFrame({"a": va, "b": vb}).fillna(0.0)
        var_by["change"] = var_by["b"] - var_by["a"]
        la = (
            pd.DataFrame(self.limits(a.run_id)).set_index("limit_id")
            if self.limits(a.run_id)
            else pd.DataFrame()
        )
        lb = (
            pd.DataFrame(self.limits(b.run_id)).set_index("limit_id")
            if self.limits(b.run_id)
            else pd.DataFrame()
        )
        limit_changes = []
        if not la.empty and not lb.empty:
            j = la[["status", "utilisation", "amount"]].join(
                lb[["status", "utilisation", "amount", "owner"]], lsuffix="_a", rsuffix="_b", how="outer"
            )
            moved = j[
                (j["status_a"] != j["status_b"]) | ((j["utilisation_b"] - j["utilisation_a"]).abs() > 0.1)
            ]
            limit_changes = _records(moved.reset_index().sort_values("utilisation_b", ascending=False))
        vla, vlb = self.valuation(a.run_id), self.valuation(b.run_id)
        m = vla[["trade_id", "pv", "desk_id", "product_type"]].merge(
            vlb[["trade_id", "pv"]], on="trade_id", how="outer", suffixes=("_a", "_b"), indicator=True
        )
        m["pv_change"] = m["pv_b"].fillna(0.0) - m["pv_a"].fillna(0.0)
        m["presence"] = m["_merge"].map({"both": "both", "left_only": "only in A", "right_only": "only in B"})
        top_trades = _records(
            m.reindex(m["pv_change"].abs().sort_values(ascending=False).index).head(15).drop(columns="_merge")
        )
        breaches_b = self.breaches(open_only=False)
        return {
            "a": self._run_dict(a),
            "b": self._run_dict(b),
            "headline": headline,
            "var_by": _records(var_by.reset_index().rename(columns={"index": by})),
            "limit_changes": limit_changes,
            "top_trade_changes": top_trades,
            "trades_only_in_a": int((m["_merge"] == "left_only").sum()),
            "trades_only_in_b": int((m["_merge"] == "right_only").sum()),
            "breaches": [
                x
                for x in breaches_b
                if x["first_run_id"] in (a.run_id, b.run_id) or x["latest_run_id"] in (a.run_id, b.run_id)
            ],
        }

    # --- alerts, jobs, reconciliation, provenance ----------------------------------------------
    def alerts(self, limit: int = 200, status: str | None = None, severity: str | None = None) -> list[dict]:
        return [
            {k: v for k, v in a.items() if k != "dedupe_key"}
            for a in self.repo.load_alerts(limit, status, severity)
        ]

    def jobs(self, limit: int = 100) -> list[dict]:
        return self.repo.load_jobs(limit)

    def reconciliation(self, run_id: str | None = None) -> dict[str, Any] | None:
        r = self.resolve(run_id)
        summ = self.repo.load_run_frame(r.run_id, "recon_summary")
        if summ.empty:
            return None
        row = summ.iloc[-1].to_dict()
        row["run_id"] = r.run_id
        for k in ("attribution", "findings", "meta"):
            if isinstance(row.get(k), str):
                try:
                    row[k] = json.loads(row[k])
                except json.JSONDecodeError:
                    pass
        row["by_desk"] = _records(self.repo.load_run_frame(r.run_id, "recon_by_desk"))
        detail = self.repo.load_run_frame(r.run_id, "recon_detail")
        row["cause_counts"] = (
            detail["cause"].replace("", "MATCH").value_counts().to_dict() if len(detail) else {}
        )
        row["largest_differences"] = (
            _records(detail.reindex(detail["var_diff"].abs().sort_values(ascending=False).index).head(15))
            if len(detail)
            else []
        )
        return row

    def provenance(self) -> list[dict]:
        return _records(self.repo.load_market_provenance())

    # --- concentration, liquidity, backtest, risk pack -------------------------------------------
    def concentration(self, run_id: str | None = None) -> dict[str, Any]:
        r = self.resolve(run_id)
        flags = self.repo.load_run_frame(r.run_id, "risk_flags")
        return {
            "run_id": r.run_id,
            "by_dimension": _records(self.repo.load_run_frame(r.run_id, "concentration")),
            "top_positions": _records(self.repo.load_run_frame(r.run_id, "concentration_top")),
            "tenor": _records(self.repo.load_run_frame(r.run_id, "concentration_tenor")),
            "flags": [x["message"] for x in _records(flags) if x.get("kind") == "CONCENTRATION"],
        }

    def lookthrough(self, run_id: str | None = None) -> dict[str, Any]:
        """ETF and mutual-fund holdings decomposed into constituents (MR-014)."""
        r = self.resolve(run_id)
        try:
            holdings = self.repo.load_run_frame(r.run_id, "lookthrough_holdings")
            cons = self.repo.load_run_frame(r.run_id, "lookthrough_constituents")
        except Exception:  # noqa: BLE001 - runs made before Phase 6 have no look-through frames
            holdings, cons = pd.DataFrame(), pd.DataFrame()
        flags = self.repo.load_run_frame(r.run_id, "risk_flags")
        by_fund = (
            _records(
                holdings.groupby(["fund", "product_type"], as_index=False)
                .agg(
                    trades=("trade_id", "nunique"),
                    exposure=("exposure", "sum"),
                    constituents=("constituent", "nunique"),
                )
                .sort_values("exposure", key=abs, ascending=False)
            )
            if len(holdings)
            else []
        )
        return {
            "run_id": r.run_id,
            "fund_trades": int(holdings["trade_id"].nunique()) if len(holdings) else 0,
            "by_fund": by_fund,
            "holdings": _records(holdings),
            "constituents": _records(cons),
            "flags": [x["message"] for x in _records(flags) if x.get("kind") == "LOOKTHROUGH"],
        }

    def market_data_proxies(self, run_id: str | None = None) -> dict[str, Any]:
        """What the run proxied before pricing, from what and why (MD-002)."""
        r = self.resolve(run_id)
        try:
            actions = self.repo.load_run_frame(r.run_id, "md_proxies")
        except Exception:  # noqa: BLE001
            actions = pd.DataFrame()
        applied = actions[actions["kind"] != "KEPT_STALE"] if len(actions) else actions
        return {
            "run_id": r.run_id,
            "raw_market_snapshot_id": r.market_snapshot_id,
            "applied": int(len(applied)),
            "kept_stale": int(len(actions) - len(applied)),
            "actions": _records(actions),
        }

    def liquidity(self, run_id: str | None = None) -> dict[str, Any]:
        r = self.resolve(run_id)
        flags = self.repo.load_run_frame(r.run_id, "risk_flags")
        trades = self.repo.load_run_frame(r.run_id, "liquidity_trades")
        return {
            "run_id": r.run_id,
            "var": r.summary.get("var"),
            "liquidity_adjusted_var": r.summary.get("liquidity_adjusted_var"),
            "horizon_days": r.summary.get("liquidity_horizon_days"),
            "by_bucket": _records(self.repo.load_run_frame(r.run_id, "liquidity_buckets")),
            "by_desk": _records(self.repo.load_run_frame(r.run_id, "liquidity_desks")),
            "slowest": _records(trades.head(20)) if len(trades) else [],
            "flags": [x["message"] for x in _records(flags) if x.get("kind") == "LIQUIDITY"],
        }

    def backtest(self, run_id: str | None = None) -> dict[str, Any]:
        r = self.resolve(run_id)
        return {
            "run_id": r.run_id,
            "summary": _records(self.repo.load_run_frame(r.run_id, "backtest_summary")),
            "series": _records(self.repo.load_run_frame(r.run_id, "backtest_series")),
            "live_series": _records(self.repo.load_run_frame(r.run_id, "backtest_live_series")),
        }

    def risk_pack(self, run_id: str | None, out_dir: str, pdf: bool = True) -> dict[str, Any]:
        from pathlib import Path as _Path

        from novera.reporting import build_pack

        files = build_pack(self.repo, run_id, _Path(out_dir), pdf=pdf)
        return {
            "html": str(files.html),
            "xlsx": str(files.xlsx),
            "pdf": str(files.pdf) if files.pdf else None,
        }

    # --- counterparty risk ---------------------------------------------------------------------
    def counterparties(self, run_id: str | None = None) -> dict[str, Any]:
        r = self.resolve(run_id)
        summ = self.repo.load_run_frame(r.run_id, "cp_counterparty_summary")
        notes = self.repo.load_run_frame(r.run_id, "cp_notes")
        return {
            "run_id": r.run_id,
            "summary": _records(summ),
            "notes": notes.iloc[0].to_dict() if len(notes) else {},
            "stressed": _records(self.repo.load_run_frame(r.run_id, "cp_stressed").head(30)),
        }

    def counterparty(self, counterparty_id: str, run_id: str | None = None) -> dict[str, Any]:
        r = self.resolve(run_id)
        prof = self.repo.load_run_frame(r.run_id, "cp_profiles")
        prof = prof[prof["counterparty_id"] == counterparty_id]
        if prof.empty:
            raise KeyError(f"no exposure stored for {counterparty_id}")
        agg = (
            prof.groupby(["step", "years"], as_index=False)
            .agg(
                ee=("ee", "sum"),
                ee_gross=("ee_gross", "sum"),
                pfe95=("pfe95", "sum"),
                pfe99=("pfe99", "sum"),
                pfe95_gross=("pfe95_gross", "sum"),
                ene=("ene", "sum"),
                mean_collateral=("mean_collateral", "sum"),
            )
            .sort_values("years")
        )
        ns_summ = self.repo.load_run_frame(r.run_id, "cp_netting_summary")
        ns_ids = sorted(prof["netting_set_id"].unique())
        netting_sets, csas = self.repo.load_netting_sets()
        csa_map = {c.csa_id: c for c in csas}
        sets = []
        for n in netting_sets:
            if n.netting_set_id in ns_ids:
                d = n.model_dump(mode="json")
                d["csa"] = (
                    csa_map[n.csa_id].model_dump(mode="json") if n.csa_id and n.csa_id in csa_map else None
                )
                sets.append(d)
        cva = self.repo.load_run_frame(r.run_id, "cp_cva")
        wwr = self.repo.load_run_frame(r.run_id, "cp_wwr")
        stressed = self.repo.load_run_frame(r.run_id, "cp_stressed")
        val = self.valuation(r.run_id, counterparty_id=counterparty_id)
        cp = next((c for c in self.repo.load_counterparties() if c.counterparty_id == counterparty_id), None)
        return {
            "run_id": r.run_id,
            "counterparty": cp.model_dump(mode="json") if cp else None,
            "profile": _records(agg),
            "netting_sets": sets,
            "netting_summary": _records(ns_summ[ns_summ["netting_set_id"].isin(ns_ids)])
            if len(ns_summ)
            else [],
            "cva": _records(cva[cva["counterparty_id"] == counterparty_id]) if len(cva) else [],
            "wwr": _records(wwr[wwr["counterparty_id"] == counterparty_id]) if len(wwr) else [],
            "stressed": _records(stressed[stressed["counterparty_id"] == counterparty_id])
            if len(stressed)
            else [],
            "trades": _records(
                val[
                    [
                        "trade_id",
                        "book_id",
                        "desk_id",
                        "product_type",
                        "currency",
                        "quantity",
                        "pv",
                        "netting_set_id",
                    ]
                ]
                if "netting_set_id" in val
                else val
            ),
        }

    def csa_what_if(
        self,
        netting_set_id: str,
        run_id: str | None = None,
        threshold_they_post: float | None = None,
        threshold_we_post: float | None = None,
        minimum_transfer_amount: float | None = None,
        independent_amount: float | None = None,
        uncollateralised: bool = False,
        margin_period_days: int = 10,
    ) -> dict[str, Any]:
        """Re-collateralise one netting set's stored paths under alternative CSA terms."""
        from novera.counterparty_risk import csa_what_if, load_exposure_result
        from novera.domain.counterparties import CSA

        r = self.resolve(run_id)
        res = load_exposure_result(self.repo, r.run_id)
        if res is None or netting_set_id not in res.netting_values:
            raise KeyError(f"no stored exposure paths for {netting_set_id}")
        ns = res.netting_sets[netting_set_id]
        base_csa = res.csas.get(ns.csa_id) if ns.csa_id else None
        if uncollateralised:
            new_csa = None
        else:
            proto = base_csa or CSA(
                csa_id="WHATIF",
                collateral_currency=r.reporting_currency,
                threshold_we_post=0.0,
                threshold_they_post=0.0,
                minimum_transfer_amount=0.0,
            )
            new_csa = proto.model_copy(
                update={
                    k: v
                    for k, v in {
                        "csa_id": "WHATIF",
                        "threshold_they_post": threshold_they_post,
                        "threshold_we_post": threshold_we_post,
                        "minimum_transfer_amount": minimum_transfer_amount,
                        "independent_amount": independent_amount,
                    }.items()
                    if v is not None
                }
            )
        before = csa_what_if(res, netting_set_id, base_csa, margin_period_days)
        after = csa_what_if(res, netting_set_id, new_csa, margin_period_days)
        return {
            "run_id": r.run_id,
            "netting_set_id": netting_set_id,
            "base_csa": base_csa.model_dump(mode="json") if base_csa else None,
            "what_if_csa": new_csa.model_dump(mode="json") if new_csa else None,
            "before": _records(before[["step", "years", "ee", "pfe95", "pfe99", "mean_collateral"]]),
            "after": _records(after[["step", "years", "ee", "pfe95", "pfe99", "mean_collateral"]]),
            "peak_pfe95_before": float(before["pfe95"].max()),
            "peak_pfe95_after": float(after["pfe95"].max()),
            "epe_before": float(before[before["years"] <= 1]["ee"].mean()),
            "epe_after": float(after[after["years"] <= 1]["ee"].mean()),
        }

    # --- regulatory capital ------------------------------------------------------------------------
    def capital(self, run_id: str | None = None) -> dict[str, Any]:
        r = self.resolve(run_id)
        summ = self.repo.load_run_frame(r.run_id, "reg_summary")
        if summ.empty:
            return {"run_id": r.run_id, "available": False}
        return {
            "run_id": r.run_id,
            "available": True,
            "summary": summ.iloc[0].to_dict(),
            "components": _records(self.repo.load_run_frame(r.run_id, "reg_capital")),
            "by_desk": _records(self.repo.load_run_frame(r.run_id, "reg_by_desk")),
            "frtb_sa_classes": _records(self.repo.load_run_frame(r.run_id, "reg_frtb_sa_classes")),
            "frtb_sa_detail": _records(self.repo.load_run_frame(r.run_id, "reg_frtb_sa_detail")),
            "ima": _records(self.repo.load_run_frame(r.run_id, "reg_frtb_ima_summary")),
            "pla": _records(self.repo.load_run_frame(r.run_id, "reg_frtb_ima_pla")),
            "saccr_counterparty": _records(self.repo.load_run_frame(r.run_id, "reg_saccr_counterparty")),
            "saccr_netting": _records(self.repo.load_run_frame(r.run_id, "reg_saccr_netting")),
            "simm": _records(self.repo.load_run_frame(r.run_id, "reg_simm")),
            "ba_cva": _records(self.repo.load_run_frame(r.run_id, "reg_ba_cva")),
            "cash_ladder": _records(self.repo.load_run_frame(r.run_id, "reg_cash_ladder")),
        }

    # --- fund face -----------------------------------------------------------------------------------
    def fund(self, run_id: str | None = None) -> dict[str, Any]:
        r = self.resolve(run_id)
        summ = self.repo.load_run_frame(r.run_id, "fund_summary")
        if summ.empty:
            return {"run_id": r.run_id, "available": False}
        org = self.repo.load_organisation(r.config.get("firm_id", "MSF"))
        fund = self.repo.load_fund(org.firm.firm_id)
        return {
            "run_id": r.run_id,
            "available": True,
            "summary": summ.iloc[0].to_dict(),
            "fund": fund.model_dump(mode="json") if fund else None,
            "var": r.summary.get("var"),
            "es": r.summary.get("es"),
            "worst_stress": r.summary.get("worst_stress"),
            "worst_stress_name": r.summary.get("worst_stress_name"),
            "exposure_strategy": _records(self.repo.load_run_frame(r.run_id, "fund_exposure_strategy")),
            "exposure_asset_class": _records(self.repo.load_run_frame(r.run_id, "fund_exposure_asset_class")),
            "margin_pb": _records(self.repo.load_run_frame(r.run_id, "fund_margin_pb")),
            "margin_detail": _records(self.repo.load_run_frame(r.run_id, "fund_margin_detail")),
            "factors": _records(self.repo.load_run_frame(r.run_id, "fund_factors")),
            "redemptions": _records(self.repo.load_run_frame(r.run_id, "fund_redemptions")),
            "attribution": _records(self.repo.load_run_frame(r.run_id, "fund_attribution")),
            "crowding": _records(self.repo.load_run_frame(r.run_id, "fund_crowding")),
            "flags": [x["message"] for x in _records(self.repo.load_run_frame(r.run_id, "fund_flags"))],
        }


class RiskWriteService:
    """Write side: breach actions and temporary increases. Opened on a writable connection."""

    def __init__(self, repo: DuckDBRepository) -> None:
        self.repo = repo

    def rerun_stage(self, run_id: str, stage: str, actor: str, reason: str = "") -> dict[str, Any]:
        """Re-run one EOD stage of a stored run into a new RERUN run; the parent is untouched
        (OPS-002)."""
        from novera.workflows.rerun import rerun_stage

        rid = RiskService(self.repo).resolve(run_id).run_id
        try:
            res = rerun_stage(self.repo, rid, stage, actor, reason)
        except ValueError as e:
            raise InvalidRequestError(str(e)) from e
        return {**RiskService._run_dict(res.run), "rerun": res.run.summary.get("rerun", {})}

    def sign_metric(self, run_id: str, metric_id: str, actor: str, comment: str = "") -> dict[str, Any]:
        from novera.workflows import signoff

        return signoff.sign(self.repo, RiskService(self.repo).resolve(run_id), metric_id, actor, comment)

    def reject_metric(self, run_id: str, metric_id: str, actor: str, comment: str = "") -> dict[str, Any]:
        from novera.workflows import signoff

        return signoff.reject(self.repo, RiskService(self.repo).resolve(run_id), metric_id, actor, comment)

    def set_signoff_policy(self, actor: str, required: list[str], comment: str = "") -> dict[str, Any]:
        from novera.workflows import signoff

        return signoff.set_policy(self.repo, actor, list(required), comment)

    def acknowledge(self, breach_id: str, actor: str, comment: str = "") -> dict[str, Any]:
        b, _ = wf.acknowledge(self.repo, breach_id, actor, comment)
        return b.model_dump(mode="json")

    def escalate(
        self, breach_id: str, actor: str, to: str | None = None, comment: str = ""
    ) -> dict[str, Any]:
        b, _ = wf.escalate(self.repo, breach_id, actor, to, comment)
        return b.model_dump(mode="json")

    def comment(self, breach_id: str, actor: str, text: str) -> dict[str, Any]:
        b, _ = wf.comment(self.repo, breach_id, actor, text)
        return b.model_dump(mode="json")

    def close(self, breach_id: str, actor: str, reason: str, comment: str = "") -> dict[str, Any]:
        b, _ = wf.close(self.repo, breach_id, actor, CloseReason(reason), comment)
        return b.model_dump(mode="json")

    def request_increase(
        self,
        limit_id: str,
        new_amount: float,
        expires_on: str,
        requested_by: str,
        rationale: str,
        effective_from: str | None = None,
        breach_id: str | None = None,
    ) -> dict[str, Any]:
        from datetime import date as _date

        inc, _ = wf.request_increase(
            self.repo,
            limit_id,
            new_amount,
            _date.fromisoformat(expires_on),
            requested_by,
            rationale,
            _date.fromisoformat(effective_from) if effective_from else None,
            breach_id,
        )
        return inc.model_dump(mode="json")

    def decide_increase(
        self, increase_id: str, approver: str, approve: bool, comment: str = ""
    ) -> dict[str, Any]:
        inc, _ = wf.decide_increase(self.repo, increase_id, approver, approve, comment)
        return inc.model_dump(mode="json")

    def cancel_increase(self, increase_id: str, actor: str) -> dict[str, Any]:
        inc, _ = wf.cancel_increase(self.repo, increase_id, actor)
        return inc.model_dump(mode="json")
