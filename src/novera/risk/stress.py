"""Stress testing: a library of hypothetical scenarios plus stylised historical episodes,
all run by full revaluation. Methodology record MR-005."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from novera.market_data.history import MarketHistory
from novera.risk.revaluation import Portfolio
from novera.risk.scenarios import episode_shocks, shocks_for_prefix

MODEL_VERSION = "1.1.0"  # 1.1.0: catalogue and Stress library page (MR-005); scenario results unchanged


@dataclass(frozen=True)
class ShockRule:
    """Shock every factor matching ``prefix``. Sizes are in shock units: decimal for zero
    rates (0.01 = +100bp), bp for CDS spreads, fraction for prices, and for vols the
    ``vol_points`` field is used instead (absolute vol points)."""

    prefix: str
    size: float = 0.0
    vol_points: float | None = None
    tenor_filter: tuple[str, ...] | None = None  # for curve nodes, e.g. ("1M", "3M", "6M", "1Y", "2Y")


@dataclass(frozen=True)
class StressScenario:
    scenario_id: str
    name: str
    description: str
    kind: str  # HYPOTHETICAL or HISTORICAL
    rules: tuple[ShockRule, ...] = ()
    episode: tuple[date, date] | None = None  # HISTORICAL: start and end dates in history


@dataclass
class StressResult:
    scenario: StressScenario
    shocks: dict[str, float]
    pnl: pd.Series  # per trade, reporting currency

    @property
    def total(self) -> float:
        return float(self.pnl.sum())

    def by(self, valuation: pd.DataFrame, column: str) -> pd.Series:
        keys = valuation[["trade_id", column]].drop_duplicates("trade_id").set_index("trade_id")[column]
        return self.pnl.groupby(keys.reindex(self.pnl.index)).sum().sort_values()


SHORT = ("1M", "3M", "6M", "1Y", "2Y")
LONG = ("7Y", "10Y", "15Y", "20Y", "30Y")

USD_PAIRS_USD_BASE = "USD"  # helper marker


def _usd_up(pct: float) -> list[ShockRule]:
    """Dollar strengthens by ``pct``: USD/xxx pairs rise, xxx/USD pairs fall."""
    return [
        ShockRule("FX:USD", pct),
        ShockRule("FX:EURUSD", -pct),
        ShockRule("FX:GBPUSD", -pct),
        ShockRule("FX:AUDUSD", -pct),
        ShockRule("FX:NZDUSD", -pct),
    ]


def _all_equity(pct: float) -> list[ShockRule]:
    return [ShockRule("EQ:", pct), ShockRule("EQIDX:", pct)]


def _all_vol(points: float) -> list[ShockRule]:
    return [ShockRule("VOL:", vol_points=points)]


HYPOTHETICAL_LIBRARY: tuple[StressScenario, ...] = (
    StressScenario(
        "usd_rates_up_100",
        "USD rates +100bp",
        "Parallel +100bp on the USD curve",
        "HYPOTHETICAL",
        (ShockRule("IR:USD:", 0.01),),
    ),
    StressScenario(
        "global_rates_up_100",
        "Global rates +100bp",
        "Parallel +100bp on every curve",
        "HYPOTHETICAL",
        (ShockRule("IR:", 0.01),),
    ),
    StressScenario(
        "usd_steepener",
        "USD curve steepening",
        "Short end -50bp, long end +50bp",
        "HYPOTHETICAL",
        (ShockRule("IR:USD:", -0.005, tenor_filter=SHORT), ShockRule("IR:USD:", 0.005, tenor_filter=LONG)),
    ),
    StressScenario(
        "usd_flattener",
        "USD curve flattening",
        "Short end +75bp, long end -25bp",
        "HYPOTHETICAL",
        (ShockRule("IR:USD:", 0.0075, tenor_filter=SHORT), ShockRule("IR:USD:", -0.0025, tenor_filter=LONG)),
    ),
    StressScenario(
        "eurusd_down_10",
        "EUR/USD -10%",
        "Euro devaluation against the dollar",
        "HYPOTHETICAL",
        (ShockRule("FX:EURUSD", -0.10), ShockRule("FX:EURGBP", -0.05)),
    ),
    StressScenario(
        "equity_crash_20_vol_15",
        "Equity -20%, vol +15",
        "Global equity crash with vol spike",
        "HYPOTHETICAL",
        tuple(_all_equity(-0.20) + _all_vol(0.15)),
    ),
    StressScenario(
        "credit_wider_150",
        "Credit spreads +150bp",
        "IG and HY indices widen 150bp",
        "HYPOTHETICAL",
        (ShockRule("CDS:", 150.0),),
    ),
    StressScenario(
        "oil_down_30",
        "Oil -30%",
        "Brent and WTI curves fall 30%",
        "HYPOTHETICAL",
        (ShockRule("CMD:BRENT:", -0.30), ShockRule("CMD:WTI:", -0.30), ShockRule("CMD:NATGAS:", -0.15)),
    ),
    StressScenario(
        "btc_down_50",
        "BTC -50%",
        "Digital assets halve",
        "HYPOTHETICAL",
        (ShockRule("CRYPTO:BTC", -0.50), ShockRule("CRYPTO:ETH", -0.55)),
    ),
    StressScenario(
        "vol_shock_10",
        "Volatility +10 points",
        "All implied vols up 10 vol points",
        "HYPOTHETICAL",
        tuple(_all_vol(0.10)),
    ),
    StressScenario(
        "global_risk_off",
        "Global risk-off",
        "Equity -20%, oil -25%, BTC -40%, USD +5%, credit +150bp, UST 10Y -50bp, vol +15",
        "HYPOTHETICAL",
        tuple(
            _all_equity(-0.20)
            + [
                ShockRule("CMD:BRENT:", -0.25),
                ShockRule("CMD:WTI:", -0.25),
                ShockRule("CRYPTO:BTC", -0.40),
                ShockRule("CRYPTO:ETH", -0.45),
                ShockRule("CDS:", 150.0),
                ShockRule("IR:USD:", -0.005),
                ShockRule("IR:EUR:", -0.003),
                ShockRule("IR:GBP:", -0.004),
                ShockRule("CMD:GOLD:", 0.05),
            ]
            + _usd_up(0.05)
            + _all_vol(0.15)
        ),
    ),
    StressScenario(
        "inflation_shock",
        "Inflation shock",
        "USD +150bp front, +90bp long; EUR/GBP +100bp; equity -12%; credit +60bp; oil +15%",
        "HYPOTHETICAL",
        tuple(
            [
                ShockRule("IR:USD:", 0.015, tenor_filter=SHORT),
                ShockRule("IR:USD:", 0.009, tenor_filter=LONG),
                ShockRule("IR:USD:", 0.012, tenor_filter=("3Y", "5Y")),
                ShockRule("IR:EUR:", 0.01),
                ShockRule("IR:GBP:", 0.01),
                ShockRule("CDS:", 60.0),
                ShockRule("CMD:BRENT:", 0.15),
                ShockRule("CMD:WTI:", 0.15),
            ]
            + _all_equity(-0.12)
            + _all_vol(0.05)
        ),
    ),
)


def build_shocks(
    scenario: StressScenario, pf: Portfolio, history: MarketHistory | None = None
) -> dict[str, float]:
    base = pf.base
    if scenario.kind == "HISTORICAL":
        if history is None or scenario.episode is None:
            raise ValueError("historical scenario needs history and an episode window")
        return episode_shocks(history, *scenario.episode, universe=pf.universe)
    shocks: dict[str, float] = {}
    for rule in scenario.rules:
        if rule.prefix.startswith("VOL:") and rule.vol_points is not None:
            for f in base.factors_with_prefix(rule.prefix):
                shocks[f] = shocks.get(f, 0.0) + rule.vol_points / base.values[f]
            continue
        if rule.prefix == "FX:USD":  # every pair quoted USD/xxx
            for f in base.factors_with_prefix("FX:USD"):
                shocks[f] = shocks.get(f, 0.0) + rule.size
            continue
        for f, s in shocks_for_prefix(base, rule.prefix, rule.size).items():
            if rule.tenor_filter and f.split(":")[-1] not in rule.tenor_filter:
                continue
            shocks[f] = shocks.get(f, 0.0) + s
    return shocks


def run_stress(
    pf: Portfolio,
    scenarios: tuple[StressScenario, ...] | list[StressScenario],
    history: MarketHistory | None = None,
) -> list[StressResult]:
    out: list[StressResult] = []
    for sc in scenarios:
        shocks = build_shocks(sc, pf, history)
        pnl = pf.pnl_under_shocks(shocks)
        series = pd.Series({tid: pnl.get(tid, 0.0) for tid in pf.priced_ids}, name=sc.scenario_id)
        out.append(StressResult(sc, shocks, series))
    return out


def stress_table(
    results: list[StressResult], valuation: pd.DataFrame, column: str = "asset_class"
) -> pd.DataFrame:
    rows = []
    for r in results:
        by = r.by(valuation, column)
        rows.append(
            {
                "scenario_id": r.scenario.scenario_id,
                "name": r.scenario.name,
                "kind": r.scenario.kind,
                "total": r.total,
                **{f"{column}:{k}": v for k, v in by.items()},
            }
        )
    return pd.DataFrame(rows).sort_values("total")


def historical_episodes_from_simulation(history: MarketHistory, episodes, end: date) -> list[StressScenario]:
    """Turn the simulator's stylised episodes into HISTORICAL stress scenarios."""
    dates = history.dates
    n = len(dates)
    out = []
    for ep in episodes:
        s = max(n - ep.start_offset_days, 0)
        e = min(s + ep.length, n) - 1
        out.append(
            StressScenario(
                ep.name,
                ep.name.replace("_", " ").title(),
                f"Observed moves from {dates[s]} to {dates[e]} in the simulated history",
                "HISTORICAL",
                episode=(dates[s], dates[e]),
            )
        )
    return out


_ = field  # keep dataclasses.field import for future optional fields
