"""Implied volatility surface in (expiry, moneyness K/F) with bilinear interpolation."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class VolSurface:
    underlying: str
    expiries: np.ndarray  # years, increasing
    moneyness: np.ndarray  # K/F, increasing
    vols: np.ndarray  # shape (len(expiries), len(moneyness)), decimal

    def __post_init__(self) -> None:
        if self.vols.shape != (len(self.expiries), len(self.moneyness)):
            raise ValueError("vols shape must be (expiries, moneyness)")

    def vol(self, expiry: float, moneyness: float) -> float:
        t = float(np.clip(expiry, self.expiries[0], self.expiries[-1]))
        m = float(np.clip(moneyness, self.moneyness[0], self.moneyness[-1]))
        i = int(np.searchsorted(self.expiries, t, side="right") - 1)
        j = int(np.searchsorted(self.moneyness, m, side="right") - 1)
        i = min(max(i, 0), len(self.expiries) - 2) if len(self.expiries) > 1 else 0
        j = min(max(j, 0), len(self.moneyness) - 2) if len(self.moneyness) > 1 else 0
        if len(self.expiries) == 1 and len(self.moneyness) == 1:
            return float(self.vols[0, 0])
        t0, t1 = self.expiries[i], self.expiries[min(i + 1, len(self.expiries) - 1)]
        m0, m1 = self.moneyness[j], self.moneyness[min(j + 1, len(self.moneyness) - 1)]
        wt = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
        wm = 0.0 if m1 == m0 else (m - m0) / (m1 - m0)
        v00 = self.vols[i, j]
        v01 = self.vols[i, min(j + 1, len(self.moneyness) - 1)]
        v10 = self.vols[min(i + 1, len(self.expiries) - 1), j]
        v11 = self.vols[min(i + 1, len(self.expiries) - 1), min(j + 1, len(self.moneyness) - 1)]
        return float((1 - wt) * ((1 - wm) * v00 + wm * v01) + wt * ((1 - wm) * v10 + wm * v11))

    def atm(self, expiry: float) -> float:
        return self.vol(expiry, 1.0)

    def shifted(self, bump: float) -> VolSurface:
        """Parallel additive shift in vol points (decimal)."""
        return VolSurface(self.underlying, self.expiries, self.moneyness, self.vols + bump)
