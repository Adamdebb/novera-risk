"""Named historical crisis windows, usable as HISTORICAL stress scenarios whenever the
stored history covers them (i.e. after `novera fetch`)."""

from __future__ import annotations

from datetime import date

from novera.market_data.history import MarketHistory
from novera.risk.stress import StressScenario

CRISES: tuple[tuple[str, str, date, date], ...] = (
    ("gfc_2008", "2008 Global financial crisis", date(2008, 9, 12), date(2008, 10, 27)),
    ("euro_2011", "2011 Euro sovereign crisis", date(2011, 7, 22), date(2011, 9, 22)),
    ("china_2015", "2015 China devaluation shock", date(2015, 8, 10), date(2015, 8, 25)),
    ("brexit_2016", "2016 Brexit vote", date(2016, 6, 23), date(2016, 6, 27)),
    ("covid_2020", "2020 COVID crash", date(2020, 2, 19), date(2020, 3, 23)),
    ("rates_2022", "2022 inflation and rates shock", date(2022, 1, 3), date(2022, 6, 14)),
    ("banks_2023", "2023 regional banking stress", date(2023, 3, 8), date(2023, 3, 17)),
)


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
