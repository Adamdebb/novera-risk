"""Fund-face run for a stored market-risk run: exposures, prime-broker margin, factor betas,
redemption stress, strategy attribution and crowding, stored as fund_* frames."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from novera.config import Settings, get_settings
from novera.fund.modules import (
    FundRun,
    crowding,
    exposures,
    factor_betas,
    pb_margin,
    redemption_stress,
    strategy_attribution,
)
from novera.fund.reference import FACTORS
from novera.market_data.history import MarketHistory
from novera.risk.scenarios import historical_shocks
from novera.storage.duckdb_repository import DuckDBRepository

MODEL_VERSION = "1.0.0"


def delta_equivalent(
    valuation: pd.DataFrame, sens: pd.DataFrame, snapshot_trades: dict[str, Any]
) -> tuple[pd.Series, pd.Series]:
    """Signed delta-equivalent exposure per trade in reporting currency, and its underlying.

    Equity, commodity and crypto deltas per +1% times 100 are delta-equivalent notionals; FX
    delta counts only for FX products (on other products it is translation); rates and
    credit products use their signed notional (receive fixed and sell protection are long).
    """
    v = valuation[valuation["status"] == "LIVE"]
    fx_products = {"FX_SPOT", "FX_FORWARD", "FX_OPTION"}
    exposure: dict[str, float] = {}
    underlying: dict[str, str] = {}
    prod = v.set_index("trade_id")["product_type"]
    by = sens[sens["measure"].isin(["EQ_DELTA", "CMD_DELTA", "CRYPTO_DELTA", "FX_DELTA"])]
    for (tid, measure), g in by.groupby(["trade_id", "measure"]):
        if tid not in prod.index:
            continue
        if measure == "FX_DELTA" and prod[tid] not in fx_products:
            continue
        exposure[tid] = exposure.get(tid, 0.0) + float(g["value"].sum()) * 100
        underlying.setdefault(tid, str(g["underlying"].iloc[0]))
    for tid, r in v.set_index("trade_id").iterrows():
        t = snapshot_trades.get(tid)
        if t is None:
            continue
        if r["product_type"] in ("GOVERNMENT_BOND", "INTEREST_RATE_SWAP"):
            fx = float(r["fx_to_reporting"] or 1.0)
            sign = 1.0
            if r["product_type"] == "INTEREST_RATE_SWAP":
                sign = 1.0 if t.swap_side.value == "RECEIVE_FIXED" else -1.0
            else:
                sign = 1.0 if t.direction.value == "BUY" else -1.0
            exposure[tid] = sign * t.quantity * fx
            underlying[tid] = t.currency
        elif r["product_type"] == "CDS_INDEX":
            fx = float(r["fx_to_reporting"] or 1.0)
            sign = -1.0 if t.direction.value == "BUY" else 1.0  # bought protection is short credit
            exposure[tid] = sign * t.quantity * fx
            underlying[tid] = t.instrument.index_family
        elif tid not in underlying:
            ins = t.instrument
            underlying[tid] = (
                getattr(ins, "ticker", None)
                or getattr(ins, "index", None)
                or getattr(ins, "underlying", None)
                or getattr(ins, "commodity", None)
                or getattr(ins, "symbol", None)
                or getattr(ins, "pair", None)
                or t.currency
            )
    return pd.Series(exposure, dtype=float), pd.Series(underlying, dtype=object)


def run_fund(
    repo: DuckDBRepository,
    run_id: str,
    settings: Settings | None = None,
    persist: bool = True,
    runs_dir: Path | None = None,
) -> FundRun:
    settings = settings or get_settings()
    run = repo.load_run(run_id)
    org = repo.load_organisation(run.config.get("firm_id", "MSF"))
    fund = repo.load_fund(org.firm.firm_id)
    if fund is None:
        raise RuntimeError(f"no fund metadata stored for {org.firm.firm_id}")
    snap = repo.load_portfolio_snapshot(run.portfolio_snapshot_id)
    trades = {t.trade_id: t for t in snap.trades}
    val = repo.load_run_frame(run_id, "valuation")
    sens = repo.load_run_frame(run_id, "sensitivities")
    contrib = repo.load_run_frame(run_id, "var_contributions")
    liq = repo.load_run_frame(run_id, "liquidity_trades")
    pnl_trade = repo.load_run_frame(run_id, "pnl_by_trade")
    pnl_by_desk = None
    if len(pnl_trade):
        keys = val[["trade_id", "desk_id"]].drop_duplicates("trade_id")
        m = pnl_trade.merge(keys, on="trade_id", how="left")
        pnl_by_desk = m.groupby("desk_id")["pnl"].sum().rename("TOTAL").reset_index()
    notionals, underlyings = delta_equivalent(val, sens, trades)
    exp = exposures(val, notionals, fund.nav)
    margin = pb_margin(val, notionals, fund.nav, underlyings=underlyings)
    # Strategy scenario P&L from the stored full-revaluation matrix.
    base = Path(runs_dir or settings.data_dir / "runs") / run_id
    f = base / "var_pnl_full_revaluation.parquet"
    strategy_pnl = pd.DataFrame()
    factors = pd.DataFrame()
    if f.exists():
        pnl = pd.read_parquet(f)
        keys = val.set_index("trade_id")["desk_id"]
        cols = {c: keys.get(c) for c in pnl.columns}
        strategy_pnl = pnl.T.groupby(pd.Series(cols)).sum().T
        history = MarketHistory.from_long(repo.load_market_history())
        universe = {x.factor_id: x for x in repo.load_risk_factors()}
        moves = historical_shocks(history, run.business_date, len(pnl), 1, universe, factor_ids=list(FACTORS))
        moves.index = pd.Index([pd.Timestamp(d).date() if not hasattr(d, "year") else d for d in moves.index])
        strategy_pnl.index = pd.Index(
            [pd.Timestamp(d).date() if not hasattr(d, "year") else d for d in strategy_pnl.index]
        )
        factors = factor_betas(strategy_pnl, moves, fund.nav)
    red = redemption_stress(fund, run.business_date, liq)
    attribution = strategy_attribution(val, contrib, pnl_by_desk, strategy_pnl, fund.nav, exp["desk_id"])
    crowd = crowding(val, notionals, underlyings, liq, fund.nav)
    out = FundRun(
        run_id,
        fund,
        exp,
        margin,
        factors,
        red,
        attribution,
        crowd,
        notes={"exposure_basis": "delta-equivalent notional (HF-001)"},
    )
    if persist:
        repo.save_run_frame(run_id, "fund_summary", pd.DataFrame([out.summary()]))
        repo.save_run_frame(run_id, "fund_exposure_strategy", exp["desk_id"])
        repo.save_run_frame(run_id, "fund_exposure_asset_class", exp["asset_class"])
        repo.save_run_frame(run_id, "fund_margin_pb", margin["by_pb"])
        repo.save_run_frame(run_id, "fund_margin_detail", margin["by_pb_asset_class"])
        if len(factors):
            repo.save_run_frame(run_id, "fund_factors", factors)
        repo.save_run_frame(run_id, "fund_redemptions", red)
        repo.save_run_frame(run_id, "fund_attribution", attribution)
        repo.save_run_frame(run_id, "fund_crowding", crowd["positions"])
        repo.save_run_frame(
            run_id,
            "fund_flags",
            pd.DataFrame(
                [{"kind": "CROWDING", "message": x} for x in crowd["flags"]], columns=["kind", "message"]
            ),
        )
    return out
