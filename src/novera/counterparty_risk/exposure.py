"""Exposure profiles per netting set and counterparty. Records CR-001 and CR-002.

For each grid date and path the bilateral OTC trades are repriced (ageing naturally: matured
trades and settled forwards fall away), summed per netting set into the value V. Collateral
under the CSA: the counterparty posts against V observed a margin period of risk earlier,
above its threshold, in minimum-transfer-amount steps, plus any independent amount; we post
symmetrically. Exposure = max(V - balance, 0); negative exposure = max(balance - V, 0). Collateral we have
posted (balance below zero) adds to our exposure if the value turns positive before it is
returned, so collateralised exposure can exceed gross exposure on some paths.

Initial margin (REG-004) is a further deduction for collateralised sets, aged across the grid
by the notional-duration still outstanding so that it decays with the portfolio.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from novera.counterparty_risk.simulation import ExposureSimConfig, FactorPaths
from novera.domain.counterparties import CSA, NettingSet
from novera.domain.enums import ClearingType, ProductType, Venue
from novera.domain.trades import Trade
from novera.market_data.history import MarketHistory
from novera.market_data.risk_factors import RiskFactor
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing.valuation import fx_to_reporting, value_trade

MODEL_VERSION = "1.1.0"


@dataclass
class ExposureResult:
    grid: list[tuple[str, date, float]]
    netting_values: dict[str, np.ndarray]  # netting_set_id -> (steps, paths) V in reporting ccy
    netting_current: dict[str, float]  # V at t0
    netting_sets: dict[str, NettingSet]
    csas: dict[str, CSA]
    trades_by_set: dict[str, list[str]]
    paths: int
    profiles: pd.DataFrame = field(default_factory=pd.DataFrame)  # filled by ``collateralise``
    proxy_paths: dict[str, np.ndarray] = field(default_factory=dict)  # factor -> (steps, paths) moves
    im_scale: dict[str, np.ndarray] = field(default_factory=dict)  # netting_set_id -> (steps,) IM ageing

    def by_counterparty(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for ns in self.netting_sets.values():
            out.setdefault(ns.counterparty_id, []).append(ns.netting_set_id)
        return out


def _bilateral(trades: list[Trade]) -> list[Trade]:
    return [
        t
        for t in trades
        if t.status.value == "LIVE"
        and t.venue is Venue.OTC
        and t.clearing is ClearingType.BILATERAL
        and t.netting_set_id
    ]


def _pv(t: Trade, snap: MarketSnapshot, as_of: date, reporting: str) -> float:
    if t.product_type is ProductType.FX_SPOT and t.settlement_date and as_of >= t.settlement_date:
        return 0.0  # settled: no counterparty exposure remains
    try:
        r = value_trade(t, snap, as_of)
    except Exception:  # noqa: BLE001
        return 0.0
    return r.pv_local * fx_to_reporting(snap, r.currency, reporting)


def _notional_duration(t: Trade, d: date) -> float:
    """Proxy for a trade's SIMM sensitivity at ``d``: notional times the remaining years to
    maturity, or notional alone for products that have none. Units are mixed across product
    types, so it is only meaningful as a ratio within one netting set over time (REG-004)."""
    mat = getattr(t.instrument, "maturity_date", None) or t.settlement_date
    if mat is None:
        return abs(t.quantity)
    yrs = (mat - d).days / 365.0
    return abs(t.quantity) * yrs if yrs > 0 else 0.0


def im_scales(
    trades: list[Trade], by_set: dict[str, list[int]], grid_dates: list[date], as_of: date
) -> dict[str, np.ndarray]:
    """Per netting set, the notional-duration still outstanding at each grid date as a fraction
    of t0. Initial margin is computed on today's sensitivities, so it has to age with the
    portfolio or it overstates the collateral benefit at the long end (REG-004)."""
    out: dict[str, np.ndarray] = {}
    for k, idx in by_set.items():
        base = sum(_notional_duration(trades[j], as_of) for j in idx)
        if base <= 0:
            out[k] = np.ones(len(grid_dates))
            continue
        out[k] = np.array(
            [min(sum(_notional_duration(trades[j], d) for j in idx) / base, 1.0) for d in grid_dates]
        )
    return out


_W: dict = {}


def _init(trades, reporting):
    _W["trades"], _W["reporting"] = trades, reporting


def _value_paths(args):
    """(as_of, [snapshots]) -> array (paths, trades) of PVs. Runs in a worker."""
    as_of, snaps = args
    trades, reporting = _W["trades"], _W["reporting"]
    out = np.zeros((len(snaps), len(trades)))
    for i, snap in enumerate(snaps):
        for j, t in enumerate(trades):
            out[i, j] = _pv(t, snap, as_of, reporting)
    return out


def simulate_exposure(
    trades: list[Trade],
    base: MarketSnapshot,
    history: MarketHistory,
    universe: dict[str, RiskFactor],
    netting_sets: list[NettingSet],
    csas: list[CSA],
    reporting: str,
    cfg: ExposureSimConfig | None = None,
    workers: int | None = None,
    initial_margin: dict[str, float] | None = None,
) -> ExposureResult:
    cfg = cfg or ExposureSimConfig()
    bil = _bilateral(trades)
    ns_map = {n.netting_set_id: n for n in netting_sets}
    csa_map = {c.csa_id: c for c in csas}
    bil = [t for t in bil if t.netting_set_id in ns_map]  # dangling netting sets are a data-quality finding
    by_set: dict[str, list[int]] = {}
    for j, t in enumerate(bil):
        by_set.setdefault(t.netting_set_id, []).append(j)
    paths = FactorPaths(base, history, universe, cfg)
    if workers is None:
        workers = int(os.environ.get("NOVERA_WORKERS", max((os.cpu_count() or 2) - 1, 1)))
    values: dict[str, list[np.ndarray]] = {k: [] for k in by_set}
    current = {
        k: float(sum(_pv(bil[j], base, base.as_of, reporting) for j in idx)) for k, idx in by_set.items()
    }
    grid: list[tuple[str, date, float]] = []
    _init(bil, reporting)
    pool = None
    if (
        workers > 1
        and cfg.paths >= 2 * workers
        and "fork" in __import__("multiprocessing").get_all_start_methods()
    ):
        pool = ProcessPoolExecutor(
            max_workers=workers, mp_context=__import__("multiprocessing").get_context("fork")
        )
    try:
        for label, d, years, snaps in paths:
            grid.append((label, d, years))
            if pool is not None:
                chunks = np.array_split(np.arange(len(snaps)), workers)
                parts = list(pool.map(_value_paths, [(d, [snaps[i] for i in c]) for c in chunks if len(c)]))
                pv = np.vstack(parts)
            else:
                pv = _value_paths((d, snaps))
            for k, idx in by_set.items():
                values[k].append(pv[:, idx].sum(axis=1))
    finally:
        if pool is not None:
            pool.shutdown()
    netting_values = {k: np.vstack(v) for k, v in values.items()}  # (steps, paths)
    res = ExposureResult(
        grid,
        netting_values,
        current,
        {k: ns_map[k] for k in by_set if k in ns_map},
        csa_map,
        {k: [bil[j].trade_id for j in idx] for k, idx in by_set.items()},
        cfg.paths,
    )
    res.proxy_paths = {f: np.vstack(v) for f, v in paths.proxy_paths.items() if v}
    res.im_scale = im_scales(bil, by_set, [g[1] for g in grid], base.as_of)
    res.profiles = collateralise(res, cfg.margin_period_days, initial_margin=initial_margin)
    return res


def collateral_balance(v: np.ndarray, years: np.ndarray, csa: CSA | None, mpor_days: int) -> np.ndarray:
    """Net collateral held from the counterparty (positive) along each path, from the CSA."""
    if csa is None:
        return np.zeros_like(v)
    mpor = mpor_days / 261.0
    lagged = np.empty_like(v)
    lagged[0] = v[0] * max(1 - mpor / max(years[0], 1e-9), 0.0)  # from zero at t0 towards V(t1)
    for i in range(1, len(years)):
        dt = years[i] - years[i - 1]
        w = min(mpor / max(dt, 1e-9), 1.0)
        lagged[i] = v[i] - w * (v[i] - v[i - 1])
    they = np.maximum(lagged - csa.threshold_they_post, 0.0)
    we = np.maximum(-lagged - csa.threshold_we_post, 0.0)
    if csa.minimum_transfer_amount > 0:
        they = np.where(they < csa.minimum_transfer_amount, 0.0, they)
        we = np.where(we < csa.minimum_transfer_amount, 0.0, we)
    if csa.rounding > 0:
        they = np.floor(they / csa.rounding) * csa.rounding
        we = np.floor(we / csa.rounding) * csa.rounding
    # The independent amount is what the counterparty posts to us regardless of value.
    return they * (1 - csa.haircut) + csa.independent_amount - we * (1 - csa.haircut)


def collateralise(
    res: ExposureResult,
    mpor_days: int = 10,
    csa_override: dict[str, CSA | None] | None = None,
    initial_margin: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Profiles per netting set: gross and collateralised EE, ENE, PFE95, PFE99 by grid point.
    ``csa_override`` maps netting_set_id to a CSA (or None) to run a what-if on terms."""
    years = np.array([g[2] for g in res.grid])
    rows = []
    for k, v in res.netting_values.items():
        ns = res.netting_sets[k]
        if csa_override is not None and k in csa_override:
            csa = csa_override[k]
        else:
            csa = res.csas.get(ns.csa_id) if ns.csa_id else None
        bal = collateral_balance(v, years, csa, mpor_days)
        im = float((initial_margin or {}).get(k, 0.0)) if csa is not None else 0.0
        scale = res.im_scale.get(k)
        im_by_step = im * (np.ones(len(years)) if scale is None or len(scale) != len(years) else scale)
        e_gross, e_coll = np.maximum(v, 0.0), np.maximum(v - bal - im_by_step[:, None], 0.0)
        ne_gross, ne_coll = np.maximum(-v, 0.0), np.maximum(bal - v, 0.0)
        for i, (label, d, yrs) in enumerate(res.grid):
            rows.append(
                {
                    "netting_set_id": k,
                    "counterparty_id": ns.counterparty_id,
                    "legal_entity_id": ns.legal_entity_id,
                    "collateralised": csa is not None,
                    "step": label,
                    "date": d,
                    "years": yrs,
                    "ee_gross": float(e_gross[i].mean()),
                    "pfe95_gross": float(np.percentile(e_gross[i], 95)),
                    "pfe99_gross": float(np.percentile(e_gross[i], 99)),
                    "ee": float(e_coll[i].mean()),
                    "pfe95": float(np.percentile(e_coll[i], 95)),
                    "pfe99": float(np.percentile(e_coll[i], 99)),
                    "ene": float(ne_coll[i].mean()),
                    "ene_gross": float(ne_gross[i].mean()),
                    "mean_value": float(v[i].mean()),
                    "mean_collateral": float(bal[i].mean()),
                    "initial_margin": float(im_by_step[i]),
                }
            )
    return pd.DataFrame(rows)


def summarise(profiles: pd.DataFrame, by: str = "counterparty_id") -> pd.DataFrame:
    """EPE, EEPE, peak PFE per group, from the profile (profiles are additive across netting
    sets only for EE; PFE is aggregated by summing per-set PFE, a conservative choice)."""
    if profiles.empty:
        return pd.DataFrame()
    g = (
        profiles.groupby([by, "step", "years"], as_index=False)
        .agg(
            ee=("ee", "sum"),
            ee_gross=("ee_gross", "sum"),
            pfe95=("pfe95", "sum"),
            pfe99=("pfe99", "sum"),
            pfe95_gross=("pfe95_gross", "sum"),
            ene=("ene", "sum"),
            collateralised=("collateralised", "all"),
        )
        .sort_values([by, "years"])
    )
    out = []
    for key, grp in g.groupby(by):
        first_year = grp[grp["years"] <= 1.0001]
        w = (
            np.diff(np.concatenate([[0.0], first_year["years"].to_numpy()]))
            if len(first_year)
            else np.array([])
        )
        epe = float((first_year["ee"].to_numpy() * w).sum() / max(w.sum(), 1e-9)) if len(first_year) else 0.0
        eepe = (
            float((np.maximum.accumulate(first_year["ee"].to_numpy()) * w).sum() / max(w.sum(), 1e-9))
            if len(first_year)
            else 0.0
        )
        out.append(
            {
                by: key,
                "epe": epe,
                "eepe": eepe,
                "peak_pfe95": float(grp["pfe95"].max()),
                "peak_pfe99": float(grp["pfe99"].max()),
                "peak_pfe95_gross": float(grp["pfe95_gross"].max()),
                "peak_pfe95_step": grp.loc[grp["pfe95"].idxmax(), "step"],
                "ee_1y": float(first_year["ee"].iloc[-1]) if len(first_year) else 0.0,
                "collateralised": bool(grp["collateralised"].all()),
            }
        )
    return pd.DataFrame(out).sort_values("peak_pfe95", ascending=False)
