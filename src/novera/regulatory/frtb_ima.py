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
from novera.regulatory.allocation import UNASSIGNED, desk_map, with_euler
from novera.risk.revaluation import Portfolio
from novera.risk.scenarios import historical_shocks
from novera.risk.var import taylor_pnl_matrix

MODEL_VERSION = "1.1.0"

LIQUIDITY_HORIZONS = (10, 20, 40, 60, 120)


def liquidity_horizon(factor_id: str) -> int:
    """Days per MAR33.12, mapped to the platform's factor ids."""
    if factor_id.startswith(("IR:", "IRBASIS:", "XCCY:")):
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
    if factor_id.startswith(("SWVOL:", "SWRHO:", "SWNU:")):
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

    desk_of = desk_map(valuation)

    def desk_pnl(mask: np.ndarray) -> pd.DataFrame:
        """Scenario P&L by desk (columns) from the factors in ``mask``."""
        sub = scen.copy()
        sub.loc[:, ~mask] = 0.0
        pnl = taylor_pnl_matrix(pf, sens, sub)
        return pnl.T.groupby(pnl.columns.map(lambda t: desk_of.get(t, UNASSIGNED))).sum().T

    # 10-day ES by square root of time (MAR33 base horizon), by liquidity horizon, then the
    # non-modellable factors on their own.
    pnl_by_h = {h: desk_pnl(modellable & (lh >= h)) for h in LIQUIDITY_HORIZONS}
    pnl_ses = desk_pnl(~modellable) if (~modellable).any() else None
    mult = multiplier_from_exceptions(backtest_exceptions)
    es_by_h, imes, ses, capital = _ima_capital(pnl_by_h, pnl_ses, mult)

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
    by_desk = _ima_by_desk(pnl_by_h, pnl_ses, mult, capital)
    return IMAResult(
        es_by_h,
        imes,
        ses,
        list(nmrf_factors),
        mult,
        backtest_exceptions,
        capital,
        pla,
        by_desk,
    )


def _ima_capital(
    pnl_by_h: dict[int, pd.DataFrame], pnl_ses: pd.DataFrame | None, mult: float, w: pd.Series | None = None
) -> tuple[dict[int, float], float, float, float]:
    """ES by horizon, IMES, SES and capital of the book with each desk's P&L weighted by ``w``
    (all 1 when None): IMES = sqrt(Σ_h (ES_h · sqrt((h − h_prev)/10))²), capital = m · IMES + SES."""

    def es(p: pd.DataFrame) -> float:
        x = p.to_numpy() @ (
            np.ones(p.shape[1]) if w is None else w.reindex(p.columns, fill_value=0.0).to_numpy()
        )
        return es_975(x) * np.sqrt(10)

    es_by_h = {h: es(p) for h, p in pnl_by_h.items()}
    prev, total = 0, 0.0
    for h in LIQUIDITY_HORIZONS:
        total += (es_by_h[h] * np.sqrt((h - prev) / 10)) ** 2
        prev = h
    imes = float(np.sqrt(total))
    ses = es(pnl_ses) if pnl_ses is not None else 0.0
    return es_by_h, imes, ses, mult * imes + ses


def _tail_contribution(p: pd.DataFrame) -> pd.Series:
    """Euler contribution of each desk to the 10-day ES of the sum: minus its mean P&L over the
    scenarios in the total's tail (exact: the contributions add up to the ES)."""
    total = p.sum(axis=1).to_numpy()
    tail = total <= np.percentile(total, 2.5, method="linear")
    return -p[tail].mean(axis=0) * np.sqrt(10) if tail.any() else p.iloc[0] * 0.0


def _ima_by_desk(
    pnl_by_h: dict[int, pd.DataFrame], pnl_ses: pd.DataFrame | None, mult: float, capital: float
) -> pd.DataFrame:
    """Desk allocation of the IMA capital (REG-008). Euler through the cascade:
    ∂IMES/∂x_d = Σ_h ES_h · w_h² · c_d,h / IMES with w_h² = (h − h_prev)/10 and c_d,h the desk's
    tail contribution to ES_h, so a desk carries m · that + its tail contribution to SES."""
    es_by_h, imes, _, _ = _ima_capital(pnl_by_h, pnl_ses, mult)
    desks = list(pnl_by_h[LIQUIDITY_HORIZONS[0]].columns)
    euler = pd.Series(0.0, index=desks)
    prev = 0
    for h in LIQUIDITY_HORIZONS:
        if imes:
            euler += mult * es_by_h[h] * (h - prev) / 10 * _tail_contribution(pnl_by_h[h]) / imes
        prev = h
    if pnl_ses is not None:
        euler += _tail_contribution(pnl_ses).reindex(desks, fill_value=0.0)
    rows = []
    for d in desks:
        alone = pd.Series([float(x == d) for x in desks], index=desks)
        others = 1.0 - alone
        rows.append(
            {
                "desk_id": d,
                "euler_raw": float(euler[d]),
                "incremental": capital - _ima_capital(pnl_by_h, pnl_ses, mult, others)[3],
                "standalone": _ima_capital(pnl_by_h, pnl_ses, mult, alone)[3],
            }
        )
    frame = pd.DataFrame(rows, columns=["desk_id", "euler_raw", "incremental", "standalone"])
    return with_euler(frame, capital).sort_values("euler", ascending=False, ignore_index=True)
