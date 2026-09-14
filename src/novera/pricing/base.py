"""Pricing contracts. A pricer maps (trade, market snapshot, as_of) to a PricingResult.

Pricers return present value and, where meaningful, cashflows and diagnostics. They never
compute sensitivities: those come from ``novera.risk`` by bumping the snapshot and
re-pricing, or from analytic hooks exposed in ``details``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from novera.domain.trades import Trade
from novera.market_data.snapshot import MarketSnapshot


@dataclass(frozen=True)
class Cashflow:
    pay_date: date
    amount: float  # in the instrument currency, signed from our perspective
    kind: str  # COUPON, PRINCIPAL, FIXED, FLOAT, PREMIUM, PROTECTION, SETTLEMENT


@dataclass(frozen=True)
class PricingResult:
    trade_id: str
    currency: str
    pv_local: float
    model: str
    model_version: str
    cashflows: tuple[Cashflow, ...] = ()
    details: dict[str, float] = field(default_factory=dict)
    note: str = ""  # e.g. "expired", "settled", "matured"


class Pricer(Protocol):
    def __call__(self, trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult: ...


class PricingError(RuntimeError):
    """Raised when a trade cannot be priced (missing data, unsupported terms)."""


def year_fraction_act365(start: date, end: date) -> float:
    return (end - start).days / 365.0
