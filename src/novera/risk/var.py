"""Historical-simulation VaR and expected shortfall. Methodology records MR-002 to MR-004.

Primary: 99% one-day VaR by full revaluation over an equally weighted two-year window,
with 97.5% expected shortfall alongside. Challenger: the same scenarios pushed through a
delta-gamma-vega Taylor expansion of the sensitivities table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from novera.market_data.history import MarketHistory
from novera.risk.revaluation import Portfolio
from novera.risk.scenarios import historical_shocks
from novera.risk.sensitivities import BUMPS

MODEL_VERSION = "1.0.0"


@dataclass(frozen=True)
class VaRConfig:
    confidence: float = 0.99
    es_confidence: float = 0.975
    window_days: int = 500
    horizon_days: int = 1
    scaling_days: int = 10  # reported alongside via square-root-of-time


@dataclass
class VaRResult:
    method: str
    config: VaRConfig
    pnl: pd.DataFrame  # scenarios x trades, reporting currency
    var: float
    es: float
    var_scenario_date: date
    portfolio_pnl: pd.Series = field(repr=False)
    contributions: pd.DataFrame = field(repr=False)  # trade_id, var_contribution, es_contribution

    @property
    def var_scaled(self) -> float:
        return self.var * np.sqrt(self.config.scaling_days / self.config.horizon_days)

    @property
    def es_scaled(self) -> float:
        return self.es * np.sqrt(self.config.scaling_days / self.config.horizon_days)

    def by(self, valuation: pd.DataFrame, column: str) -> pd.DataFrame:
        """Component VaR/ES and standalone VaR per group of trades."""
        keys = valuation[["trade_id", column]].drop_duplicates("trade_id").set_index("trade_id")[column]
        groups = keys.reindex(self.pnl.columns)
        out = []
        for g, cols in groups.groupby(groups, dropna=False).groups.items():
            sub = self.pnl[list(cols)].sum(axis=1)
            var_g, es_g, _ = tail_measures(sub, self.config)
            contrib = self.contributions[self.contributions["trade_id"].isin(cols)]
            out.append(
                {
                    column: g,
                    "standalone_var": var_g,
                    "standalone_es": es_g,
                    "component_var": contrib["var_contribution"].sum(),
                    "component_es": contrib["es_contribution"].sum(),
                    "trades": len(cols),
                }
            )
        return pd.DataFrame(out).sort_values("component_var", ascending=False).reset_index(drop=True)


def tail_measures(portfolio_pnl: pd.Series, cfg: VaRConfig) -> tuple[float, float, date]:
    """(VaR, ES, VaR scenario date). VaR is the loss at the confidence quantile using linear
    interpolation between order statistics; ES is the mean loss beyond the ES quantile."""
    losses = -portfolio_pnl.sort_values()  # ascending P&L -> descending losses first
    pnl = portfolio_pnl.to_numpy()
    var = float(-np.percentile(pnl, (1 - cfg.confidence) * 100, method="linear"))
    es_cut = np.percentile(pnl, (1 - cfg.es_confidence) * 100, method="linear")
    tail = pnl[pnl <= es_cut]
    es = float(-tail.mean()) if len(tail) else var
    order = portfolio_pnl.sort_values()
    k = int(np.floor((1 - cfg.confidence) * len(order)))
    var_date = order.index[min(max(k, 0), len(order) - 1)]
    _ = losses
    return var, es, var_date


def _contributions(pnl: pd.DataFrame, portfolio_pnl: pd.Series, cfg: VaRConfig, var: float) -> pd.DataFrame:
    """Component allocation that sums to the total: VaR from the average of the scenarios
    around the quantile (kernel of three), ES from the tail scenarios themselves."""
    order = portfolio_pnl.sort_values()
    n = len(order)
    k = int(np.floor((1 - cfg.confidence) * n))
    window = order.index[max(k - 1, 0) : min(k + 2, n)]
    var_alloc = -pnl.loc[window].mean(axis=0)
    total = var_alloc.sum()
    if total != 0:
        var_alloc = var_alloc * (var / total)
    es_cut = np.percentile(portfolio_pnl.to_numpy(), (1 - cfg.es_confidence) * 100, method="linear")
    tail_idx = portfolio_pnl[portfolio_pnl <= es_cut].index
    es_alloc = -pnl.loc[tail_idx].mean(axis=0) if len(tail_idx) else var_alloc * 0.0
    return pd.DataFrame(
        {
            "trade_id": pnl.columns,
            "var_contribution": var_alloc.to_numpy(),
            "es_contribution": es_alloc.to_numpy(),
        }
    )


def historical_var(pf: Portfolio, history: MarketHistory, cfg: VaRConfig | None = None) -> VaRResult:
    cfg = cfg or VaRConfig()
    universe = pf.universe
    scen = historical_shocks(
        history, pf.as_of, cfg.window_days, cfg.horizon_days, universe, factor_ids=list(pf.base.values)
    )
    pnl = pf.pnl_matrix(scen)
    port = pnl.sum(axis=1)
    var, es, var_date = tail_measures(port, cfg)
    return VaRResult(
        "historical_full_revaluation", cfg, pnl, var, es, var_date, port, _contributions(pnl, port, cfg, var)
    )


def taylor_var(
    pf: Portfolio, sens: pd.DataFrame, history: MarketHistory, cfg: VaRConfig | None = None
) -> VaRResult:
    """Delta-gamma-vega approximation on the same historical scenarios.

    P&L_s ≈ Σ_f delta_f · x_f,s / bump_f + ½ Σ_f gamma_f · (x_f,s / bump_f)² + Σ_u vega_u · Δvol_u,s / 0.01
    where x is the scenario shock in factor units. Commodity curves use the mean node return;
    vega uses the mean ATM vol change of the surface.
    """
    cfg = cfg or VaRConfig()
    scen = historical_shocks(
        history, pf.as_of, cfg.window_days, cfg.horizon_days, pf.universe, factor_ids=list(pf.base.values)
    )
    cols = pf.priced_ids
    pos = {tid: j for j, tid in enumerate(cols)}
    mat = np.zeros((len(scen), len(cols)))

    def shock_series(fid: str) -> np.ndarray | None:
        if fid.endswith(":"):  # family: mean move across nodes
            members = [c for c in scen.columns if c.startswith(fid)]
            if fid.startswith("VOL:"):
                base_vols = np.array([pf.base.values[m] for m in members])
                # relative vol moves -> absolute vol-point moves, averaged
                return (scen[members].to_numpy() * base_vols).mean(axis=1)
            return scen[members].to_numpy().mean(axis=1) if members else None
        return scen[fid].to_numpy() if fid in scen.columns else None

    for (measure, fid), grp in sens.groupby(["measure", "factor_id"]):
        if measure == "THETA":
            continue
        x = shock_series(fid)
        if x is None:
            continue
        bump = BUMPS.get(measure, 1.0)
        units = x / bump  # number of bumps moved in this scenario
        for _, r in grp.iterrows():
            j = pos.get(r["trade_id"])
            if j is None:
                continue
            if measure == "GAMMA":
                mat[:, j] += 0.5 * r["value"] * units**2
            else:
                mat[:, j] += r["value"] * units
    pnl = pd.DataFrame(mat, index=scen.index, columns=cols)
    port = pnl.sum(axis=1)
    var, es, var_date = tail_measures(port, cfg)
    return VaRResult(
        "delta_gamma_vega", cfg, pnl, var, es, var_date, port, _contributions(pnl, port, cfg, var)
    )


def compare(
    primary: VaRResult, challenger: VaRResult, valuation: pd.DataFrame, column: str = "asset_class"
) -> pd.DataFrame:
    """Where and why two VaR methods differ, by group."""
    a = primary.by(valuation, column).set_index(column)["component_var"]
    b = challenger.by(valuation, column).set_index(column)["component_var"]
    out = pd.DataFrame({"primary": a, "challenger": b}).fillna(0.0)
    out["difference"] = out["challenger"] - out["primary"]
    out["difference_pct"] = np.where(out["primary"] != 0, out["difference"] / out["primary"].abs(), np.nan)
    return out.sort_values("difference", key=np.abs, ascending=False)
