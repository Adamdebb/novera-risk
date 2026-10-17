"""Standalone factor stress, sliding scales, jump to default and the worst historical windows
(MR-019, decision round 48).

Four views of today's book that the scenario library does not give, all for information:

- **Standalone shocks.** Every risk factor moves alone, up and down, by the 99.9th percentile of
  its own 10-day moves over the stored history (each direction measured separately), in full
  revaluation. Losses are reported by desk, so each desk sees the factors that hurt it most.
- **Sliding scales.** P&L from −3σ to +3σ in half-sigma steps (σ: the factor's 10-day standard
  deviation) for the headline basket and each desk's three worst standalone factors. The shape
  shows where the book is not linear.
- **Jump to default.** Each corporate issuer defaults alone: its single-name CDS settle at
  ``notional × (1 − recovery)`` on each trade's own recovery, and its stock falls to 0.01% of
  today's price, which reprices options, exotics and the funds and ETFs that hold it. Sovereign
  issuers are out of scope (decision 48.2).
- **Worst windows.** Every overlapping window of 1, 5, 10, 20 and 60 days in the stored history
  replayed on today's book, raw and rescaled to today's volatility. The delta-gamma-vega
  approximation screens every window; full revaluation confirms the worst.

The engine computes every figure from the stored history and today's book; nothing is fitted
to a target.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from novera.market_data.history import MarketHistory
from novera.risk.revaluation import Portfolio
from novera.risk.scenario_covariance import HEADLINE_FACTORS
from novera.risk.scenarios import historical_shocks, shock_type_of

RECORD = "MR-019"
MODEL_VERSION = "1.0.0"

SIZE_HORIZON = 10
SIZE_QUANTILE = 0.999
SCALE_STEPS = tuple(x / 2 for x in range(-6, 7))  # −3σ to +3σ in half-sigma steps
SCALE_TOP_PER_DESK = 3
WINDOW_HORIZONS = (1, 5, 10, 20, 60)
WINDOW_CONFIRM = 3
"""Distinct worst windows per horizon and version confirmed in full revaluation."""
EWMA_LAMBDA = 0.94
RESCALE_CLIP = (0.25, 4.0)
"""Bounds on the ratio of today's volatility to the window's, so a factor that was nearly flat
then does not explode today."""
EQUITY_DEFAULT_PRICE = 1e-4
"""A defaulted stock trades at this fraction of today's price: zero would break the option
pricers' log of spot."""
ISSUER_EQUITY = {"BOEING": "BA"}
"""Issuers whose CDS reference entity and stock ticker differ."""
INDEX_PREFIXES = ("CDX.", "ITRAXX.")
FIRM = "(firm)"


@dataclass
class FactorStressResult:
    sizes: pd.DataFrame
    """factor_id, down, up, sigma: shock sizes in shock units."""
    standalone: pd.DataFrame
    """factor_id, direction, shock, desk_id, pnl (one row per desk the shock moves)."""
    scales: pd.DataFrame
    """factor_id, sigmas, shock, desk_id, pnl; desk_id FIRM carries the total."""
    jump_to_default: pd.DataFrame
    """issuer, equity, desk_id, equity_pnl, cds_pnl, pnl."""
    windows: pd.DataFrame
    """horizon_days, version, rank, window_start, window_end, approx_pnl, pnl."""

    def summary(self) -> dict[str, object]:
        out: dict[str, object] = {"factor_stress_factors": int(self.sizes["factor_id"].nunique())}
        if len(self.standalone):
            tot = self.standalone.groupby(["factor_id", "direction"])["pnl"].sum()
            (fid, direction), worst = tot.idxmin(), float(tot.min())
            out.update(worst_factor_id=fid, worst_factor_direction=direction, worst_factor=worst)
        if len(self.jump_to_default):
            jtd = self.jump_to_default.groupby("issuer")["pnl"].sum()
            out.update(worst_jtd_issuer=jtd.idxmin(), worst_jtd=float(jtd.min()))
        if len(self.windows):
            w = self.windows.sort_values("pnl").iloc[0]
            out.update(
                worst_window_start=str(w["window_start"]),
                worst_window_end=str(w["window_end"]),
                worst_window_days=int(w["horizon_days"]),
                worst_window_version=str(w["version"]),
                worst_window=float(w["pnl"]),
            )
        return out


# --- sizes ---------------------------------------------------------------------------------------
def shock_sizes(
    history: MarketHistory,
    as_of: date,
    pf: Portfolio,
    horizon: int = SIZE_HORIZON,
    quantile: float = SIZE_QUANTILE,
) -> pd.DataFrame:
    """Per factor, the down and up move at the chosen quantile of its overlapping ``horizon``-day
    moves over every stored day up to ``as_of``, and their standard deviation. A factor that
    never moved in one direction gets no shock in it."""
    days = len([d for d in history.dates if d <= as_of]) - horizon
    if days < 2:
        return pd.DataFrame(columns=["factor_id", "down", "up", "sigma"])
    moves = historical_shocks(history, as_of, days, horizon, pf.universe, factor_ids=list(pf.base.values))
    x = moves.to_numpy(dtype=float)
    down = np.minimum(np.quantile(x, 1.0 - quantile, axis=0), 0.0)
    up = np.maximum(np.quantile(x, quantile, axis=0), 0.0)
    sigma = x.std(axis=0, ddof=1)
    out = pd.DataFrame({"factor_id": moves.columns, "down": down, "up": up, "sigma": sigma})
    return out[(out["down"] != 0.0) | (out["up"] != 0.0)].reset_index(drop=True)


def _by_desk(pnl: dict[str, float], desks: pd.Series) -> dict[str, float]:
    out: dict[str, float] = {}
    for tid, v in pnl.items():
        d = str(desks.get(tid, "UNKNOWN"))
        out[d] = out.get(d, 0.0) + v
    return out


# --- standalone and scales -----------------------------------------------------------------------
def standalone(pf: Portfolio, sizes: pd.DataFrame, desks: pd.Series) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for r in sizes.itertuples(index=False):
        for direction, shock in (("DOWN", r.down), ("UP", r.up)):
            if shock == 0.0:
                continue
            pnl = pf.pnl_under_shocks({r.factor_id: float(shock)})
            for desk, v in _by_desk(pnl, desks).items():
                rows.append(
                    {
                        "factor_id": r.factor_id,
                        "direction": direction,
                        "shock": float(shock),
                        "desk_id": desk,
                        "pnl": v,
                    }
                )
    return pd.DataFrame(rows, columns=["factor_id", "direction", "shock", "desk_id", "pnl"])


def scale_factors(
    standalone_rows: pd.DataFrame, sizes: pd.DataFrame, top: int = SCALE_TOP_PER_DESK
) -> list[str]:
    """The headline basket, then each desk's ``top`` worst standalone factors (decision 48.4)."""
    known = set(sizes["factor_id"])
    out = [f for f, _ in HEADLINE_FACTORS if f in known]
    if len(standalone_rows):
        worst = standalone_rows.groupby(["desk_id", "factor_id"])["pnl"].min().reset_index()
        for _, grp in worst[worst["pnl"] < 0].groupby("desk_id", sort=True):
            out += list(grp.sort_values(["pnl", "factor_id"])["factor_id"].head(top))
    return list(dict.fromkeys(out))


def sliding_scales(pf: Portfolio, sizes: pd.DataFrame, factors: list[str], desks: pd.Series) -> pd.DataFrame:
    sigma = sizes.set_index("factor_id")["sigma"]
    rows: list[dict[str, object]] = []
    for fid in factors:
        s = float(sigma.get(fid, 0.0))
        if s <= 0.0:
            continue
        for k in SCALE_STEPS:
            shock = k * s
            if shock_type_of(fid, pf.universe) != "ABSOLUTE":
                shock = max(shock, -0.99)  # a price never falls through zero
            pnl = pf.pnl_under_shocks({fid: shock}) if k else {}
            by = _by_desk(pnl, desks)
            rows.append(
                {"factor_id": fid, "sigmas": k, "shock": shock, "desk_id": FIRM, "pnl": sum(by.values())}
            )
            rows += [
                {"factor_id": fid, "sigmas": k, "shock": shock, "desk_id": d, "pnl": v} for d, v in by.items()
            ]
    return pd.DataFrame(rows, columns=["factor_id", "sigmas", "shock", "desk_id", "pnl"])


# --- jump to default -----------------------------------------------------------------------------
def issuers(pf: Portfolio) -> list[tuple[str, str | None, str | None]]:
    """(issuer, equity factor, CDS reference entity) for every corporate name in the universe:
    single-name CDS entities and stock tickers, joined where one issuer has both."""
    cds = sorted(
        f.split(":", 1)[1]
        for f in pf.universe
        if f.startswith("CDS:") and not f.split(":", 1)[1].startswith(INDEX_PREFIXES)
    )
    eq = {f.split(":", 1)[1] for f in pf.universe if f.startswith("EQ:")}
    out: list[tuple[str, str | None, str | None]] = []
    for name in cds:
        ticker = ISSUER_EQUITY.get(name, name if name in eq else None)
        out.append((name, f"EQ:{ticker}" if ticker in eq else None, name))
        eq.discard(ticker or "")
    out += [(t, f"EQ:{t}", None) for t in sorted(eq)]
    return out


def jump_to_default(pf: Portfolio, desks: pd.Series) -> pd.DataFrame:
    from novera.domain.enums import BuySell, ProductType
    from novera.pricing.valuation import fx_to_reporting

    by_entity: dict[str, list] = {}
    for tid in pf.priced_ids:
        t = pf.by_id[tid]
        if t.product_type is ProductType.CDS_SINGLE_NAME:
            by_entity.setdefault(t.instrument.reference_entity, []).append(t)  # type: ignore[attr-defined]
    rows: list[dict[str, object]] = []
    for issuer, equity, entity in issuers(pf):
        eq_pnl = _by_desk(pf.pnl_under_shocks({equity: EQUITY_DEFAULT_PRICE - 1.0}), desks) if equity else {}
        cds_pnl: dict[str, float] = {}
        for t in by_entity.get(entity or "", []):
            ins = t.instrument
            if ins.maturity_date <= pf.as_of:
                continue
            sign = 1.0 if t.direction is BuySell.BUY else -1.0  # BUY = bought protection
            settle = sign * t.quantity * (1.0 - ins.recovery_rate)
            fx = fx_to_reporting(pf.base, ins.currency, pf.reporting_currency)
            d = str(desks.get(t.trade_id, "UNKNOWN"))
            cds_pnl[d] = cds_pnl.get(d, 0.0) + settle * fx - pf.base_pv[t.trade_id]
        for d in sorted(set(eq_pnl) | set(cds_pnl)):
            e, c = eq_pnl.get(d, 0.0), cds_pnl.get(d, 0.0)
            rows.append(
                {
                    "issuer": issuer,
                    "equity": equity,
                    "desk_id": d,
                    "equity_pnl": e,
                    "cds_pnl": c,
                    "pnl": e + c,
                }
            )
    return pd.DataFrame(rows, columns=["issuer", "equity", "desk_id", "equity_pnl", "cds_pnl", "pnl"])


# --- worst windows -------------------------------------------------------------------------------
def _vol_ratio(history: MarketHistory, as_of: date, pf: Portfolio, cols: list[str]) -> pd.DataFrame:
    """Today's EWMA daily volatility over each date's, per factor, clipped (RESCALE_CLIP)."""
    days = len([d for d in history.dates if d <= as_of]) - 1
    daily = historical_shocks(history, as_of, days, 1, pf.universe, factor_ids=cols)
    var = daily.pow(2).ewm(alpha=1.0 - EWMA_LAMBDA, adjust=False).mean()
    vol = np.sqrt(var)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = vol.iloc[-1] / vol
    return ratio.replace([np.inf, -np.inf], np.nan).fillna(1.0).clip(*RESCALE_CLIP)


def _distinct_worst(pnl: pd.Series, horizon: int, n: int) -> list[date]:
    """The ``n`` worst window end dates whose windows do not overlap."""
    picked: list[date] = []
    dates = list(pnl.index)
    pos = {d: i for i, d in enumerate(dates)}
    for d in pnl.sort_values().index:
        if all(abs(pos[d] - pos[p]) >= horizon for p in picked):
            picked.append(d)
        if len(picked) == n:
            break
    return picked


def worst_windows(
    pf: Portfolio,
    sens: pd.DataFrame,
    history: MarketHistory,
    as_of: date,
    horizons: tuple[int, ...] = WINDOW_HORIZONS,
    confirm: int = WINDOW_CONFIRM,
) -> pd.DataFrame:
    from novera.risk.var import taylor_pnl_matrix

    cols = list(pf.base.values)
    ratio = _vol_ratio(history, as_of, pf, cols)
    rows: list[dict[str, object]] = []
    stored = [d for d in history.dates if d <= as_of]
    for h in horizons:
        days = len(stored) - h
        if days < 2:
            continue
        raw = historical_shocks(history, as_of, days, h, pf.universe, factor_ids=cols)
        # The window's moves times today's volatility over the volatility when the window began.
        start_of = {stored[i + h]: stored[i] for i in range(len(stored) - h)}
        rescaled = raw * ratio.reindex([start_of[d] for d in raw.index]).set_axis(raw.index)[raw.columns]
        for version, scen in (("RAW", raw), ("VOL_RESCALED", rescaled)):
            approx = taylor_pnl_matrix(pf, sens, scen).sum(axis=1)
            ends = _distinct_worst(approx, h, confirm)
            full = pf.pnl_matrix(scen.loc[ends], workers=1).sum(axis=1)
            for rank, end in enumerate(sorted(ends, key=lambda d: float(full[d])), 1):
                rows.append(
                    {
                        "horizon_days": h,
                        "version": version,
                        "rank": rank,
                        "window_start": start_of[end],
                        "window_end": end,
                        "approx_pnl": float(approx[end]),
                        "pnl": float(full[end]),
                    }
                )
    return pd.DataFrame(
        rows, columns=["horizon_days", "version", "rank", "window_start", "window_end", "approx_pnl", "pnl"]
    )


def run_factor_stress(
    pf: Portfolio, sens: pd.DataFrame, history: MarketHistory, valuation: pd.DataFrame
) -> FactorStressResult:
    desks = valuation.set_index("trade_id")["desk_id"]
    sizes = shock_sizes(history, pf.as_of, pf)
    alone = standalone(pf, sizes, desks)
    scales = sliding_scales(pf, sizes, scale_factors(alone, sizes), desks)
    jtd = jump_to_default(pf, desks)
    windows = worst_windows(pf, sens, history, pf.as_of)
    return FactorStressResult(sizes, alone, scales, jtd, windows)
