"""Counterparty-risk run for a stored market-risk run: exposure, collateral, CVA, DVA,
wrong-way risk, stressed current exposure, and the per-counterparty summary used by limits."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from novera.config import Settings, get_settings
from novera.counterparty_risk.cva import (
    CreditTerms,
    credit_proxy,
    cva_table,
    hazard_from_spread,
    wrong_way_indicators,
)
from novera.counterparty_risk.exposure import ExposureResult, collateralise, simulate_exposure, summarise
from novera.counterparty_risk.simulation import ExposureSimConfig
from novera.domain.counterparties import CSA
from novera.market_data.history import MarketHistory
from novera.storage.duckdb_repository import DuckDBRepository

MODEL_VERSION = "1.0.0"


@dataclass
class CounterpartyRun:
    run_id: str
    profiles: pd.DataFrame
    netting_summary: pd.DataFrame
    counterparty_summary: pd.DataFrame
    cva: pd.DataFrame
    wwr: pd.DataFrame
    stressed_ce: pd.DataFrame
    result: ExposureResult
    notes: dict[str, Any] = field(default_factory=dict)


def _current_exposure_under_stress(repo: DuckDBRepository, run_id: str, res: ExposureResult) -> pd.DataFrame:
    """Current exposure per counterparty after each stress scenario, pre-collateral: the
    netting-set value plus the scenario's trade P&L, floored at zero and summed."""
    st = repo.load_run_frame(run_id, "stress")
    if st.empty:
        return pd.DataFrame(
            columns=["scenario_id", "counterparty_id", "current_exposure", "stressed_exposure"]
        )
    trade_set = {tid: k for k, ids in res.trades_by_set.items() for tid in ids}
    st = st[st["trade_id"].isin(trade_set)].copy()
    st["netting_set_id"] = st["trade_id"].map(trade_set)
    pnl = st.groupby(["scenario_id", "netting_set_id"])["pnl"].sum().reset_index()
    pnl["value"] = pnl["netting_set_id"].map(res.netting_current) + pnl["pnl"]
    pnl["counterparty_id"] = pnl["netting_set_id"].map(lambda k: res.netting_sets[k].counterparty_id)
    pnl["current"] = pnl["netting_set_id"].map(res.netting_current).clip(lower=0.0)
    pnl["stressed"] = pnl["value"].clip(lower=0.0)
    out = pnl.groupby(["scenario_id", "counterparty_id"], as_index=False).agg(
        current_exposure=("current", "sum"), stressed_exposure=("stressed", "sum")
    )
    out["increase"] = out["stressed_exposure"] - out["current_exposure"]
    return out.sort_values("increase", ascending=False)


def run_counterparty(
    repo: DuckDBRepository,
    run_id: str,
    cfg: ExposureSimConfig | None = None,
    settings: Settings | None = None,
    persist: bool = True,
    runs_dir: Path | None = None,
    workers: int | None = None,
) -> CounterpartyRun:
    settings = settings or get_settings()
    cfg = cfg or ExposureSimConfig(paths=settings.exposure_paths)
    run = repo.load_run(run_id)
    snap = repo.load_portfolio_snapshot(run.portfolio_snapshot_id)
    market = repo.load_run_market(run.run_id)
    universe = {f.factor_id: f for f in repo.load_risk_factors()}
    history = MarketHistory.from_long(repo.load_market_history())
    netting_sets, csas = repo.load_netting_sets()
    counterparties = {c.counterparty_id: c for c in repo.load_counterparties()}
    simm_frame = repo.load_run_frame(run_id, "reg_simm")
    initial_margin = (
        {str(r["netting_set_id"]): float(r["im"]) for _, r in simm_frame.iterrows()}
        if len(simm_frame)
        else {}
    )
    res = simulate_exposure(
        list(snap.trades),
        market,
        history,
        universe,
        netting_sets,
        csas,
        run.reporting_currency,
        cfg,
        workers,
        initial_margin=initial_margin,
    )
    profiles = res.profiles
    netting_summary = summarise(profiles, "netting_set_id")
    cp_summary = summarise(profiles, "counterparty_id")
    curve = market.zero_curve(run.reporting_currency)
    own = CreditTerms(hazard_from_spread(settings.own_credit_spread_bp, 1 - settings.lgd), settings.lgd)
    cva = cva_table(profiles, counterparties, curve, own, settings.lgd)
    first_year = [i for i, g in enumerate(res.grid) if g[2] <= 1.0001] or [0]
    wwr = wrong_way_indicators(res, res.proxy_paths, counterparties, first_year)
    stressed = _current_exposure_under_stress(repo, run_id, res)

    # Counterparty summary: exposure, CVA, WWR, reference data, current exposure.
    cur = pd.Series({k: v for k, v in res.netting_current.items()})
    cur_by_cp = cur.groupby(cur.index.map(lambda k: res.netting_sets[k].counterparty_id)).apply(
        lambda s: float(s.clip(lower=0).sum())
    )
    cva_by_cp = cva.groupby("counterparty_id")[["cva", "dva", "bcva", "cva_gross"]].sum()
    wwr_by_cp = wwr.groupby("counterparty_id").agg(
        wwr_correlation=("correlation", "max"), wrong_way=("wrong_way", "any"), wwr_proxy=("proxy", "first")
    )
    cp_summary = cp_summary.set_index("counterparty_id").join(cva_by_cp).join(wwr_by_cp)
    cp_summary["current_exposure"] = cur_by_cp
    cp_summary["netting_sets"] = profiles.groupby("counterparty_id")["netting_set_id"].nunique()
    cp_summary["trades"] = pd.Series(
        {cp: sum(len(res.trades_by_set[k]) for k in ks) for cp, ks in res.by_counterparty().items()}
    )
    for col in ("name", "counterparty_type", "rating", "country", "on_watchlist"):
        cp_summary[col] = cp_summary.index.map(
            lambda c, col=col: getattr(counterparties[c], col, None) if c in counterparties else None
        )
    cp_summary = cp_summary.reset_index().sort_values("peak_pfe95", ascending=False)
    out = CounterpartyRun(
        run_id,
        profiles,
        netting_summary,
        cp_summary,
        cva,
        wwr,
        stressed,
        res,
        notes={
            "paths": cfg.paths,
            "grid": [g[0] for g in res.grid],
            "margin_period_days": cfg.margin_period_days,
            "own_spread_bp": settings.own_credit_spread_bp,
            "lgd": settings.lgd,
            "initial_margin_sets": len(initial_margin),
        },
    )
    if persist:
        repo.save_run_frame(run_id, "cp_profiles", profiles)
        repo.save_run_frame(run_id, "cp_netting_summary", netting_summary)
        repo.save_run_frame(run_id, "cp_counterparty_summary", cp_summary)
        repo.save_run_frame(run_id, "cp_cva", cva)
        repo.save_run_frame(run_id, "cp_wwr", wwr)
        repo.save_run_frame(run_id, "cp_stressed", stressed)
        repo.save_run_frame(run_id, "cp_notes", pd.DataFrame([{k: str(v) for k, v in out.notes.items()}]))
        base = Path(runs_dir or settings.data_dir / "runs") / run_id
        base.mkdir(parents=True, exist_ok=True)
        steps = [g[0] for g in res.grid]
        frames = []
        for k, v in res.netting_values.items():
            df = pd.DataFrame(v.T, columns=steps)
            df.insert(0, "path", range(v.shape[1]))
            df.insert(0, "netting_set_id", k)
            frames.append(df)
        pd.concat(frames, ignore_index=True).to_parquet(base / "cp_netting_values.parquet")
        pd.DataFrame([{"step": g[0], "date": g[1], "years": g[2]} for g in res.grid]).to_parquet(
            base / "cp_grid.parquet"
        )
    return out


def load_exposure_result(
    repo: DuckDBRepository, run_id: str, runs_dir: Path | None = None
) -> ExposureResult | None:
    """Rebuild an ExposureResult from the stored path values for CSA what-ifs."""
    base = Path(runs_dir or get_settings().data_dir / "runs") / run_id
    f = base / "cp_netting_values.parquet"
    if not f.exists():
        return None
    vals = pd.read_parquet(f)
    grid_df = pd.read_parquet(base / "cp_grid.parquet")
    grid = [
        (
            r["step"],
            pd.Timestamp(r["date"]).date() if not isinstance(r["date"], date) else r["date"],
            float(r["years"]),
        )
        for _, r in grid_df.iterrows()
    ]
    steps = [g[0] for g in grid]
    netting_sets, csas = repo.load_netting_sets()
    ns_map = {n.netting_set_id: n for n in netting_sets}
    values = {k: g.sort_values("path")[steps].to_numpy().T for k, g in vals.groupby("netting_set_id")}
    run = repo.load_run(run_id)
    snap = repo.load_portfolio_snapshot(run.portfolio_snapshot_id)
    trades_by_set: dict[str, list[str]] = {}
    for t in snap.trades:
        if t.netting_set_id in values:
            trades_by_set.setdefault(t.netting_set_id, []).append(t.trade_id)
    current = {k: float(v[0].mean()) for k, v in values.items()}  # approximation; t0 value not stored
    return ExposureResult(
        grid,
        values,
        current,
        {k: ns_map[k] for k in values if k in ns_map},
        {c.csa_id: c for c in csas},
        trades_by_set,
        int(vals["path"].max()) + 1,
    )


def csa_what_if(
    res: ExposureResult, netting_set_id: str, csa: CSA | None, mpor_days: int = 10
) -> pd.DataFrame:
    """Profile of one netting set under alternative CSA terms (None = uncollateralised)."""
    sub = ExposureResult(
        res.grid,
        {netting_set_id: res.netting_values[netting_set_id]},
        {netting_set_id: res.netting_current.get(netting_set_id, 0.0)},
        {netting_set_id: res.netting_sets[netting_set_id]},
        res.csas,
        {netting_set_id: res.trades_by_set.get(netting_set_id, [])},
        res.paths,
    )
    return collateralise(sub, mpor_days, {netting_set_id: csa})


_ = (credit_proxy, np)
