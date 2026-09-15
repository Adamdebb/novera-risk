"""The stress library as a catalogue (MR-005): every scenario by category with the shocks it
applies, whether it runs in the daily EOD, and, for historical windows, the realised moves of
headline factors in the stored history. Serves the dashboard's "Stress library" page and
``GET /reference/stress-library``. Nothing here changes what the EOD run computes."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from novera.market_data.crises import CRISES
from novera.market_data.history import MarketHistory
from novera.market_data.risk_factors import RiskFactor
from novera.risk.scenarios import episode_shocks
from novera.risk.stress import (
    HYPOTHETICAL_LIBRARY,
    ShockRule,
    StressScenario,
    historical_episodes_from_simulation,
)
from novera.simulation.market_data import DEFAULT_EPISODES

RECORD = "MR-005"

HEADLINE_FACTORS: tuple[tuple[str, str], ...] = (
    ("EQIDX:SPX", "S&P 500"),
    ("EQIDX:SX5E", "Euro Stoxx 50"),
    ("IR:USD:2Y", "USD 2Y zero"),
    ("IR:USD:10Y", "USD 10Y zero"),
    ("IR:EUR:10Y", "EUR 10Y zero"),
    ("FX:EURUSD", "EUR/USD"),
    ("FX:USDJPY", "USD/JPY"),
    ("CDS:CDX.NA.IG", "CDX IG"),
    ("CDS:CDX.NA.HY", "CDX HY"),
    ("VOL:SPX:3M:1.00", "SPX 3M ATM vol"),
    ("SWVOL:USD:1Y:10Y", "USD 1Y10Y swaption vol"),
    ("CMD:BRENT:1M", "Brent front"),
    ("CMD:GOLD:1M", "Gold front"),
    ("CRYPTO:BTC", "Bitcoin"),
)
"""Factors whose realised move summarises a historical window on the page."""

CATEGORIES: tuple[dict[str, Any], ...] = (
    {
        "category": "HYPOTHETICAL",
        "title": "Hypothetical",
        "description": (
            "Rule-based instantaneous shocks on factor families with full revaluation, no "
            "liquidity horizon and no rebalancing. Sizes are assumptions in the range regulators "
            "and desks use; none is measured from a real market move."
        ),
        "in_daily_run": True,
    },
    {
        "category": "STYLISED_HISTORICAL",
        "title": "Stylised historical episodes",
        "description": (
            "The simulator's two synthetic crisis windows, replayed as the observed move of every "
            "factor between the window's first and last day. They carry the full cross-asset "
            "correlation of the synthetic history but are calibrated to look like a crisis, not "
            "to any real one (SIM-001)."
        ),
        "in_daily_run": True,
    },
    {
        "category": "NAMED_CRISIS",
        "title": "Named real crises",
        "description": (
            "Seven real windows (MD-001). A window can only be replayed as a stress when the "
            "stored history covers it, and the result is only a real replay when the factors on "
            "those dates were fetched from a real source. None runs in the daily EOD today."
        ),
        "in_daily_run": False,
    },
)

_FAMILY_LABELS: dict[str, str] = {
    "IR:": "every zero curve",
    "EQ:": "single-name equities",
    "EQIDX:": "equity indices",
    "CDS:": "credit spreads, indices and single names",
    "VOL:": "every vol surface",
    "FX:USD": "USD/xxx pairs (dollar up)",
    "CRYPTO:BTC": "Bitcoin",
    "CRYPTO:ETH": "Ether",
}


def _family_label(prefix: str) -> str:
    if prefix in _FAMILY_LABELS:
        return _FAMILY_LABELS[prefix]
    parts = prefix.rstrip(":").split(":")
    kind = parts[0]
    name = parts[1] if len(parts) > 1 else ""
    if kind == "IR":
        return f"{name} zero curve"
    if kind == "CMD":
        return f"{name.title()} curve"
    if kind == "FX":
        return f"{name[:3]}/{name[3:]}"
    if kind == "CRYPTO":
        return name
    return prefix


def describe_rule(rule: ShockRule) -> dict[str, Any]:
    """One hypothetical shock rule in the units a risk manager reads: bp for rates and
    spreads, vol points for surfaces, per cent for everything else."""
    prefix = rule.prefix
    tenors = ", ".join(rule.tenor_filter) if rule.tenor_filter else None
    if rule.vol_points is not None:
        size, unit = rule.vol_points * 100.0, "vol points"
        text = f"{size:+.0f} vol points on every node"
    elif prefix.startswith("IR:"):
        size, unit = rule.size * 1e4, "bp"
        text = f"{size:+.0f}bp" + (f" on {tenors}" if tenors else " parallel")
    elif prefix.startswith("CDS:"):
        size, unit = rule.size, "bp"
        text = f"{size:+.0f}bp"
    else:
        size, unit = rule.size * 100.0, "%"
        text = f"{size:+.0f}%"
    return {
        "target": prefix,
        "family": _family_label(prefix),
        "size": float(size),
        "unit": unit,
        "tenors": tenors,
        "text": f"{_family_label(prefix)}: {text}",
        "kind": "RULE",
    }


def _realised(fid: str, shock: float) -> dict[str, Any]:
    label = dict(HEADLINE_FACTORS).get(fid, fid)
    if fid.startswith(("IR:", "CDS:", "SWRHO:")):
        size, unit = (shock * 1e4 if fid.startswith("IR:") else shock), "bp"
        text = f"{size:+.0f}bp"
    else:
        size, unit = shock * 100.0, "%"
        text = f"{size:+.1f}%"
    return {
        "target": fid,
        "family": label,
        "size": float(size),
        "unit": unit,
        "tenors": None,
        "text": f"{label}: {text}",
        "kind": "REALISED",
    }


def _window_shocks(
    history: MarketHistory, start: date, end: date, universe: dict[str, RiskFactor]
) -> list[dict]:
    try:
        shocks = episode_shocks(history, start, end, universe)
    except ValueError:
        return []
    return [_realised(fid, shocks[fid]) for fid, _ in HEADLINE_FACTORS if fid in shocks]


def _real_share(provenance: pd.DataFrame | None, start: date, end: date, n_factors: int) -> float:
    if provenance is None or provenance.empty or not n_factors:
        return 0.0
    first = pd.to_datetime(provenance["first_date"]).dt.date
    last = pd.to_datetime(provenance["last_date"]).dt.date
    covered = provenance[(first <= start) & (last >= end)]["factor_id"].nunique()
    return float(covered) / float(n_factors)


def _scenario_row(
    sc: StressScenario, category: str, in_run: bool, status: str, shocks: list[dict], **extra
) -> dict:
    return {
        "scenario_id": sc.scenario_id,
        "name": sc.name,
        "category": category,
        "kind": sc.kind,
        "description": sc.description,
        "in_daily_run": in_run,
        "status": status,
        "window_start": str(sc.episode[0]) if sc.episode else None,
        "window_end": str(sc.episode[1]) if sc.episode else None,
        "shocks": shocks,
        "shock_count": len(shocks),
        **extra,
    }


def stress_library(
    history: MarketHistory | None,
    universe: list[RiskFactor],
    provenance: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """The library by category. ``history`` may hold only the headline factors; its dates
    decide which historical windows are covered."""
    by_id = {f.factor_id: f for f in universe}
    dates = history.dates if history is not None else []
    first, last = (dates[0], dates[-1]) if dates else (None, None)
    real_factors = (
        int(provenance["factor_id"].nunique()) if provenance is not None and not provenance.empty else 0
    )

    hypothetical = [
        _scenario_row(sc, "HYPOTHETICAL", True, "IN_RUN", [describe_rule(r) for r in sc.rules])
        for sc in HYPOTHETICAL_LIBRARY
    ]

    stylised = []
    if history is not None and dates:
        for sc in historical_episodes_from_simulation(history, DEFAULT_EPISODES, dates[-1]):
            assert sc.episode is not None
            stylised.append(
                _scenario_row(
                    sc, "STYLISED_HISTORICAL", True, "IN_RUN", _window_shocks(history, *sc.episode, by_id)
                )
            )

    crises = []
    for sid, name, start, end in CRISES:
        covered = first is not None and last is not None and first <= start and end <= last
        share = _real_share(provenance, start, end, len(universe)) if covered else 0.0
        if not covered:
            status, note = "NOT_COVERED", f"outside the stored history ({first} to {last})"
        elif share == 0.0:
            status, note = "SYNTHETIC_WINDOW", "dates covered, but every factor on them is synthetic"
        elif share < 1.0:
            status, note = "PARTLY_REAL", f"{share:.0%} of factors fetched from a real source on these dates"
        else:
            status, note = "REAL", "every factor fetched from a real source on these dates"
        sc = StressScenario(sid, name, f"Observed moves {start} to {end}", "HISTORICAL", episode=(start, end))
        shocks = _window_shocks(history, start, end, by_id) if covered and history is not None else []
        crises.append(_scenario_row(sc, "NAMED_CRISIS", False, status, shocks, real_share=share, note=note))

    rows = {"HYPOTHETICAL": hypothetical, "STYLISED_HISTORICAL": stylised, "NAMED_CRISIS": crises}
    return {
        "record": RECORD,
        "history": {
            "start": str(first) if first else None,
            "end": str(last) if last else None,
            "days": len(dates),
            "factors": len(universe),
            "real_factors": real_factors,
        },
        "categories": [
            {**c, "scenarios": rows[c["category"]], "count": len(rows[c["category"]])} for c in CATEGORIES
        ],
        "summary": {
            "scenarios": sum(len(v) for v in rows.values()),
            "in_daily_run": sum(1 for v in rows.values() for r in v if r["in_daily_run"]),
            "replayable_real": sum(1 for r in crises if r["status"] == "REAL"),
        },
    }
