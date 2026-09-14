"""Full revaluation of a portfolio under shocked snapshots, repricing only affected trades."""

from __future__ import annotations

import multiprocessing as mp
import os
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from datetime import date

import numpy as np
import pandas as pd

from novera.domain.trades import Trade
from novera.market_data.risk_factors import RiskFactor
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing.valuation import fx_to_reporting, value_trade
from novera.risk.factor_mapping import DependencyIndex
from novera.risk.scenarios import apply_shocks


class Portfolio:
    """Live trades plus their base valuation, the unit every risk measure works on."""

    def __init__(
        self,
        trades: Iterable[Trade],
        base: MarketSnapshot,
        reporting_currency: str,
        as_of: date | None = None,
        universe: dict[str, RiskFactor] | None = None,
    ) -> None:
        self.trades: list[Trade] = [t for t in trades if t.status.value == "LIVE"]
        self.by_id = {t.trade_id: t for t in self.trades}
        self.base = base
        self.as_of = as_of or base.as_of
        self.reporting_currency = reporting_currency
        self.universe = universe
        self.index = DependencyIndex.build(self.trades, reporting_currency)
        self.base_pv: dict[str, float] = {}
        self.base_pv_local: dict[str, float] = {}
        self.errors: dict[str, str] = {}
        for t in self.trades:
            try:
                r = value_trade(t, base, self.as_of)
                self.base_pv_local[t.trade_id] = r.pv_local
                self.base_pv[t.trade_id] = r.pv_local * fx_to_reporting(base, r.currency, reporting_currency)
            except Exception as e:  # noqa: BLE001
                self.errors[t.trade_id] = f"{type(e).__name__}: {e}"
        self.priced_ids = [t.trade_id for t in self.trades if t.trade_id in self.base_pv]

    @property
    def total_pv(self) -> float:
        return float(sum(self.base_pv.values()))

    def pv_under(
        self, snapshot: MarketSnapshot, trade_ids: Iterable[str] | None = None, as_of: date | None = None
    ) -> dict[str, float]:
        """Reporting-currency PV of the given trades (default: all priced) on ``snapshot``."""
        ids = list(trade_ids) if trade_ids is not None else self.priced_ids
        as_of = as_of or self.as_of
        out: dict[str, float] = {}
        for tid in ids:
            if tid in self.errors:
                continue
            t = self.by_id[tid]
            try:
                r = value_trade(t, snapshot, as_of)
                out[tid] = r.pv_local * fx_to_reporting(snapshot, r.currency, self.reporting_currency)
            except Exception:  # noqa: BLE001 - a scenario that breaks a pricer counts as no change
                out[tid] = self.base_pv[tid]
        return out

    def pnl_under_shocks(self, shocks: dict[str, float]) -> dict[str, float]:
        """P&L per affected trade for one shock set; unaffected trades are omitted (zero)."""
        affected = self.index.trades_for(shocks) & set(self.priced_ids)
        if not affected:
            return {}
        shocked = apply_shocks(self.base, shocks, self.universe)
        pv = self.pv_under(shocked, affected)
        return {tid: pv[tid] - self.base_pv[tid] for tid in pv}

    def _pnl_rows(self, scenarios: pd.DataFrame) -> np.ndarray:
        cols = self.priced_ids
        mat = np.zeros((len(scenarios), len(cols)))
        pos = {tid: j for j, tid in enumerate(cols)}
        factor_cols = list(scenarios.columns)
        for i, row in enumerate(scenarios.to_numpy(dtype=float)):
            shocks = {f: float(v) for f, v in zip(factor_cols, row, strict=True) if v != 0.0}
            shocked = apply_shocks(self.base, shocks, self.universe)
            pv = self.pv_under(shocked)
            for tid, v in pv.items():
                mat[i, pos[tid]] = v - self.base_pv[tid]
        return mat

    def pnl_matrix(self, scenarios: pd.DataFrame, workers: int | None = None) -> pd.DataFrame:
        """Full revaluation: rows = scenarios (index preserved), columns = trade ids, values =
        P&L in reporting currency. Scenarios are split across processes when ``workers`` > 1
        (default: all cores but one)."""
        if workers is None:
            workers = int(os.environ.get("NOVERA_WORKERS", max((os.cpu_count() or 2) - 1, 1)))
        if workers <= 1 or len(scenarios) < 2 * workers or "fork" not in mp.get_all_start_methods():
            mat = self._pnl_rows(scenarios)
        else:
            # Fork: children inherit this object without pickling and without re-importing
            # the caller's main module (safe from scripts and notebooks).
            global _WORKER_PF  # noqa: PLW0603
            _WORKER_PF = self
            chunks = np.array_split(np.arange(len(scenarios)), workers)
            try:
                with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork")) as ex:
                    parts = list(ex.map(_worker_rows, [scenarios.iloc[c] for c in chunks]))
            finally:
                _WORKER_PF = None
            mat = np.vstack(parts)
        return pd.DataFrame(mat, index=scenarios.index, columns=self.priced_ids)


_WORKER_PF: Portfolio | None = None


def _worker_rows(scenarios: pd.DataFrame) -> np.ndarray:
    assert _WORKER_PF is not None
    return _WORKER_PF._pnl_rows(scenarios)
