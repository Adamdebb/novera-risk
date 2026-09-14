"""Simulated 'official risk system' feed for the independent-challenger demo.

Produces per-trade PV and component VaR for the same business date as a Novera run, with
four planted differences a real second engine typically shows:

1. Scope: the trades of one book and the invalid trades are missing from the feed.
2. Market-data timestamp: one asset class is valued on the previous day's market.
3. Pricing model: equity options are priced with flat ATM volatility (no smile).
4. Methodology: VaR uses a 250-day window instead of 500.

The feed is a CSV plus a JSON metadata file, the shape a vendor extract would have.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from novera.domain.enums import ProductType
from novera.market_data.history import MarketHistory
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing.valuation import fx_to_reporting, value_trade
from novera.risk import Portfolio, VaRConfig, historical_var
from novera.storage.duckdb_repository import DuckDBRepository

VENDOR = "OfficialRisk 9.2"


@dataclass
class VendorFeedConfig:
    excluded_book: str = "SN_OPTIONS"
    stale_asset_class: str = "FX"
    flat_vol_products: tuple[str, ...] = ("EQUITY_OPTION",)
    var_window_days: int = 250
    plant: bool = True
    planted: list[str] = field(default_factory=list)


def _flat_vol_snapshot(market: MarketSnapshot) -> MarketSnapshot:
    """Every equity vol node set to its expiry's ATM value (smile removed)."""
    updates: dict[str, float] = {}
    for u in sorted({f.split(":")[1] for f in market.factors_with_prefix("VOL:")}):
        if u.endswith("USD") or u.startswith("USD"):
            continue
        nodes = market.factors_with_prefix(f"VOL:{u}:")
        by_exp: dict[str, float] = {}
        for f in nodes:
            _, _, exp, m = f.split(":")
            if abs(float(m) - 1.0) < 1e-9:
                by_exp[exp] = market.values[f]
        for f in nodes:
            exp = f.split(":")[2]
            if exp in by_exp:
                updates[f] = by_exp[exp]
    return market.with_values(updates)


def generate_vendor_feed(
    repo: DuckDBRepository, run_id: str, out_dir: Path, cfg: VendorFeedConfig | None = None
) -> tuple[Path, Path]:
    cfg = cfg or VendorFeedConfig()
    run = repo.load_run(run_id)
    snap = repo.load_portfolio_snapshot(run.portfolio_snapshot_id)
    market = repo.load_market_snapshot(run.market_snapshot_id)
    prev = (
        repo.load_market_snapshot(run.previous_market_snapshot_id)
        if run.previous_market_snapshot_id
        else market
    )
    universe = {f.factor_id: f for f in repo.load_risk_factors()}
    hist = MarketHistory.from_long(repo.load_market_history())
    org = repo.load_organisation("GMB")
    reporting = run.reporting_currency

    trades = [t for t in snap.trades if t.status.value == "LIVE"]
    if cfg.plant:
        before = len(trades)
        trades = [t for t in trades if t.book_id != cfg.excluded_book]
        cfg.planted.append(
            f"scope: {before - len(trades)} trades of book {cfg.excluded_book} not in the feed"
        )
    hierarchy = {b.book_id: org.hierarchy_for_book(b.book_id) for b in org.books}

    # Official valuation: per-trade market choice.
    flat = _flat_vol_snapshot(market) if cfg.plant else market
    rows = []
    for t in trades:
        mkt = market
        if cfg.plant and t.asset_class.value == cfg.stale_asset_class:
            mkt = prev
        elif cfg.plant and t.product_type.value in cfg.flat_vol_products:
            mkt = flat
        try:
            r = value_trade(t, mkt, market.as_of)
            pv = r.pv_local * fx_to_reporting(mkt, r.currency, reporting)
        except Exception:  # noqa: BLE001
            continue
        h = hierarchy.get(t.book_id, {})
        rows.append(
            {
                "trade_id": t.trade_id,
                "book": t.book_id,
                "desk": h.get("desk_id"),
                "product": t.product_type.value,
                "asset_class": t.asset_class.value,
                "currency": t.currency,
                "official_pv": pv,
            }
        )
    if cfg.plant:
        cfg.planted.append(f"market data: {cfg.stale_asset_class} trades valued on {prev.as_of} market")
        cfg.planted.append(f"pricing: {', '.join(cfg.flat_vol_products)} priced with flat ATM vol (no smile)")
        cfg.planted.append(f"methodology: VaR window {cfg.var_window_days} days (Novera 500)")

    # Official VaR: same engine on the official trade set, shorter window, official market.
    pf = Portfolio(trades, market, reporting, universe=universe)
    var = historical_var(pf, hist, VaRConfig(window_days=cfg.var_window_days))
    contrib = var.contributions.set_index("trade_id")["var_contribution"]
    feed = pd.DataFrame(rows)
    feed["official_var_contribution"] = feed["trade_id"].map(contrib).fillna(0.0)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv = out_dir / f"official_risk_{run.business_date}.csv"
    meta = out_dir / f"official_risk_{run.business_date}.json"
    feed.to_csv(csv, index=False)
    meta.write_text(
        json.dumps(
            {
                "vendor": VENDOR,
                "business_date": str(run.business_date),
                "reporting_currency": reporting,
                "var": {
                    "confidence": 0.99,
                    "horizon_days": 1,
                    "window_days": cfg.var_window_days,
                    "method": "historical_full_revaluation",
                    "total": var.var,
                },
                "trades": len(feed),
                "generated_from_run": run_id,
                "planted_differences": cfg.planted,
            },
            indent=2,
        )
    )
    return csv, meta


_ = (date, ProductType)
