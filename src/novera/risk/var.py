"""Historical-simulation VaR and expected shortfall. Methodology records MR-002 to MR-004,
MR-015 and MR-016.

Primary: 99% one-day VaR by full revaluation over an equally weighted two-year window,
with 97.5% expected shortfall alongside. Challenger: the same scenarios pushed through a
delta-gamma-vega Taylor expansion of the sensitivities table. The same code produces the
exponentially weighted variant (``VaRConfig.decay``, MR-015) and the stressed variant on a
fixed historical window (``VaRConfig.window_start`` and ``window_end``, MR-016); which
variants a firm produces is the VaR setup (``novera.risk.var_measures``, OPS-004).
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

MODEL_VERSION = "1.1.0"


@dataclass(frozen=True)
class VaRConfig:
    confidence: float = 0.99
    es_confidence: float = 0.975
    window_days: int = 500
    horizon_days: int = 1
    scaling_days: int = 10  # reported alongside via square-root-of-time
    decay: float | None = None
    """Exponential weighting factor lambda (MR-015); None gives equal weights."""
    window_start: str | None = None
    """ISO date: fixed historical window for stressed VaR (MR-016); overrides ``window_days``."""
    window_end: str | None = None
    """ISO date: end of the fixed window (inclusive). With ``window_start`` alone the window
    ends at the valuation date."""

    @property
    def fixed_window(self) -> tuple[date, date | None] | None:
        if self.window_start is None and self.window_end is None:
            return None
        if self.window_start is None:
            raise ValueError("window_end needs window_start")
        return (
            date.fromisoformat(self.window_start),
            date.fromisoformat(self.window_end) if self.window_end else None,
        )


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
    weights: np.ndarray | None = field(default=None, repr=False)
    """Scenario weights in ``portfolio_pnl`` order when the config decays; None for equal weights."""

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


def scenario_weights(index: pd.Index, cfg: VaRConfig) -> np.ndarray | None:
    """Exponentially decaying weights by scenario age (MR-015): the newest scenario date gets
    weight proportional to 1, the one before ``decay``, and so on; normalised to sum to one.
    None when the config has no decay, so equally weighted results are untouched."""
    if cfg.decay is None:
        return None
    if not 0 < cfg.decay < 1:
        raise ValueError("decay must lie strictly between 0 and 1")
    n = len(index)
    newest_first = np.argsort(np.asarray(index, dtype=object), kind="stable")[::-1]
    age = np.empty(n)
    age[newest_first] = np.arange(n)
    w = cfg.decay**age
    return w / w.sum()


def _weighted_quantile(pnl: np.ndarray, w: np.ndarray, p: float) -> float:
    """P&L at cumulative weight ``p`` (ascending): the weighted historical simulation of
    Boudoukh, Richardson and Whitelaw, interpolating linearly on the grid
    ``(cum_i − w_i) / (1 − w_n)`` so that equal weights reproduce exactly the linear
    order-statistic quantile of the unweighted measure."""
    order = np.argsort(pnl, kind="stable")
    ps, ws = pnl[order], w[order]
    cum = np.cumsum(ws)
    grid = (cum - ws) / max(1.0 - ws[-1], 1e-300)
    return float(np.interp(p, grid, ps))


def tail_measures(
    portfolio_pnl: pd.Series, cfg: VaRConfig, weights: np.ndarray | None = None
) -> tuple[float, float, date]:
    """(VaR, ES, VaR scenario date). VaR is the loss at the confidence quantile using linear
    interpolation between order statistics; ES is the mean loss beyond the ES quantile.
    With decaying weights (``cfg.decay`` or ``weights``) the quantile is the weighted one
    and ES the weighted mean of the tail (MR-015)."""
    pnl = portfolio_pnl.to_numpy()
    w = weights if weights is not None else scenario_weights(portfolio_pnl.index, cfg)
    order = portfolio_pnl.sort_values(kind="stable")
    if w is None:
        var = float(-np.percentile(pnl, (1 - cfg.confidence) * 100, method="linear"))
        es_cut = np.percentile(pnl, (1 - cfg.es_confidence) * 100, method="linear")
        tail = pnl[pnl <= es_cut]
        es = float(-tail.mean()) if len(tail) else var
        k = int(np.floor((1 - cfg.confidence) * len(order)))
    else:
        var = -_weighted_quantile(pnl, w, 1 - cfg.confidence)
        es_cut = _weighted_quantile(pnl, w, 1 - cfg.es_confidence)
        tail = pnl <= es_cut
        es = float(-(pnl[tail] * w[tail]).sum() / w[tail].sum()) if tail.any() else var
        k = _weighted_rank(portfolio_pnl, w, 1 - cfg.confidence)
    var_date = order.index[min(max(k, 0), len(order) - 1)]
    return var, es, var_date


def _weighted_rank(portfolio_pnl: pd.Series, w: np.ndarray, p: float) -> int:
    """Position, in ascending P&L order, of the scenario at which the cumulative weight
    reaches ``p``."""
    order = np.argsort(portfolio_pnl.to_numpy(), kind="stable")
    cum = np.cumsum(w[order])
    return int(np.searchsorted(cum, p))


def _contributions(
    pnl: pd.DataFrame,
    portfolio_pnl: pd.Series,
    cfg: VaRConfig,
    var: float,
    weights: np.ndarray | None = None,
) -> pd.DataFrame:
    """Component allocation that sums to the total: VaR from the average of the scenarios
    around the quantile (kernel of three), ES from the tail scenarios themselves (their
    weighted mean when the scenarios are weighted)."""
    order = portfolio_pnl.sort_values(kind="stable")
    n = len(order)
    k = (
        int(np.floor((1 - cfg.confidence) * n))
        if weights is None
        else _weighted_rank(portfolio_pnl, weights, 1 - cfg.confidence)
    )
    window = order.index[max(k - 1, 0) : min(k + 2, n)]
    var_alloc = -pnl.loc[window].mean(axis=0)
    total = var_alloc.sum()
    if total != 0:
        var_alloc = var_alloc * (var / total)
    if weights is None:
        es_cut = np.percentile(portfolio_pnl.to_numpy(), (1 - cfg.es_confidence) * 100, method="linear")
        tail_idx = portfolio_pnl[portfolio_pnl <= es_cut].index
        es_alloc = -pnl.loc[tail_idx].mean(axis=0) if len(tail_idx) else var_alloc * 0.0
    else:
        es_cut = _weighted_quantile(portfolio_pnl.to_numpy(), weights, 1 - cfg.es_confidence)
        tail = (portfolio_pnl <= es_cut).to_numpy()
        if tail.any():
            wt = weights[tail]
            es_alloc = -pnl.loc[tail].mul(wt, axis=0).sum(axis=0) / wt.sum()
        else:
            es_alloc = var_alloc * 0.0
    return pd.DataFrame(
        {
            "trade_id": pnl.columns,
            "var_contribution": var_alloc.to_numpy(),
            "es_contribution": es_alloc.to_numpy(),
        }
    )


def scenario_shocks(
    history: MarketHistory, as_of: date, cfg: VaRConfig, universe: dict, factor_ids: list[str]
) -> pd.DataFrame:
    """The historical scenario matrix the config asks for: the last ``window_days`` moves
    ending at ``as_of``, or every move inside the fixed window of a stressed VaR (MR-016)."""
    fixed = cfg.fixed_window
    end, days = as_of, cfg.window_days
    if fixed is not None:
        start, fixed_end = fixed
        end = fixed_end or as_of
        idx = history.wide.index
        n = int(idx.searchsorted(end, side="right") - idx.searchsorted(start, side="left"))
        if n <= cfg.horizon_days:
            raise ValueError(f"fixed VaR window {start} to {end} has {n} observations in the history")
        days = n - cfg.horizon_days
    return historical_shocks(history, end, days, cfg.horizon_days, universe, factor_ids=factor_ids)


def result_from_pnl(method: str, cfg: VaRConfig, pnl: pd.DataFrame) -> VaRResult:
    """VaR, ES, the VaR scenario and the contributions from a scenario-by-trade P&L matrix."""
    port = pnl.sum(axis=1)
    weights = scenario_weights(port.index, cfg)
    var, es, var_date = tail_measures(port, cfg, weights)
    return VaRResult(
        method, cfg, pnl, var, es, var_date, port, _contributions(pnl, port, cfg, var, weights), weights
    )


def historical_var(pf: Portfolio, history: MarketHistory, cfg: VaRConfig | None = None) -> VaRResult:
    cfg = cfg or VaRConfig()
    scen = scenario_shocks(history, pf.as_of, cfg, pf.universe, list(pf.base.values))
    return result_from_pnl(historical_method(cfg, "full_revaluation"), cfg, pf.pnl_matrix(scen))


def historical_method(cfg: VaRConfig, compute: str) -> str:
    """Method token of a historical-simulation result: ``historical_full_revaluation`` or
    ``delta_gamma_vega``, prefixed ``historical_weighted_`` when the scenarios decay."""
    if compute == "full_revaluation":
        return "historical_weighted_full_revaluation" if cfg.decay else "historical_full_revaluation"
    return "historical_weighted_delta_gamma_vega" if cfg.decay else "delta_gamma_vega"


def taylor_pnl_matrix(pf: Portfolio, sens: pd.DataFrame, scen: pd.DataFrame) -> pd.DataFrame:
    """Delta-gamma-vega P&L of every trade under a scenario matrix (rows scenarios, columns
    factor ids in shock units):

    P&L_s ≈ Σ_f delta_f · x_f,s / bump_f + ½ Σ_f gamma_f · (x_f,s / bump_f)² + Σ_u vega_u · Δvol_u,s / 0.01

    Commodity curves use the mean node return; vega uses the mean ATM vol change of the surface.
    """
    cols = pf.priced_ids
    pos = {tid: j for j, tid in enumerate(cols)}
    mat = np.zeros((len(scen), len(cols)))

    def shock_series(fid: str) -> np.ndarray | None:
        if fid.endswith(":"):  # family: mean move across nodes
            members = [c for c in scen.columns if c.startswith(fid)]
            if fid.startswith(("VOL:", "SWVOL:")):  # vol points (or normal bp) from relative moves
                base_vols = np.array([pf.base.values[m] for m in members])
                return (scen[members].to_numpy() * base_vols).mean(axis=1)
            return scen[members].to_numpy().mean(axis=1) if members else None
        return scen[fid].to_numpy() if fid in scen.columns else None

    for (measure, fid), grp in sens.groupby(["measure", "factor_id"]):
        if measure == "THETA":
            continue
        x = shock_series(fid)
        if x is None:
            continue
        bump = float(grp["bump"].iloc[0]) if measure == "VEGA" else BUMPS.get(measure, 1.0)
        units = x / bump
        for _, r in grp.iterrows():
            j = pos.get(r["trade_id"])
            if j is None:
                continue
            mat[:, j] += 0.5 * r["value"] * units**2 if measure == "GAMMA" else r["value"] * units
    return pd.DataFrame(mat, index=scen.index, columns=cols)


def taylor_var(
    pf: Portfolio, sens: pd.DataFrame, history: MarketHistory, cfg: VaRConfig | None = None
) -> VaRResult:
    """Delta-gamma-vega approximation on the same historical scenarios (the challenger)."""
    cfg = cfg or VaRConfig()
    scen = scenario_shocks(history, pf.as_of, cfg, pf.universe, list(pf.base.values))
    return result_from_pnl(historical_method(cfg, "sensitivity"), cfg, taylor_pnl_matrix(pf, sens, scen))


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
