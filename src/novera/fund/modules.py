"""Hedge-fund modules. Records HF-001 to HF-006.

exposure      gross, net, long, short by strategy and asset class; leverage on NAV
margin        prime-broker margin replication with netting benefit and crowding surcharge
factors       factor betas from the strategy's hypothetical P&L series on factor moves
redemption    redemption stress against the liquidity ladder and dealing terms
attribution   strategy P&L, risk contribution and return statistics
crowding      position-weighted crowding and crowded exit horizons
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from novera.fund.reference import (
    CROWDING,
    CROWDING_SURCHARGE,
    FACTORS,
    LIQUIDITY_BUFFER,
    LISTED_PB,
    NETTING_BENEFIT,
    PB_MARGIN,
)
from novera.simulation.fund import DealingFrequency, Fund

MODEL_VERSION = "1.0.0"


def _underlying(row: pd.Series) -> str:
    tid = str(row["trade_id"])
    return str(row.get("underlying", "")) or tid


# --- exposure --------------------------------------------------------------------------------


def exposures(valuation: pd.DataFrame, notionals: pd.Series, nav: float) -> dict[str, Any]:
    """``notionals``: signed market exposure per trade (delta-equivalent notional in reporting ccy)."""
    v = valuation[valuation["status"] == "LIVE"].copy()
    v["exposure"] = v["trade_id"].map(notionals).fillna(0.0)
    v["long"] = v["exposure"].clip(lower=0.0)
    v["short"] = (-v["exposure"]).clip(lower=0.0)
    out = {}
    for by in ("desk_id", "asset_class"):
        g = v.groupby(by, dropna=False).agg(long=("long", "sum"), short=("short", "sum")).reset_index()
        g["gross"] = g["long"] + g["short"]
        g["net"] = g["long"] - g["short"]
        for c in ("long", "short", "gross", "net"):
            g[f"{c}_pct_nav"] = g[c] / nav
        out[by] = g.sort_values("gross", ascending=False)
    gross, net = float(v["long"].sum() + v["short"].sum()), float(v["long"].sum() - v["short"].sum())
    out["totals"] = {
        "nav": nav,
        "long": float(v["long"].sum()),
        "short": float(v["short"].sum()),
        "gross": gross,
        "net": net,
        "gross_leverage": gross / nav,
        "net_leverage": net / nav,
        "long_short_ratio": float(v["long"].sum() / v["short"].sum()) if v["short"].sum() else None,
    }
    return out


# --- prime-broker margin ------------------------------------------------------------------------


def pb_margin(
    valuation: pd.DataFrame,
    notionals: pd.Series,
    nav: float,
    crowding: dict[str, float] | None = None,
    underlyings: pd.Series | None = None,
) -> dict[str, Any]:
    crowding = crowding or CROWDING
    v = valuation[valuation["status"] == "LIVE"].copy()
    v["exposure"] = v["trade_id"].map(notionals).fillna(0.0)
    v["pb"] = v["counterparty_id"].map(lambda c: c if c in PB_MARGIN else LISTED_PB.get(c, "PB_GS"))
    v["underlying"] = v["trade_id"].map(underlyings) if underlyings is not None else ""
    rows = []
    for (pb, ac), g in v.groupby(["pb", "asset_class"]):
        rates = PB_MARGIN.get(pb, PB_MARGIN["PB_GS"])
        gross_margin = 0.0
        for _, r in g.iterrows():
            rate = rates.get(r["product_type"], 0.10)
            if crowding.get(str(r["underlying"]), 0.0) > 0.7:
                rate += CROWDING_SURCHARGE
            gross_margin += abs(r["exposure"]) * rate
        long, short = g["exposure"].clip(lower=0).sum(), (-g["exposure"]).clip(lower=0).sum()
        offset = min(long, short)
        benefit = NETTING_BENEFIT * (offset / max(long + short, 1e-9)) * gross_margin if offset else 0.0
        rows.append(
            {
                "prime_broker": pb,
                "asset_class": ac,
                "gross_exposure": float(long + short),
                "margin_gross": gross_margin,
                "netting_benefit": benefit,
                "margin": gross_margin - benefit,
                "trades": int(len(g)),
            }
        )
    by = pd.DataFrame(rows)
    by_pb = by.groupby("prime_broker", as_index=False).agg(
        gross_exposure=("gross_exposure", "sum"), margin=("margin", "sum"), trades=("trades", "sum")
    )
    total = float(by_pb["margin"].sum()) if len(by_pb) else 0.0
    by_pb["share"] = by_pb["margin"] / total if total else 0.0
    cash = nav * LIQUIDITY_BUFFER
    excess = nav - total  # equity not consumed by margin
    return {
        "by_pb_asset_class": by.sort_values("margin", ascending=False),
        "by_pb": by_pb.sort_values("margin", ascending=False),
        "total_margin": total,
        "margin_to_nav": total / nav,
        "unencumbered_cash": cash,
        "excess_equity": excess,
        "largest_pb_share": float(by_pb["share"].max()) if len(by_pb) else 0.0,
    }


# --- factor exposures ---------------------------------------------------------------------------


def factor_betas(strategy_pnl: pd.DataFrame, factor_moves: pd.DataFrame, nav: float) -> pd.DataFrame:
    """OLS of each strategy's scenario P&L (rows: scenario dates) on the factor moves for the
    same dates. Returns betas as P&L per one standard deviation move, t-stats and R²."""
    x = factor_moves.reindex(strategy_pnl.index).fillna(0.0)
    x = x.loc[:, x.std() > 0]
    if x.empty or len(x) < 10:
        return pd.DataFrame()
    xs = (x - x.mean()) / x.std()  # standardised factors
    design = np.column_stack([np.ones(len(xs)), xs.to_numpy()])
    rows = []
    for col in strategy_pnl.columns:
        y = strategy_pnl[col].to_numpy(dtype=float)
        beta, res, _, _ = np.linalg.lstsq(design, y, rcond=None)
        fitted = design @ beta
        resid = y - fitted
        ss_res, ss_tot = float((resid**2).sum()), float(((y - y.mean()) ** 2).sum())
        r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
        dof = max(len(y) - design.shape[1], 1)
        sigma2 = ss_res / dof
        cov = sigma2 * np.linalg.pinv(design.T @ design)
        se = np.sqrt(np.maximum(np.diag(cov), 1e-18))
        for j, f in enumerate(xs.columns, start=1):
            rows.append(
                {
                    "strategy": col,
                    "factor": f,
                    "label": FACTORS.get(f, f),
                    "beta_per_sigma": beta[j],
                    "beta_pct_nav": beta[j] / nav,
                    "t_stat": beta[j] / se[j],
                    "r2": r2,
                    "residual_vol": float(np.sqrt(sigma2)),
                }
            )
    return pd.DataFrame(rows)


# --- redemption stress ----------------------------------------------------------------------------


def _next_dealing(as_of: date, freq: DealingFrequency, notice_days: int) -> date:
    earliest = as_of + timedelta(days=notice_days)
    if freq is DealingFrequency.MONTHLY:
        step = 1
    elif freq is DealingFrequency.QUARTERLY:
        step = 3
    else:
        step = 12
    d = date(as_of.year, as_of.month, 1)
    while True:
        m = d.month + step
        d = date(d.year + (m - 1) // 12, (m - 1) % 12 + 1, 1)
        last = date(d.year + (d.month // 12), d.month % 12 + 1, 1) - timedelta(days=1)
        if last >= earliest:
            return last


@dataclass
class RedemptionScenario:
    name: str
    redemption_share: float  # share of each investor's holding redeemed
    market_haircut: float  # loss applied to liquidatable assets in stress
    investor_types: tuple[str, ...] | None = None  # None = all


DEFAULT_REDEMPTION_SCENARIOS = (
    RedemptionScenario("normal_quarter", 0.05, 0.0),
    RedemptionScenario("stressed_quarter", 0.20, 0.10),
    RedemptionScenario("fof_run", 0.60, 0.15, ("FOF",)),
    RedemptionScenario("severe", 0.35, 0.20),
)


def redemption_stress(
    fund: Fund,
    as_of: date,
    liquidity_trades: pd.DataFrame,
    scenarios: tuple[RedemptionScenario, ...] = DEFAULT_REDEMPTION_SCENARIOS,
) -> pd.DataFrame:
    """Cash needed at each dealing date under the scenario versus what can be liquidated by then
    (from days-to-liquidate), after gates and with a market haircut on the assets sold."""
    nav = fund.nav
    liq = (
        liquidity_trades[["trade_id", "pv", "days_to_liquidate"]].copy()
        if len(liquidity_trades)
        else pd.DataFrame(columns=["trade_id", "pv", "days_to_liquidate"])
    )
    liq["abs_pv"] = liq["pv"].abs()
    total_assets = float(liq["abs_pv"].sum()) or nav
    rows = []
    for sc in scenarios:
        by_date: dict[date, float] = {}
        gated = 0.0
        for inv in fund.investors:
            if sc.investor_types and inv.investor_type not in sc.investor_types:
                continue
            if inv.lockup_until and inv.lockup_until > as_of:
                continue
            requested = inv.share_of_nav * nav * sc.redemption_share
            allowed = min(requested, inv.share_of_nav * nav * inv.gate_pct)
            gated += requested - allowed
            d = _next_dealing(as_of, inv.dealing, inv.notice_days)
            by_date[d] = by_date.get(d, 0.0) + allowed
        cum_need = 0.0
        for d in sorted(by_date):
            cum_need += by_date[d]
            days = np.busday_count(as_of, d)
            liquidatable = float(liq[liq["days_to_liquidate"] <= days]["abs_pv"].sum()) * (
                1 - sc.market_haircut
            )
            liquidatable = liquidatable / total_assets * nav + nav * LIQUIDITY_BUFFER
            rows.append(
                {
                    "scenario": sc.name,
                    "dealing_date": d,
                    "business_days": int(days),
                    "redemptions_due": by_date[d],
                    "cumulative_due": cum_need,
                    "liquidatable_by_then": liquidatable,
                    "gated": gated,
                    "coverage": liquidatable / cum_need if cum_need else None,
                    "shortfall": max(cum_need - liquidatable, 0.0),
                }
            )
    return pd.DataFrame(rows)


# --- strategy attribution ----------------------------------------------------------------------


def strategy_attribution(
    valuation: pd.DataFrame,
    contributions: pd.DataFrame,
    pnl_by_desk: pd.DataFrame | None,
    strategy_pnl: pd.DataFrame,
    nav: float,
    exposures_by_desk: pd.DataFrame,
) -> pd.DataFrame:
    v = valuation[valuation["status"] == "LIVE"]
    c = contributions.merge(v[["trade_id", "desk_id"]], on="trade_id", how="inner")
    var_by = c.groupby("desk_id")["var_contribution"].sum()
    total_var = float(var_by.sum())
    rows = []
    for desk in sorted(v["desk_id"].dropna().unique()):
        series = strategy_pnl[desk] if desk in strategy_pnl else pd.Series(dtype=float)
        ann_vol = float(series.std() * np.sqrt(252)) if len(series) > 2 else 0.0
        ann_ret = float(series.mean() * 252) if len(series) else 0.0
        cum = series.cumsum() if len(series) else pd.Series(dtype=float)
        dd = float((cum - cum.cummax()).min()) if len(cum) else 0.0
        today = None
        if pnl_by_desk is not None and len(pnl_by_desk) and "TOTAL" in pnl_by_desk:
            m = pnl_by_desk[pnl_by_desk["desk_id"] == desk]
            today = float(m["TOTAL"].iloc[0]) if len(m) else None
        ex = exposures_by_desk[exposures_by_desk["desk_id"] == desk]
        rows.append(
            {
                "strategy": desk,
                "pv": float(v[v["desk_id"] == desk]["pv"].sum()),
                "gross_pct_nav": float(ex["gross_pct_nav"].iloc[0]) if len(ex) else 0.0,
                "net_pct_nav": float(ex["net_pct_nav"].iloc[0]) if len(ex) else 0.0,
                "component_var": float(var_by.get(desk, 0.0)),
                "share_of_var": float(var_by.get(desk, 0.0) / total_var) if total_var else 0.0,
                "pnl_today": today,
                "hypothetical_ann_return_pct_nav": ann_ret / nav,
                "hypothetical_ann_vol_pct_nav": ann_vol / nav,
                "sharpe_like": (ann_ret / ann_vol) if ann_vol else 0.0,
                "max_drawdown": dd,
                "trades": int((v["desk_id"] == desk).sum()),
            }
        )
    return pd.DataFrame(rows).sort_values("component_var", ascending=False)


# --- crowding ---------------------------------------------------------------------------------


def crowding(
    valuation: pd.DataFrame,
    notionals: pd.Series,
    underlyings: pd.Series,
    liquidity_trades: pd.DataFrame,
    nav: float,
    scores: dict[str, float] | None = None,
) -> dict[str, Any]:
    scores = scores or CROWDING
    v = valuation[valuation["status"] == "LIVE"].copy()
    v["exposure"] = v["trade_id"].map(notionals).fillna(0.0)
    v["underlying"] = v["trade_id"].map(underlyings)
    v["score"] = v["underlying"].map(scores).fillna(0.2)
    g = (
        v.groupby("underlying")
        .agg(exposure=("exposure", "sum"), score=("score", "first"), trades=("trade_id", "count"))
        .reset_index()
    )
    g["abs_exposure"] = g["exposure"].abs()
    g["pct_nav"] = g["exposure"] / nav
    g["crowded"] = (g["score"] >= 0.7) & (g["abs_exposure"] >= 0.03 * nav)
    weighted = (
        float((g["abs_exposure"] * g["score"]).sum() / g["abs_exposure"].sum())
        if g["abs_exposure"].sum()
        else 0.0
    )
    liq = (
        liquidity_trades.set_index("trade_id")["days_to_liquidate"]
        if len(liquidity_trades)
        else pd.Series(dtype=float)
    )
    v["days_to_liquidate"] = v["trade_id"].map(liq).fillna(1.0)
    v["crowded_exit_days"] = v["days_to_liquidate"] * (1 + 2 * v["score"])
    exits = (
        v.groupby("underlying")
        .agg(days_to_liquidate=("days_to_liquidate", "max"), crowded_exit_days=("crowded_exit_days", "max"))
        .reset_index()
    )
    g = g.merge(exits, on="underlying", how="left")
    flags = [
        f"{r['underlying']}: {r['pct_nav']:+.1%} of NAV, crowding {r['score']:.2f}, exit "
        f"{r['crowded_exit_days']:.0f} days if peers sell too."
        for _, r in g[g["crowded"]].sort_values("abs_exposure", ascending=False).iterrows()
    ]
    return {
        "positions": g.sort_values("abs_exposure", ascending=False),
        "weighted_score": weighted,
        "crowded_share_of_gross": float(g[g["crowded"]]["abs_exposure"].sum() / g["abs_exposure"].sum())
        if g["abs_exposure"].sum()
        else 0.0,
        "flags": flags,
    }


@dataclass
class FundRun:
    run_id: str
    fund: Fund
    exposures: dict[str, Any]
    margin: dict[str, Any]
    factors: pd.DataFrame
    redemptions: pd.DataFrame
    attribution: pd.DataFrame
    crowding: dict[str, Any]
    notes: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        t = self.exposures["totals"]
        worst = self.redemptions.sort_values("coverage").iloc[0] if len(self.redemptions) else None
        return {
            "nav": self.fund.nav,
            "gross_leverage": t["gross_leverage"],
            "net_leverage": t["net_leverage"],
            "long": t["long"],
            "short": t["short"],
            "total_margin": self.margin["total_margin"],
            "margin_to_nav": self.margin["margin_to_nav"],
            "largest_pb_share": self.margin["largest_pb_share"],
            "crowding_score": self.crowding["weighted_score"],
            "crowded_share_of_gross": self.crowding["crowded_share_of_gross"],
            "worst_redemption_scenario": str(worst["scenario"]) if worst is not None else None,
            "worst_redemption_coverage": float(worst["coverage"])
            if worst is not None and worst["coverage"]
            else None,
            "redemption_shortfall": float(self.redemptions["shortfall"].max())
            if len(self.redemptions)
            else 0.0,
        }
