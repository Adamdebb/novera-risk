"""Risk-factor path simulation for exposure profiles. Record CR-001.

Increments between grid dates are drawn from the sample covariance of daily factor moves
over the VaR window, scaled by the number of business days in the step (Brownian scaling,
zero drift). ABSOLUTE factors (rates, spreads) move additively; RELATIVE factors move
in log space. Rates are floored at -1%, vols at 3%. Seeded, so reruns reproduce.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

from novera.market_data.history import MarketHistory
from novera.market_data.risk_factors import RiskFactor
from novera.market_data.snapshot import MarketSnapshot
from novera.risk.scenarios import historical_shocks, shock_type_of

MODEL_VERSION = "1.0.0"
BUSINESS_DAYS_PER_YEAR = 261

DEFAULT_GRID: tuple[tuple[str, relativedelta], ...] = (
    ("1W", relativedelta(weeks=1)),
    ("2W", relativedelta(weeks=2)),
    ("1M", relativedelta(months=1)),
    ("3M", relativedelta(months=3)),
    ("6M", relativedelta(months=6)),
    ("1Y", relativedelta(years=1)),
    ("2Y", relativedelta(years=2)),
    ("3Y", relativedelta(years=3)),
    ("5Y", relativedelta(years=5)),
    ("7Y", relativedelta(years=7)),
    ("10Y", relativedelta(years=10)),
    ("15Y", relativedelta(years=15)),
)


@dataclass(frozen=True)
class ExposureSimConfig:
    paths: int = 1000
    seed: int = 11
    window_days: int = 500
    grid: tuple[tuple[str, relativedelta], ...] = DEFAULT_GRID
    margin_period_days: int = 10
    track_factors: tuple[str, ...] = (
        "FX:USDARS",
        "FX:USDTRY",
        "FX:USDBRL",
        "FX:USDMXN",
        "FX:USDZAR",
        "CDS:CDX.NA.HY",
        "CDS:ITRAXX.EUR.XOVER",
        "CDS:CDX.NA.IG",
        "CDS:ITRAXX.EUR.MAIN",
        "EQIDX:SPX",
    )


def grid_dates(as_of: date, grid=DEFAULT_GRID) -> list[tuple[str, date, float]]:
    """(label, date, years from as_of) for each grid point."""
    out = []
    for label, delta in grid:
        d = as_of + delta
        out.append((label, d, (d - as_of).days / 365.0))
    return out


class FactorPaths:
    """Simulated factor values at each grid point. Iterating yields (label, date, years,
    list of snapshots) one grid point at a time to keep memory bounded."""

    def __init__(
        self,
        base: MarketSnapshot,
        history: MarketHistory,
        universe: dict[str, RiskFactor],
        cfg: ExposureSimConfig,
    ) -> None:
        self.base, self.cfg, self.universe = base, cfg, universe
        self.factor_ids = sorted(base.values)
        shocks = historical_shocks(
            history, base.as_of, cfg.window_days, 1, universe, factor_ids=self.factor_ids
        )
        self.factor_ids = list(shocks.columns)
        x = shocks.to_numpy(dtype=float)
        self.is_abs = np.array([shock_type_of(f, universe) == "ABSOLUTE" for f in self.factor_ids])
        self.is_vol = np.array([f.startswith("VOL:") for f in self.factor_ids])
        self.is_rate = np.array([f.startswith("IR:") for f in self.factor_ids])
        # Log-space increments for relative factors so cumulated moves stay positive.
        xl = np.where(self.is_abs, x, np.log1p(np.clip(x, -0.95, None)))
        self.xc = xl - xl.mean(axis=0)
        self.n_obs = self.xc.shape[0]
        self.grid = grid_dates(base.as_of, cfg.grid)
        self.rng = np.random.default_rng(cfg.seed)
        self.track: list[str] = [f for f in cfg.track_factors if f in self.factor_ids]
        self.proxy_paths: dict[str, list[np.ndarray]] = {}

    def __iter__(self) -> Iterator[tuple[str, date, float, list[MarketSnapshot]]]:
        self.proxy_paths: dict[str, list[np.ndarray]] = {f: [] for f in self.track}
        base_vals = np.array([self.base.values[f] for f in self.factor_ids])
        state = np.tile(
            np.where(self.is_abs, base_vals, np.log(np.maximum(base_vals, 1e-12))), (self.cfg.paths, 1)
        )
        prev_days = 0
        for label, d, years in self.grid:
            days = max(int(round(years * BUSINESS_DAYS_PER_YEAR)), 1)
            step = max(days - prev_days, 1)
            z = self.rng.standard_normal((self.cfg.paths, self.n_obs))
            state = state + np.sqrt(step) * (z @ self.xc) / np.sqrt(max(self.n_obs - 1, 1))
            prev_days = days
            values = state.copy()
            rel = ~self.is_abs
            values[:, rel] = np.exp(np.clip(state[:, rel], -30, 30))
            values[:, self.is_rate] = np.maximum(values[:, self.is_rate], -0.01)
            values[:, self.is_vol] = np.maximum(values[:, self.is_vol], 0.03)
            for f in self.track:
                j = self.factor_ids.index(f)
                move = values[:, j] - base_vals[j] if self.is_abs[j] else values[:, j] / base_vals[j] - 1.0
                self.proxy_paths[f].append(move)
            snaps = [
                MarketSnapshot(
                    as_of=d,
                    values=dict(zip(self.factor_ids, row.tolist(), strict=True)),
                    source="EXPOSURE_SIM",
                )
                for row in values
            ]
            yield label, d, years, snaps


_ = pd
