"""Ad-hoc what-if scenarios for the Analyst: shock named factor groups and reprice the
stored run's portfolio through the deterministic engine. The engine computes, the model
only asks (ADR 0003)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import pandas as pd

from novera.market_data.snapshot import MarketSnapshot
from novera.risk import Portfolio, run_stress
from novera.risk.stress import ShockRule, StressScenario
from novera.storage.duckdb_repository import DuckDBRepository

# Friendly target -> shock rules. Sizes: pct as a fraction, bp for rates and spreads,
# vol_points for surfaces. "usd" means dollar strength.
TARGETS: dict[str, tuple[str, ...]] = {
    "equities": ("EQ:", "EQIDX:"),
    "us_equities": ("EQIDX:SPX", "EQIDX:NDX"),
    "european_equities": ("EQIDX:SX5E", "EQIDX:DAX", "EQIDX:FTSE"),
    "rates_all": ("IR:",),
    "rates_usd": ("IR:USD:",),
    "rates_eur": ("IR:EUR:",),
    "rates_gbp": ("IR:GBP:",),
    "rates_jpy": ("IR:JPY:",),
    "credit": ("CDS:",),
    "credit_ig": ("CDS:CDX.NA.IG", "CDS:ITRAXX.EUR.MAIN"),
    "credit_hy": ("CDS:CDX.NA.HY", "CDS:ITRAXX.EUR.XOVER"),
    "oil": ("CMD:BRENT:", "CMD:WTI:"),
    "natgas": ("CMD:NATGAS:",),
    "gold": ("CMD:GOLD:",),
    "metals": ("CMD:COPPER:", "CMD:ALUMINIUM:", "CMD:GOLD:", "CMD:SILVER:"),
    "commodities": ("CMD:",),
    "btc": ("CRYPTO:BTC",),
    "eth": ("CRYPTO:ETH",),
    "crypto": ("CRYPTO:",),
    "vol": ("VOL:",),
    "equity_vol": tuple(),  # filled below from EQ names
    "fx_vol": tuple(),
    "eurusd": ("FX:EURUSD",),
    "gbpusd": ("FX:GBPUSD",),
    "usdjpy": ("FX:USDJPY",),
    "em_fx": ("FX:USDMXN", "FX:USDBRL", "FX:USDZAR", "FX:USDINR", "FX:USDTRY", "FX:USDARS"),
    "usd": ("__USD_UP__",),
}
UNITS = ("pct", "bp", "vol_points")


@dataclass(frozen=True)
class Shock:
    target: str
    size: float
    unit: str = "pct"
    tenors: tuple[str, ...] | None = None


def _rules(shock: Shock, base: MarketSnapshot) -> list[ShockRule]:
    t = shock.target.lower()
    if t == "usd":
        pct = shock.size / 100.0
        return [
            ShockRule("FX:USD", pct),
            ShockRule("FX:EURUSD", -pct),
            ShockRule("FX:GBPUSD", -pct),
            ShockRule("FX:AUDUSD", -pct),
            ShockRule("FX:NZDUSD", -pct),
        ]
    if t == "equity_vol":
        prefixes = tuple(
            f"VOL:{u}:"
            for u in sorted(
                {
                    f.split(":")[1]
                    for f in base.factors_with_prefix("VOL:")
                    if not f.split(":")[1].endswith("USD") and not f.split(":")[1].startswith("USD")
                }
            )
        )
    elif t == "fx_vol":
        prefixes = tuple(
            f"VOL:{u}:"
            for u in sorted(
                {
                    f.split(":")[1]
                    for f in base.factors_with_prefix("VOL:")
                    if f.split(":")[1].endswith("USD") or f.split(":")[1].startswith("USD")
                }
            )
        )
    elif t in TARGETS:
        prefixes = TARGETS[t]
    elif ":" in shock.target:
        prefixes = (shock.target,)  # raw factor id or prefix
    else:
        raise ValueError(
            f"unknown target {shock.target!r}; use one of {', '.join(TARGETS)} or a factor prefix"
        )
    out: list[ShockRule] = []
    for p in prefixes:
        if shock.unit == "vol_points" or (p.startswith("VOL:") and shock.unit == "pct"):
            out.append(
                ShockRule(p, vol_points=shock.size / 100.0 if shock.unit == "pct" else shock.size / 100.0)
            )
        elif shock.unit == "bp":
            size = shock.size / 1e4 if p.startswith("IR:") else shock.size  # CDS spreads are quoted in bp
            out.append(ShockRule(p, size, tenor_filter=shock.tenors))
        else:
            out.append(ShockRule(p, shock.size / 100.0, tenor_filter=shock.tenors))
    return out


@lru_cache(maxsize=4)
def _portfolio(db_path: str, run_id: str) -> tuple[Portfolio, pd.DataFrame]:
    with DuckDBRepository(db_path, read_only=True) as repo:
        run = repo.load_run(run_id)
        snap = repo.load_portfolio_snapshot(run.portfolio_snapshot_id)
        market = repo.load_run_market(run.run_id)
        universe = {f.factor_id: f for f in repo.load_risk_factors()}
        val = repo.load_run_frame(run_id, "valuation")
        pf = Portfolio(snap.trades, market, run.reporting_currency, universe=universe)
    return pf, val


def what_if(
    db_path: str, run_id: str, shocks: list[Shock], by: str = "asset_class", top: int = 8
) -> dict[str, Any]:
    pf, val = _portfolio(db_path, run_id)
    rules: list[ShockRule] = []
    for s in shocks:
        rules += _rules(s, pf.base)
    name = "; ".join(
        f"{s.target} {s.size:+g}{'%' if s.unit == 'pct' else (' bp' if s.unit == 'bp' else ' vol pts')}"
        for s in shocks
    )
    sc = StressScenario("whatif", name, "Analyst what-if", "HYPOTHETICAL", tuple(rules))
    res = run_stress(pf, [sc])[0]
    keys = val.set_index("trade_id")
    by_group = res.by(val, by)
    contrib = res.pnl.sort_values().head(top)
    return {
        "run_id": run_id,
        "scenario": name,
        "factors_shocked": len(res.shocks),
        "total_pnl": res.total,
        "by": {str(k): float(v) for k, v in by_group.items()},
        "worst_trades": [
            {
                "trade_id": tid,
                "pnl": float(v),
                "desk_id": keys.at[tid, "desk_id"],
                "product_type": keys.at[tid, "product_type"],
            }
            for tid, v in contrib.items()
        ],
        "note": "Instantaneous shock, full revaluation, no rebalancing (MR-005).",
    }
