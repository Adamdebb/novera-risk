"""Shocks and scenarios applied to a market snapshot.

ABSOLUTE factors (zero rates, credit spreads in bp) shift additively; RELATIVE factors
(prices, levels, vols) scale multiplicatively by ``1 + shock``. Historical scenarios are
the observed one-day changes in those same units.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import numpy as np
import pandas as pd

from novera.market_data.history import MarketHistory
from novera.market_data.risk_factors import RiskFactor
from novera.market_data.snapshot import MarketSnapshot

ABSOLUTE_PREFIXES = ("IR:", "CDS:")


def shock_type_of(factor_id: str, universe: dict[str, RiskFactor] | None = None) -> str:
    if universe and factor_id in universe:
        return universe[factor_id].shock_type
    return "ABSOLUTE" if factor_id.startswith(ABSOLUTE_PREFIXES) else "RELATIVE"


def apply_shocks(
    snapshot: MarketSnapshot, shocks: dict[str, float], universe: dict[str, RiskFactor] | None = None
) -> MarketSnapshot:
    updates: dict[str, float] = {}
    for fid, s in shocks.items():
        if fid not in snapshot.values or s == 0.0:
            continue
        v = snapshot.values[fid]
        updates[fid] = v + s if shock_type_of(fid, universe) == "ABSOLUTE" else v * (1.0 + s)
    return snapshot.with_values(updates) if updates else snapshot


def shocks_for_prefix(snapshot: MarketSnapshot, prefix: str, size: float) -> dict[str, float]:
    """Same shock on every factor whose id starts with ``prefix`` (or equals it)."""
    if prefix.endswith(":"):
        return {f: size for f in snapshot.factors_with_prefix(prefix)}
    return {prefix: size} if snapshot.has(prefix) else {}


def historical_shocks(
    history: MarketHistory,
    end: date,
    window_days: int,
    horizon_days: int = 1,
    universe: dict[str, RiskFactor] | None = None,
    factor_ids: Iterable[str] | None = None,
) -> pd.DataFrame:
    """One row per historical scenario (dated by the end of the move), one column per factor.
    Returns changes in shock units: absolute differences for ABSOLUTE factors, simple returns
    for RELATIVE ones. Needs ``window_days + horizon_days`` observations ending at ``end``."""
    w = history.window(end, window_days + horizon_days)
    if factor_ids is not None:
        cols = [c for c in factor_ids if c in w.columns]
        w = w[cols]
    is_abs = np.array([shock_type_of(c, universe) == "ABSOLUTE" for c in w.columns])
    vals = w.to_numpy(dtype=float)
    cur, prev = vals[horizon_days:], vals[:-horizon_days]
    with np.errstate(divide="ignore", invalid="ignore"):
        moves = np.where(is_abs, cur - prev, cur / prev - 1.0)
    out = pd.DataFrame(moves, index=w.index[horizon_days:], columns=w.columns)
    out = out.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out.index.name = "scenario_date"
    return out


def episode_shocks(
    history: MarketHistory, start: date, end: date, universe: dict[str, RiskFactor] | None = None
) -> dict[str, float]:
    """Peak-to-end move of every factor between two dates, in shock units. Used to turn a
    stylised historical episode into a stress scenario."""
    w = history.window(end, 10_000)
    w = w[w.index >= start]
    if len(w) < 2:
        raise ValueError("episode window too short")
    first, last = w.iloc[0], w.iloc[-1]
    shocks: dict[str, float] = {}
    for fid in w.columns:
        if shock_type_of(fid, universe) == "ABSOLUTE":
            shocks[fid] = float(last[fid] - first[fid])
        else:
            shocks[fid] = float(last[fid] / first[fid] - 1.0) if first[fid] else 0.0
    return shocks
