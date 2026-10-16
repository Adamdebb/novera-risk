"""Named historical crisis windows, usable as HISTORICAL stress scenarios whenever the
stored history covers them (i.e. after `novera fetch`)."""

from __future__ import annotations

from datetime import date

from novera.market_data.history import MarketHistory
from novera.risk.crisis_tables import CRISIS_TABLES
from novera.risk.stress import StressScenario

CRISES: tuple[tuple[str, str, date, date], ...] = tuple(
    (c.scenario_id, c.name, c.start, c.end) for c in CRISIS_TABLES
)
"""The named real crises: 14 windows since 2008 (round 47); each also has a calibrated replay
drafted in ``novera.risk.crisis_tables``."""


def named_crisis_scenarios(history: MarketHistory) -> list[StressScenario]:
    """Crises fully inside the stored history, as historical stress scenarios."""
    dates = history.dates
    if not dates:
        return []
    first, last = dates[0], dates[-1]
    out = []
    for sid, name, start, end in CRISES:
        if first <= start and end <= last:
            s = next(d for d in dates if d >= start)
            e = max(d for d in dates if d <= end)
            out.append(StressScenario(sid, name, f"Observed moves {s} to {e}", "HISTORICAL", episode=(s, e)))
    return out
