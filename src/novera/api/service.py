"""Read-side service over stored runs. The API and the UI both go through this class, so
no screen ever computes a number itself (ADR 0004). Every answer carries the run id."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from novera.api.errors import ForbiddenError, InvalidRequestError, NotFoundError
from novera.api.errors import RunNotFoundError as _RunNotFoundError
from novera.domain.breaches import CloseReason
from novera.limits import workflow as wf
from novera.security import Action, User, oversees, require, require_on_limit
from novera.security.permissions import require_signer, require_stress_proposal
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import RunRecord

STRESS_META = (
    "scenario_id",
    "total",
    "name",
    "kind",
    "description",
    "goal",
    "version",
    "source",
    "consistent",
    "covariance",
    "z_score",
    "exceedance",
    "correlation_z",
    "ruler_z",
    "filled",
)
"""Columns of a stress row that describe the scenario, not a group's P&L (OPS-005, MR-018)."""

HIERARCHY = ["firm_id", "business_id", "desk_id", "book_id", "trader_id", "trade_id"]
VAR_CONTRIBUTION_FRAMES: dict[str, str] = {
    "historical_full_revaluation": "var_contributions",
    "delta_gamma_vega": "var_contributions_challenger",
    "monte_carlo_delta_gamma_vega": "var_contributions_monte_carlo",
}
"""Stored per-trade contribution frame for each VaR method the EOD run writes (MR-002, MR-004, MR-010)."""

GEOGRAPHY = ["region", "country", "risk_currency", "currency_pair"]
"""Geography dimensions (decision round 44): an FX trade's currency is the one its pair attaches
to, any other trade's its own currency; country and region follow from the currency."""
DIMENSIONS = HIERARCHY + [
    "legal_entity_id",
    "asset_class",
    "product_type",
    "currency",
    "counterparty_id",
    *GEOGRAPHY,
]


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
        r, v = self._frame(run_id, "valuation")
        if not v.empty:
            v = self._with_geography(r, v)
        for k, val in filters.items():
            if val and k in v.columns:
                v = v[v[k] == val]
        return v

    def _with_geography(self, r: RunRecord, v: pd.DataFrame) -> pd.DataFrame:
        """The valuation with the geography dimensions; the pair comes from the run's portfolio
        snapshot, so every stored run gets them. Left empty when the database has no table yet
        (``novera reference seed``)."""
        geo = self.repo.load_geography()
        if geo is None:
            return v.assign(**{c: None for c in GEOGRAPHY})
        try:
            trades = self.repo.load_portfolio_snapshot(r.portfolio_snapshot_id).trades
        except RuntimeError:  # stored under an older trade model: FX trades fall back to their currency
            trades = ()
        pair = {
            t.trade_id: t.instrument.pair.replace("/", "") for t in trades if hasattr(t.instrument, "pair")
        }
        pairs = v["trade_id"].map(pair)
        ccy = pairs.map(lambda p: geo.pair_currency(p) if isinstance(p, str) else None).fillna(v["currency"])
        info = {c: geo.describe(c) for c in ccy.dropna().unique()}
        return v.assign(
            currency_pair=pairs,
            risk_currency=ccy,
            country=ccy.map(lambda c: info.get(c, {}).get("country_name")),
            region=ccy.map(lambda c: info.get(c, {}).get("region_name")),
        )

    def geography(self) -> dict[str, Any]:
        """The static table: regions, countries, currencies with liquidity, attached pairs."""
        geo = self.repo.load_geography()
        if geo is None:
            return {"regions": [], "countries": [], "currencies": [], "currency_pairs": []}
        return {
            "regions": [{"region": k, "name": n} for k, n in geo.regions.items()],
            "countries": [
                {
                    "country": k,
                    "name": n,
                    "region": r,
                    "region_name": geo.regions.get(r),
                    "currency": c,
                    "is_union": u,
                }
                for k, (n, r, c, u) in sorted(geo.countries.items())
            ],
            "currencies": [
                {"currency": k, "name": n, "country": c, "liquidity_score": sc, **geo.describe(k)}
                for k, (n, c, sc) in sorted(geo.currencies.items(), key=lambda kv: kv[1][2])
            ],
            "currency_pairs": [
                {
                    **p,
                    **geo.describe(p["currency"]),
                    "liquidity_scores": [geo.score(p["currency_1"]), geo.score(p["currency_2"])],
                }
                for p in self.repo.load_currency_pairs()
            ],
        }

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
        measure_id: str | None = None,
        **filters: str,
    ) -> list[dict[str, Any]]:
        """Component VaR and ES by group: of a stored method's frame, or of one measure of
        the run's VaR setup when ``measure_id`` is given (OPS-004)."""
        r = self.resolve(run_id)
        if measure_id:
            contrib = self.repo.load_run_frame(r.run_id, "var_measure_contributions")
            contrib = contrib[contrib["measure_id"] == measure_id] if len(contrib) else contrib
        else:
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
        wanted = [c for c in STRESS_META if c not in ("scenario_id", "total")]
        meta = [c for c in wanted if c in summary.columns]
        piv = piv.join(summary.set_index("scenario_id")[meta]).reset_index()
        return _records(piv.sort_values("total"))

    def factor_stress(
        self, run_id: str | None = None, desk_id: str | None = None, top: int = 25
    ) -> dict[str, Any]:
        """Standalone shocks (the ``top`` worst), sliding scales, jump to default by issuer and the
        worst windows (firm only) of one run, for the firm or one desk (MR-019)."""
        r, alone = self._frame(run_id, "factor_stress")
        out: dict[str, Any] = {
            "record": "MR-019",
            "run_id": r.run_id,
            "available": not alone.empty,
            "desk_id": desk_id,
        }
        if alone.empty:
            return out
        out["desks"] = sorted(str(d) for d in alone["desk_id"].unique())
        if desk_id:
            alone = alone[alone["desk_id"] == desk_id]
        st = alone.groupby(["factor_id", "direction"], as_index=False).agg(
            shock=("shock", "first"), pnl=("pnl", "sum")
        )
        out["standalone"] = _records(st.sort_values(["pnl", "factor_id"]).head(top))
        scales = self.repo.load_run_frame(r.run_id, "factor_stress_scales")
        scales = scales[scales["desk_id"] == (desk_id or "(firm)")]
        if desk_id:  # the desk's own curve of every scaled factor; zero where the desk has no position
            grid = self.repo.load_run_frame(r.run_id, "factor_stress_scales")
            grid = grid[grid["desk_id"] == "(firm)"][["factor_id", "sigmas", "shock"]]
            scales = grid.merge(
                scales[["factor_id", "sigmas", "pnl"]], on=["factor_id", "sigmas"], how="left"
            )
            scales["pnl"] = scales["pnl"].fillna(0.0)
            moved = scales.groupby("factor_id")["pnl"].apply(lambda p: (p != 0).any())
            scales = scales[scales["factor_id"].isin(moved[moved].index)]
        out["scale_factors"] = list(dict.fromkeys(scales["factor_id"]))
        out["scales"] = _records(scales[["factor_id", "sigmas", "shock", "pnl"]])
        jtd = self.repo.load_run_frame(r.run_id, "jump_to_default")
        if desk_id:
            jtd = jtd[jtd["desk_id"] == desk_id]
        if len(jtd):
            jtd = jtd.groupby(["issuer"], as_index=False).agg(
                equity=("equity", "first"),
                equity_pnl=("equity_pnl", "sum"),
                cds_pnl=("cds_pnl", "sum"),
                pnl=("pnl", "sum"),
            )
        out["jump_to_default"] = _records(jtd.sort_values(["pnl", "issuer"])) if len(jtd) else []
        if not desk_id:
            w = self.repo.load_run_frame(r.run_id, "worst_windows")
            for c in ("window_start", "window_end"):
                w[c] = w[c].astype(str)
            out["windows"] = _records(w.sort_values(["horizon_days", "version", "rank"]))
        return out

    def stress_explain(
        self, run_id: str | None = None, scenario_id: str | None = None, desk_id: str | None = None
    ) -> dict[str, Any]:
        """One scenario's loss by factor family and term (firm or one desk), its worst trades, the
        hedge-failure line of every scenario and the summary of significant risks (MR-020)."""
        r, ex = self._frame(run_id, "stress_explain")
        out: dict[str, Any] = {"record": "MR-020", "run_id": r.run_id, "available": not ex.empty}
        if ex.empty:
            return out
        summ = self.repo.load_run_frame(r.run_id, "stress_summary").set_index("scenario_id")
        firm = ex.groupby("scenario_id")["total"].sum()
        basis = ex[ex["family"] == "BASIS"].groupby("scenario_id")["total"].sum()
        out["desks"] = sorted(str(d) for d in ex["desk_id"].unique())
        out["scenarios"] = [
            {
                "scenario_id": sid,
                "name": str(summ["name"].get(sid, sid)),
                "goal": str(summ["goal"].get(sid, "LIMIT")),
                "total": float(firm[sid]),
                "hedge_failure": float(basis.get(sid, 0.0)),
            }
            for sid in firm.sort_values().index
        ]
        sid = scenario_id or out["scenarios"][0]["scenario_id"]
        out["scenario_id"], out["desk_id"] = sid, desk_id
        rows = ex[ex["scenario_id"] == sid]
        if desk_id:
            rows = rows[rows["desk_id"] == desk_id]
        terms = ["delta", "gamma", "vega", "higher_order", "cross_family", "total"]
        out["families"] = _records(rows.groupby("family", as_index=False)[terms].sum().sort_values("total"))
        top = self.repo.load_run_frame(r.run_id, "stress_top_trades")
        top = top[top["scenario_id"] == sid]
        if desk_id:
            top = top[top["desk_id"] == desk_id]
        out["top_trades"] = _records(top.sort_values("pnl").drop(columns=["scenario_id"]).head(10))
        rs = self.repo.load_run_frame(r.run_id, "risk_summary")
        out["risk_summary"] = _records(rs) if len(rs) else []
        return out

    def reverse_stress(self, run_id: str | None = None, target_id: str | None = None) -> dict[str, Any]:
        """The scenarios that lose each stress-loss limit (most plausible first), and for one target
        their near neighbours and what drives each loss (MR-021)."""
        r, sol = self._frame(run_id, "reverse_stress")
        out: dict[str, Any] = {"record": "MR-021", "run_id": r.run_id, "available": not sol.empty}
        if sol.empty:
            return out
        order = {"FIRM": 0, "DESK": 1}
        sol = sol.assign(_o=sol["level"].map(order).fillna(2)).sort_values(["_o", "target_id", "solution"])
        out["solutions"] = _records(sol.drop(columns=["_o"]))
        tid = target_id or str(sol["target_id"].iloc[0])
        out["target_id"] = tid
        nb = self.repo.load_run_frame(r.run_id, "reverse_stress_neighbours")
        out["neighbours"] = _records(nb[nb["target_id"] == tid]) if len(nb) else []
        mv = self.repo.load_run_frame(r.run_id, "reverse_stress_moves")
        out["moves"] = _records(mv[mv["target_id"] == tid]) if len(mv) else []
        space = self.repo.load_run_frame(r.run_id, "pca_space")
        out["space"] = _records(space)[0] if len(space) else None
        out["tail_check"] = _records(self.repo.load_run_frame(r.run_id, "pca_tail"))
        return out

    def stress_monte_carlo(self, run_id: str | None = None) -> dict[str, Any]:
        """Gaussian and fat-tailed stressed paths for the firm and each desk, the firm's loss quantiles
        and the share of paths losing each stress-loss limit (MR-022)."""
        r, summ = self._frame(run_id, "stress_mc")
        out: dict[str, Any] = {"record": "MR-022", "run_id": r.run_id, "available": not summ.empty}
        if summ.empty:
            return out
        out["summary"] = _records(summ)
        out["quantiles"] = _records(self.repo.load_run_frame(r.run_id, "stress_mc_quantiles"))
        out["targets"] = _records(self.repo.load_run_frame(r.run_id, "stress_mc_targets"))
        par = self.repo.load_run_frame(r.run_id, "stress_mc_parameters")
        out["parameters"] = _records(par)[0] if len(par) else None
        return out

    def stress_paths(self, run_id: str | None = None, path_id: str | None = None) -> dict[str, Any]:
        """Every stress path frozen and managed (worst frozen drawdown first), and for one path its
        days, desks and actions (MR-023)."""
        r, paths = self._frame(run_id, "stress_paths")
        out: dict[str, Any] = {"record": "MR-023", "run_id": r.run_id, "available": not paths.empty}
        if paths.empty:
            return out
        worst = paths[paths["book"] == "FROZEN"].sort_values("max_drawdown")["path_id"].tolist()
        order = {p: i for i, p in enumerate(worst)}
        paths = paths.assign(_o=paths["path_id"].map(order), _b=paths["book"] != "FROZEN")
        out["paths"] = _records(paths.sort_values(["_o", "_b"]).drop(columns=["_o", "_b"]))
        pid = path_id or worst[0]
        out["path_id"] = pid
        for key, table in (("days", "stress_path_days"), ("desks", "stress_path_desks")):
            f = self.repo.load_run_frame(r.run_id, table)
            out[key] = _records(f[f["path_id"] == pid]) if len(f) else []
        a = self.repo.load_run_frame(r.run_id, "stress_path_actions")
        out["actions"] = _records(a[a["path_id"] == pid].sort_values(["desk_id", "action"])) if len(a) else []
        par = self.repo.load_run_frame(r.run_id, "stress_path_parameters")
        if len(par):
            p = _records(par)[0]
            p["rules"] = json.loads(p.pop("rules_json", "[]") or "[]")
            out["parameters"] = p
        return out

    def stress_liquidity(self, run_id: str | None = None, path_id: str | None = None) -> dict[str, Any]:
        """The margin, collateral and funding chain on every stress path (lowest liquidity first), one
        path's days, and every scenario as an instantaneous call (MR-024)."""
        r, paths = self._frame(run_id, "stress_liquidity")
        out: dict[str, Any] = {"record": "MR-024", "run_id": r.run_id, "available": not paths.empty}
        if paths.empty:
            return out
        order = (
            paths[paths["book"] == "MANAGED"]
            .groupby("path_id")["min_liquidity"]
            .min()
            .sort_values(kind="stable")
            .index.tolist()
        )
        rank = {p: i for i, p in enumerate(order)}
        scen = {s: i for i, s in enumerate(("NORMAL", "FIRM_SPECIFIC", "MARKET_WIDE"))}
        paths = paths.assign(
            _o=paths["path_id"].map(rank),
            _b=paths["book"] != "FROZEN",
            _s=paths["funding_scenario"].map(scen),
        )
        out["paths"] = _records(paths.sort_values(["_o", "_s", "_b"]).drop(columns=["_o", "_b", "_s"]))
        pid = path_id or order[0]
        out["path_id"] = pid
        d = self.repo.load_run_frame(r.run_id, "stress_liquidity_days")
        out["days"] = _records(d[d["path_id"] == pid]) if len(d) else []
        inst = self.repo.load_run_frame(r.run_id, "stress_liquidity_instant")
        out["instant"] = _records(inst.sort_values("net_liquidity", kind="stable")) if len(inst) else []
        par = self.repo.load_run_frame(r.run_id, "stress_liquidity_parameters")
        if len(par):
            p = _records(par)[0]
            p["rules"] = json.loads(p.pop("rules_json", "[]") or "[]")
            out["parameters"] = p
        return out

    def stress_funding(self) -> dict[str, Any]:
        """The funding scenarios and liquidity resources the stress paths' cash walks apply (MR-024)."""
        from novera.workflows import stress_paths

        return stress_paths.funding_setup(self.repo)

    def stress_actions(self) -> dict[str, Any]:
        """The action rules desks follow in a stress path (MR-023)."""
        from novera.workflows import stress_paths

        return stress_paths.setup(self.repo)

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
        "currency_pair",
        "risk_currency",
        "country",
        "region",
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
        if not v.empty:
            v = self._with_geography(r, v)
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
                # an equity's own country is its issuer's; ``country`` is the currency's (round 44)
                d.setdefault("issuer_country" if k == "country" else k, val)
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

    def morning_commentary(self, run_id: str | None = None) -> dict[str, Any]:
        """The latest commentary drafted automatically after this run (decision round 38)."""
        from novera.ai.commentary import AUTO_SESSION

        r = self.resolve(run_id)
        ans = next(
            (
                a
                for a in self.repo.load_analyst_answers(500, session_id=AUTO_SESSION)
                if a.get("run_id") == r.run_id
            ),
            None,
        )
        if ans is None:
            return {"run_id": r.run_id, "available": False}
        return {
            "run_id": r.run_id,
            "available": True,
            **{k: ans.get(k) for k in ("answer_id", "answer", "provider", "model", "at")},
        }

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

    def var_setup(self) -> dict[str, Any]:
        """The VaR measures the firm produces daily, which feed limits and which are for
        information, with the templates and options of the Admin page (OPS-004)."""
        from novera.workflows import var_setup

        return var_setup.setup(self.repo)

    def var_measure_scenarios(
        self, run_id: str | None = None, measure_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Scenario P&L and weight of one measure of the run (empty for Monte Carlo)."""
        _, s = self._frame(run_id, "var_measure_scenarios")
        if measure_id and len(s):
            s = s[s["measure_id"] == measure_id]
        return _records(s.sort_values("scenario_date")) if len(s) else []

    def run_options(self) -> dict[str, Any]:
        """What a manual full end-of-day run can be launched on, and the manual runs made so far
        (OPS-001): the stored market-snapshot dates, the latest run, and which engines a run
        includes under the current settings."""
        from novera.config import get_settings

        st = get_settings()
        snaps = self.repo.list_market_snapshots()
        latest = self.repo.latest_run()
        ids = self.repo.firm_ids()
        jobs = [j for j in self.repo.load_jobs(200) if str(j.get("action", "")).startswith("MANUAL")][:20]
        return {
            "record": "OPS-001",
            "firm_id": ids[0] if ids else None,
            "snapshot_dates": [row[1].isoformat() for row in snaps[-10:]][::-1],
            "latest_snapshot_date": snaps[-1][1].isoformat() if snaps else None,
            "latest_run_date": latest.business_date.isoformat() if latest else None,
            "counterparty_enabled": bool(st.exposure_enabled),
            "regulatory_enabled": bool(st.regulatory_enabled),
            "jobs": jobs,
        }

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
        from novera.workflows.stress_setup import active

        return stress_library(
            history,
            universe,
            self.repo.load_market_provenance(),
            self.repo.load_market_episodes(),
            definitions=active(self.repo),
        )

    def stress_plausibility(self) -> dict[str, Any]:
        """How extreme each approved scenario and draft is, and the risk ruler (MR-018)."""
        from novera.workflows import stress_setup

        return stress_setup.plausibility(self.repo)

    def stress_setup(self) -> dict[str, Any]:
        """The stress scenario library: approved versions in run order, open drafts, decided
        history, and what a proposal may use (OPS-005)."""
        from novera.workflows import stress_setup

        return stress_setup.setup(self.repo)

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

    def limit_drafts(self, status: str | None = None) -> list[dict[str, Any]]:
        """Limit recalibration drafts, newest first (MR-017)."""
        return self.repo.load_limit_drafts(status=status)

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
            "history_series": _records(self.repo.load_run_frame(r.run_id, "backtest_history_series")),
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
                initial_margin=("initial_margin", "sum"),
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
        from novera.counterparty_risk import csa_what_if, load_exposure_result, stored_initial_margin
        from novera.counterparty_risk.engine import stored_posted_margin
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
        im, posted = stored_initial_margin(self.repo, r.run_id), stored_posted_margin(self.repo, r.run_id)
        before = csa_what_if(res, netting_set_id, base_csa, margin_period_days, im, posted)
        after = csa_what_if(res, netting_set_id, new_csa, margin_period_days, im, posted)
        # XVA on both sets of terms, with the same credit and funding inputs as the run (CR-005).
        from novera.config import get_settings
        from novera.counterparty_risk.cva import (
            CreditTerms,
            counterparty_terms,
            hazard_from_spread,
            netting_set_xva,
        )

        st = get_settings()
        cps = {c.counterparty_id: c for c in self.repo.load_counterparties()}
        _, cpty = counterparty_terms(cps.get(ns.counterparty_id), st.lgd)
        own = CreditTerms(hazard_from_spread(st.own_credit_spread_bp, 1 - st.lgd), st.lgd)
        curve = self.repo.load_run_market(r.run_id).zero_curve(r.reporting_currency)
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
            "xva_before": netting_set_xva(before, cpty, own, curve, st.funding_spread_bp),
            "xva_after": netting_set_xva(after, cpty, own, curve, st.funding_spread_bp),
        }

    # --- identity (ADR 0006) ------------------------------------------------------------------------
    def users(self) -> list[dict[str, Any]]:
        return [u.model_dump(mode="json") for u in self.repo.load_users()]

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
            "desk_allocation": _records(self.repo.load_run_frame(r.run_id, "reg_desk_allocation")),
            "allocation_summary": _records(self.repo.load_run_frame(r.run_id, "reg_allocation_summary")),
            "frtb_sa_classes": _records(self.repo.load_run_frame(r.run_id, "reg_frtb_sa_classes")),
            "frtb_sa_detail": _records(self.repo.load_run_frame(r.run_id, "reg_frtb_sa_detail")),
            "ima": _records(self.repo.load_run_frame(r.run_id, "reg_frtb_ima_summary")),
            "pla": _records(self.repo.load_run_frame(r.run_id, "reg_frtb_ima_pla")),
            "saccr_counterparty": _records(self.repo.load_run_frame(r.run_id, "reg_saccr_counterparty")),
            "saccr_netting": _records(self.repo.load_run_frame(r.run_id, "reg_saccr_netting")),
            "simm": _records(self.repo.load_run_frame(r.run_id, "reg_simm")),
            "ccp": _records(self.repo.load_run_frame(r.run_id, "reg_ccp")),
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
            "worst_stress_all": r.summary.get("worst_stress_all"),
            "worst_stress_all_name": r.summary.get("worst_stress_all_name"),
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
    """Write side, opened on a writable connection for one signed-in user (ADR 0006). Every
    method checks the user's role and scope, then records the user's name as the actor; the
    workflows' own rules (approval matrix, never the requester, breach states) still apply."""

    def __init__(self, repo: DuckDBRepository, user: User) -> None:
        self.repo = repo
        self.user = user

    @property
    def actor(self) -> str:
        return self.user.name

    def _org(self):
        ids = self.repo.firm_ids()
        return self.repo.load_organisation(ids[0]) if ids else None

    def _limit(self, limit_id: str):
        lim = next((x for x in self.repo.load_limits() if x.limit_id == limit_id), None)
        if lim is None:
            raise NotFoundError(f"limit {limit_id} not found", context={"limit_id": limit_id})
        return lim

    def _on_breach(self, breach_id: str, action: Action) -> None:
        b = self.repo.load_breach(breach_id)
        require_on_limit(self.user, action, self._limit(b.limit_id), self._org())

    def run_full(
        self,
        reason: str = "",
        business_date: str | None = None,
        advance: bool = False,
        cfg: Any = None,
    ) -> dict[str, Any]:
        """Launch one full end-of-day run by hand (OPS-001). Returns the job record with the
        run it produced; a failed run comes back as a FAILED job with its error, not an
        exception, so the launcher sees exactly what the scheduler would have logged."""
        from datetime import date as _date

        from novera.workflows.eod import EODConfig
        from novera.workflows.scheduler import run_manual

        require(self.user, Action.RUN)
        bd = None
        if business_date:
            try:
                bd = _date.fromisoformat(business_date)
            except ValueError as e:
                raise InvalidRequestError(f"business_date must be YYYY-MM-DD, got {business_date!r}") from e
        ids = self.repo.firm_ids()
        cfg = cfg or EODConfig(firm_id=ids[0] if ids else "GMB")
        job = run_manual(self.repo, self.actor, reason, bd, advance, cfg)
        run = RiskService._run_dict(self.repo.load_run(job.run_id)) if job.run_id else None
        return {**job.to_dict(), "run": run}

    def rerun_stage(self, run_id: str, stage: str, reason: str = "") -> dict[str, Any]:
        """Re-run one EOD stage of a stored run into a new RERUN run; the parent is untouched
        (OPS-002)."""
        from novera.workflows.rerun import rerun_stage

        require(self.user, Action.RERUN)
        rid = RiskService(self.repo).resolve(run_id).run_id
        try:
            res = rerun_stage(self.repo, rid, stage, self.actor, reason)
        except ValueError as e:
            raise InvalidRequestError(str(e)) from e
        return {**RiskService._run_dict(res.run), "rerun": res.run.summary.get("rerun", {})}

    def _signed(self, fn, run_id: str, metric_id: str, comment: str) -> dict[str, Any]:
        from novera.workflows import signoff

        m = signoff.METRIC_BY_ID.get(metric_id)
        if m is not None:
            require_signer(self.user, m.signer, metric_id)
        return fn(self.repo, RiskService(self.repo).resolve(run_id), metric_id, self.actor, comment)

    def sign_metric(self, run_id: str, metric_id: str, comment: str = "") -> dict[str, Any]:
        from novera.workflows import signoff

        return self._signed(signoff.sign, run_id, metric_id, comment)

    def reject_metric(self, run_id: str, metric_id: str, comment: str = "") -> dict[str, Any]:
        from novera.workflows import signoff

        return self._signed(signoff.reject, run_id, metric_id, comment)

    def set_signoff_policy(self, required: list[str], comment: str = "") -> dict[str, Any]:
        from novera.workflows import signoff

        require(self.user, Action.SIGNOFF_POLICY)
        return signoff.set_policy(self.repo, self.actor, list(required), comment)

    def set_stress_funding(self, rules: list[dict[str, Any]], comment: str = "") -> dict[str, Any]:
        """Replace the funding rules and liquidity resources; validated and audited (MR-024)."""
        from novera.workflows import stress_paths

        require(self.user, Action.STRESS_FUNDING)
        return stress_paths.set_funding_rules(self.repo, self.actor, [dict(r) for r in rules], comment)

    def set_stress_actions(self, rules: list[dict[str, Any]], comment: str = "") -> dict[str, Any]:
        """Replace the stress-path action rules; validated and audited (MR-023)."""
        from novera.workflows import stress_paths

        require(self.user, Action.STRESS_ACTIONS)
        return stress_paths.set_rules(self.repo, self.actor, [dict(r) for r in rules], comment)

    def set_var_setup(self, measures: list[dict[str, Any]], comment: str = "") -> dict[str, Any]:
        """Replace the VaR setup; validated against the stored history and audited (OPS-004)."""
        from novera.workflows import var_setup

        require(self.user, Action.VAR_SETUP)
        return var_setup.set_setup(self.repo, self.actor, [dict(m) for m in measures], comment)

    def propose_stress_scenario(self, definition: dict[str, Any], note: str = "") -> dict[str, Any]:
        """A new stress scenario, or a new version of one, as a DRAFT (OPS-005). Desk and
        business heads propose information scenarios for their area (decision 46.2)."""
        from novera.workflows import stress_setup

        sid = str(definition.get("scenario_id") or "")
        area = require_stress_proposal(
            self.user, str(definition.get("goal") or "INFORMATION"), stress_setup.current_goal(self.repo, sid)
        )
        return stress_setup.propose(self.repo, self.actor, dict(definition), note, area=area)

    def decide_stress_scenario(
        self, scenario_id: str, version: int, approve: bool, note: str = ""
    ) -> dict[str, Any]:
        """Approve or reject a draft; never the proposer's own (four eyes, OPS-005)."""
        from novera.workflows import stress_setup

        require(self.user, Action.STRESS_DECIDE)
        return stress_setup.decide(self.repo, self.actor, scenario_id, int(version), bool(approve), note)

    def review_stress_scenario(self, scenario_id: str, note: str = "") -> dict[str, Any]:
        """Confirm an approved scenario still fits; the next review falls due three months on."""
        from novera.workflows import stress_setup

        require(self.user, Action.STRESS_REVIEW)
        return stress_setup.review(self.repo, self.actor, scenario_id, note)

    def upgrade_stress_built_ins(self, note: str = "") -> list[dict[str, Any]]:
        """Drafts of the built-ins whose code version differs from the stored approved version."""
        from novera.workflows import stress_setup

        require(self.user, Action.STRESS_PROPOSE)
        return stress_setup.upgrade_built_ins(self.repo, self.actor, note)

    def draft_stress_crises(self) -> list[dict[str, Any]]:
        """Drafts of the calibrated named crises the library does not hold (decision 47.3)."""
        from novera.workflows import stress_setup

        require(self.user, Action.STRESS_PROPOSE)
        return stress_setup.draft_crises(self.repo, self.actor)

    def draft_stress_narratives(self) -> list[dict[str, Any]]:
        """Drafts of the forward-looking narratives the library does not hold (decision 47d)."""
        from novera.workflows import stress_setup

        require(self.user, Action.STRESS_PROPOSE)
        return stress_setup.draft_narratives(self.repo, self.actor)

    def seed_stress_library(self, note: str = "") -> dict[str, Any]:
        """Store the built-in library as approved version 1 and start its review clock."""
        from novera.workflows import stress_setup

        require(self.user, Action.STRESS_DECIDE)
        stress_setup.seed(self.repo, self.actor, note)
        return stress_setup.setup(self.repo)

    def apply_var_template(self, template: str, comment: str = "") -> dict[str, Any]:
        """Replace the VaR setup with a template (bank or hedge_fund) (OPS-004)."""
        from novera.workflows import var_setup

        require(self.user, Action.VAR_SETUP)
        return var_setup.apply_template(self.repo, template, self.actor, comment)

    def acknowledge(self, breach_id: str, comment: str = "") -> dict[str, Any]:
        self._on_breach(breach_id, Action.BREACH_ACKNOWLEDGE)
        b, _ = wf.acknowledge(self.repo, breach_id, self.actor, comment)
        return b.model_dump(mode="json")

    def escalate(self, breach_id: str, to: str | None = None, comment: str = "") -> dict[str, Any]:
        self._on_breach(breach_id, Action.BREACH_ESCALATE)
        b, _ = wf.escalate(self.repo, breach_id, self.actor, to, comment)
        return b.model_dump(mode="json")

    def comment(self, breach_id: str, text: str) -> dict[str, Any]:
        self._on_breach(breach_id, Action.BREACH_COMMENT)
        b, _ = wf.comment(self.repo, breach_id, self.actor, text)
        return b.model_dump(mode="json")

    def close(self, breach_id: str, reason: str, comment: str = "") -> dict[str, Any]:
        self._on_breach(breach_id, Action.BREACH_CLOSE)
        b, _ = wf.close(self.repo, breach_id, self.actor, CloseReason(reason), comment)
        return b.model_dump(mode="json")

    def request_increase(
        self,
        limit_id: str,
        new_amount: float,
        expires_on: str,
        rationale: str,
        effective_from: str | None = None,
        breach_id: str | None = None,
    ) -> dict[str, Any]:
        from datetime import date as _date

        require_on_limit(self.user, Action.INCREASE_REQUEST, self._limit(limit_id), self._org())
        inc, _ = wf.request_increase(
            self.repo,
            limit_id,
            new_amount,
            _date.fromisoformat(expires_on),
            self.actor,
            rationale,
            _date.fromisoformat(effective_from) if effective_from else None,
            breach_id,
        )
        return inc.model_dump(mode="json")

    def decide_increase(self, increase_id: str, approve: bool, comment: str = "") -> dict[str, Any]:
        """The approval matrix picks the approver by the limit's level and size; never the
        requester (MR-008)."""
        require(self.user, Action.INCREASE_DECIDE)
        inc, _ = wf.decide_increase(self.repo, increase_id, self.actor, approve, comment)
        return inc.model_dump(mode="json")

    def cancel_increase(self, increase_id: str) -> dict[str, Any]:
        """The requester withdraws it, or a head of risk over the limit cancels it."""
        inc0 = self.repo.load_increase(increase_id)
        if inc0.requested_by != self.actor and not oversees(self.user, self._limit(inc0.limit_id)):
            raise ForbiddenError(
                f"{self.actor} may not cancel an increase requested by {inc0.requested_by}",
                context={"user_id": self.user.user_id, "increase_id": increase_id},
            )
        inc, _ = wf.cancel_increase(self.repo, increase_id, self.actor)
        return inc.model_dump(mode="json")

    def propose_limit_recalibration(
        self, run_id: str | None = None, limit_types: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """DRAFT limit versions from a run's measures at their target utilisation (MR-017)."""
        from novera.domain.limits import LimitType
        from novera.workflows.limit_calibration import propose_recalibration

        require(self.user, Action.LIMIT_RECALIBRATE)
        r = RiskService(self.repo).resolve(run_id)
        drafts, _ = propose_recalibration(
            self.repo, r.run_id, self.actor, {LimitType(t) for t in limit_types or []} or None
        )
        return drafts

    def decide_limit_draft(self, draft_id: str, approve: bool, comment: str = "") -> dict[str, Any]:
        from novera.workflows.limit_calibration import decide_draft

        require(self.user, Action.LIMIT_DRAFT_DECIDE)
        return decide_draft(self.repo, draft_id, self.actor, approve, comment)


def with_commentary(db_path: str, job: dict[str, Any]) -> dict[str, Any]:
    """A manual run's job, after its connection is closed, with the morning commentary drafted
    for the run it produced and the outcome noted on the job (decision round 38)."""
    if not job.get("run_id") or job.get("status") not in ("COMPLETED", "PARTIAL"):
        return job
    from novera.ai.commentary import draft_after_run, note_for_job

    outcome = draft_after_run(db_path, job["run_id"])
    job = {**job, "notes": [*job.get("notes", []), note_for_job(outcome)], "commentary": outcome}
    with DuckDBRepository(db_path) as repo:
        repo.save_job({k: v for k, v in job.items() if k not in ("run", "commentary")})
    return job
