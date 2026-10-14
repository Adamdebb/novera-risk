"""Government bonds and interest-rate swaps. Methodology records PR-001 and PR-002."""

from __future__ import annotations

import math
from datetime import date

from novera.domain.enums import Frequency, SwapSide
from novera.domain.instruments import GovernmentBond, InterestRateSwap
from novera.domain.trades import Trade
from novera.market_data.risk_factors import TENOR_BASIS_INDICES
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing.base import Cashflow, PricingResult, year_fraction_act365
from novera.pricing.schedule import remaining_periods, schedule, year_fraction

BOND_MODEL_VERSION = "1.0.0"
SWAP_MODEL_VERSION = "2.2.0"

# Static government-to-swap spread used to discount government bonds off the zero curve.
# In Phase 3 this becomes a market-data factor per currency.
GOVT_SPREAD: dict[str, float] = {
    "USD": 0.0005,
    "EUR": -0.0035,
    "GBP": 0.0005,
    "JPY": -0.0005,
    "MXN": 0.0020,
    "BRL": 0.0050,
    "ZAR": 0.0080,
}


def price_government_bond(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    bond = trade.instrument
    assert isinstance(bond, GovernmentBond)
    if bond.maturity_date <= as_of:
        return PricingResult(
            trade.trade_id, bond.currency, 0.0, "bond_discounting", BOND_MODEL_VERSION, note="matured"
        )
    curve = market.zero_curve(bond.currency).shifted(GOVT_SPREAD.get(bond.currency, 0.0))
    bounds = schedule(bond.issue_date, bond.maturity_date, bond.coupon_frequency)
    periods = list(zip(bounds[:-1], bounds[1:], strict=True))
    pv_per_100 = 0.0
    flows: list[Cashflow] = []
    for start, end in periods:
        if end <= as_of:
            continue
        coupon = 100.0 * bond.coupon_rate * year_fraction(start, end, bond.day_count)
        t = year_fraction_act365(as_of, end)
        pv_per_100 += coupon * float(curve.df(t))
        flows.append(Cashflow(end, coupon, "COUPON"))
    t_mat = year_fraction_act365(as_of, bond.maturity_date)
    pv_per_100 += 100.0 * float(curve.df(t_mat))
    flows.append(Cashflow(bond.maturity_date, 100.0, "PRINCIPAL"))
    # Accrued interest on the current period.
    cur = next(((s, e) for s, e in periods if s <= as_of < e), None)
    accrued = 0.0
    if cur is not None:
        s, e = cur
        full = 100.0 * bond.coupon_rate * year_fraction(s, e, bond.day_count)
        accrued = full * (as_of - s).days / (e - s).days
    scale = trade.signed_quantity / 100.0
    return PricingResult(
        trade_id=trade.trade_id,
        currency=bond.currency,
        pv_local=pv_per_100 * scale,
        model="bond_discounting",
        model_version=BOND_MODEL_VERSION,
        cashflows=tuple(Cashflow(f.pay_date, f.amount * scale, f.kind) for f in flows),
        details={
            "dirty_price": pv_per_100,
            "clean_price": pv_per_100 - accrued,
            "accrued": accrued,
            "years_to_maturity": t_mat,
        },
    )


def projection_details(market: MarketSnapshot, index: str) -> dict[str, float]:
    """``tenor_basis_applied`` for a term-rate index: 1 when its basis was in the snapshot, 0 when
    it was projected off the discount curve instead (a real-data snapshot without the basis), so
    the fallback is never silent. Empty for an overnight index, which needs no basis."""
    if index in TENOR_BASIS_INDICES:
        return {"tenor_basis_applied": 1.0 if market.has_tenor_basis(index) else 0.0}
    return {}


def price_interest_rate_swap(
    trade: Trade, market: MarketSnapshot, as_of: date, collateral: str | None = None
) -> PricingResult:
    swap = trade.instrument
    assert isinstance(swap, InterestRateSwap)
    if swap.maturity_date <= as_of:
        return PricingResult(
            trade.trade_id, swap.currency, 0.0, "swap_multi_curve", SWAP_MODEL_VERSION, note="matured"
        )
    # Discount on the collateral curve: the currency's OIS curve, adjusted by the cross-currency
    # basis when the CSA posts another currency (decision 35); project the floating index off its
    # own curve, the OIS curve plus any tenor basis (PR-002).
    curve = market.collateral_curve(swap.currency, collateral)
    projection = market.projection_curve(swap.float_index, swap.currency)
    n = trade.quantity
    zero_coupon = swap.fixed_frequency is Frequency.ZERO_COUPON
    fixed_pv = 0.0
    annuity = 0.0
    flows: list[Cashflow] = []
    for start, end in remaining_periods(swap.effective_date, swap.maturity_date, swap.fixed_frequency, as_of):
        tau = year_fraction(start, end, swap.fixed_day_count)
        df = float(curve.df(year_fraction_act365(as_of, end)))
        annuity += tau * df
        # A zero-coupon fixed leg compounds annually over its single period (DI pre).
        amt = n * ((1.0 + swap.fixed_rate) ** tau - 1.0) if zero_coupon else n * swap.fixed_rate * tau
        fixed_pv += amt * df
        flows.append(Cashflow(end, amt, "FIXED"))
    # The running floating period uses the trade's stored fixing (decision 34.4): a term index
    # fixed at the period start pays that rate for the whole period; an overnight index compounds
    # the realised rate to the fixing date and projects the rest. Without a fixing for the period
    # (a later period on an exposure path) the elapsed part accrues at today's short rate.
    term_index = swap.float_index in TENOR_BASIS_INDICES
    short_rate = float(projection.zero(0.0))
    fixing = swap.fixing
    float_pv = 0.0
    for start, end in remaining_periods(swap.effective_date, swap.maturity_date, swap.float_frequency, as_of):
        t1 = max(year_fraction_act365(as_of, start), 0.0)
        t2 = year_fraction_act365(as_of, end)
        df = float(curve.df(t2))
        # Projected growth from max(start, today) to the payment date: P(t1)/P(t2).
        growth = float(projection.df(t1)) / float(projection.df(t2))
        tau = year_fraction(start, end, swap.float_day_count)
        if start >= as_of:
            accrual = growth - 1.0
        elif fixing is not None and fixing.period_start == start and term_index:
            accrual = fixing.rate * tau
        elif fixing is not None and fixing.period_start == start:
            realised = math.exp(fixing.rate * year_fraction_act365(start, min(fixing.through, as_of)))
            gap = (
                math.exp(short_rate * year_fraction_act365(fixing.through, as_of))
                if as_of > fixing.through
                else 1.0
            )
            accrual = realised * gap * growth - 1.0
        elif term_index or swap.float_frequency is not Frequency.ZERO_COUPON:
            accrual = short_rate * year_fraction(start, as_of, swap.float_day_count) + (growth - 1.0)
        else:
            accrual = math.exp(short_rate * year_fraction_act365(start, as_of)) * growth - 1.0
        amt = n * (accrual + swap.float_spread * tau)
        float_pv += amt * df
        flows.append(Cashflow(end, -amt, "FLOAT"))
    sign = 1.0 if trade.swap_side is SwapSide.RECEIVE_FIXED else -1.0
    pv = sign * (fixed_pv - float_pv)
    if zero_coupon and annuity > 0:
        # (1 + K)^tau - 1 = float PV / (N DF(T)), with tau and DF(T) from the one fixed period.
        df_end = float(curve.df(year_fraction_act365(as_of, swap.maturity_date)))
        tau_total = annuity / df_end
        par_rate = (1.0 + float_pv / (n * df_end)) ** (1.0 / tau_total) - 1.0
    else:
        par_rate = (float_pv / n) / annuity if annuity > 0 else 0.0
    return PricingResult(
        trade_id=trade.trade_id,
        currency=swap.currency,
        pv_local=pv,
        model="swap_multi_curve",
        model_version=SWAP_MODEL_VERSION,
        cashflows=tuple(Cashflow(f.pay_date, sign * f.amount, f.kind) for f in flows),
        details={
            **projection_details(market, swap.float_index),
            "fixed_leg_pv": fixed_pv,
            "float_leg_pv": float_pv,
            "annuity": annuity,
            "par_rate": par_rate,
            "years_to_maturity": year_fraction_act365(as_of, swap.maturity_date),
        },
    )
