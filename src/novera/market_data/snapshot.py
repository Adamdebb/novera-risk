"""A market-data snapshot: factor values as of a date, with per-factor observation dates.

Identity is the content hash of values and observation dates, so two snapshots with the
same numbers have the same id and a stale factor changes the id.
"""

from __future__ import annotations

from datetime import date
from functools import cached_property
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, computed_field

from novera.domain.snapshots import content_hash
from novera.market_data.curves import CommodityCurve, ZeroCurve
from novera.market_data.risk_factors import TENOR_YEARS
from novera.market_data.vol_surface import VolSurface


class MarketSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of: date
    values: dict[str, float]
    observed_at: dict[str, date] = Field(default_factory=dict, description="Per factor; missing means as_of")
    source: str = "SIM"
    _cache: dict[str, Any] = PrivateAttr(default_factory=dict)

    @cached_property
    def _groups(self) -> dict[str, list[str]]:
        """Factor ids grouped by 'TYPE:' and by 'TYPE:UNDERLYING:' so family lookups are O(1)."""
        groups: dict[str, list[str]] = {}
        for k in sorted(self.values):
            parts = k.split(":")
            groups.setdefault(parts[0] + ":", []).append(k)
            if len(parts) >= 3:
                groups.setdefault(parts[0] + ":" + parts[1] + ":", []).append(k)
        return groups

    @computed_field  # type: ignore[prop-decorator]
    @property
    def snapshot_id(self) -> str:
        return content_hash(
            {
                "as_of": self.as_of.isoformat(),
                "values": dict(sorted(self.values.items())),
                "observed_at": {k: v.isoformat() for k, v in sorted(self.observed_at.items())},
            }
        )

    # --- accessors -----------------------------------------------------------------
    def value(self, factor_id: str) -> float:
        try:
            return self.values[factor_id]
        except KeyError as e:
            raise KeyError(f"factor {factor_id!r} missing from snapshot {self.as_of}") from e

    def has(self, factor_id: str) -> bool:
        return factor_id in self.values

    def observation_date(self, factor_id: str) -> date:
        return self.observed_at.get(factor_id, self.as_of)

    def stale_factors(self) -> list[str]:
        return sorted(k for k, d in self.observed_at.items() if d < self.as_of)

    def factors_with_prefix(self, prefix: str) -> list[str]:
        hit = self._groups.get(prefix)
        if hit is not None:
            return hit
        return sorted(k for k in self.values if k.startswith(prefix))

    def zero_curve(self, currency: str) -> ZeroCurve:
        key = f"zc:{currency}"
        if key in self._cache:
            return self._cache[key]
        nodes = []
        for k in self.factors_with_prefix(f"IR:{currency}:"):
            tenor = k.split(":")[2]
            nodes.append((TENOR_YEARS[tenor], self.values[k]))
        if not nodes:
            raise KeyError(f"no zero curve for {currency} in snapshot {self.as_of}")
        nodes.sort()
        curve = ZeroCurve(currency, np.array([n[0] for n in nodes]), np.array([n[1] for n in nodes]))
        self._cache[key] = curve
        return curve

    def fx_spot(self, pair: str) -> float:
        """Spot for BASE/QUOTE, deriving crosses and inverses from USD pairs when needed."""
        base, quote = pair[:3], pair[4:]
        if base == quote:
            return 1.0
        direct, inverse = f"FX:{base}{quote}", f"FX:{quote}{base}"
        if direct in self.values:
            return self.values[direct]
        if inverse in self.values:
            return 1.0 / self.values[inverse]
        return self.fx_spot(f"{base}/USD") * self.fx_spot(f"USD/{quote}")

    def equity_spot(self, ticker: str) -> float:
        return self.value(f"EQ:{ticker}")

    def index_level(self, index: str) -> float:
        return self.value(f"EQIDX:{index}")

    def commodity_curve(self, code: str) -> CommodityCurve:
        key = f"cc:{code}"
        if key in self._cache:
            return self._cache[key]
        nodes = sorted(
            (TENOR_YEARS[k.split(":")[2]], self.values[k]) for k in self.factors_with_prefix(f"CMD:{code}:")
        )
        if not nodes:
            raise KeyError(f"no commodity curve for {code}")
        curve = CommodityCurve(code, np.array([n[0] for n in nodes]), np.array([n[1] for n in nodes]))
        self._cache[key] = curve
        return curve

    def cds_spread_bp(self, family: str) -> float:
        return self.value(f"CDS:{family}")

    def crypto_spot(self, symbol: str) -> float:
        return self.value(f"CRYPTO:{symbol}")

    def vol_surface(self, underlying: str) -> VolSurface:
        key = underlying.replace("/", "")
        ck = f"vs:{key}"
        if ck in self._cache:
            return self._cache[ck]
        grid: dict[tuple[float, float], float] = {}
        for k in self.factors_with_prefix(f"VOL:{key}:"):
            _, _, expiry, m = k.split(":")
            grid[(TENOR_YEARS[expiry], float(m))] = self.values[k]
        if not grid:
            raise KeyError(f"no vol surface for {underlying}")
        expiries = np.array(sorted({e for e, _ in grid}))
        moneyness = np.array(sorted({m for _, m in grid}))
        vols = np.array([[grid[(e, m)] for m in moneyness] for e in expiries])
        surface = VolSurface(key, expiries, moneyness, vols)
        self._cache[ck] = surface
        return surface

    def swaption_normal_vol_bp(self, currency: str, expiry_years: float, tenor_years: float) -> float:
        """Bachelier vol in bp/yr, bilinear in (expiry, tenor) on the SWVOL cube, flat outside."""
        ck = f"swv:{currency}"
        grid = self._cache.get(ck)
        if grid is None:
            pts: dict[tuple[float, float], float] = {}
            for k in self.factors_with_prefix(f"SWVOL:{currency}:"):
                _, _, e, t = k.split(":")
                pts[(TENOR_YEARS[e], TENOR_YEARS[t])] = self.values[k]
            if not pts:
                raise KeyError(f"no swaption vol cube for {currency}")
            es = np.array(sorted({e for e, _ in pts}))
            ts = np.array(sorted({t for _, t in pts}))
            grid = (es, ts, np.array([[pts[(e, t)] for t in ts] for e in es]))
            self._cache[ck] = grid
        es, ts, vals = grid
        x = float(np.clip(expiry_years, es[0], es[-1]))
        y = float(np.clip(tenor_years, ts[0], ts[-1]))
        i = int(np.clip(np.searchsorted(es, x, side="right") - 1, 0, max(len(es) - 2, 0)))
        j = int(np.clip(np.searchsorted(ts, y, side="right") - 1, 0, max(len(ts) - 2, 0)))
        i2, j2 = min(i + 1, len(es) - 1), min(j + 1, len(ts) - 1)
        wx = 0.0 if es[i2] == es[i] else (x - es[i]) / (es[i2] - es[i])
        wy = 0.0 if ts[j2] == ts[j] else (y - ts[j]) / (ts[j2] - ts[j])
        v = (
            vals[i, j] * (1 - wx) * (1 - wy)
            + vals[i2, j] * wx * (1 - wy)
            + vals[i, j2] * (1 - wx) * wy
            + vals[i2, j2] * wx * wy
        )
        return float(v)

    def with_values(self, updates: dict[str, float]) -> MarketSnapshot:
        """Return a new snapshot with some factor values replaced (used by stress and bumping).
        Built fresh rather than copied so cached curves and surfaces are not inherited."""
        return MarketSnapshot(
            as_of=self.as_of,
            values={**self.values, **updates},
            observed_at=self.observed_at,
            source=self.source,
        )
