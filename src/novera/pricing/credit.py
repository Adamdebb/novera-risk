"""CDS index with a flat hazard rate from the credit triangle. Methodology record PR-008."""

from __future__ import annotations

import math
from datetime import date

from novera.domain.enums import BuySell, DayCount
from novera.domain.instruments import CDSIndex
from novera.domain.trades import Trade
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing.base import PricingResult, year_fraction_act365
from novera.pricing.schedule import remaining_periods, year_fraction

CDS_MODEL_VERSION = "1.0.0"


def cds_legs(
    notional: float,
    coupon: float,
    spread_bp: float,
    recovery: float,
    periods: list[tuple[date, date]],
    as_of: date,
    df,
) -> tuple[float, float, float]:
    """(protection_pv, premium_pv, risky_annuity) per unit notional scaled by ``notional``.
    Flat hazard λ = s / (1 - R); survival Q(t) = exp(-λ t); default assumed at period midpoint."""
    lam = (spread_bp / 1e4) / max(1.0 - recovery, 1e-6)
    protection = premium = annuity = 0.0
    q_prev = 1.0
    for start, end in periods:
        t1 = max(year_fraction_act365(as_of, start), 0.0)
        t2 = year_fraction_act365(as_of, end)
        tau = year_fraction(max(start, as_of), end, DayCount.ACT_360)
        q = math.exp(-lam * t2)
        df_end = float(df(t2))
        df_mid = float(df(0.5 * (t1 + t2)))
        dq = q_prev - q
        protection += (1.0 - recovery) * df_mid * dq
        annuity += tau * (df_end * q + 0.5 * df_mid * dq)  # accrual on default at midpoint
        q_prev = q
    premium = coupon * annuity
    return notional * protection, notional * premium, annuity


def price_cds_index(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    ins = trade.instrument
    assert isinstance(ins, CDSIndex)
    if ins.maturity_date <= as_of:
        return PricingResult(
            trade.trade_id, ins.currency, 0.0, "cds_flat_hazard", CDS_MODEL_VERSION, note="matured"
        )
    curve = market.zero_curve(ins.currency)
    spread = market.cds_spread_bp(ins.index_family)
    periods = remaining_periods(as_of.replace(day=1), ins.maturity_date, ins.premium_frequency, as_of)
    prot, prem, annuity = cds_legs(
        trade.quantity, ins.fixed_coupon, spread, ins.recovery_rate, periods, as_of, curve.df
    )
    sign = 1.0 if trade.direction is BuySell.BUY else -1.0  # BUY = buy protection
    pv = sign * (prot - prem)
    return PricingResult(
        trade.trade_id,
        ins.currency,
        pv,
        "cds_flat_hazard",
        CDS_MODEL_VERSION,
        details={
            "spread_bp": spread,
            "hazard_rate": (spread / 1e4) / (1 - ins.recovery_rate),
            "protection_leg_pv": sign * prot,
            "premium_leg_pv": -sign * prem,
            "risky_annuity": annuity,
            "years_to_maturity": year_fraction_act365(as_of, ins.maturity_date),
        },
    )
