"""The VaR setup: which VaR measures a firm produces every day, which of them feed limits
and which are for information (OPS-004).

A measure is one row of the firm's VaR matrix: a goal (LIMIT or INFORMATION), a metric
(VaR, expected shortfall or stressed VaR), a confidence level, how scenarios are built
(historical, exponentially weighted historical, Monte Carlo), how they are valued (full
revaluation or the delta-gamma-vega sensitivities), a window (years back from the valuation
date, or a fixed date range for stressed VaR) and, for weighted scenarios, the decay.

``compute_measures`` runs every enabled measure on one portfolio. Measures that share a
scenario set and a valuation method share the P&L matrix, so adding an ES row next to a VaR
row costs nothing; only the tail measure differs. The LIMIT row of each metric feeds the
limits of the matching type (VaR limits, expected-shortfall limits, stressed-VaR limits).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from novera.market_data.history import MarketHistory
from novera.risk.monte_carlo import METHOD as MC_METHOD
from novera.risk.monte_carlo import MonteCarloConfig, monte_carlo_pnl_matrix
from novera.risk.revaluation import Portfolio
from novera.risk.var import (
    VaRConfig,
    VaRResult,
    historical_method,
    result_from_pnl,
    scenario_shocks,
    taylor_pnl_matrix,
)

RECORD = "OPS-004"
DAYS_PER_YEAR = 250

GOALS = ("LIMIT", "INFORMATION")
METRICS = ("VAR", "ES", "STRESSED_VAR")
SHOCKS = ("HISTORICAL", "HISTORICAL_WEIGHTED", "MONTE_CARLO")
COMPUTES = ("FULL_REVALUATION", "SENSITIVITY")

METRIC_TITLES = {"VAR": "VaR", "ES": "ES", "STRESSED_VAR": "Stressed VaR"}
SHOCK_TITLES = {
    "HISTORICAL": "historical",
    "HISTORICAL_WEIGHTED": "weighted historical",
    "MONTE_CARLO": "Monte Carlo",
}
COMPUTE_TITLES = {"FULL_REVALUATION": "full revaluation", "SENSITIVITY": "delta-gamma-vega"}
LIMIT_TYPE_FOR_METRIC = {"VAR": "VAR", "ES": "EXPECTED_SHORTFALL", "STRESSED_VAR": "STRESSED_VAR"}
RECORD_FOR_SHOCKS = {"HISTORICAL": "MR-002", "HISTORICAL_WEIGHTED": "MR-015", "MONTE_CARLO": "MR-010"}


@dataclass(frozen=True)
class VaRMeasure:
    goal: str
    metric: str
    confidence: float
    shocks: str
    compute: str
    window_years: float | None = None
    """Years of history back from the valuation date; None uses the run's base window."""
    window_start: str | None = None
    """ISO date: fixed window (stressed VaR)."""
    window_end: str | None = None
    decay: float | None = None
    """Exponential weighting lambda for HISTORICAL_WEIGHTED scenarios."""
    enabled: bool = True
    measure_id: str = ""
    """Derived from the parameters when empty, so two identical rows collide by construction."""

    def __post_init__(self) -> None:
        if not self.measure_id:
            object.__setattr__(self, "measure_id", derive_id(self))

    # --- description ---------------------------------------------------------------
    @property
    def fixed_window(self) -> bool:
        return bool(self.window_start or self.window_end)

    def window_text(self, days: int | None = None) -> str:
        """The window as a reader says it; ``days`` names the resolved base window."""
        if self.fixed_window:
            return f"{self.window_start} to {self.window_end or 'valuation date'}"
        if self.window_years:
            return f"{self.window_years:g}Y"
        return f"{days} days (base window)" if days else "base window"

    @property
    def window_label(self) -> str:
        return self.window_text()

    def label_with(self, days: int | None = None) -> str:
        shocks = SHOCK_TITLES[self.shocks]
        if self.decay is not None:
            shocks += f" (λ {self.decay:g})"
        return (
            f"{METRIC_TITLES[self.metric]} {self.confidence:.1%}".replace(".0%", "%")
            + f" · {shocks} · {COMPUTE_TITLES[self.compute]} · {self.window_text(days)}"
        )

    @property
    def label(self) -> str:
        return self.label_with()

    @property
    def method(self) -> str:
        if self.shocks == "MONTE_CARLO":
            return MC_METHOD
        cfg = VaRConfig(decay=self.decay)
        return historical_method(
            cfg, "full_revaluation" if self.compute == "FULL_REVALUATION" else "sensitivity"
        )

    @property
    def limit_type(self) -> str | None:
        return LIMIT_TYPE_FOR_METRIC[self.metric] if self.goal == "LIMIT" else None

    @property
    def record(self) -> str:
        if self.metric == "STRESSED_VAR":
            return "MR-016"
        if self.metric == "ES":
            return "MR-003"
        if self.compute == "SENSITIVITY" and self.shocks == "HISTORICAL":
            return "MR-004"
        return RECORD_FOR_SHOCKS[self.shocks]

    # --- engine config -------------------------------------------------------------
    def window_days(self, base: VaRConfig) -> int:
        return int(round(self.window_years * DAYS_PER_YEAR)) if self.window_years else base.window_days

    def var_config(self, base: VaRConfig) -> VaRConfig:
        """The engine config of this measure: its own confidence, window and decay on top of
        the run's base horizon and scaling. An ES row puts its confidence on the ES side."""
        return replace(
            base,
            confidence=base.confidence if self.metric == "ES" else self.confidence,
            es_confidence=self.confidence if self.metric == "ES" else base.es_confidence,
            window_days=self.window_days(base),
            window_start=self.window_start,
            window_end=self.window_end,
            decay=self.decay,
        )

    # --- serialisation -------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "measure_id": self.measure_id,
            "goal": self.goal,
            "metric": self.metric,
            "confidence": self.confidence,
            "shocks": self.shocks,
            "compute": self.compute,
            "window_years": self.window_years,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "decay": self.decay,
            "enabled": self.enabled,
        }

    def describe(self, base: VaRConfig | None = None) -> dict[str, Any]:
        base = base or VaRConfig()
        return {
            **self.to_dict(),
            "label": self.label_with(self.window_days(base)),
            "method": self.method,
            "record": self.record,
            "limit_type": self.limit_type,
            "window_days": self.window_days(base),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VaRMeasure:
        def _opt(v: Any) -> Any:
            return None if v is None or v == "" or (isinstance(v, float) and pd.isna(v)) else v

        return cls(
            goal=str(d.get("goal", "")).upper(),
            metric=str(d.get("metric", "")).upper(),
            confidence=float(d.get("confidence") or 0.0),
            shocks=str(d.get("shocks", "")).upper(),
            compute=str(d.get("compute", "")).upper(),
            window_years=float(_opt(d.get("window_years")))
            if _opt(d.get("window_years")) is not None
            else None,
            window_start=str(_opt(d.get("window_start")))
            if _opt(d.get("window_start")) is not None
            else None,
            window_end=str(_opt(d.get("window_end"))) if _opt(d.get("window_end")) is not None else None,
            decay=float(_opt(d.get("decay"))) if _opt(d.get("decay")) is not None else None,
            enabled=bool(d.get("enabled", True)),
            measure_id=str(d.get("measure_id") or ""),
        )


def derive_id(m: VaRMeasure) -> str:
    conf = f"{m.confidence * 100:g}".replace(".", "")
    shocks = {"HISTORICAL": "HS", "HISTORICAL_WEIGHTED": "WHS", "MONTE_CARLO": "MC"}.get(m.shocks, m.shocks)
    comp = {"FULL_REVALUATION": "FULL", "SENSITIVITY": "SENS"}.get(m.compute, m.compute)
    if m.fixed_window:
        win = f"{(m.window_start or '').replace('-', '')}_{(m.window_end or 'ASOF').replace('-', '')}"
    else:
        win = f"{m.window_years:g}Y".replace(".", "P") if m.window_years else "BASE"
    parts = [m.metric, conf, shocks, comp, win]
    if m.decay is not None:
        parts.append("L" + f"{m.decay:g}".replace("0.", "").replace(".", ""))
    return "_".join(parts)


# --- validation ----------------------------------------------------------------------


def _iso(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def validate_measures(
    measures: list[VaRMeasure], history_start: date | None = None, history_end: date | None = None
) -> list[str]:
    """Every reason the setup cannot run, in the order a reader fixes them. Empty when valid."""
    errors: list[str] = []
    for i, m in enumerate(measures, start=1):
        where = f"row {i} ({m.measure_id})"
        if m.goal not in GOALS:
            errors.append(f"{where}: goal must be one of {', '.join(GOALS)}")
        if m.metric not in METRICS:
            errors.append(f"{where}: metric must be one of {', '.join(METRICS)}")
        if m.shocks not in SHOCKS:
            errors.append(f"{where}: shocks must be one of {', '.join(SHOCKS)}")
        if m.compute not in COMPUTES:
            errors.append(f"{where}: compute must be one of {', '.join(COMPUTES)}")
        if not 0.5 <= m.confidence < 1:
            errors.append(f"{where}: confidence must lie between 0.5 and 1 (0.99 for 99%)")
        if m.shocks == "HISTORICAL_WEIGHTED":
            if m.decay is None or not 0 < m.decay < 1:
                errors.append(f"{where}: weighted historical scenarios need a decay between 0 and 1")
        elif m.decay is not None:
            errors.append(f"{where}: decay applies to weighted historical scenarios only")
        if m.shocks == "MONTE_CARLO" and m.compute == "FULL_REVALUATION":
            errors.append(f"{where}: Monte Carlo runs on the sensitivities only (10,000 paths)")
        if m.metric == "STRESSED_VAR" and not m.fixed_window:
            errors.append(f"{where}: stressed VaR needs a fixed window (start and end dates)")
        if m.fixed_window:
            try:
                start, end = _iso(m.window_start), _iso(m.window_end)
            except ValueError:
                errors.append(f"{where}: window dates must be ISO dates (YYYY-MM-DD)")
                continue
            if start is None:
                errors.append(f"{where}: a fixed window needs a start date")
            elif end is not None and end <= start:
                errors.append(f"{where}: the window end must be after its start")
            elif history_start is not None and start < history_start:
                errors.append(f"{where}: window starts before the stored history ({history_start})")
            elif history_end is not None and end is not None and end > history_end:
                errors.append(f"{where}: window ends after the stored history ({history_end})")
            if m.window_years:
                errors.append(f"{where}: give either a window in years or fixed dates, not both")
        elif m.window_years is not None and m.window_years <= 0:
            errors.append(f"{where}: window years must be positive")
    ids = [m.measure_id for m in measures]
    for dup in sorted({x for x in ids if ids.count(x) > 1}):
        errors.append(f"measure {dup} appears more than once")
    enabled = [m for m in measures if m.enabled]
    if not any(m.metric == "VAR" for m in enabled):
        errors.append("at least one enabled VaR measure is needed: it is the headline VaR of the run")
    for metric in METRICS:
        lim = [m for m in enabled if m.goal == "LIMIT" and m.metric == metric]
        if len(lim) > 1:
            errors.append(
                f"only one {METRIC_TITLES[metric]} measure can feed limits; found "
                + ", ".join(m.measure_id for m in lim)
            )
    return errors


# --- defaults and templates ------------------------------------------------------------

DEFAULT_MEASURES: tuple[VaRMeasure, ...] = (
    VaRMeasure("LIMIT", "VAR", 0.99, "HISTORICAL", "FULL_REVALUATION"),
    VaRMeasure("LIMIT", "ES", 0.975, "HISTORICAL", "FULL_REVALUATION"),
    VaRMeasure("INFORMATION", "VAR", 0.99, "HISTORICAL", "SENSITIVITY"),
    VaRMeasure("INFORMATION", "VAR", 0.99, "MONTE_CARLO", "SENSITIVITY"),
)
"""What the run produces until a setup is saved: the official VaR and ES on the base window
(both feed limits), the delta-gamma-vega challenger and Monte Carlo for information."""


def templates(stress_window: tuple[date, date] | None = None) -> dict[str, list[VaRMeasure]]:
    """The two starting matrices: a bank on Basel-style measures and a hedge fund on a
    weighted 95% VaR. ``stress_window`` fills the bank's stressed-VaR dates."""
    start, end = (
        (stress_window[0].isoformat(), stress_window[1].isoformat()) if stress_window else (None, None)
    )
    return {
        "bank": [
            VaRMeasure("LIMIT", "VAR", 0.99, "HISTORICAL", "FULL_REVALUATION", window_years=2.0),
            VaRMeasure(
                "LIMIT",
                "STRESSED_VAR",
                0.99,
                "HISTORICAL",
                "FULL_REVALUATION",
                window_start=start,
                window_end=end,
            ),
            VaRMeasure("INFORMATION", "ES", 0.975, "HISTORICAL", "FULL_REVALUATION", window_years=2.0),
            VaRMeasure("INFORMATION", "VAR", 0.99, "HISTORICAL", "SENSITIVITY", window_years=2.0),
            VaRMeasure("INFORMATION", "VAR", 0.99, "MONTE_CARLO", "SENSITIVITY", window_years=2.0),
        ],
        "hedge_fund": [
            VaRMeasure(
                "LIMIT", "VAR", 0.95, "HISTORICAL_WEIGHTED", "FULL_REVALUATION", window_years=1.0, decay=0.94
            ),
            VaRMeasure(
                "INFORMATION",
                "ES",
                0.95,
                "HISTORICAL_WEIGHTED",
                "FULL_REVALUATION",
                window_years=1.0,
                decay=0.94,
            ),
            VaRMeasure("INFORMATION", "VAR", 0.99, "HISTORICAL", "FULL_REVALUATION", window_years=5.0),
        ],
    }


def most_volatile_year(
    history: MarketHistory, factor_id: str = "EQIDX:SPX", days: int = DAYS_PER_YEAR
) -> tuple[date, date] | None:
    """The ``days``-long window of the stored history over which the factor's daily log
    returns were most volatile: the natural stressed-VaR window of a synthetic or short
    history. None when the factor is missing or the history is shorter than the window."""
    if factor_id not in history.wide.columns or len(history.wide) <= days:
        return None
    level = history.wide[factor_id].astype(float)
    returns = np.log(level / level.shift(1)).dropna()
    vol = returns.rolling(days).std().dropna()
    if vol.empty:
        return None
    end = vol.idxmax()
    pos = int(history.wide.index.get_loc(end))
    return history.wide.index[max(pos - days + 1, 0)], end


# --- computation -----------------------------------------------------------------------


@dataclass
class MeasureResult:
    measure: VaRMeasure
    result: VaRResult
    seconds: float = 0.0
    shared_with: str | None = None
    """Measure id whose P&L matrix this one reused, when the scenarios and valuation match."""

    @property
    def value(self) -> float:
        return self.result.es if self.measure.metric == "ES" else self.result.var

    def row(self) -> dict[str, Any]:
        """One line of the run's ``var_summary`` table."""
        x, cfg = self.result, self.result.config
        return {
            "measure_id": self.measure.measure_id,
            "goal": self.measure.goal,
            "metric": self.measure.metric,
            "label": self.measure.label_with(cfg.window_days),
            "method": x.method,
            "value": self.value,
            "var": x.var,
            "es": x.es,
            "var_scaled": x.var_scaled,
            "es_scaled": x.es_scaled,
            "confidence": cfg.confidence,
            "es_confidence": cfg.es_confidence,
            "window_days": cfg.window_days,
            "window_start": cfg.window_start,
            "window_end": cfg.window_end,
            "decay": cfg.decay,
            "scenarios": len(x.pnl),
            "var_scenario_date": x.var_scenario_date,
            "limit_type": self.measure.limit_type,
            "seconds": self.seconds,
        }


@dataclass
class MeasureSet:
    results: list[MeasureResult] = field(default_factory=list)

    def __iter__(self):
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    @property
    def headline(self) -> MeasureResult:
        """The LIMIT VaR measure, else the first VaR measure: the run's official VaR."""
        vars_ = [r for r in self.results if r.measure.metric == "VAR"]
        if not vars_:
            raise ValueError("no VaR measure in the setup")
        return next((r for r in vars_ if r.measure.goal == "LIMIT"), vars_[0])

    def for_limit(self, limit_type: str) -> MeasureResult | None:
        return next((r for r in self.results if r.measure.limit_type == limit_type), None)

    def first(self, method: str) -> MeasureResult | None:
        return next((r for r in self.results if r.result.method == method), None)

    def first_metric(self, metric: str) -> MeasureResult | None:
        return self.for_limit(LIMIT_TYPE_FOR_METRIC[metric]) or next(
            (r for r in self.results if r.measure.metric == metric), None
        )

    def limit_inputs(self) -> dict[str, VaRResult]:
        """Limit type -> the VaR result whose scenarios that limit type reads."""
        return {r.measure.limit_type: r.result for r in self.results if r.measure.limit_type}

    def values(self) -> dict[str, float]:
        return {r.measure.measure_id: r.value for r in self.results}

    def summary(self) -> dict[str, Any]:
        """Run-summary keys: the headline VaR and ES, the stressed VaR, the challenger and
        Monte Carlo figures when the setup produces them, and every measure's value."""
        head = self.headline
        es = self.first_metric("ES")
        stressed = self.first_metric("STRESSED_VAR")
        chal = self.first("delta_gamma_vega") or self.first("historical_weighted_delta_gamma_vega")
        mc = self.first(MC_METHOD)
        return {
            "var": head.result.var,
            "es": es.value if es else head.result.es,
            "es_confidence": es.measure.confidence if es else head.result.config.es_confidence,
            "var_scaled": head.result.var_scaled,
            "var_scenario_date": str(head.result.var_scenario_date),
            "var_confidence": head.measure.confidence,
            "var_measure_id": head.measure.measure_id,
            "stressed_var": stressed.value if stressed else None,
            "challenger_var": chal.result.var if chal else None,
            "monte_carlo_var": mc.result.var if mc else None,
            "monte_carlo_es": mc.result.es if mc else None,
            "var_measures": self.values(),
        }


def _pnl_key(m: VaRMeasure, cfg: VaRConfig) -> tuple:
    family = "MC" if m.shocks == "MONTE_CARLO" else "HS"
    return (family, m.compute, cfg.window_days, cfg.window_start, cfg.window_end, cfg.horizon_days)


def compute_measures(
    pf: Portfolio,
    sens: pd.DataFrame,
    history: MarketHistory,
    measures: list[VaRMeasure] | tuple[VaRMeasure, ...],
    base: VaRConfig | None = None,
    workers: int | None = None,
    mc_cfg: MonteCarloConfig | None = None,
) -> MeasureSet:
    """Every enabled measure on the portfolio, in setup order. P&L matrices are shared
    between measures with the same scenarios and valuation (weights only touch the tail)."""
    import time

    base = base or VaRConfig()
    enabled = [m for m in measures if m.enabled]
    errors = validate_measures(enabled)
    if errors:
        raise ValueError("; ".join(errors))
    cache: dict[tuple, tuple[str, pd.DataFrame]] = {}
    out = MeasureSet()
    for m in enabled:
        t0 = time.perf_counter()
        cfg = m.var_config(base)
        key = _pnl_key(m, cfg)
        shared = None
        if key in cache:
            shared, pnl = cache[key]
        else:
            if m.shocks == "MONTE_CARLO":
                pnl = monte_carlo_pnl_matrix(pf, sens, history, replace(cfg, decay=None), mc_cfg)
            else:
                scen = scenario_shocks(history, pf.as_of, cfg, pf.universe, list(pf.base.values))
                pnl = (
                    pf.pnl_matrix(scen, workers=workers)
                    if m.compute == "FULL_REVALUATION"
                    else taylor_pnl_matrix(pf, sens, scen)
                )
            cache[key] = (m.measure_id, pnl)
        res = result_from_pnl(m.method, cfg, pnl)
        if m.shocks == "MONTE_CARLO":
            res.var_scenario_date = pf.as_of
        out.results.append(MeasureResult(m, res, time.perf_counter() - t0, shared))
    return out
