"""Limit amounts from a target utilisation (decision round 40, record MR-017).

``amount = measure / target``, the measure being what limit monitoring reports as ``current`` for
the limit on a reference run. Targets come by limit type (``TARGETS``), except for the limits that
carry the demo's story (``STORY_TARGETS``: the planted breaches and the ones that cross later),
which are set to land exactly on their designed utilisation whatever the models compute.
An ordinary limit moves towards its target only within ``APPETITE_BAND`` of its designed amount,
the risk appetite (decisions 40.6, 40.7): ``max(appetite, usage / target)``, capped at double the
appetite. A quiet desk keeps its designed limit; only a desk running hot earns headroom.
Amounts are rounded up to a
quarter of their leading digit (1.23m to 1.25m), so a limit never opens above its target; story
limits are exact and outside the band. A limit with nothing in scope keeps its amount; a limit in
breach on the reference run is never touched (that is an increase request, MR-008).

Calibration is a deliberate, named act: seeding when a firm is created, or a recalibration
proposed as DRAFT versions after a methodology change. It never runs from the EOD or the
scheduler, so a change in positions never moves a limit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from novera.domain.limits import Limit, LimitType

MODEL_VERSION = "1.0.0"

TARGETS: dict[LimitType, float] = {
    LimitType.VAR: 0.75,
    LimitType.EXPECTED_SHORTFALL: 0.75,
    LimitType.STRESSED_VAR: 0.75,
    LimitType.DV01: 0.70,
    LimitType.CS01: 0.70,
    LimitType.FX_DELTA: 0.70,
    LimitType.EQUITY_DELTA: 0.70,
    LimitType.COMMODITY_DELTA: 0.70,
    LimitType.VEGA: 0.70,
    LimitType.GAMMA: 0.70,
    LimitType.CONCENTRATION: 0.70,
    LimitType.STRESS_LOSS: 0.60,
    LimitType.COUNTERPARTY_EXPOSURE: 0.60,
    LimitType.LEVERAGE: 0.70,
    LimitType.MARGIN_USAGE: 0.70,
    LimitType.PB_CONCENTRATION: 0.70,
}
DEFAULT_TARGET = 0.70
# Calibrated amounts stay within this multiple of the designed amount (decision 40.6).
APPETITE_BAND: tuple[float, float] = (1.0, 2.0)  # never below the appetite (decision 40.7)

# Day-1 utilisation of the limits that carry the demo's story (decision 40, values of the demo
# before calibration): the four planted bank breaches and TransAtlantic Air's, the three that
# cross on later days, and the fund's three.
STORY_TARGETS: dict[str, float] = {
    "USD_RATES_DV01_USD_10Y": 1.99,
    "DIGITAL_STRESS": 1.65,
    "USD_RATES_CONC_10Y": 1.50,
    "ENERGY_CONC_BRENT": 1.38,
    "CPTY_CORP_AIR": 1.08,
    "CPTY_CORP_ENERGY": 0.98,
    "SINGLE_NAME_EQ_VEGA": 0.94,
    "EUR_RATES_DV01_EUR": 0.85,
    "EQ_LS_US_CONC_NVDA": 2.52,
    "FUND_PB_CONCENTRATION": 1.61,
    "EQ_LS_US_VAR": 0.98,
}


def target_for(limit: Limit) -> tuple[float, bool]:
    """(target utilisation, whether it is a story target)."""
    if limit.limit_id in STORY_TARGETS:
        return STORY_TARGETS[limit.limit_id], True
    return TARGETS.get(limit.limit_type, DEFAULT_TARGET), False


def round_amount(x: float) -> float:
    """Up to the next quarter of the leading digit: 1,230,417 -> 1,250,000; 0.587 -> 0.6."""
    if x <= 0:
        return x
    step = 10 ** math.floor(math.log10(x)) / 4
    return math.ceil(x / step - 1e-9) * step


@dataclass(frozen=True)
class Proposal:
    limit: Limit
    current: float
    target: float
    story: bool
    new_amount: float | None  # None: left as it is
    reason: str

    @property
    def new_utilisation(self) -> float | None:
        return self.current / self.new_amount if self.new_amount else None


def propose(limits: list[Limit], measured: pd.DataFrame, guard_breaches: bool = True) -> list[Proposal]:
    """One proposal per limit from a reference run's limit table (columns limit_id, current,
    status). ``guard_breaches`` leaves a limit in breach alone; seeding turns it off because the
    provisional amounts it replaces mean nothing."""
    by_id = measured.set_index("limit_id") if len(measured) else pd.DataFrame()
    out = []
    for lim in limits:
        target, story = target_for(lim)
        if lim.limit_id not in by_id.index:
            out.append(Proposal(lim, 0.0, target, story, None, "not measured on the reference run"))
            continue
        row = by_id.loc[lim.limit_id]
        current = float(abs(row["current"])) if pd.notna(row["current"]) else 0.0
        if guard_breaches and str(row.get("status")) == "BREACH":
            out.append(
                Proposal(
                    lim, current, target, story, None, "in breach: an increase request, not a recalibration"
                )
            )
            continue
        if current <= 0:
            out.append(Proposal(lim, current, target, story, None, "nothing in scope on the reference run"))
            continue
        if story:
            out.append(Proposal(lim, current, target, story, current / target, "story target"))
            continue
        appetite = lim.appetite or lim.amount
        lo, hi = APPETITE_BAND[0] * appetite, APPETITE_BAND[1] * appetite
        raw = current / target
        reason = "type target"
        if raw < lo:
            raw, reason = (
                lo,
                "kept at its appetite"
                if APPETITE_BAND[0] == 1.0
                else f"floored at {APPETITE_BAND[0]:.0%} of appetite",
            )
        elif raw > hi:
            raw, reason = hi, f"capped at {APPETITE_BAND[1]:.0%} of appetite"
        out.append(Proposal(lim, current, target, story, round_amount(raw), reason))
    return out
