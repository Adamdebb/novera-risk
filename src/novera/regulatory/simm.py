"""SIMM-lite initial margin per netting set. Record REG-004.

The structure of the ISDA SIMM (delta and vega margins per risk class, bucket aggregation
with correlations, product-class aggregation) with approximate published-style
parameters. Not the licensed calibration: a demonstration of the mechanics on the
platform's sensitivities.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from novera.simulation import reference_levels as _ref

MODEL_VERSION = "1.3.0"

# Risk weights per bp of sensitivity by tenor (regular-volatility currencies), in currency units per bp.
IR_RW = {
    "1M": 113,
    "3M": 98,
    "6M": 69,
    "1Y": 56,
    "2Y": 52,
    "3Y": 51,
    "5Y": 51,
    "7Y": 51,
    "10Y": 51,
    "15Y": 53,
    "20Y": 56,
    "30Y": 64,
}
IR_RW_HIGH_VOL = 1.6  # multiplier for MXN, BRL, ZAR, TRY, ARS, INR
IR_TENOR_RHO_THETA = 0.15
IR_SUBCURVE_RHO = 0.993  # ISDA SIMM correlation between sub-curves of one currency
# Cross-currency basis: one factor per currency, approximate published-style weight per bp
# and correlation to the curve factors (REG-004).
IR_XCCY_RW = 21.0
IR_XCCY_RHO = 0.2
IR_CCY_GAMMA = 0.27
CS_RW = {"IG": 75, "HY": 240}  # per bp
CS_RHO = 0.5
EQ_RW = {"large_cap_developed": 0.25, "index_developed": 0.17}  # per unit relative move
EQ_RHO = {"large_cap_developed": 0.25, "index_developed": 0.75}
EQ_GAMMA = 0.2
FX_RW = 0.081
FX_RHO = 0.5
CMD_RW = {"energy": 0.24, "natgas": 0.40, "precious": 0.19, "base": 0.21}
CMD_RHO = {"energy": 0.9, "natgas": 0.9, "precious": 0.7, "base": 0.6}
CMD_GAMMA = 0.2
VEGA_RW = {"EQ": 0.3, "FX": 0.3, "CMD": 0.36, "IR": 0.16}
COMMODITY_CODES = ("BRENT", "WTI", "NATGAS", "GOLD", "SILVER", "COPPER", "ALUMINIUM")
VEGA_RHO = 0.5
PRODUCT_CLASS_PSI = 0.0  # product classes (RatesFX, Credit, Equity, Commodity) are summed
TENOR_YEARS = {
    "1M": 1 / 12,
    "3M": 0.25,
    "6M": 0.5,
    "1Y": 1,
    "2Y": 2,
    "3Y": 3,
    "5Y": 5,
    "7Y": 7,
    "10Y": 10,
    "15Y": 15,
    "20Y": 20,
    "30Y": 30,
}


def _ir_rho(a: str, b: str) -> float:
    """Keys are ``"5Y"`` on the discount curve, ``"6M:5Y"`` on a term-rate sub-curve and
    ``"XCCY"`` for the cross-currency basis."""
    if "XCCY" in (a, b):
        return 1.0 if a == b else IR_XCCY_RHO
    ca, _, a = a.rpartition(":")
    cb, _, b = b.rpartition(":")
    ta, tb = TENOR_YEARS[a], TENOR_YEARS[b]
    rho = max(np.exp(-IR_TENOR_RHO_THETA * abs(ta - tb) / min(ta, tb)), 0.4)
    return rho if ca == cb else rho * IR_SUBCURVE_RHO


def _k(ws: dict[str, float], rho) -> float:
    keys = list(ws)
    v = np.array([ws[k] for k in keys])
    if not len(v):
        return 0.0
    r = np.array(
        [[1.0 if i == j else rho(keys[i], keys[j]) for j in range(len(keys))] for i in range(len(keys))]
    )
    return float(np.sqrt(max(v @ r @ v, 0.0)))


def _across(kb: dict[str, float], sb: dict[str, float], gamma: float) -> float:
    total = sum(k**2 for k in kb.values())
    bs = list(kb)
    for i, a in enumerate(bs):
        for b in bs[i + 1 :]:
            total += 2 * gamma * max(min(sb[a], kb[a]), -kb[a]) * max(min(sb[b], kb[b]), -kb[b])
    return float(np.sqrt(max(total, 0.0)))


@dataclass
class SIMMResult:
    by_netting_set: pd.DataFrame
    total: float
    detail: pd.DataFrame = field(default_factory=pd.DataFrame)


def simm_for_sensitivities(sens: pd.DataFrame, base_vols: dict[str, float]) -> dict[str, float]:
    """Margin by risk class for one sensitivity set (already filtered to a netting set)."""
    out: dict[str, float] = {}
    # Rates.
    # A term-rate sub-curve (EURIBOR-6M) is its own risk factor: its delta is BASIS01 and the
    # discount curve's is DV01 less BASIS01, as in FRTB SA (REG-004).
    dv = sens[sens["measure"] == "DV01"]
    basis = sens[sens["measure"] == "BASIS01"]
    xccy = sens[sens["measure"] == "XCCY01"]
    kb, sb = {}, {}
    for ccy in sorted(set(dv["underlying"]) | set(xccy["underlying"])):
        mult = IR_RW_HIGH_VOL if ccy in ("MXN", "BRL", "ZAR", "TRY", "ARS", "INR") else 1.0
        delta = dv[dv["underlying"] == ccy].groupby("bucket")["value"].sum().to_dict()
        bg = basis[basis["underlying"] == ccy]
        curve_of = bg["factor_id"].str.split(":").str[2]
        for (curve, b), v in bg.groupby([curve_of, "bucket"])["value"].sum().items():
            delta[f"{curve}:{b}"] = delta.get(f"{curve}:{b}", 0.0) + v
            delta[b] = delta.get(b, 0.0) - v
        # v is P&L per bp; RW is per bp, so WS = v(per bp) x RW(bp) in currency units.
        ws = {k: v * IR_RW.get(k.rpartition(":")[2], 51) * mult for k, v in delta.items()}
        xg = xccy[xccy["underlying"] == ccy]["value"].sum()
        if xg:
            ws["XCCY"] = xg * IR_XCCY_RW * mult
        kb[ccy] = _k(ws, _ir_rho)
        sb[ccy] = sum(ws.values())
    if kb:
        out["IR"] = _across(kb, sb, IR_CCY_GAMMA)
    # Credit qualifying.
    cs = sens[sens["measure"] == "CS01"]
    if len(cs):
        ws = {u: v * CS_RW[_credit_quality(u)] for u, v in cs.groupby("underlying")["value"].sum().items()}
        out["CREDIT"] = _k(ws, lambda a, b: CS_RHO)
    # Equity.
    eq = sens[sens["measure"] == "EQ_DELTA"]
    kb, sb = {}, {}
    for u, v in eq.groupby("underlying")["value"].sum().items():
        b = "index_developed" if u in ("SPX", "NDX", "SX5E", "DAX", "FTSE", "NKY") else "large_cap_developed"
        kb.setdefault(b, {})[u] = v * 100 * EQ_RW[b]
    if kb:
        kbs = {b: _k(ws, lambda a, c, b=b: EQ_RHO[b]) for b, ws in kb.items()}
        sbs = {b: sum(ws.values()) for b, ws in kb.items()}
        out["EQUITY"] = _across(kbs, sbs, EQ_GAMMA)
    # FX.
    fx = sens[sens["measure"] == "FX_DELTA"]
    if len(fx):
        ws = {u: v * 100 * FX_RW for u, v in fx.groupby("underlying")["value"].sum().items()}
        out["FX"] = _k(ws, lambda a, b: FX_RHO)
    # Commodity.
    cm = sens[sens["measure"] == "CMD_DELTA"]
    kb = {}
    for u, v in cm.groupby("underlying")["value"].sum().items():
        b = {
            "BRENT": "energy",
            "WTI": "energy",
            "NATGAS": "natgas",
            "GOLD": "precious",
            "SILVER": "precious",
        }.get(u, "base")
        kb.setdefault(b, {})[u] = v * 100 * CMD_RW[b]
    if kb:
        kbs = {b: _k(ws, lambda a, c, b=b: CMD_RHO[b]) for b, ws in kb.items()}
        sbs = {b: sum(ws.values()) for b, ws in kb.items()}
        out["COMMODITY"] = _across(kbs, sbs, CMD_GAMMA)
    # Vega (equity, FX and commodity surfaces per vol point; swaption cubes per normal bp).
    vg = sens[sens["measure"] == "VEGA"]
    if len(vg):
        ws = {}
        for (fid, u), v in vg.groupby(["factor_id", "underlying"])["value"].sum().items():
            if str(fid).startswith("SWVOL:"):
                ws[f"SWVOL:{u}"] = v * base_vols.get(f"SWVOL:{u}", 80.0) * VEGA_RW["IR"]
                continue
            if u in COMMODITY_CODES:
                cls = "CMD"
            elif u.endswith("USD") or u.startswith("USD"):
                cls = "FX"
            else:
                cls = "EQ"
            ws[u] = v * 100 * base_vols.get(u, 0.2) * VEGA_RW[cls]
        out["VEGA"] = _k(ws, lambda a, b: VEGA_RHO)
    return out


def _credit_quality(underlying: str) -> str:
    if underlying in _ref.CDS_SINGLE_NAMES:
        return _ref.CDS_SINGLE_NAMES[underlying][5]
    return "IG" if ("IG" in underlying or "MAIN" in underlying) else "HY"


def simm(
    sens: pd.DataFrame,
    valuation: pd.DataFrame,
    base_vols: dict[str, float],
    csa_sets: set[str] | None = None,
    threshold: float = 0.0,
    ccp_sets: set[str] | frozenset[str] = frozenset(),
) -> SIMMResult:
    """IM per netting set: sum over risk classes of the class margin (the delta and vega margins
    are combined by simple sum, the SIMM's ψ cross-class terms are dropped).

    Both directions (decision 39.1): ``im_model`` is the margin the counterparty would post on our
    exposure, ``im_posted_model`` ours on theirs (the same model on the sign-flipped
    sensitivities, so curvature differs). Bilateral margin is exchanged only on a netting set with
    a CSA (``csa_sets``; all sets when None) and only above ``threshold`` per counterparty, split
    across its sets pro rata: ``im`` and ``im_posted``. A CCP set (``ccp_sets``) receives nothing
    and posts its full model margin, with no threshold."""
    v = (
        valuation[(valuation["status"] == "LIVE") & valuation["netting_set_id"].notna()]
        if "netting_set_id" in valuation
        else pd.DataFrame()
    )
    cols = ["netting_set_id", "counterparty_id", "im", "im_posted", "im_model", "im_posted_model", "ccp"]
    if v.empty:
        return SIMMResult(pd.DataFrame(columns=cols), 0.0)
    keys = v[["trade_id", "netting_set_id", "counterparty_id"]].drop_duplicates("trade_id")
    s2 = sens.merge(keys, on="trade_id", how="inner")
    rows, detail = [], []
    for ns, g in s2.groupby("netting_set_id"):
        parts = simm_for_sensitivities(g, base_vols)
        posted = simm_for_sensitivities(g.assign(value=-g["value"]), base_vols)
        rows.append(
            {
                "netting_set_id": ns,
                "counterparty_id": g["counterparty_id"].iloc[0],
                "im_model": float(sum(parts.values())),
                "im_posted_model": float(sum(posted.values())),
                "ccp": ns in ccp_sets,
                **parts,
            }
        )
        for cls, val in parts.items():
            detail.append({"netting_set_id": ns, "risk_class": cls, "margin": val})
    df = pd.DataFrame(rows)
    exchanged = (
        df["netting_set_id"].isin(csa_sets) if csa_sets is not None else pd.Series(True, index=df.index)
    )
    bilateral = exchanged & ~df["ccp"]
    for model, out in (("im_model", "im"), ("im_posted_model", "im_posted")):
        gross = df[model].where(bilateral, 0.0)
        total = gross.groupby(df["counterparty_id"]).transform("sum")
        above = (total - threshold).clip(lower=0.0)
        df[out] = (gross * (above / total.where(total > 0, 1.0))).fillna(0.0)
    df["im"] = df["im"].where(~df["ccp"], 0.0)
    df["im_posted"] = df["im_posted"].where(~df["ccp"], df["im_posted_model"])
    df = df.sort_values("im", ascending=False)
    return SIMMResult(df, float(df["im"].sum()), pd.DataFrame(detail))
