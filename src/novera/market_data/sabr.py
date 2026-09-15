"""Normal SABR smile for the swaption cube (PR-012, SIM-001).

Hagan, Kumar, Lesniewski and Woodward (2002) normal-volatility expansion with beta = 0, which
handles low and negative forwards without a shift:

    sigma_N(K) = alpha * z / x(z) * [1 + (2 - 3 rho^2) / 24 * nu^2 * T]
    z = nu / alpha * (F - K)
    x(z) = ln((sqrt(1 - 2 rho z + z^2) + z - rho) / (1 - rho))

At the money z / x(z) -> 1, so alpha follows from the quoted at-the-money normal vol and the
cube keeps its ATM quote as the vega instrument; rho sets the skew and nu the curvature.
"""

from __future__ import annotations

import math

SABR_BETA = 0.0
MODEL_VERSION = "1.0.0"
RHO_BOUND = 0.95


def alpha_from_atm(atm_vol: float, expiry: float, rho: float, nu: float) -> float:
    """SABR alpha that reproduces ``atm_vol`` (decimal normal vol) at ``expiry`` years."""
    rho = max(min(rho, RHO_BOUND), -RHO_BOUND)
    return atm_vol / (1.0 + (2.0 - 3.0 * rho * rho) / 24.0 * nu * nu * expiry)


def normal_sabr_vol(
    forward: float, strike: float, expiry: float, alpha: float, rho: float, nu: float
) -> float:
    """Normal (Bachelier) vol, decimal, at ``strike`` under normal SABR with beta = 0."""
    if alpha <= 0.0 or expiry <= 0.0:
        return max(alpha, 0.0)
    rho = max(min(rho, RHO_BOUND), -RHO_BOUND)
    corr = 1.0 + (2.0 - 3.0 * rho * rho) / 24.0 * nu * nu * expiry
    z = nu / alpha * (forward - strike)
    if nu <= 0.0 or abs(z) < 1e-7:
        return alpha * corr
    x = math.log((math.sqrt(1.0 - 2.0 * rho * z + z * z) + z - rho) / (1.0 - rho))
    return alpha * z / x * corr
