"""Liquidity measures. Methodology record MR-013.

Days to liquidate = |position| / (participation × average daily volume), with synthetic
volume and bid-ask assumptions per product (documented, demo only). Liquidity-adjusted
VaR adds half the bid-ask cost of the whole book and scales VaR by the square root of the
liquidation horizon where it exceeds one day (Bangia et al. simplified).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MODEL_VERSION = "1.0.0"

PARTICIPATION = 0.20  # share of daily volume we assume we can take without moving the price

# product -> (average daily volume in position units, bid-ask spread as a fraction of PV or notional)
ADV: dict[str, tuple[float, float]] = {
    "GOVERNMENT_BOND": (500e6, 0.0003),  # notional, 3bp of price
    "INTEREST_RATE_SWAP": (2_000e6, 0.0001),  # ~0.25bp in rate times duration
    "FX_SPOT": (5_000e6, 0.00005),
    "FX_FORWARD": (2_000e6, 0.0001),
    "FX_OPTION": (300e6, 0.0030),  # spread as a share of premium
    "CASH_EQUITY": (2_000_000, 0.0010),  # shares
    "EQUITY_INDEX_FUTURE": (150_000, 0.0001),  # contracts
    "EQUITY_OPTION": (5_000, 0.0200),  # contracts, spread on premium
    "COMMODITY_FUTURE": (100_000, 0.0003),  # contracts
    "CDS_INDEX": (1_000e6, 0.0005),
    "CRYPTO_SPOT": (20_000, 0.0010),  # units
}
# Overrides for known illiquid pockets in the simulated book.
ADV_OVERRIDES: dict[str, tuple[float, float]] = {
    "COMMODITY_FUTURE:BRENT:far": (2_000, 0.0030),  # far-dated Brent: thin
    "EQUITY_OPTION:index": (20_000, 0.0100),
}


@dataclass
class LiquidityReport:
    by_trade: pd.DataFrame  # trade_id, desk_id, product_type, position, adv, days_to_liquidate, bidask_cost
    by_bucket: pd.DataFrame  # horizon bucket -> pv share, trades
    by_desk: pd.DataFrame  # desk -> weighted days, max days, bidask cost
    var: float
    liquidity_adjusted_var: float
    bidask_cost: float
    horizon_days: float
    flags: list[str] = field(default_factory=list)


def _assumption(row: pd.Series, far_dated: bool) -> tuple[float, float]:
    pt = row["product_type"]
    if pt == "COMMODITY_FUTURE" and far_dated:
        return ADV_OVERRIDES["COMMODITY_FUTURE:BRENT:far"]
    if pt == "EQUITY_OPTION" and str(row.get("book_id", "")).startswith("INDEX"):
        return ADV_OVERRIDES["EQUITY_OPTION:index"]
    return ADV.get(pt, (1e9, 0.001))


def liquidity(
    valuation: pd.DataFrame,
    var: float,
    far_dated_trade_ids: set[str] | None = None,
    participation: float = PARTICIPATION,
) -> LiquidityReport:
    far = far_dated_trade_ids or set()
    v = valuation[(valuation["status"] == "LIVE") & valuation["pv"].notna()].copy()
    adv, spread = (
        zip(*[_assumption(r, r["trade_id"] in far) for _, r in v.iterrows()], strict=True)
        if len(v)
        else ([], [])
    )
    v["adv"] = list(adv)
    v["spread"] = list(spread)
    v["position"] = v["quantity"].abs()
    v["days_to_liquidate"] = v["position"] / (participation * v["adv"])
    # Bid-ask cost: half spread on |PV| for cash-like products, on notional for OTC linear ones.
    notional_like = v["product_type"].isin(
        ["GOVERNMENT_BOND", "INTEREST_RATE_SWAP", "FX_SPOT", "FX_FORWARD", "CDS_INDEX"]
    )
    base = np.where(notional_like, v["position"] * v["fx_to_reporting"].fillna(1.0), v["pv"].abs())
    v["bidask_cost"] = 0.5 * v["spread"] * base
    bins = [0, 1, 5, 10, np.inf]
    labels = ["<= 1 day", "1-5 days", "5-10 days", "> 10 days"]
    v["horizon_bucket"] = pd.cut(
        v["days_to_liquidate"].clip(lower=1e-9), bins=bins, labels=labels, right=True
    )
    abs_pv = v["pv"].abs().sum()
    by_bucket = (
        v.groupby("horizon_bucket", observed=False)
        .agg(trades=("trade_id", "count"), abs_pv=("pv", lambda s: s.abs().sum()))
        .reset_index()
    )
    by_bucket["share_of_abs_pv"] = by_bucket["abs_pv"] / abs_pv if abs_pv else 0.0
    w = v["pv"].abs()
    by_desk = (
        v.groupby("desk_id", dropna=False)
        .apply(
            lambda g: pd.Series(
                {
                    "weighted_days": float(
                        (g["days_to_liquidate"] * g["pv"].abs()).sum() / max(g["pv"].abs().sum(), 1e-9)
                    ),
                    "max_days": float(g["days_to_liquidate"].max()),
                    "bidask_cost": float(g["bidask_cost"].sum()),
                    "trades": int(len(g)),
                }
            ),
            include_groups=False,
        )
        .reset_index()
    )
    horizon = float((v["days_to_liquidate"] * w).sum() / w.sum()) if w.sum() else 1.0
    cost = float(v["bidask_cost"].sum())
    lvar = var * np.sqrt(max(horizon, 1.0)) + cost
    flags = []
    slow = v[v["days_to_liquidate"] > 10].sort_values("days_to_liquidate", ascending=False)
    for _, r in slow.head(5).iterrows():
        flags.append(
            f"{r['trade_id']} ({r['desk_id']}, {r['product_type']}): {r['days_to_liquidate']:.0f} days to "
            f"liquidate at {participation:.0%} participation."
        )
    cols = [
        "trade_id",
        "desk_id",
        "book_id",
        "product_type",
        "position",
        "adv",
        "days_to_liquidate",
        "bidask_cost",
        "horizon_bucket",
        "pv",
    ]
    return LiquidityReport(
        v[cols].sort_values("days_to_liquidate", ascending=False),
        by_bucket,
        by_desk,
        var,
        lvar,
        cost,
        horizon,
        flags,
    )
