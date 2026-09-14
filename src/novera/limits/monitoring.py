"""Limit utilisation and breach status from risk results. Methodology record MR-006.

Each limit has a scope (a hierarchy node plus optional filters) and a type that maps to a
measure of the trades in scope:

    VAR / EXPECTED_SHORTFALL  standalone historical VaR / ES of the scope's trades
    STRESS_LOSS               worst loss across the stress library for the scope
    DV01, CS01, *_DELTA, VEGA, GAMMA   absolute net sensitivity in scope (filters: currency,
                              tenor_bucket, risk_factor = underlying)
    CONCENTRATION             share of the scope's net sensitivity carried by one risk_factor
                              or tenor_bucket: |net part| / Σ_groups |net group| (fraction of 1)
    COUNTERPARTY_EXPOSURE     positive net PV facing the counterparty (pre-collateral)

Status: BREACH at utilisation >= 100%, WARNING at >= warning_threshold, else OK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from novera.domain.enums import HierarchyLevel
from novera.domain.limits import Limit, LimitType
from novera.risk.stress import StressResult
from novera.risk.var import VaRResult, tail_measures

MODEL_VERSION = "1.0.0"

COLUMNS = [
    "limit_id",
    "limit_type",
    "level",
    "entity_id",
    "filters",
    "amount",
    "base_amount",
    "increase_id",
    "current",
    "utilisation",
    "status",
    "warning_threshold",
    "owner",
    "trades_in_scope",
]

_SENS_MEASURE: dict[LimitType, str] = {
    LimitType.DV01: "DV01",
    LimitType.CS01: "CS01",
    LimitType.FX_DELTA: "FX_DELTA",
    LimitType.EQUITY_DELTA: "EQ_DELTA",
    LimitType.COMMODITY_DELTA: "CMD_DELTA",
    LimitType.VEGA: "VEGA",
    LimitType.GAMMA: "GAMMA",
}


@dataclass
class RiskInputs:
    valuation: pd.DataFrame  # one row per trade with hierarchy columns
    sensitivities: pd.DataFrame  # long table from compute_sensitivities
    var: VaRResult | None = None
    stress: list[StressResult] = field(default_factory=list)


def trades_in_scope(limit: Limit, valuation: pd.DataFrame) -> pd.Index:
    v = valuation
    level = limit.scope.level
    if level is HierarchyLevel.FIRM:
        mask = pd.Series(True, index=v.index)
    else:
        mask = v[level.value] == limit.scope.entity_id
    if limit.scope.currency:
        mask &= v["currency"] == limit.scope.currency
    if limit.scope.asset_class:
        mask &= v["asset_class"] == limit.scope.asset_class
    return pd.Index(v.loc[mask, "trade_id"])


def _sens_in_scope(limit: Limit, sens: pd.DataFrame, ids: pd.Index, measure: str) -> pd.DataFrame:
    s = sens[(sens["measure"] == measure) & sens["trade_id"].isin(ids)]
    if limit.scope.currency and measure in ("DV01",):
        s = s[s["underlying"] == limit.scope.currency]
    if limit.scope.tenor_bucket:
        s = s[s["bucket"] == limit.scope.tenor_bucket]
    if limit.scope.risk_factor:
        s = s[s["underlying"] == limit.scope.risk_factor]
    return s


def current_value(limit: Limit, inputs: RiskInputs) -> tuple[float, int]:
    ids = trades_in_scope(limit, inputs.valuation)
    lt = limit.limit_type
    if lt in (LimitType.VAR, LimitType.EXPECTED_SHORTFALL):
        if inputs.var is None:
            return float("nan"), len(ids)
        cols = [c for c in inputs.var.pnl.columns if c in set(ids)]
        if not cols:
            return 0.0, 0
        var, es, _ = tail_measures(inputs.var.pnl[cols].sum(axis=1), inputs.var.config)
        return (var if lt is LimitType.VAR else es), len(cols)
    if lt is LimitType.STRESS_LOSS:
        if not inputs.stress:
            return float("nan"), len(ids)
        worst = min(float(r.pnl.reindex(ids).fillna(0.0).sum()) for r in inputs.stress)
        return max(-worst, 0.0), len(ids)
    if lt in _SENS_MEASURE:
        measure = _SENS_MEASURE[lt]
        if lt is LimitType.COMMODITY_DELTA and limit.scope.risk_factor in ("BTC", "ETH"):
            measure = "CRYPTO_DELTA"  # digital assets reuse the commodity-delta limit type
        s = _sens_in_scope(limit, inputs.sensitivities, ids, measure)
        return float(abs(s["value"].sum())), int(s["trade_id"].nunique())
    if lt is LimitType.CONCENTRATION:
        # Share of the scope's absolute sensitivity in one bucket or underlying. The measure
        # is inferred from what the filter names: a tenor bucket means DV01, otherwise the
        # underlying's asset class decides.
        measure = (
            "DV01" if limit.scope.tenor_bucket else _measure_for_underlying(limit.scope.risk_factor or "")
        )
        total = inputs.sensitivities[
            (inputs.sensitivities["measure"] == measure) & inputs.sensitivities["trade_id"].isin(ids)
        ]
        if limit.scope.currency and measure == "DV01":
            total = total[total["underlying"] == limit.scope.currency]
        part = _sens_in_scope(limit, inputs.sensitivities, ids, measure)
        group = "bucket" if limit.scope.tenor_bucket else "underlying"
        denom = float(total.groupby(group)["value"].sum().abs().sum())  # sum of |net| per bucket
        return (float(abs(part["value"].sum())) / denom if denom else 0.0), int(total["trade_id"].nunique())
    if lt is LimitType.COUNTERPARTY_EXPOSURE:
        v = inputs.valuation
        rows = (
            v[(v["counterparty_id"] == limit.scope.entity_id) & v["netting_set_id_present"]]
            if "netting_set_id_present" in v.columns
            else v[v["counterparty_id"] == limit.scope.entity_id]
        )
        return max(float(rows["pv"].fillna(0.0).sum()), 0.0), len(rows)
    return float("nan"), len(ids)


def _measure_for_underlying(u: str) -> str:
    if u in ("BTC", "ETH"):
        return "CRYPTO_DELTA"
    if u in ("BRENT", "WTI", "NATGAS", "GOLD", "SILVER", "COPPER", "ALUMINIUM"):
        return "CMD_DELTA"
    if "/" in u or (len(u) == 6 and u.isupper()):
        return "FX_DELTA"
    if u.startswith(("CDX", "ITRAXX")):
        return "CS01"
    return "EQ_DELTA"


def status_of(utilisation: float, warning: float) -> str:
    if np.isnan(utilisation):
        return "NO_DATA"
    if utilisation >= 1.0:
        return "BREACH"
    if utilisation >= warning:
        return "WARNING"
    return "OK"


def monitor(
    limits: list[Limit],
    inputs: RiskInputs,
    on: date,
    base_amounts: dict[str, float] | None = None,
    increase_ids: dict[str, str] | None = None,
) -> pd.DataFrame:
    """``limits`` should already carry effective amounts (see ``workflow.effective_limits``);
    ``base_amounts`` and ``increase_ids`` record where an approved increase applied."""
    base_amounts = base_amounts or {}
    increase_ids = increase_ids or {}
    rows = []
    for lim in limits:
        if not lim.is_effective(on):
            continue
        current, n = current_value(lim, inputs)
        util = current / lim.amount if lim.amount else float("nan")
        filters = ", ".join(
            f"{k}={v}" for k, v in lim.scope.model_dump().items() if k not in ("level", "entity_id") and v
        )
        rows.append(
            {
                "limit_id": lim.limit_id,
                "limit_type": lim.limit_type.value,
                "level": lim.scope.level.name,
                "entity_id": lim.scope.entity_id,
                "filters": filters,
                "amount": lim.amount,
                "base_amount": base_amounts.get(lim.limit_id, lim.amount),
                "increase_id": increase_ids.get(lim.limit_id),
                "current": current,
                "utilisation": util,
                "status": status_of(util, lim.warning_threshold),
                "warning_threshold": lim.warning_threshold,
                "owner": lim.owner,
                "trades_in_scope": n,
            }
        )
    out = pd.DataFrame(rows, columns=COLUMNS)
    order = {"BREACH": 0, "WARNING": 1, "OK": 2, "NO_DATA": 3}
    return (
        out.assign(_o=out["status"].map(order))
        .sort_values(["_o", "utilisation"], ascending=[True, False])
        .drop(columns="_o")
        .reset_index(drop=True)
    )
