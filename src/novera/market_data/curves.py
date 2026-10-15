"""Term-structure objects built from snapshot values. Pure numpy, no I/O."""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class ZeroCurve:
    """Continuously compounded zero curve. Interpolation is linear in ``r * t`` (log-linear
    in discount factors); extrapolation is flat in the zero rate."""

    currency: str
    tenors: np.ndarray  # years, strictly increasing
    zeros: np.ndarray  # decimal rates
    # Plain-Python copies for the scalar fast path: pricers ask for one discount factor at a
    # time, millions of times per run, and numpy's array machinery cost ~12µs a call.
    _t: list[float] = field(init=False, repr=False, compare=False)
    _rt: list[float] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if len(self.tenors) != len(self.zeros) or len(self.tenors) == 0:
            raise ValueError("tenors and zeros must be non-empty and equal length")
        if np.any(np.diff(self.tenors) <= 0):
            raise ValueError("tenors must be strictly increasing")
        object.__setattr__(self, "_t", [float(x) for x in self.tenors])
        object.__setattr__(
            self, "_rt", [float(z) * float(x) for z, x in zip(self.zeros, self.tenors, strict=True)]
        )

    def _zero_scalar(self, t: float) -> float:
        """The same formula as the array path, linear in r·t, flat in r outside the nodes."""
        ts, rts = self._t, self._rt
        tc = ts[0] if t < ts[0] else (ts[-1] if t > ts[-1] else t)
        if tc >= ts[-1]:
            rt = rts[-1]
        else:
            j = bisect_right(ts, tc) - 1
            rt = (rts[j + 1] - rts[j]) / (ts[j + 1] - ts[j]) * (tc - ts[j]) + rts[j]
        return rt / tc if tc > 0 else float(self.zeros[0])

    def zero(self, t: float | np.ndarray) -> np.ndarray:
        if isinstance(t, (float, int)):
            return self._zero_scalar(float(t))  # type: ignore[return-value]
        t = np.asarray(t, dtype=float)
        t_clip = np.clip(t, self.tenors[0], self.tenors[-1])
        rt = np.interp(t_clip, self.tenors, self.zeros * self.tenors)
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(t_clip > 0, rt / np.where(t_clip > 0, t_clip, 1.0), self.zeros[0])
        return r

    def df(self, t: float | np.ndarray) -> np.ndarray:
        if isinstance(t, (float, int)):
            return 1.0 if t <= 0 else math.exp(-self._zero_scalar(float(t)) * t)  # type: ignore[return-value]
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
