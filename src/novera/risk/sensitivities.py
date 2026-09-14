"""Bump-and-reprice sensitivities. Methodology record MR-001.

Conventions (all in reporting currency, signed as P&L for the stated move):
    DV01          P&L for +1bp in one zero-curve node (ladder) or the whole curve (parallel)
    CS01          P&L for +1bp in a CDS index spread
    FX_DELTA      P&L for +1% in an FX spot factor (pair as quoted in the snapshot)
    EQ_DELTA      P&L for +1% in an equity or index spot
    CMD_DELTA     P&L for +1% across a commodity curve
    CRYPTO_DELTA  P&L for +1% in a crypto spot
    VEGA          P&L for +1 vol point across a surface
    GAMMA         PV(+1%) + PV(-1%) - 2 PV, per spot factor with options on it
    THETA         PV(as_of + 1 day) - PV(as_of), market unchanged
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pandas as pd

from novera.risk.revaluation import Portfolio
from novera.risk.scenarios import shocks_for_prefix

MODEL_VERSION = "1.0.0"

BUMPS = {
    "DV01": 0.0001,
    "CS01": 1.0,
    "FX_DELTA": 0.01,
    "EQ_DELTA": 0.01,
    "CMD_DELTA": 0.01,
    "CRYPTO_DELTA": 0.01,
    "VEGA": 0.01,
    "GAMMA": 0.01,
}

COLUMNS = ["trade_id", "measure", "factor_id", "bucket", "underlying", "bump", "value"]


@dataclass
class SensitivityConfig:
    dv01_ladder: bool = True
    gamma: bool = True
    theta: bool = True
    vega: bool = True


def _rows(
    pnl: dict[str, float], measure: str, factor_id: str, bucket: str, underlying: str, bump: float
) -> list:
    return [(tid, measure, factor_id, bucket, underlying, bump, v) for tid, v in pnl.items() if v != 0.0]


def compute_sensitivities(pf: Portfolio, cfg: SensitivityConfig | None = None) -> pd.DataFrame:
    """Long table, one row per (trade, measure, factor). Zero rows are dropped."""
    cfg = cfg or SensitivityConfig()
    base = pf.base
    rows: list = []

    # Rates: node ladder per currency (parallel DV01 is the sum of the ladder).
    currencies = sorted({f.split(":")[1] for f in base.factors_with_prefix("IR:")})
    for ccy in currencies:
        nodes = base.factors_with_prefix(f"IR:{ccy}:")
        if cfg.dv01_ladder:
            for fid in nodes:
                tenor = fid.split(":")[2]
                rows += _rows(
                    pf.pnl_under_shocks({fid: BUMPS["DV01"]}), "DV01", fid, tenor, ccy, BUMPS["DV01"]
                )
        else:
            rows += _rows(
                pf.pnl_under_shocks(dict.fromkeys(nodes, BUMPS["DV01"])),
                "DV01",
                f"IR:{ccy}:",
                "PARALLEL",
                ccy,
                BUMPS["DV01"],
            )

    for fid in base.factors_with_prefix("CDS:"):
        rows += _rows(
            pf.pnl_under_shocks({fid: BUMPS["CS01"]}), "CS01", fid, "", fid.split(":")[1], BUMPS["CS01"]
        )

    spot_groups = [
        ("FX:", "FX_DELTA"),
        ("EQ:", "EQ_DELTA"),
        ("EQIDX:", "EQ_DELTA"),
        ("CRYPTO:", "CRYPTO_DELTA"),
    ]
    for prefix, measure in spot_groups:
        for fid in base.factors_with_prefix(prefix):
            bump = BUMPS[measure]
            up = pf.pnl_under_shocks({fid: bump})
            rows += _rows(up, measure, fid, "", fid.split(":")[1], bump)
            if cfg.gamma and up:
                option_ids = {
                    tid for tid in up if pf.by_id[tid].product_type.value in ("FX_OPTION", "EQUITY_OPTION")
                }
                if option_ids:
                    down = pf.pnl_under_shocks({fid: -bump})
                    gamma = {tid: up.get(tid, 0.0) + down.get(tid, 0.0) for tid in option_ids}
                    rows += _rows(gamma, "GAMMA", fid, "", fid.split(":")[1], bump)

    commodities = sorted({f.split(":")[1] for f in base.factors_with_prefix("CMD:")})
    for code in commodities:
        shocks = shocks_for_prefix(base, f"CMD:{code}:", BUMPS["CMD_DELTA"])
        rows += _rows(pf.pnl_under_shocks(shocks), "CMD_DELTA", f"CMD:{code}:", "", code, BUMPS["CMD_DELTA"])

    if cfg.vega:
        underlyings = sorted({f.split(":")[1] for f in base.factors_with_prefix("VOL:")})
        for u in underlyings:
            # Vol nodes are RELATIVE factors in the snapshot; +1 vol point is +0.01/vol relative per node.
            shocks = {f: BUMPS["VEGA"] / base.values[f] for f in base.factors_with_prefix(f"VOL:{u}:")}
            rows += _rows(pf.pnl_under_shocks(shocks), "VEGA", f"VOL:{u}:", "", u, BUMPS["VEGA"])

    if cfg.theta:
        tomorrow = pf.as_of + timedelta(days=1)
        pv_t = pf.pv_under(base, as_of=tomorrow)
        theta = {tid: pv_t[tid] - pf.base_pv[tid] for tid in pv_t}
        rows += _rows(theta, "THETA", "", "1D", "", 1.0)

    return pd.DataFrame(rows, columns=COLUMNS)


def aggregate(sens: pd.DataFrame, valuation: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Sum sensitivities by hierarchy columns taken from the valuation table."""
    keys = valuation[["trade_id", *by]].drop_duplicates("trade_id")
    merged = sens.merge(keys, on="trade_id", how="left")
    return (
        merged.groupby([*by, "measure", "factor_id", "bucket", "underlying"], dropna=False)["value"]
        .sum()
        .reset_index()
    )
