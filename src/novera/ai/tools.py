"""Copilot tools: the same service methods the API exposes, wrapped as tool definitions.

Every tool returns compact JSON with the run id it read from, so answers can cite it.
The model never sees the database; it only sees these results (ADR 0003).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from novera.ai.whatif import TARGETS, UNITS, Shock, what_if
from novera.api.service import RiskService
from novera.storage.duckdb_repository import DuckDBRepository

M = 1e6


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., Any]

    def definition(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


RUN = {"type": "string", "description": "Run id; omit or 'latest' for the latest completed EOD run"}
BY = {
    "type": "string",
    "enum": [
        "asset_class",
        "business_id",
        "desk_id",
        "book_id",
        "product_type",
        "currency",
        "counterparty_id",
        "trader_id",
    ],
}


def _m(x: Any) -> Any:
    """Round money to 0.01m for compact tool output."""
    return None if x is None else round(float(x) / M, 2)


def build_tools(db_path: str) -> list[Tool]:
    def svc() -> tuple[DuckDBRepository, RiskService]:
        repo = DuckDBRepository(db_path, read_only=True)
        return repo, RiskService(repo)

    def run_summary(run_id: str | None = None) -> dict:
        repo, s = svc()
        with repo:
            d = s.summary(run_id)
        sm = d["summary"]
        return {
            "run_id": d["run_id"],
            "business_date": d["business_date"],
            "verdict": d["verdict"],
            "portfolio_snapshot_id": d["portfolio_snapshot_id"],
            "market_snapshot_id": d["market_snapshot_id"],
            "reporting_currency": d["reporting_currency"],
            "model_versions": d["model_versions"],
            "pv_m": _m(sm["pv"]),
            "var_99_1d_m": _m(sm["var"]),
            "es_975_1d_m": _m(sm["es"]),
            "var_10d_m": _m(sm["var_scaled"]),
            "challenger_var_m": _m(sm["challenger_var"]),
            "var_worst_scenario_date": sm["var_scenario_date"],
            "worst_stress": {"name": sm["worst_stress_name"], "loss_m": _m(sm["worst_stress"])},
            "limits": {
                "monitored": sm["limits_monitored"],
                "breaches": sm["breaches"],
                "warnings": sm["warnings"],
            },
            "data_quality": {"verdict": sm["dq_verdict"], "findings": sm["dq_findings"]},
            "pnl_total_m": _m(sm.get("pnl_total")),
            "pnl_steps_m": {k: _m(v) for k, v in (sm.get("pnl_steps") or {}).items()},
            "var_by_asset_class_m": {
                r["asset_class"]: _m(r["component_var"]) for r in d["var_by_asset_class"]
            },
        }

    def var_by(by: str = "asset_class", run_id: str | None = None, desk_id: str | None = None) -> dict:
        repo, s = svc()
        with repo:
            r = s.resolve(run_id)
            rows = s.var_by(by, r.run_id, desk_id=desk_id)
            ch = s.var_by(by, r.run_id, method="delta_gamma_vega", desk_id=desk_id)
        chal = {x[by]: _m(x["component_var"]) for x in ch}
        return {
            "run_id": r.run_id,
            "by": by,
            "rows": [
                {
                    by: x[by],
                    "component_var_m": _m(x["component_var"]),
                    "component_es_m": _m(x["component_es"]),
                    "challenger_var_m": chal.get(x[by]),
                    "trades": x["trades"],
                }
                for x in rows
            ],
        }

    def sensitivities(
        measure: str = "DV01", by: str = "desk_id", run_id: str | None = None, desk_id: str | None = None
    ) -> dict:
        repo, s = svc()
        with repo:
            r = s.resolve(run_id)
            rows = s.sensitivities(r.run_id, measure=measure, by=by, desk_id=desk_id)
        out = [
            {**{k: v for k, v in x.items() if k != "value"}, "value_k": round(x["value"] / 1e3, 1)}
            for x in rows
        ]
        out = sorted(out, key=lambda x: -abs(x["value_k"]))[:40]
        units = {
            "DV01": "P&L per +1bp",
            "CS01": "P&L per +1bp",
            "VEGA": "P&L per +1 vol point",
            "THETA": "P&L per calendar day",
        }.get(measure, "P&L per +1% move")
        return {"run_id": r.run_id, "measure": measure, "unit": f"{units}, in thousands", "rows": out}

    def stress(by: str = "asset_class", run_id: str | None = None, desk_id: str | None = None) -> dict:
        repo, s = svc()
        with repo:
            r = s.resolve(run_id)
            rows = s.stress(r.run_id, by=by, desk_id=desk_id)
        out = []
        for x in rows:
            groups = {
                k: _m(v)
                for k, v in x.items()
                if k not in ("scenario_id", "name", "kind", "description", "total")
            }
            out.append(
                {"scenario": x["name"], "kind": x["kind"], "total_m": _m(x["total"]), f"by_{by}_m": groups}
            )
        return {"run_id": r.run_id, "scenarios": out}

    def limits(status: str | None = None, level: str | None = None, run_id: str | None = None) -> dict:
        repo, s = svc()
        with repo:
            r = s.resolve(run_id)
            rows = s.limits(r.run_id, status=status, level=level)
        out = []
        for x in rows[:60]:
            conc = x["limit_type"] == "CONCENTRATION"
            out.append(
                {
                    "limit_id": x["limit_id"],
                    "type": x["limit_type"],
                    "level": x["level"],
                    "entity": x["entity_id"],
                    "status": x["status"],
                    "utilisation_pct": round(x["utilisation"] * 100),
                    "current": round(x["current"], 2) if conc else _m(x["current"]),
                    "limit": round(x["amount"], 2) if conc else _m(x["amount"]),
                    "unit": "share" if conc else "m",
                    "owner": x["owner"],
                    "temporary_increase": x.get("increase_id"),
                }
            )
        return {"run_id": r.run_id, "count": len(rows), "limits": out}

    def breaches(open_only: bool = True) -> dict:
        repo, s = svc()
        with repo:
            rows = s.breaches(open_only)
        return {
            "breaches": [
                {
                    k: b[k]
                    for k in (
                        "breach_id",
                        "limit_id",
                        "status",
                        "owner",
                        "first_date",
                        "latest_date",
                        "consecutive_days",
                        "latest_utilisation",
                        "peak_utilisation",
                        "escalated_to",
                        "within_limit_on_latest_run",
                    )
                }
                for b in rows
            ]
        }

    def pnl(by: str = "asset_class", run_id: str | None = None, desk_id: str | None = None) -> dict:
        repo, s = svc()
        with repo:
            r = s.resolve(run_id)
            p = s.pnl(r.run_id, by=by, desk_id=desk_id)
        out: dict[str, Any] = {
            "run_id": r.run_id,
            "total_m": _m(p["total"]),
            "steps_m": {x["step"]: _m(x["pnl"]) for x in p["steps"]},
        }
        if "by" in p:
            out[f"by_{by}_m"] = [
                {k: (_m(v) if isinstance(v, int | float) else v) for k, v in row.items()} for row in p["by"]
            ]
        if "challenger" in p:
            c = p["challenger"]
            out["sensitivity_challenger_m"] = {
                "official": _m(c["actual"]),
                "predicted": _m(c["predicted"]),
                "unexplained": _m(c["residual"]),
                "largest_unexplained": [
                    {"trade_id": w["trade_id"], "residual_m": _m(w["residual"])}
                    for w in c["worst_residuals"][:5]
                ],
            }
        return out

    def data_quality(run_id: str | None = None) -> dict:
        repo, s = svc()
        with repo:
            r = s.resolve(run_id)
            rows = s.dq(r.run_id)
        return {
            "run_id": r.run_id,
            "verdict": r.verdict,
            "findings": [
                {k: x[k] for k in ("code", "severity", "subject", "message", "affected_trades", "owner")}
                for x in rows
            ],
        }

    def trade(trade_id: str, run_id: str | None = None) -> dict:
        repo, s = svc()
        with repo:
            t = s.trade(trade_id, run_id)
        v = t["valuation"]
        ins = (t["trade"] or {}).get("instrument", {})
        return {
            "run_id": t["run_id"],
            "trade_id": trade_id,
            "book": v["book_id"],
            "desk": v["desk_id"],
            "counterparty": v["counterparty_id"],
            "product": v["product_type"],
            "currency": v["currency"],
            "quantity": v["quantity"],
            "pv_m": _m(v["pv"]),
            "instrument": ins,
            "sensitivities_k": [
                {"measure": x["measure"], "factor": x["factor_id"], "value_k": round(x["value"] / 1e3, 1)}
                for x in t["sensitivities"][:20]
            ],
            "stress_m": {x["scenario_id"]: _m(x["pnl"]) for x in t["stress"] if abs(x["pnl"]) > 1e4},
        }

    def positions(by: str = "desk_id", run_id: str | None = None, desk_id: str | None = None) -> dict:
        repo, s = svc()
        with repo:
            r = s.resolve(run_id)
            rows = s.positions(r.run_id, by=by, desk_id=desk_id)
        return {
            "run_id": r.run_id,
            "by": by,
            "rows": [{by: x[by], "pv_m": _m(x["pv"]), "trades": x["trades"]} for x in rows],
        }

    def compare_runs(run_a: str | None = None, run_b: str | None = None, by: str = "asset_class") -> dict:
        repo, s = svc()
        with repo:
            runs = s.runs(10)
            if run_b in (None, "", "latest"):
                run_b = runs[0]["run_id"]
            if run_a in (None, "", "previous"):
                others = [x["run_id"] for x in runs if x["run_id"] != run_b]
                if not others:
                    return {"error": "only one run stored; nothing to compare"}
                run_a = others[0]
            c = s.compare(run_a, run_b, by)
        return {
            "run_a": {"run_id": c["a"]["run_id"], "business_date": c["a"]["business_date"]},
            "run_b": {"run_id": c["b"]["run_id"], "business_date": c["b"]["business_date"]},
            "headline_m": {
                k: {
                    "a": _m(v["a"]) if k != "breaches" else v["a"],
                    "b": _m(v["b"]) if k != "breaches" else v["b"],
                    "change": _m(v["change"]) if k != "breaches" else v["change"],
                }
                for k, v in c["headline"].items()
            },
            f"var_by_{by}_m": [
                {by: x[by], "a": _m(x["a"]), "b": _m(x["b"]), "change": _m(x["change"])} for x in c["var_by"]
            ],
            "limits_changed": [
                {k: x[k] for k in ("limit_id", "status_a", "status_b", "utilisation_a", "utilisation_b")}
                for x in c["limit_changes"][:15]
            ],
            "trades_only_in_a": c["trades_only_in_a"],
            "trades_only_in_b": c["trades_only_in_b"],
            "largest_trade_moves_m": [
                {
                    "trade_id": x["trade_id"],
                    "desk_id": x["desk_id"],
                    "product_type": x["product_type"],
                    "pv_change_m": _m(x["pv_change"]),
                    "presence": x["presence"],
                }
                for x in c["top_trade_changes"][:10]
            ],
        }

    def concentration_tool(run_id: str | None = None) -> dict:
        repo, s = svc()
        with repo:
            r = s.resolve(run_id)
            c = s.concentration(r.run_id)
        return {
            "run_id": r.run_id,
            "flags": c["flags"],
            "by_dimension": [
                {k: (round(v, 3) if isinstance(v, float) else v) for k, v in x.items()}
                for x in c["by_dimension"]
            ],
            "top_positions_m": [
                {
                    "trade_id": x["trade_id"],
                    "desk_id": x["desk_id"],
                    "product": x["product_type"],
                    "pv_m": _m(x["pv"]),
                    "component_var_m": _m(x["var_contribution"]),
                    "share_of_var": round(x["share_of_var"], 3),
                }
                for x in c["top_positions"][:10]
            ],
        }

    def liquidity_tool(run_id: str | None = None) -> dict:
        repo, s = svc()
        with repo:
            r = s.resolve(run_id)
            q = s.liquidity(r.run_id)
        return {
            "run_id": r.run_id,
            "var_m": _m(q["var"]),
            "liquidity_adjusted_var_m": _m(q["liquidity_adjusted_var"]),
            "weighted_horizon_days": round(q["horizon_days"] or 0, 2),
            "flags": q["flags"],
            "by_bucket": [
                {
                    "bucket": x["horizon_bucket"],
                    "trades": x["trades"],
                    "abs_pv_m": _m(x["abs_pv"]),
                    "share": round(x["share_of_abs_pv"], 3),
                }
                for x in q["by_bucket"]
            ],
            "slowest": [
                {
                    "trade_id": x["trade_id"],
                    "desk_id": x["desk_id"],
                    "product": x["product_type"],
                    "days_to_liquidate": round(x["days_to_liquidate"], 1),
                }
                for x in q["slowest"][:8]
            ],
        }

    def backtest_tool(run_id: str | None = None) -> dict:
        repo, s = svc()
        with repo:
            r = s.resolve(run_id)
            b = s.backtest(r.run_id)
        exc = [x["date"] for x in b["series"] if x.get("exception")]
        return {
            "run_id": r.run_id,
            "summary": [
                {k: (round(v, 4) if isinstance(v, float) else v) for k, v in x.items()} for x in b["summary"]
            ],
            "exception_dates": exc[-10:],
        }

    def counterparty_tool(counterparty_id: str | None = None, run_id: str | None = None) -> dict:
        repo, s = svc()
        with repo:
            r = s.resolve(run_id)
            if counterparty_id:
                d = s.counterparty(counterparty_id, r.run_id)
                return {
                    "run_id": r.run_id,
                    "counterparty": d["counterparty"],
                    "profile_m": [
                        {
                            "step": x["step"],
                            "ee": _m(x["ee"]),
                            "pfe95": _m(x["pfe95"]),
                            "ee_gross": _m(x["ee_gross"]),
                            "collateral": _m(x["mean_collateral"]),
                        }
                        for x in d["profile"]
                    ],
                    "netting_sets": [
                        {
                            "netting_set_id": n["netting_set_id"],
                            "legal_entity": n["legal_entity_id"],
                            "csa": n["csa"],
                        }
                        for n in d["netting_sets"]
                    ],
                    "cva_m": [
                        {"netting_set_id": x["netting_set_id"], "cva": _m(x["cva"]), "dva": _m(x["dva"])}
                        for x in d["cva"]
                    ],
                    "wrong_way": d["wwr"],
                    "stressed_exposure_m": [
                        {
                            "scenario": x["scenario_id"],
                            "current": _m(x["current_exposure"]),
                            "stressed": _m(x["stressed_exposure"]),
                        }
                        for x in d["stressed"][:6]
                    ],
                }
            c = s.counterparties(r.run_id)
        return {
            "run_id": r.run_id,
            "notes": c["notes"],
            "counterparties": [
                {
                    "counterparty_id": x["counterparty_id"],
                    "rating": x["rating"],
                    "collateralised": x["collateralised"],
                    "current_exposure_m": _m(x["current_exposure"]),
                    "epe_m": _m(x["epe"]),
                    "peak_pfe95_m": _m(x["peak_pfe95"]),
                    "peak_pfe95_gross_m": _m(x["peak_pfe95_gross"]),
                    "cva_m": _m(x["cva"]),
                    "dva_m": _m(x["dva"]),
                    "wrong_way": x["wrong_way"],
                    "wwr_correlation": x["wwr_correlation"],
                }
                for x in c["summary"][:15]
            ],
        }

    def capital_tool(run_id: str | None = None) -> dict:
        repo, s = svc()
        with repo:
            r = s.resolve(run_id)
            c = s.capital(r.run_id)
        if not c.get("available"):
            return {"run_id": r.run_id, "error": "no regulatory run stored"}
        sm = c["summary"]
        return {
            "run_id": r.run_id,
            "capital_m": {
                k: _m(v)
                for k, v in sm.items()
                if k
                in (
                    "frtb_sa",
                    "frtb_sa_sbm",
                    "frtb_sa_drc",
                    "frtb_ima",
                    "imes",
                    "ses",
                    "saccr_ead",
                    "saccr_rwa",
                    "saccr_capital",
                    "simm_im",
                    "ba_cva_capital",
                )
            },
            "ima_multiplier": sm.get("ima_multiplier"),
            "nmrf": sm.get("nmrf"),
            "pla_red_desks": sm.get("pla_red_desks"),
            "frtb_sa_by_class_m": [
                {
                    "class": x["risk_class"],
                    "delta": _m(x["delta"]),
                    "vega": _m(x["vega"]),
                    "curvature": _m(x["curvature"]),
                }
                for x in c["frtb_sa_classes"]
            ],
            "by_desk_m": [
                {
                    "desk_id": x["desk_id"],
                    "frtb_sa": _m(x["frtb_sa"]),
                    "frtb_ima": _m(x["frtb_ima"]),
                    "saccr": _m(x["saccr"]),
                    "ba_cva": _m(x["ba_cva"]),
                }
                for x in c["by_desk"][:10]
            ],
            "saccr_top_m": [
                {"counterparty_id": x["counterparty_id"], "ead": _m(x["ead"]), "rwa": _m(x["rwa"])}
                for x in c["saccr_counterparty"][:6]
            ],
        }

    def fund_tool(run_id: str | None = None) -> dict:
        repo, s = svc()
        with repo:
            r = s.resolve(run_id)
            f = s.fund(r.run_id)
        if not f.get("available"):
            return {"run_id": r.run_id, "error": "this run is not a fund run (bank face)"}
        sm = f["summary"]
        nav = sm["nav"]
        return {
            "run_id": r.run_id,
            "nav_m": _m(nav),
            "gross_leverage": round(sm["gross_leverage"], 2),
            "net_leverage": round(sm["net_leverage"], 2),
            "var_pct_nav": round((f["var"] or 0) / nav * 100, 2),
            "worst_stress_pct_nav": round((f["worst_stress"] or 0) / nav * 100, 1),
            "margin_to_nav": round(sm["margin_to_nav"], 3),
            "largest_pb_share": round(sm["largest_pb_share"], 2),
            "crowding_score": round(sm["crowding_score"], 2),
            "flags": f["flags"],
            "strategies": [
                {
                    "strategy": x["strategy"],
                    "gross_pct_nav": round(x["gross_pct_nav"] * 100, 1),
                    "net_pct_nav": round(x["net_pct_nav"] * 100, 1),
                    "share_of_var": round(x["share_of_var"], 2),
                    "pnl_today_m": _m(x["pnl_today"]),
                }
                for x in f["attribution"]
            ],
            "margin_by_pb": [
                {"pb": x["prime_broker"], "margin_m": _m(x["margin"]), "share": round(x["share"], 2)}
                for x in f["margin_pb"]
            ],
            "redemptions": [
                {
                    "scenario": x["scenario"],
                    "date": x["dealing_date"],
                    "coverage": round(x["coverage"], 1) if x["coverage"] else None,
                    "shortfall_m": _m(x["shortfall"]),
                }
                for x in f["redemptions"]
            ],
            "top_factor_betas": sorted(
                [
                    {
                        "strategy": x["strategy"],
                        "factor": x["label"],
                        "beta_pct_nav_per_sigma": round(x["beta_pct_nav"] * 100, 2),
                        "t": round(x["t_stat"], 1),
                    }
                    for x in f["factors"]
                ],
                key=lambda z: -abs(z["beta_pct_nav_per_sigma"]),
            )[:8],
        }

    def what_if_tool(shocks: list[dict], run_id: str | None = None, by: str = "asset_class") -> dict:
        repo, s = svc()
        with repo:
            r = s.resolve(run_id)
        parsed = [
            Shock(
                x["target"],
                float(x["size"]),
                x.get("unit", "pct"),
                tuple(x["tenors"]) if x.get("tenors") else None,
            )
            for x in shocks
        ]
        res = what_if(db_path, r.run_id, parsed, by=by)
        res["total_pnl_m"] = _m(res.pop("total_pnl"))
        res["by_m"] = {k: _m(v) for k, v in res.pop("by").items()}
        for w in res["worst_trades"]:
            w["pnl_m"] = _m(w.pop("pnl"))
        return res

    return [
        Tool(
            "run_summary",
            "Headline numbers of a run: PV, VaR, ES, challenger VaR, worst stress, limits, data "
            "quality verdict, P&L and its steps, VaR by asset class. Start here for most questions.",
            _obj({"run_id": RUN}),
            run_summary,
        ),
        Tool(
            "var_by",
            "Component VaR and ES by a hierarchy dimension, with the delta-gamma-vega challenger.",
            _obj({"by": BY, "run_id": RUN, "desk_id": {"type": "string"}}),
            var_by,
        ),
        Tool(
            "sensitivities",
            "Sensitivities by group and underlying. DV01 rows carry a tenor bucket "
            "(curve ladder). Measures: DV01, CS01, FX_DELTA, EQ_DELTA, CMD_DELTA, CRYPTO_DELTA, VEGA, "
            "GAMMA, THETA.",
            _obj(
                {
                    "measure": {
                        "type": "string",
                        "enum": [
                            "DV01",
                            "CS01",
                            "FX_DELTA",
                            "EQ_DELTA",
                            "CMD_DELTA",
                            "CRYPTO_DELTA",
                            "VEGA",
                            "GAMMA",
                            "THETA",
                        ],
                    },
                    "by": BY,
                    "run_id": RUN,
                    "desk_id": {"type": "string"},
                }
            ),
            sensitivities,
        ),
        Tool(
            "stress",
            "Stress-test losses for every scenario in the library, split by a dimension.",
            _obj({"by": BY, "run_id": RUN, "desk_id": {"type": "string"}}),
            stress,
        ),
        Tool(
            "limits",
            "Limit utilisation and status. Filter by status (BREACH, WARNING, OK) or level "
            "(FIRM, BUSINESS, DESK, BOOK, COUNTERPARTY).",
            _obj(
                {
                    "status": {"type": "string", "enum": ["BREACH", "WARNING", "OK", "NO_DATA"]},
                    "level": {"type": "string"},
                    "run_id": RUN,
                }
            ),
            limits,
        ),
        Tool(
            "breaches",
            "Breach workflow state: open breaches with status, owner, days, escalation.",
            _obj({"open_only": {"type": "boolean"}}),
            breaches,
        ),
        Tool(
            "pnl",
            "Daily P&L explain: waterfall steps (carry, factor groups, data, new and dead trades), by "
            "group, and the sensitivity-based challenger with unexplained residuals.",
            _obj({"by": BY, "run_id": RUN, "desk_id": {"type": "string"}}),
            pnl,
        ),
        Tool(
            "data_quality",
            "Data-quality findings and the run verdict: can today's numbers be trusted?",
            _obj({"run_id": RUN}),
            data_quality,
        ),
        Tool(
            "trade",
            "One trade: booking, valuation, sensitivities, stress P&L.",
            _obj({"trade_id": {"type": "string"}, "run_id": RUN}, ["trade_id"]),
            trade,
        ),
        Tool(
            "positions",
            "PV and trade count by a dimension.",
            _obj({"by": BY, "run_id": RUN, "desk_id": {"type": "string"}}),
            positions,
        ),
        Tool(
            "compare_runs",
            "What changed between two runs (default: previous versus latest): headline, VaR by "
            "group, limits that moved, trades that moved. Use for 'what changed' and 'why did VaR change'.",
            _obj({"run_a": {"type": "string"}, "run_b": {"type": "string"}, "by": BY}),
            compare_runs,
        ),
        Tool(
            "concentration",
            "Concentration: HHI and top shares by trade, desk, book, counterparty, currency, "
            "asset class and risk factor; largest VaR contributors; flags.",
            _obj({"run_id": RUN}),
            concentration_tool,
        ),
        Tool(
            "liquidity",
            "Liquidity: days to liquidate, liquidation horizon buckets, liquidity-adjusted VaR, "
            "slowest positions, flags.",
            _obj({"run_id": RUN}),
            liquidity_tool,
        ),
        Tool(
            "backtest",
            "VaR backtest: exceptions, Kupiec and Christoffersen p-values, Basel zone, for the "
            "static-portfolio test and the live series.",
            _obj({"run_id": RUN}),
            backtest_tool,
        ),
        Tool(
            "counterparty_exposure",
            "Counterparty risk from the exposure engine: EPE, peak PFE95 after and before "
            "collateral, CVA, DVA, wrong-way flags for all counterparties, or the full profile, netting sets, CSA "
            "terms and stressed exposure for one counterparty_id.",
            _obj({"counterparty_id": {"type": "string"}, "run_id": RUN}),
            counterparty_tool,
        ),
        Tool(
            "capital",
            "Regulatory capital: FRTB SA by risk class and desk, FRTB IMA (IMES, multiplier, NMRF, "
            "P&L attribution zones), SA-CCR EAD and RWA by counterparty, SIMM-lite initial margin, BA-CVA.",
            _obj({"run_id": RUN}),
            capital_tool,
        ),
        Tool(
            "fund_overview",
            "Hedge-fund view: NAV, leverage, VaR and stress as % of NAV, prime-broker margin, "
            "strategy exposures and attribution, factor betas, redemption stress, crowding flags.",
            _obj({"run_id": RUN}),
            fund_tool,
        ),
        Tool(
            "what_if",
            "Run a hypothetical scenario through the pricing engine on the run's portfolio. Shocks: "
            f"target one of {', '.join(TARGETS)} or a factor prefix like IR:USD: or EQIDX:SPX; "
            "size with unit "
            "pct (percent move, e.g. -20), bp (rates and credit spreads, e.g. 100) or vol_points (e.g. 15). "
            "Optional tenors for curve shocks, e.g. ['10Y','30Y'].",
            _obj(
                {
                    "shocks": {
                        "type": "array",
                        "items": _obj(
                            {
                                "target": {"type": "string"},
                                "size": {"type": "number"},
                                "unit": {"type": "string", "enum": list(UNITS)},
                                "tenors": {"type": "array", "items": {"type": "string"}},
                            },
                            ["target", "size"],
                        ),
                    },
                    "run_id": RUN,
                    "by": BY,
                },
                ["shocks"],
            ),
            what_if_tool,
        ),
    ]


def execute(tools: list[Tool], name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
    """Run a tool; return (json text, is_error)."""
    by_name = {t.name: t for t in tools}
    if name not in by_name:
        return json.dumps({"error": f"unknown tool {name}"}), True
    try:
        return json.dumps(by_name[name].fn(**arguments), default=str), False
    except Exception as e:  # noqa: BLE001 - the model must see the failure and adapt
        return json.dumps({"error": f"{type(e).__name__}: {e}"}), True
