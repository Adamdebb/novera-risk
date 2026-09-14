"""Payment schedules and day-count conventions.

Schedules are generated backwards from maturity with no calendar adjustment (the
simulator has no holiday calendars). Day counts implement the standard formulas; ACT/ACT
is approximated as ACT/365.25 and documented as such in the methodology records.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache

from dateutil.relativedelta import relativedelta

from novera.domain.enums import DayCount, Frequency

MONTHS: dict[Frequency, int] = {
    Frequency.ANNUAL: 12, Frequency.SEMI_ANNUAL: 6, Frequency.QUARTERLY: 3, Frequency.MONTHLY: 1,
}


def year_fraction(start: date, end: date, day_count: DayCount) -> float:
    if end <= start:
        return 0.0
    if day_count is DayCount.ACT_360:
        return (end - start).days / 360.0
    if day_count is DayCount.ACT_365:
        return (end - start).days / 365.0
    if day_count is DayCount.ACT_ACT:
        return (end - start).days / 365.25
    # 30/360 US
    d1, d2 = min(start.day, 30), end.day
    if d1 == 30 and d2 == 31:
        d2 = 30
    return ((end.year - start.year) * 360 + (end.month - start.month) * 30 + (d2 - d1)) / 360.0


@lru_cache(maxsize=65536)
def schedule(effective: date, maturity: date, frequency: Frequency) -> list[date]:
    """Period boundaries [effective, ..., maturity], generated backwards from maturity.
    A short first stub absorbs any remainder. Cached: schedules are pure and reused across
    every reprice of the same instrument."""
    step = MONTHS[frequency]
    dates = [maturity]
    k = 1
    while True:
        d = maturity - relativedelta(months=step * k)
        if d <= effective:
            break
        dates.append(d)
        k += 1
    dates.append(effective)
    return sorted(set(dates))


def remaining_periods(
    effective: date, maturity: date, frequency: Frequency, as_of: date
) -> list[tuple[date, date]]:
    """(start, end) periods whose payment date is after as_of, including the current one."""
    bounds = schedule(effective, maturity, frequency)
    return [(s, e) for s, e in zip(bounds[:-1], bounds[1:], strict=True) if e > as_of]
