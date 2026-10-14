"""Floating-rate fixings as a trade system holds them, taken from the simulated history.

Each swap carries the fixing of the floating period running on the portfolio's business date
(decision 34.4), so its pricer never has to guess interest that is already known. A term index
(EURIBOR, TIIE, JIBAR) fixes once at the period start, read off that day's projection curve; an
overnight index (SOFR, SONIA, TONA, CDI) is the overnight rate compounded from the period start
to the business date, the shortest node of each day's discount curve standing for the overnight
fixing. Methodology record PR-002.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from novera.domain.instruments import FloatingFixing, InterestRateSwap
from novera.domain.trades import Trade
from novera.market_data.history import MarketHistory
from novera.market_data.risk_factors import TENOR_BASIS_INDICES
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing.base import year_fraction_act365
from novera.pricing.schedule import remaining_periods, year_fraction

OVERNIGHT_NODE = "1M"  # shortest node of every simulated discount curve


def running_period(swap: InterestRateSwap, on: date) -> tuple[date, date] | None:
    """The floating period that started before ``on`` and pays after it, if any."""
    for start, end in remaining_periods(swap.effective_date, swap.maturity_date, swap.float_frequency, on):
        if start < on:
            return start, end
    return None


def attach_fixings(trades: list[Trade], history: MarketHistory | None, business_date: date) -> list[Trade]:
    """Every swap with the fixing of its running period on ``business_date``; a swap with no
    running period, or whose period starts before the stored history, carries none (the pricer
    then estimates the elapsed accrual)."""
    if history is None:
        return trades
    snapshots: dict[date, MarketSnapshot] = {}
    first = history.dates[0] if history.dates else business_date
    out: list[Trade] = []
    for t in trades:
        ins = t.instrument
        if not isinstance(ins, InterestRateSwap):
            out.append(t)
            continue
        period = running_period(ins, business_date)
        fixing = None
        if period is not None and period[0] >= first:
            start, end = period
            if ins.float_index in TENOR_BASIS_INDICES:
                snap = snapshots.get(start) or snapshots.setdefault(start, history.snapshot_at(start))
                curve = snap.projection_curve(ins.float_index, ins.currency)
                tau = year_fraction(start, end, ins.float_day_count)
                rate = (1.0 / float(curve.df(year_fraction_act365(start, end))) - 1.0) / tau
                fixing = FloatingFixing(period_start=start, rate=rate, through=start)
            else:
                fid = f"IR:{ins.currency}:{OVERNIGHT_NODE}"
                if fid in history.wide.columns:
                    s = history.wide[fid]
                    days = s[(s.index >= start) & (s.index < business_date)].dropna()
                    if len(days):
                        fixing = FloatingFixing(
                            period_start=start, rate=float(np.mean(days.to_numpy())), through=business_date
                        )
        if fixing != ins.fixing:
            t = t.model_copy(update={"instrument": ins.model_copy(update={"fixing": fixing})})
        out.append(t)
    return out
