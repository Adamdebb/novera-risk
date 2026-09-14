"""Monte Carlo VaR on the delta-gamma-vega approximation. Methodology record MR-010.

Daily factor moves over the VaR window give a covariance matrix; simulated moves are
drawn from the multivariate normal with that covariance (eigen-decomposition, negative
eigenvalues from the rank-deficient sample dropped) and pushed through the sensitivities.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from novera.market_data.history import MarketHistory
from novera.risk.revaluation import Portfolio
from novera.risk.scenarios import historical_shocks
from novera.risk.var import VaRConfig, VaRResult, _contributions, tail_measures, taylor_pnl_matrix

MODEL_VERSION = "1.0.0"


@dataclass(frozen=True)
class MonteCarloConfig:
    paths: int = 10_000
    seed: int = 7
    demean: bool = True  # simulate around zero drift


def simulate_factor_moves(hist_shocks: pd.DataFrame, cfg: MonteCarloConfig) -> pd.DataFrame:
    x = hist_shocks.to_numpy(dtype=float)
    mu = x.mean(axis=0) if not cfg.demean else np.zeros(x.shape[1])
    xc = x - x.mean(axis=0)
    n = xc.shape[0]
    # Simulate in observation space: cov = Xᵀ X / (n-1); draws = Xᵀ z / sqrt(n-1), z ~ N(0, I_n).
    # Exact for the sample covariance and avoids a 1,190 x 1,190 decomposition.
    rng = np.random.default_rng(cfg.seed)
    z = rng.standard_normal((cfg.paths, n))
    sims = mu + (z @ xc) / np.sqrt(max(n - 1, 1))
    return pd.DataFrame(sims, columns=hist_shocks.columns, index=pd.RangeIndex(cfg.paths, name="path"))


def monte_carlo_var(
    pf: Portfolio,
    sens: pd.DataFrame,
    history: MarketHistory,
    var_cfg: VaRConfig | None = None,
    mc_cfg: MonteCarloConfig | None = None,
) -> VaRResult:
    var_cfg = var_cfg or VaRConfig()
    mc_cfg = mc_cfg or MonteCarloConfig()
    hist = historical_shocks(
        history,
        pf.as_of,
        var_cfg.window_days,
        var_cfg.horizon_days,
        pf.universe,
        factor_ids=list(pf.base.values),
    )
    sims = simulate_factor_moves(hist, mc_cfg)
    pnl = taylor_pnl_matrix(pf, sens, sims)
    port = pnl.sum(axis=1)
    var, es, _ = tail_measures(port, var_cfg)
    return VaRResult(
        "monte_carlo_delta_gamma_vega",
        var_cfg,
        pnl,
        var,
        es,
        pf.as_of,
        port,
        _contributions(pnl, port, var_cfg, var),
    )
