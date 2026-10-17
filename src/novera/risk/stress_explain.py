"""Explaining the stress results: P&L by factor family split into delta, gamma, vega, higher order
and cross-family terms, the top trades, the hedge-failure (basis) line and a summary of the
significant risks (MR-020, decision round 49).

For every stress scenario and every desk the loss is taken apart so that the parts add up to the
full-revaluation figure exactly:

- **Family alone.** The scenario's shocks of one factor family are applied alone, in full
  revaluation. This is the family's P&L.
- **Delta, gamma, vega.** The same family's Taylor terms from the stored sensitivities (the
  approximation the challenger VaR uses): first-order spot, rate and spread terms, the second-order
  spot term, and the vega term.
- **Higher order.** The family's full-revaluation P&L less its Taylor terms: smile, convexity beyond
  gamma, exotic payoffs and everything the sensitivities do not carry.
- **Cross-family.** The scenario's total less the sum of the families alone: what the families do
  to one another when they move together (a rate shock on an option whose spot also moves).

The hedge-failure line is the BASIS family alone: tenor basis and cross-currency basis, the
residual a hedge keeps when its two legs do not move together. It is reported on its own line
whatever the sign.

The summary of significant risks puts VaR and the worst stress side by side per desk and, for the
positions that matter, per position.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from novera.risk.revaluation import Portfolio
from novera.risk.sensitivities import BUMPS

RECORD = "MR-020"
MODEL_VERSION = "1.0.0"

FAMILY_OF_PREFIX = {
    "IR": "RATES",
    "IRBASIS": "BASIS",
    "XCCY": "BASIS",
    "CDS": "CREDIT",
    "FX": "FX",
    "EQ": "EQUITY",
    "EQIDX": "EQUITY",
    "CMD": "COMMODITY",
    "CRYPTO": "CRYPTO",
    "VOL": "VOLATILITY",
    "SWVOL": "RATES_VOL",
    "SWRHO": "RATES_VOL",
    "SWNU": "RATES_VOL",
}
HEDGE_FAILURE_FAMILY = "BASIS"
CROSS_FAMILY = "CROSS_FAMILY"
TOP_TRADES = 10
DESK_SHARE = 0.10
"""A desk is significant when it carries this share of the firm's VaR or of the firm's worst stress."""
POSITION_SHARE = 0.02
"""A position is significant at this share of the firm's VaR or of the firm's worst stress loss."""
MAX_POSITIONS = 30
FIRM = "(firm)"
TERMS = ("delta", "gamma", "vega", "higher_order", "cross_family")
EXPLAIN_COLUMNS = ["scenario_id", "desk_id", "family", *TERMS, "total"]
TOP_COLUMNS = ["scenario_id", "rank", "trade_id", "desk_id", "asset_class", "product_type", "pnl"]
SUMMARY_COLUMNS = [
    "level",
    "key",
    "desk_id",
    "var",
    "var_share",
    "worst_stress",
    "worst_scenario",
    "stress_in_firm_worst",
    "stress_share",
    "significant",
]


def family_of(factor_id: str) -> str:
    return FAMILY_OF_PREFIX.get(factor_id.split(":", 1)[0], "OTHER")


@dataclass
class StressExplain:
    explain: pd.DataFrame
    """scenario_id, desk_id, family, delta, gamma, vega, higher_order, cross_family, total."""
    top_trades: pd.DataFrame
    risk_summary: pd.DataFrame
    firm_worst_scenario: str | None = None

    def summary(self) -> dict[str, object]:
        out: dict[str, object] = {}
        if len(self.explain):
            basis = self.explain[self.explain["family"] == HEDGE_FAILURE_FAMILY]
            if len(basis):
                firm = basis.groupby("scenario_id")["total"].sum()
                out.update(hedge_failure_scenario=str(firm.idxmin()), hedge_failure=float(firm.min()))
        if len(self.risk_summary):
            out["significant_risks"] = int(self.risk_summary["significant"].sum())
        return out


# --- Taylor terms --------------------------------------------------------------------------------
def _shock_x(pf: Portfolio, shocks: dict[str, float], fid: str) -> float | None:
    """The scenario move of one sensitivity factor in shock units, as ``taylor_pnl_matrix`` reads it:
    a family id (trailing colon) takes the mean node move, in vol points for vol surfaces."""
    if fid.endswith(":"):
        members = [c for c in shocks if c.startswith(fid)]
        if not members:
            return None
        if fid.startswith(("VOL:", "SWVOL:")):
            return float(np.mean([shocks[m] * pf.base.values[m] for m in members]))
        return float(np.mean([shocks[m] for m in members]))
    return shocks.get(fid)


def taylor_terms(pf: Portfolio, sens: pd.DataFrame, shocks: dict[str, float]) -> pd.DataFrame:
    """Delta, gamma and vega P&L per (trade, family) under one set of shocks."""
    s = sens[(sens["measure"] != "THETA") & sens["trade_id"].isin(set(pf.priced_ids))]
    if s.empty:
        return pd.DataFrame(columns=["trade_id", "family", "delta", "gamma", "vega"])
    xmap = {fid: _shock_x(pf, shocks, fid) for fid in s["factor_id"].unique()}
    x = s["factor_id"].map(xmap).astype(float).fillna(0.0).to_numpy()
    measure = s["measure"].to_numpy()
    bump = np.where(measure == "VEGA", s["bump"].to_numpy(), s["measure"].map(BUMPS).fillna(1.0).to_numpy())
    units = x / bump
    value = s["value"].to_numpy()
    contrib = np.where(measure == "GAMMA", 0.5 * value * units**2, value * units)
    out = pd.DataFrame(
        {
            "trade_id": s["trade_id"].to_numpy(),
            "family": s["factor_id"].map(family_of).to_numpy(),
            "delta": np.where(~np.isin(measure, ["GAMMA", "VEGA"]), contrib, 0.0),
            "gamma": np.where(measure == "GAMMA", contrib, 0.0),
            "vega": np.where(measure == "VEGA", contrib, 0.0),
        }
    )
    return out.groupby(["trade_id", "family"], as_index=False).sum()


# --- explanation ---------------------------------------------------------------------------------
def explain_scenario(
    pf: Portfolio,
    sens: pd.DataFrame,
    scenario_id: str,
    shocks: dict[str, float],
    total: pd.Series,
    desks: pd.Series,
) -> pd.DataFrame:
    """The scenario's loss by desk and family; the terms add up to ``total`` per desk."""
    families = sorted({family_of(f) for f in shocks})
    taylor = taylor_terms(pf, sens, shocks)
    tid = list(total.index)
    alone: dict[str, pd.Series] = {}
    for fam in families:
        sub = {f: v for f, v in shocks.items() if family_of(f) == fam}
        alone[fam] = pd.Series(pf.pnl_under_shocks(sub)).reindex(tid).fillna(0.0)
    desk_of = desks.reindex(tid).fillna("UNKNOWN").astype(str)
    rows: list[pd.DataFrame] = []
    for fam in families:
        t = taylor[taylor["family"] == fam].set_index("trade_id").reindex(tid).fillna(0.0)
        frame = pd.DataFrame(
            {
                "desk_id": desk_of.to_numpy(),
                "delta": t["delta"].to_numpy(),
                "gamma": t["gamma"].to_numpy(),
                "vega": t["vega"].to_numpy(),
                "higher_order": (alone[fam] - t[["delta", "gamma", "vega"]].sum(axis=1)).to_numpy(),
                "cross_family": 0.0,
                "total": alone[fam].to_numpy(),
            }
        )
        frame.insert(1, "family", fam)
        rows.append(frame)
    cross = total.reindex(tid).fillna(0.0) - sum(alone.values(), pd.Series(0.0, index=tid))
    frame = pd.DataFrame(
        {
            "desk_id": desk_of.to_numpy(),
            "delta": 0.0,
            "gamma": 0.0,
            "vega": 0.0,
            "higher_order": 0.0,
            "cross_family": cross.to_numpy(),
            "total": cross.to_numpy(),
        }
    )
    frame.insert(1, "family", CROSS_FAMILY)
    rows.append(frame)
    out = pd.concat(rows, ignore_index=True).groupby(["desk_id", "family"], as_index=False).sum()
    out.insert(0, "scenario_id", scenario_id)
    return out[EXPLAIN_COLUMNS]


def top_trades(
    scenario_id: str, total: pd.Series, valuation: pd.DataFrame, n: int = TOP_TRADES
) -> pd.DataFrame:
    info = valuation.drop_duplicates("trade_id").set_index("trade_id")
    worst = total.sort_values().head(n)
    rows = [
        {
            "scenario_id": scenario_id,
            "rank": rank,
            "trade_id": tid,
            "desk_id": str(info["desk_id"].get(tid, "UNKNOWN")),
            "asset_class": str(info["asset_class"].get(tid, "")),
            "product_type": str(info["product_type"].get(tid, "")),
            "pnl": float(v),
        }
        for rank, (tid, v) in enumerate(worst.items(), 1)
    ]
    return pd.DataFrame(rows, columns=TOP_COLUMNS)


def risk_summary(
    valuation: pd.DataFrame,
    var_result,
    limit_stress: list,
    desk_share: float = DESK_SHARE,
    position_share: float = POSITION_SHARE,
    max_positions: int = MAX_POSITIONS,
) -> tuple[pd.DataFrame, str | None]:
    """VaR and the worst stress per desk and for the significant positions, with the firm on top.

    ``var`` is the headline measure's component VaR (a loss, positive); stress figures are P&L
    (a loss is negative). A desk's ``stress_in_firm_worst`` is its P&L in the firm's worst limit
    scenario, so the desks add up to the firm; ``worst_stress`` is its own worst scenario."""
    if not limit_stress:
        return pd.DataFrame(columns=SUMMARY_COLUMNS), None
    info = valuation.drop_duplicates("trade_id").set_index("trade_id")
    pnl = pd.DataFrame({r.scenario.scenario_id: r.pnl for r in limit_stress}).fillna(0.0)
    names = {r.scenario.scenario_id: r.scenario.name for r in limit_stress}
    firm_by_scenario = pnl.sum()
    worst_id = str(firm_by_scenario.idxmin())
    firm_worst = float(firm_by_scenario.min())
    comp = var_result.contributions.set_index("trade_id")["var_contribution"]
    firm_var = float(var_result.var)
    desk_of = info["desk_id"].reindex(pnl.index).fillna("UNKNOWN").astype(str)
    by_desk = pnl.groupby(desk_of).sum()
    desk_var = comp.groupby(info["desk_id"].reindex(comp.index).fillna("UNKNOWN").astype(str)).sum()

    def share(x: float, whole: float) -> float:
        return float(x / whole) if whole else 0.0

    rows: list[dict[str, object]] = [
        {
            "level": "FIRM",
            "key": FIRM,
            "desk_id": FIRM,
            "var": firm_var,
            "var_share": 1.0,
            "worst_stress": firm_worst,
            "worst_scenario": names[worst_id],
            "stress_in_firm_worst": firm_worst,
            "stress_share": 1.0,
            "significant": True,
        }
    ]
    for desk in sorted(set(by_desk.index) | set(desk_var.index)):
        own = by_desk.loc[desk] if desk in by_desk.index else pd.Series(0.0, index=pnl.columns)
        sid = str(own.idxmin())
        v, ss = float(desk_var.get(desk, 0.0)), share(float(own.get(worst_id, 0.0)), firm_worst)
        rows.append(
            {
                "level": "DESK",
                "key": desk,
                "desk_id": desk,
                "var": v,
                "var_share": share(v, firm_var),
                "worst_stress": float(own.min()),
                "worst_scenario": names[sid],
                "stress_in_firm_worst": float(own.get(worst_id, 0.0)),
                "stress_share": ss,
                "significant": share(v, firm_var) >= desk_share or ss >= desk_share,
            }
        )
    worst_by_trade = pnl.min(axis=1)
    where = pnl.idxmin(axis=1)
    pos = pd.DataFrame(
        {
            "var": comp.reindex(pnl.index).fillna(0.0),
            "worst": worst_by_trade,
            "in_worst": pnl[worst_id],
        }
    )
    pos["var_share"] = pos["var"] / firm_var if firm_var else 0.0
    pos["stress_share"] = pos["in_worst"] / firm_worst if firm_worst else 0.0
    keep = pos[(pos["var_share"] >= position_share) | (pos["stress_share"] >= position_share)]
    keep = keep.reindex(keep["worst"].abs().sort_values(ascending=False).index).head(max_positions)
    for tid, r in keep.iterrows():
        rows.append(
            {
                "level": "POSITION",
                "key": tid,
                "desk_id": str(desk_of.get(tid, "UNKNOWN")),
                "var": float(r["var"]),
                "var_share": float(r["var_share"]),
                "worst_stress": float(r["worst"]),
                "worst_scenario": names[str(where[tid])],
                "stress_in_firm_worst": float(r["in_worst"]),
                "stress_share": float(r["stress_share"]),
                "significant": True,
            }
        )
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS), worst_id


def run_stress_explain(
    pf: Portfolio, sens: pd.DataFrame, stress: list, limit_stress: list, valuation: pd.DataFrame, var_result
) -> StressExplain:
    desks = valuation.drop_duplicates("trade_id").set_index("trade_id")["desk_id"]
    parts, tops = [], []
    for r in stress:
        sid = r.scenario.scenario_id
        parts.append(explain_scenario(pf, sens, sid, r.shocks, r.pnl, desks))
        tops.append(top_trades(sid, r.pnl, valuation))
    explain = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=EXPLAIN_COLUMNS)
    top = pd.concat(tops, ignore_index=True) if tops else pd.DataFrame(columns=TOP_COLUMNS)
    summary, worst = risk_summary(valuation, var_result, limit_stress)
    return StressExplain(explain, top, summary, worst)
