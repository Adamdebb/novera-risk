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
from novera.risk.scenario_covariance import HEADLINE_FACTORS
from novera.risk.scenarios import episode_shocks
from novera.risk.stress import ShockRule, StressScenario
from novera.risk.stress_definitions import built_in_definitions, category_of, episode_windows

RECORD = "MR-005"

# HEADLINE_FACTORS: the factors whose realised move summarises a historical window on the page;
# the same basket is the risk ruler's coordinates (MR-018).


CATEGORIES: tuple[dict[str, Any], ...] = (
    {
        "category": "NAMED_CRISIS",
        "title": "Historical real crises",
        "description": (
            "Fourteen real crises since 2008, and any historical window the firm adds. A crisis "
            "replays as the observed move of every factor when the stored history holds it with "
            "real data; until then it runs as a calibrated replay once its headline moves are "
            "approved (MR-018): status CALIBRATED."
        ),
        "in_daily_run": False,
    },
    {
        "category": "FORWARD_LOOKING",
        "title": "Hypothetical forward-looking scenarios",
        "description": (
            "Stories of what could happen next, with a catalyst: the rules set the key moves and "
            "every other factor follows under stressed correlations (MR-018). Tagged MACRO or "
            "GEOPOLITICAL; written and reviewed by the risk function."
        ),
        "in_daily_run": True,
    },
    {
        "category": "MULTI_FACTOR",
        "title": "Multi-factor scenarios",
        "description": (
            "One market moves by a set amount and every other market follows under stressed "
            "correlations (conditional stress testing, MR-018). Generic and stable: the set the "
            "stress-loss limits are built to read."
        ),
        "in_daily_run": True,
    },
    {
        "category": "SINGLE_FACTOR",
        "title": "Single-factor scenarios",
        "description": (
            "Standalone shocks: one factor, curve or basis moves by a set amount and every other "
            "factor stays where it is, so the result isolates the book's exposure to that move."
        ),
        "in_daily_run": True,
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
    if kind == "XCCY":
        return f"{name}/USD cross-currency basis" if name else "every cross-currency basis"
    if kind == "IRBASIS":
        return f"{name} {parts[2]} tenor basis" if len(parts) > 2 else f"{name} tenor basis"
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
    elif prefix.startswith(("IR:", "IRBASIS:", "XCCY:")):
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
    if fid.startswith(("IR:", "IRBASIS:", "XCCY:", "CDS:", "SWRHO:")):
        size, unit = (shock * 1e4 if fid.startswith(("IR:", "IRBASIS:", "XCCY:")) else shock), "bp"
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


def _governance(row: dict[str, Any] | None) -> dict[str, Any]:
    """Goal, version and review facts of a stored definition (OPS-005) for a library row."""
    if row is None:
        return {}
    d = row["definition"]
    due = row.get("review_due")
    due_s = due.isoformat() if isinstance(due, date) else due
    return {
        "goal": d.goal,
        "version": d.version,
        "source": d.source,
        "tags": list(d.tags),
        "severity": d.severity,
        "enabled": d.enabled,
        "review_due": due_s,
        "consistent": d.expand,
    }


def describe_pca(rule: dict[str, Any]) -> dict[str, Any]:
    """A PCA shape rule (MR-018) as a library row."""
    names = {1: "level", 2: "slope", 3: "curvature"}
    comp = int(rule["component"])
    sig = float(rule["sigmas"])
    return {
        "target": rule["pca"],
        "family": _family_label(rule["pca"]),
        "size": sig,
        "unit": f"σ of PC{comp}",
        "tenors": None,
        "text": f"{_family_label(rule['pca'])}: {sig:+g}σ of its {names.get(comp, f'PC{comp}')} component",
        "kind": "PCA",
    }


def stress_library(
    history: MarketHistory | None,
    universe: list[RiskFactor],
    provenance: pd.DataFrame | None = None,
    episodes: dict[str, tuple[date, date]] | None = None,
    definitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The library by category. ``history`` may hold only the headline factors; its dates
    decide which historical windows are covered. ``definitions`` are the approved versions of
    the stored library (``stress_setup.active``); without them the built-ins are listed."""
    by_id = {f.factor_id: f for f in universe}
    dates = history.dates if history is not None else []
    first, last = (dates[0], dates[-1]) if dates else (None, None)
    real_factors = (
        int(provenance["factor_id"].nunique()) if provenance is not None and not provenance.empty else 0
    )

    if definitions is None:
        windows = episode_windows(history, episodes) if history is not None else {}
        definitions = [{"definition": d, "review_due": None} for d in built_in_definitions(list(windows))]
    by_cat: dict[str, list[dict[str, Any]]] = {c["category"]: [] for c in CATEGORIES}
    replays: list[dict[str, Any]] = []
    windows = episode_windows(history, episodes) if history is not None and dates else {}
    calibrated: dict[str, dict[str, Any]] = {}
    for row in definitions:
        d = row["definition"]
        status = "IN_RUN" if d.enabled else "DISABLED"
        gov = _governance(row)
        if d.kind == "HYPOTHETICAL" and d.source == "CALIBRATED":
            calibrated[d.scenario_id] = row  # shown with its named crisis below
            continue
        if d.kind == "HYPOTHETICAL":
            sc = d.to_scenario()
            shocks = [describe_rule(r) for r in sc.rules] + [describe_pca(r) for r in d.pca_rules()]
            cat = category_of(d)
            by_cat[cat].append(_scenario_row(sc, cat, d.enabled, status, shocks, **gov))
            continue
        w = windows.get(d.episode_id) if d.episode_id else None
        if w is None and d.window_start and d.window_end:
            w = (date.fromisoformat(d.window_start), date.fromisoformat(d.window_end))
        if w is None or history is None:
            continue
        sc = d.to_scenario(w)
        replays.append(  # a historical window the firm added: listed with the real crises
            _scenario_row(sc, "NAMED_CRISIS", d.enabled, status, _window_shocks(history, *w, by_id), **gov)
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
        in_run = False
        extra: dict[str, Any] = {}
        cal = calibrated.pop(sid, None)
        if cal is not None and status != "REAL":
            d = cal["definition"]
            in_run = d.enabled
            status = "CALIBRATED" if d.enabled else "DISABLED"
            note = (
                "calibrated replay: approved headline moves, every other factor with stressed correlations "
                f"(version {d.version}); {note}"
            )
            shocks = [describe_rule(r) for r in d.shock_rules()]
            extra = _governance(cal)
        crises.append(
            _scenario_row(sc, "NAMED_CRISIS", in_run, status, shocks, real_share=share, note=note, **extra)
        )

    rows = {**by_cat, "NAMED_CRISIS": crises + replays}
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
