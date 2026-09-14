"""Term-structure objects built from snapshot values. Pure numpy, no I/O."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ZeroCurve:
    """Continuously compounded zero curve. Interpolation is linear in ``r * t`` (log-linear
    in discount factors); extrapolation is flat in the zero rate."""

    currency: str
    tenors: np.ndarray  # years, strictly increasing
    zeros: np.ndarray  # decimal rates

    def __post_init__(self) -> None:
        if len(self.tenors) != len(self.zeros) or len(self.tenors) == 0:
            raise ValueError("tenors and zeros must be non-empty and equal length")
        if np.any(np.diff(self.tenors) <= 0):
            raise ValueError("tenors must be strictly increasing")

    def zero(self, t: float | np.ndarray) -> np.ndarray:
        t = np.asarray(t, dtype=float)
        t_clip = np.clip(t, self.tenors[0], self.tenors[-1])
        rt = np.interp(t_clip, self.tenors, self.zeros * self.tenors)
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(t_clip > 0, rt / np.where(t_clip > 0, t_clip, 1.0), self.zeros[0])
        return r

    def df(self, t: float | np.ndarray) -> np.ndarray:
        t = np.asarray(t, dtype=float)
        return np.where(t <= 0, 1.0, np.exp(-self.zero(t) * np.maximum(t, 0.0)))

    def forward(self, t1: float, t2: float) -> float:
        """Simple forward rate between t1 and t2 (annualised, ACT-style year fractions)."""
        if t2 <= t1:
            raise ValueError("t2 must exceed t1")
        return float((self.df(t1) / self.df(t2) - 1.0) / (t2 - t1))

    def shifted(self, bump: float, tenor_index: int | None = None) -> ZeroCurve:
        """Parallel shift (tenor_index None) or single-node bump in decimal."""
        z = self.zeros.copy()
        if tenor_index is None:
            z += bump
        else:
            z[tenor_index] += bump
        return ZeroCurve(self.currency, self.tenors, z)


@dataclass(frozen=True)
class CommodityCurve:
    """Futures prices by time to expiry, linearly interpolated, flat beyond the ends."""

    commodity: str
    tenors: np.ndarray
    prices: np.ndarray

    def price(self, t: float | np.ndarray) -> np.ndarray:
        t = np.clip(np.asarray(t, dtype=float), self.tenors[0], self.tenors[-1])
        return np.interp(t, self.tenors, self.prices)

    def scaled(self, factor: float) -> CommodityCurve:
        return CommodityCurve(self.commodity, self.tenors, self.prices * factor)
