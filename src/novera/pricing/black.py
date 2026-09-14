"""Black (1976) and Black–Scholes–Merton formulas on a forward. Pure functions."""
from __future__ import annotations

import math

from novera.domain.enums import OptionType


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_price(
    kind: OptionType, forward: float, strike: float, vol: float, expiry: float, df: float
) -> float:
    """Undiscounted Black value times ``df``. Handles expiry <= 0 as intrinsic."""
    if expiry <= 0 or vol <= 0:
        intrinsic = max(forward - strike, 0.0) if kind is OptionType.CALL else max(strike - forward, 0.0)
        return df * intrinsic
    sd = vol * math.sqrt(expiry)
    d1 = (math.log(forward / strike) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    if kind is OptionType.CALL:
        return df * (forward * norm_cdf(d1) - strike * norm_cdf(d2))
    return df * (strike * norm_cdf(-d2) - forward * norm_cdf(-d1))


def black_greeks(
    kind: OptionType, forward: float, strike: float, vol: float, expiry: float, df: float
) -> dict[str, float]:
    """Forward delta, gamma (per unit forward), vega (per 1.00 vol), theta (per year)."""
    if expiry <= 0 or vol <= 0:
        itm = (forward > strike) if kind is OptionType.CALL else (forward < strike)
        return {"delta_fwd": df * (1.0 if itm else 0.0) * (1 if kind is OptionType.CALL else -1),
                "gamma_fwd": 0.0, "vega": 0.0, "theta": 0.0}
    sd = vol * math.sqrt(expiry)
    d1 = (math.log(forward / strike) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    delta = df * (norm_cdf(d1) if kind is OptionType.CALL else norm_cdf(d1) - 1.0)
    gamma = df * norm_pdf(d1) / (forward * sd)
    vega = df * forward * norm_pdf(d1) * math.sqrt(expiry)
    theta = -df * forward * norm_pdf(d1) * vol / (2 * math.sqrt(expiry))
    return {"delta_fwd": delta, "gamma_fwd": gamma, "vega": vega, "theta": theta, "d1": d1, "d2": d2}
