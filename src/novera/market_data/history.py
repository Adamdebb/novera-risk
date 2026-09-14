"""Wide-format access to the daily factor history: snapshots at any date, return series."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from novera.market_data.snapshot import MarketSnapshot


@dataclass
class MarketHistory:
    """dates x factors matrix built from the long (as_of, factor_id, value) table."""

    wide: pd.DataFrame  # index: date, columns: factor_id

    @classmethod
    def from_long(cls, history: pd.DataFrame) -> MarketHistory:
        w = history.pivot(index="as_of", columns="factor_id", values="value").sort_index()
        w.index = pd.to_datetime(w.index).date
        return cls(w)

    @property
    def dates(self) -> list[date]:
        return list(self.wide.index)

    @property
    def factor_ids(self) -> list[str]:
        return list(self.wide.columns)

    def snapshot_at(self, as_of: date, source: str = "SIM") -> MarketSnapshot:
        """Snapshot on ``as_of`` or the latest date before it."""
        idx = self.wide.index.searchsorted(as_of, side="right") - 1
        if idx < 0:
            raise KeyError(f"no market data on or before {as_of}")
        row = self.wide.iloc[idx]
        return MarketSnapshot(as_of=self.wide.index[idx], values={k: float(v) for k, v in row.items()
                                                                if not np.isnan(v)}, source=source)

    def window(self, end: date, days: int) -> pd.DataFrame:
        """The last ``days`` rows ending at ``end`` (inclusive)."""
        idx = self.wide.index.searchsorted(end, side="right")
        return self.wide.iloc[max(idx - days, 0):idx]
