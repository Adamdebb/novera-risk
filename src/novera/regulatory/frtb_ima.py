"""FRTB internal models approach. Record REG-002.

Expected shortfall at 97.5% over 10 days with liquidity horizons per risk-factor class,
computed on the historical scenarios by the delta-gamma-vega mapping (MR-004) with
factor subsets switched on by horizon (MAR33). The P&L attribution test compares the
risk-theoretical P&L (sensitivity-based) with the hypothetical P&L (full revaluation) per
desk on the scenario series: Spearman correlation and Kolmogorov–Smirnov, with the
traffic-light zones. Non-modellable risk factors are those the data-quality module found
stale or missing; their stress add-on is the class ES on those factors alone.
Backtesting exceptions feed the capital multiplier.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

from novera.market_data.history import MarketHistory
from novera.risk.revaluation import Portfolio
from novera.risk.scenarios import historical_shocks
from novera.risk.var import taylor_pnl_matrix

MODEL_VERSION = "1.0.0"

LIQUIDITY_HORIZONS = (10, 20, 40, 60, 120)


def liquidity_horizon(factor_id: str) -> int:
    """Days per MAR33.12, mapped to the platform's factor ids."""
    if factor_id.startswith("IR:"):
        ccy = factor_id.split(":")[1]
        return 10 if ccy in ("USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "SEK") else 20
    if factor_id.startswith("FX:"):
        pair = factor_id.split(":")[1]
        return (
            10
            if pair
            in (
                "EURUSD",
                "USDJPY",
                "GBPUSD",
                "AUDUSD",
                "USDCAD",
                "USDCHF",
                "USDMXN",
                "NZDUSD",
                "EURGBP",
                "USDSGD",
            )
            else 20
        )
    if factor_id.startswith("CDS:"):
        return 40 if "IG" in factor_id or "MAIN" in factor_id else 60
    if factor_id.startswith("EQIDX:"):
        return 10
    if factor_id.startswith("EQ:"):
        return 20
    if factor_id.startswith("CMD:"):
        return 20 if "GOLD" in factor_id or "SILVER" in factor_id else 40
    if factor_id.startswith("CRYPTO:"):
        return 120
    if factor_id.startswith("VOL:"):
        u = factor_id.split(":")[1]
        if u in ("BRENT", "WTI", "NATGAS", "GOLD", "SILVER", "COPPER", "ALUMINIUM"):
            return 60
        return 40 if (u.endswith("USD") or u.startswith("USD")) else 20
    if factor_id.startswith("SWVOL:"):
        return 60
    return 120


def es_975(pnl: np.ndarray) -> float:
    cut = np.percentile(pnl, 2.5, method="linear")
    tail = pnl[pnl <= cut]
    return float(-tail.mean()) if len(tail) else 0.0


@dataclass
class IMAResult:
    es_by_horizon: dict[int, float]  # ES of P&L from factors with LH >= h (10-day scaled)
    imes: float  # liquidity-horizon adjusted ES
    ses: float  # non-modellable add-on
    nmrf: list[str]
    multiplier: float
    exceptions: int
    capital: float
    pla: pd.DataFrame = field(default_factory=pd.DataFrame)
    by_desk: pd.DataFrame = field(default_factory=pd.DataFrame)

    def summary(self) -> dict:
        return {
            "imes": self.imes,
            "ses": self.ses,
            "nmrf": len(self.nmrf),
            "multiplier": self.multiplier,
            "backtest_exceptions": self.exceptions,
            "capital": self.capital,
            **{f"es_lh{h}": v for h, v in self.es_by_horizon.items()},
        }


def multiplier_from_exceptions(exceptions: int) -> float:
    """MAR32 add-on to the 1.5 floor by 250-day exceptions at 99%."""
    table = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.2, 6: 0.26, 7: 0.33, 8: 0.38, 9: 0.42}
    return 1.5 + table.get(exceptions, 0.5)


def frtb_ima(
    pf: Portfolio,
    sens: pd.DataFrame,
    history: MarketHistory,
    valuation: pd.DataFrame,
    hpl_pnl: pd.DataFrame,
    nmrf_factors: list[str],
    backtest_exceptions: int,
    window_days: int = 500,
) -> IMAResult:
    scen = historical_shocks(history, pf.as_of, window_days, 1, pf.universe, factor_ids=list(pf.base.values))
    lh = np.array([liquidity_horizon(f) for f in scen.columns])
    modellable = np.array([f not in set(nmrf_factors) for f in scen.columns])

    def es_with(mask: np.ndarray) -> float:
        sub = scen.copy()
        sub.loc[:, ~mask] = 0.0
        pnl = taylor_pnl_matrix(pf, sens, sub).sum(axis=1).to_numpy()
        return es_975(pnl) * np.sqrt(10)  # 10-day by square root of time (MAR33 base horizon)

    es_by_h = {h: es_with(modellable & (lh >= h)) for h in LIQUIDITY_HORIZONS}
    prev = 0
    total = 0.0
    for h in LIQUIDITY_HORIZONS:
        total += (es_by_h[h] * np.sqrt((h - prev) / 10)) ** 2
        prev = h
    imes = float(np.sqrt(total))
    ses = es_with(~modellable) if (~modellable).any() else 0.0
    mult = multiplier_from_exceptions(backtest_exceptions)
    capital = mult * imes + ses

    # P&L attribution test per desk on the scenario series: RTPL (Taylor) versus HPL (full reval).
    rtpl = taylor_pnl_matrix(pf, sens, scen)
    keys = valuation[["trade_id", "desk_id"]].drop_duplicates("trade_id").set_index("trade_id")["desk_id"]
    rows = []
    for desk in sorted(keys.dropna().unique()):
        ids = [t for t in rtpl.columns if keys.get(t) == desk and t in hpl_pnl.columns]
        if not ids:
            continue
        r = rtpl[ids].sum(axis=1).to_numpy()
        h = hpl_pnl[ids].sum(axis=1).reindex(rtpl.index).to_numpy()
        rho = float(stats.spearmanr(r, h).statistic) if np.std(r) > 0 and np.std(h) > 0 else 0.0
        ks = float(stats.ks_2samp(r, h).statistic)
        zone = "GREEN" if (rho >= 0.8 and ks <= 0.09) else ("RED" if (rho < 0.7 or ks > 0.12) else "AMBER")
        rows.append({"desk_id": desk, "spearman": rho, "ks": ks, "zone": zone, "trades": len(ids)})
    pla = pd.DataFrame(rows)
    # Desk attribution of IMES: standalone IMES per desk scaled to the total.
    by_desk_rows = []
    for desk in sorted(keys.dropna().unique()):
        ids = set(keys[keys == desk].index)
        sub_sens = sens[sens["trade_id"].isin(ids)]
        if sub_sens.empty:
            continue
        sub = scen.copy()
        sub.loc[:, ~(modellable & (lh >= 10))] = 0.0
        pnl = taylor_pnl_matrix(pf, sub_sens, sub).sum(axis=1).to_numpy()
        by_desk_rows.append({"desk_id": desk, "standalone_es10": es_975(pnl) * np.sqrt(10)})
    by_desk = pd.DataFrame(by_desk_rows)
    if len(by_desk) and by_desk["standalone_es10"].sum():
        by_desk["attributed_capital"] = (
            by_desk["standalone_es10"] / by_desk["standalone_es10"].sum() * capital
        )
    return IMAResult(
        es_by_h,
        imes,
        ses,
        list(nmrf_factors),
        mult,
        backtest_exceptions,
        capital,
        pla,
        by_desk.sort_values("standalone_es10", ascending=False) if len(by_desk) else by_desk,
    )
