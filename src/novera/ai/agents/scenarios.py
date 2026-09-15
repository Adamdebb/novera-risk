"""Scenario suggestion agent (AI-002).

Deterministic evidence: the book's largest factor exposures on the run (from stored
sensitivities), the largest recent moves in the history (last 20 business days, in units of
the 500-day daily standard deviation), and the run's worst library stress. From those the
agent proposes scenarios sized by the history (a 99% ten-day move is 2.33 σ √10), runs
every proposal through the what-if engine on the stored run, and drafts the note from the
engine's results.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from novera.ai.agents.base import Agent, AgentNote, _m
from novera.ai.whatif import Shock, what_if
from novera.market_data.history import MarketHistory
from novera.risk.scenarios import historical_shocks
from novera.storage.duckdb_repository import DuckDBRepository

Z99_10D = 2.33 * np.sqrt(10)
RECENT_DAYS = 20

# Family prefix -> (what-if target, unit, measure whose sign decides the loss direction)
FAMILIES: dict[str, tuple[str, str, str]] = {
    "IR:": ("IR:{u}:", "bp", "DV01"),
    "CDS:": ("CDS:{u}", "bp", "CS01"),
    "EQIDX:": ("EQIDX:{u}", "pct", "EQ_DELTA"),
    "EQ:": ("EQ:{u}", "pct", "EQ_DELTA"),
    "FX:": ("FX:{u}", "pct", "FX_DELTA"),
    "CMD:": ("CMD:{u}:", "pct", "CMD_DELTA"),
    "CRYPTO:": ("CRYPTO:{u}", "pct", "CRYPTO_DELTA"),
    "VOL:": ("VOL:{u}:", "vol_points", "VEGA"),
}


def _family(fid: str) -> str:
    return fid.split(":")[0] + ":"


class ScenarioSuggester(Agent):
    kind = "SCENARIO_SUGGESTION"
    instructions = (
        "Propose the stress scenarios a risk manager should run today, from the evidence: name each "
        "scenario, why it is relevant (the exposure or the recent move that motivates it), its size and "
        "the engine's result (total P&L, the asset classes and trades that drive it). Rank them by loss. "
        "Close with which one you would add to the standing library and why."
    )

    def gather(self, run_id: str | None = None, n: int = 4, **_: Any) -> dict[str, Any]:
        with DuckDBRepository(self.db_path, read_only=True) as repo:
            run = repo.load_run(run_id) if run_id else repo.list_runs(run_type="EOD", limit=1)[0]
            run_id = run.run_id
            sens = repo.load_run_frame(run_id, "sensitivities")
            market = repo.load_run_market(run_id)
            universe = {f.factor_id: f for f in repo.load_risk_factors()}
            history = MarketHistory.from_long(repo.load_market_history())
            stress = repo.load_run_frame(run_id, "stress")
            flags = repo.load_run_frame(run_id, "risk_flags")
        # 1. Largest net exposures per factor family and underlying, in P&L per unit bump.
        lin = sens[
            sens["measure"].isin(
                ["DV01", "CS01", "EQ_DELTA", "FX_DELTA", "CMD_DELTA", "CRYPTO_DELTA", "VEGA"]
            )
        ]
        by = (
            lin.groupby(["measure", "underlying"])["value"]
            .sum()
            .reset_index()
            .assign(abs_value=lambda d: d["value"].abs())
        )
        by = by.sort_values("abs_value", ascending=False)
        # 2. Recent moves in σ units, per family of the underlying (mean over the family's nodes).
        end = market.as_of
        daily = historical_shocks(history, end, 500, 1, universe, factor_ids=list(market.values))
        sigma = daily.std(ddof=0).replace(0.0, np.nan)
        recent = daily.tail(RECENT_DAYS)
        z = (recent / sigma).abs()
        fam_of = pd.Series({c: (_family(c), c.split(":")[1]) for c in daily.columns})
        moves: list[dict[str, Any]] = []
        for (fam, u), cols in fam_of.groupby(fam_of).groups.items():
            zz = z[list(cols)].mean(axis=1)
            if zz.empty or zz.isna().all():
                continue
            day = zz.idxmax()
            signed = float(recent.loc[day, list(cols)].mean())
            moves.append(
                {
                    "family": fam,
                    "underlying": u,
                    "date": str(day),
                    "sigma": round(float(zz.max()), 2),
                    "move": round(signed, 5),
                    "daily_sigma": round(float(sigma[list(cols)].mean()), 5),
                }
            )
        moves = sorted(moves, key=lambda m: -m["sigma"])[:6]
        # 3. Proposals: loss-direction 99%/10-day shocks on the top exposures, a repeat of the largest
        #    recent move (x3), and the two largest exposures together.
        proposals: list[dict[str, Any]] = []
        used: set[tuple[str, str]] = set()
        for _, r in by.iterrows():
            measure, u = r["measure"], r["underlying"]
            fams = [k for k, v in FAMILIES.items() if v[2] == measure]
            fam = next(
                (k for k in fams if any(_family(c) == k and c.split(":")[1] == u for c in daily.columns)),
                None,
            )
            if fam is None or (fam, u) in used:
                continue
            target, unit, _ = FAMILIES[fam]
            cols = [c for c in daily.columns if _family(c) == fam and c.split(":")[1] == u]
            s = float(sigma[cols].mean())
            if not np.isfinite(s) or s <= 0:
                continue
            sign = -1.0 if r["value"] > 0 else 1.0  # exposure gains for +1 unit: shock the other way
            if unit == "bp":
                size = sign * Z99_10D * s * (1e4 if fam == "IR:" else 1.0)
            elif unit == "vol_points":
                base = float(np.mean([market.values[c] for c in cols]))
                size = sign * Z99_10D * s * base * 100.0
            else:
                size = sign * Z99_10D * s * 100.0
            proposals.append(
                {
                    "name": f"{u} {size:+.0f}{'bp' if unit == 'bp' else (' vol pts' if unit == 'vol_points' else '%')} (99% 10-day)",
                    "why": f"largest {measure} exposure: {r['value']:,.0f} per unit bump",
                    "shocks": [{"target": target.format(u=u), "size": round(float(size), 2), "unit": unit}],
                }
            )
            used.add((fam, u))
            if len(proposals) >= max(n - 2, 2):
                break
        if moves:
            m0 = moves[0]
            target, unit, _ = FAMILIES[m0["family"]]
            mv = m0["move"]
            size = 3 * mv * (1e4 if m0["family"] == "IR:" else (100.0 if unit != "bp" else 1.0))
            if unit == "vol_points":
                base = float(
                    np.mean(
                        [
                            market.values[c]
                            for c in daily.columns
                            if _family(c) == m0["family"] and c.split(":")[1] == m0["underlying"]
                        ]
                    )
                )
                size = 3 * mv * base * 100.0
            proposals.append(
                {
                    "name": f"Repeat of {m0['date']} {m0['underlying']} move, three times over",
                    "why": f"largest recent move: {m0['sigma']:.1f} sigma on {m0['date']}",
                    "shocks": [
                        {
                            "target": target.format(u=m0["underlying"]),
                            "size": round(float(size), 2),
                            "unit": unit,
                        }
                    ],
                }
            )
        if len(proposals) >= 2:
            proposals.append(
                {
                    "name": "Combined: " + " and ".join(p["name"].split(" (")[0] for p in proposals[:2]),
                    "why": "the two largest exposures moving against the book together",
                    "shocks": proposals[0]["shocks"] + proposals[1]["shocks"],
                }
            )
        # 4. Run them through the engine.
        results = []
        for p in proposals[: n + 1]:
            try:
                res = what_if(
                    self.db_path, run_id, [Shock(**s) for s in p["shocks"]], by="asset_class", top=5
                )
                results.append(
                    {
                        **p,
                        "total_pnl_m": _m(res["total_pnl"]),
                        "by_asset_class_m": {k: _m(v) for k, v in res["by"].items()},
                        "worst_trades": [
                            {"trade_id": t["trade_id"], "desk_id": t["desk_id"], "pnl_m": _m(t["pnl"])}
                            for t in res["worst_trades"][:4]
                        ],
                    }
                )
            except Exception as e:  # noqa: BLE001
                results.append({**p, "error": str(e)})
        results.sort(key=lambda r: r.get("total_pnl_m") if r.get("total_pnl_m") is not None else 0.0)
        worst_lib = None
        if len(stress):
            tot = stress.groupby("scenario_id")["pnl"].sum()
            worst_lib = {"scenario_id": str(tot.idxmin()), "total_pnl_m": _m(tot.min())}
        return {
            "run_id": run_id,
            "business_date": str(run.business_date),
            "largest_exposures": [
                {"measure": r["measure"], "underlying": r["underlying"], "value": round(float(r["value"]), 0)}
                for _, r in by.head(8).iterrows()
            ],
            "largest_recent_moves": moves,
            "library_worst": worst_lib,
            "flags": [str(x) for x in flags["message"].head(5)] if len(flags) else [],
            "proposals": results,
        }


def suggest_scenarios(
    db_path: str, run_id: str | None = None, n: int = 4, provider=None, persist: bool = True
) -> AgentNote:
    agent = ScenarioSuggester(db_path, provider)
    evidence = agent.gather(run_id=run_id, n=n)
    return agent.run(evidence["run_id"], run_id=evidence["run_id"], persist=persist, evidence=evidence)
