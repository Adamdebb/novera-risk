"""Closed-form formulas for the Phase 6 products. Pure functions, no market objects.

- Bachelier (normal) swaption value on a forward swap rate and annuity.
- Cash-or-nothing digital under Black–Scholes.
- Continuously monitored single-barrier options after Reiner–Rubinstein (1991), in the
  form given by Haug, *The Complete Guide to Option Pricing Formulas*, with a rebate paid
  at expiry for knock-out options that never hit, or at hit for knock-in options that expire
  unexercised (Haug's convention, also QuantLib's AnalyticBarrierEngine).
"""

from __future__ import annotations

import math

from novera.domain.enums import BarrierType, OptionType
from novera.pricing.black import norm_cdf, norm_pdf


def bachelier_price(payer: bool, forward: float, strike: float, normal_vol: float, expiry: float) -> float:
    """Undiscounted normal-model value per unit annuity. ``normal_vol`` is decimal (bp/1e4)."""
    if expiry <= 0 or normal_vol <= 0:
        return max(forward - strike, 0.0) if payer else max(strike - forward, 0.0)
    sd = normal_vol * math.sqrt(expiry)
    d = (forward - strike) / sd
    if payer:
        return (forward - strike) * norm_cdf(d) + sd * norm_pdf(d)
    return (strike - forward) * norm_cdf(-d) + sd * norm_pdf(d)


def cash_or_nothing(
    kind: OptionType, spot: float, strike: float, rate: float, vol: float, expiry: float, payout: float
) -> float:
    """Digital paying ``payout`` at expiry if in the money, Black–Scholes with q = 0."""
    if expiry <= 0 or vol <= 0:
        itm = spot > strike if kind is OptionType.CALL else spot < strike
        return payout if itm else 0.0
    sd = vol * math.sqrt(expiry)
    d2 = (math.log(spot / strike) + (rate - 0.5 * vol * vol) * expiry) / sd
    df = math.exp(-rate * expiry)
    return payout * df * (norm_cdf(d2) if kind is OptionType.CALL else norm_cdf(-d2))


def barrier_price(
    kind: OptionType,
    barrier_type: BarrierType,
    spot: float,
    strike: float,
    barrier: float,
    rate: float,
    vol: float,
    expiry: float,
    rebate: float = 0.0,
    dividend_yield: float = 0.0,
) -> float:
    """Reiner–Rubinstein single-barrier price with continuous monitoring."""
    if expiry <= 0 or vol <= 0:
        intrinsic = max(spot - strike, 0.0) if kind is OptionType.CALL else max(strike - spot, 0.0)
        knocked = _knocked(barrier_type, spot, barrier)
        is_in = barrier_type in (BarrierType.UP_AND_IN, BarrierType.DOWN_AND_IN)
        return intrinsic if (knocked == is_in) else rebate
    r, b, s, T = rate, rate - dividend_yield, vol, expiry
    S, K, H, R = spot, strike, barrier, rebate
    sq = s * math.sqrt(T)
    mu = (b - 0.5 * s * s) / (s * s)
    lam = math.sqrt(mu * mu + 2.0 * r / (s * s))
    phi = 1.0 if kind is OptionType.CALL else -1.0
    down = barrier_type in (BarrierType.DOWN_AND_IN, BarrierType.DOWN_AND_OUT)
    eta = 1.0 if down else -1.0
    is_in = barrier_type in (BarrierType.UP_AND_IN, BarrierType.DOWN_AND_IN)
    # Already through the barrier: an "in" is a vanilla, an "out" pays the rebate now.
    if _knocked(barrier_type, S, H):
        if is_in:
            from novera.pricing.black import black_price

            fwd = S * math.exp(b * T)
            return black_price(kind, fwd, K, s, T, math.exp(-r * T))
        return R
    x1 = math.log(S / K) / sq + (1 + mu) * sq
    x2 = math.log(S / H) / sq + (1 + mu) * sq
    y1 = math.log(H * H / (S * K)) / sq + (1 + mu) * sq
    y2 = math.log(H / S) / sq + (1 + mu) * sq
    z = math.log(H / S) / sq + lam * sq
    carry = math.exp((b - r) * T)
    disc = math.exp(-r * T)
    hs = H / S
    A = phi * S * carry * norm_cdf(phi * x1) - phi * K * disc * norm_cdf(phi * x1 - phi * sq)
    B = phi * S * carry * norm_cdf(phi * x2) - phi * K * disc * norm_cdf(phi * x2 - phi * sq)
    C = phi * S * carry * hs ** (2 * (mu + 1)) * norm_cdf(eta * y1) - phi * K * disc * hs ** (
        2 * mu
    ) * norm_cdf(eta * y1 - eta * sq)
    D = phi * S * carry * hs ** (2 * (mu + 1)) * norm_cdf(eta * y2) - phi * K * disc * hs ** (
        2 * mu
    ) * norm_cdf(eta * y2 - eta * sq)
    E = R * disc * (norm_cdf(eta * x2 - eta * sq) - hs ** (2 * mu) * norm_cdf(eta * y2 - eta * sq))
    F = R * (hs ** (mu + lam) * norm_cdf(eta * z) + hs ** (mu - lam) * norm_cdf(eta * z - 2 * eta * lam * sq))
    call = kind is OptionType.CALL
    k_above = K > H
    if barrier_type is BarrierType.DOWN_AND_IN:
        v = (C + E if k_above else A - B + D + E) if call else (B - C + D + E if k_above else A + E)
    elif barrier_type is BarrierType.UP_AND_IN:
        v = (A + E if k_above else B - C + D + E) if call else (A - B + D + E if k_above else C + E)
    elif barrier_type is BarrierType.DOWN_AND_OUT:
        v = (A - C + F if k_above else B - D + F) if call else (A - B + C - D + F if k_above else F)
    else:  # UP_AND_OUT
        v = (F if k_above else A - B + C - D + F) if call else (B - D + F if k_above else A - C + F)
    return max(float(v), 0.0)


def _knocked(barrier_type: BarrierType, spot: float, barrier: float) -> bool:
    if barrier_type in (BarrierType.UP_AND_IN, BarrierType.UP_AND_OUT):
        return spot >= barrier
    return spot <= barrier
