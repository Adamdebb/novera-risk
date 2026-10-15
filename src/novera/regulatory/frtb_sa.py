"""FRTB standardised approach, sensitivities-based method. Record REG-001.

Structure follows the Basel text (MAR21): delta, vega and curvature charges per risk
class, bucket-level aggregation with prescribed risk weights and correlations, three
correlation scenarios (low, medium, high) with the maximum taken, plus a simplified
default risk charge. Parameters are the published 2019 values where the platform's
risk factors map directly; simplifications are listed in the record.

Inputs are the platform's stored sensitivities (MR-001) in reporting currency:
DV01 per node (P&L per +1bp), CS01 (per +1bp), deltas per +1%, vega per +1 vol point,
gamma as the ±1% second difference.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from novera.regulatory.allocation import UNASSIGNED, by_bump, desk_map, with_euler
from novera.simulation import reference_levels as _ref

MODEL_VERSION = "1.3.0"

# --- GIRR ----------------------------------------------------------------------------------
GIRR_VERTICES = np.array([0.25, 0.5, 1, 2, 3, 5, 10, 15, 20, 30])
GIRR_RW = {
    0.25: 0.017,
    0.5: 0.017,
    1: 0.016,
    2: 0.013,
    3: 0.012,
    5: 0.011,
    10: 0.011,
    15: 0.011,
    20: 0.011,
    30: 0.011,
}
GIRR_THETA = 0.03
GIRR_GAMMA = 0.5  # across currencies
GIRR_CROSS_CURVE_RHO = 0.999  # two curves of one currency (MAR21.46), e.g. ESTR and EURIBOR-6M
# Cross-currency basis against USD: one GIRR risk factor per currency with no term structure,
# risk weight and correlation to the curve factors as read from MAR21 (REG-001: to be checked
# against the text before relying on them).
GIRR_XCCY_RW = 0.016
GIRR_XCCY_RHO = 0.0
XCCY_KEY = "XCCY"
NODE_TO_VERTEX = {
    "1M": 0.25,
    "3M": 0.25,
    "6M": 0.5,
    "1Y": 1,
    "2Y": 2,
    "3Y": 3,
    "5Y": 5,
    "7Y": 5,
    "10Y": 10,
    "15Y": 15,
    "20Y": 20,
    "30Y": 30,
}
# 7Y split half to 5y half to 10y per the text; handled in mapping below.

# --- CSR non-securitisation (indices mapped to bucket quality) ---------------------------------
CSR_BUCKETS = {
    "CDX.NA.IG": ("IG", 0.05),
    "ITRAXX.EUR.MAIN": ("IG", 0.05),
    "CDX.NA.HY": ("HY", 0.12),
    "ITRAXX.EUR.XOVER": ("HY", 0.12),
    # Single names by rating bucket (reference data of the simulated universe).
    **{e: (v[5], 0.05 if v[5] == "IG" else 0.12) for e, v in _ref.CDS_SINGLE_NAMES.items()},
}
COMMODITY_CODES = ("BRENT", "WTI", "NATGAS", "GOLD", "SILVER", "COPPER", "ALUMINIUM")
RRAO_RW = 0.001  # residual risk add-on: 0.1% of gross notional for barrier and digital options
CSR_RHO_SAME_BUCKET = 0.35
CSR_GAMMA = 0.05  # across buckets of different quality (IG vs HY)

# --- Equity ----------------------------------------------------------------------------------
EQ_RW = {"large_cap_developed": 0.25, "index_developed": 0.15}
EQ_RHO = {"large_cap_developed": 0.25, "index_developed": 0.75}
EQ_GAMMA = 0.15

# --- FX --------------------------------------------------------------------------------------
FX_RW = 0.15
FX_RW_LIQUID = 0.15 / np.sqrt(2)
FX_LIQUID = {
    "EURUSD",
    "USDJPY",
    "GBPUSD",
    "AUDUSD",
    "USDCAD",
    "USDCHF",
    "USDMXN",
    "USDCNY",
    "NZDUSD",
    "USDSEK",
    "USDNOK",
    "USDSGD",
    "EURGBP",
    "EURJPY",
}
FX_RHO = 0.6

# --- Commodity -------------------------------------------------------------------------------
CMD_BUCKET = {
    "BRENT": ("energy", 0.30),
    "WTI": ("energy", 0.30),
    "NATGAS": ("natgas", 0.45),
    "GOLD": ("precious", 0.20),
    "SILVER": ("precious", 0.20),
    "COPPER": ("base", 0.20),
    "ALUMINIUM": ("base", 0.20),
}
CMD_RHO = {"energy": 0.95, "natgas": 0.90, "precious": 0.75, "base": 0.55}
CMD_GAMMA = 0.20

# --- Vega risk weights (RW_sigma = 55%, liquidity horizon scaling capped at 100%) ---------------
VEGA_RW = {"GIRR": 1.0, "EQ": min(0.55 * np.sqrt(20 / 10), 1.0), "FX": 1.0, "CSR": 1.0, "CMD": 1.0}
VEGA_RHO = 0.5

# --- Default risk charge (simplified: index protection by quality) -------------------------------
DRC_RW = {"IG": 0.03, "HY": 0.15}
DRC_LGD = 0.6

CORRELATION_SCENARIOS = {"low": 0.75, "medium": 1.0, "high": 1.25}
CRYPTO_RW = 1.0  # BCBS group 2 crypto: capital equal to exposure


@dataclass
class ClassCharge:
    risk_class: str
    delta: float
    vega: float
    curvature: float
    scenario: str  # which correlation scenario gave the maximum

    @property
    def total(self) -> float:
        return self.delta + self.vega + self.curvature


@dataclass
class FRTBSAResult:
    classes: list[ClassCharge]
    drc: float
    rrao: float
    crypto: float
    by_desk: pd.DataFrame = field(default_factory=pd.DataFrame)
    detail: pd.DataFrame = field(default_factory=pd.DataFrame)  # bucket-level table

    @property
    def sbm(self) -> float:
        return sum(c.total for c in self.classes)

    @property
    def total(self) -> float:
        return self.sbm + self.drc + self.rrao + self.crypto

    def summary(self) -> dict:
        return {
            "sbm": self.sbm,
            "drc": self.drc,
            "rrao": self.rrao,
            "crypto": self.crypto,
            "total": self.total,
            **{f"{c.risk_class}_delta": c.delta for c in self.classes},
            **{f"{c.risk_class}_vega": c.vega for c in self.classes},
            **{f"{c.risk_class}_curvature": c.curvature for c in self.classes},
        }


def _aggregate(ws: dict[str, dict[str, float]], rho_within, gamma: float, scale: float) -> float:
    """Bucket-level then cross-bucket aggregation. ``ws`` maps bucket -> {factor: weighted sensitivity}.
    ``rho_within(bucket, f1, f2)`` gives the within-bucket correlation; ``scale`` is the
    correlation-scenario multiplier."""
    kb: dict[str, float] = {}
    sb: dict[str, float] = {}
    for b, items in ws.items():
        keys = list(items)
        v = np.array([items[k] for k in keys])
        if not len(v):
            continue
        r = np.array(
            [
                [
                    1.0 if i == j else min(max(rho_within(b, keys[i], keys[j]) * scale, -1.0), 1.0)
                    for j in range(len(keys))
                ]
                for i in range(len(keys))
            ]
        )
        kb[b] = float(np.sqrt(max(v @ r @ v, 0.0)))
        sb[b] = float(v.sum())
    if not kb:
        return 0.0
    g = min(gamma * scale, 1.0)
    bs = list(kb)
    total = sum(kb[b] ** 2 for b in bs)
    for i, b1 in enumerate(bs):
        for b2 in bs[i + 1 :]:
            total += 2 * g * sb[b1] * sb[b2]
    if total < 0:  # fallback of the text: use S_b = max(min(S_b, K_b), -K_b)
        total = sum(kb[b] ** 2 for b in bs)
        for i, b1 in enumerate(bs):
            for b2 in bs[i + 1 :]:
                s1, s2 = (max(min(sb[b], kb[b]), -kb[b]) for b in (b1, b2))
                total += 2 * g * s1 * s2
    return float(np.sqrt(max(total, 0.0)))


def _girr_rho(_b: str, f1: str, f2: str) -> float:
    """Factor keys are ``"5.0"`` on the discount curve and ``"6M:5.0"`` on a term-rate curve.
    Tenor correlation as MAR21.46; two different curves of one currency multiply it by 0.999."""
    if XCCY_KEY in (f1, f2):
        return 1.0 if f1 == f2 else GIRR_XCCY_RHO
    c1, _, v1 = f1.rpartition(":")
    c2, _, v2 = f2.rpartition(":")
    t1, t2 = float(v1), float(v2)
    rho = max(np.exp(-GIRR_THETA * abs(t1 - t2) / min(t1, t2)), 0.4)
    return rho if c1 == c2 else rho * GIRR_CROSS_CURVE_RHO


def _girr_ws(sens: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Weighted sensitivities per currency. The platform bumps a term-rate curve as discount
    curve plus basis, so a DV01 bump drags the EURIBOR projection with it. MAR21 wants each
    curve bumped with the others held: the EURIBOR curve's sensitivity is BASIS01 and the
    discount curve's is DV01 less BASIS01, vertex by vertex."""
    raw: dict[str, dict[str, float]] = {}

    def add(ccy: str, curve: str, bucket: str, v: float, sign: float) -> None:
        targets = [(5, 0.5), (10, 0.5)] if bucket == "7Y" else [(NODE_TO_VERTEX[bucket], 1.0)]
        for vert, w in targets:
            key = f"{curve}:{float(vert)}" if curve else str(float(vert))
            raw.setdefault(ccy, {})
            raw[ccy][key] = raw[ccy].get(key, 0.0) + sign * w * v / 1e-4  # per 1bp -> per unit rate

    dv = sens[sens["measure"] == "DV01"]
    for (ccy, bucket), v in dv.groupby(["underlying", "bucket"])["value"].sum().items():
        add(ccy, "", bucket, v, 1.0)
    basis = sens[sens["measure"] == "BASIS01"]
    if not basis.empty:
        curve = basis["factor_id"].str.split(":").str[2]
        for (ccy, index_tenor, bucket), v in (
            basis.assign(curve=curve).groupby(["underlying", "curve", "bucket"])["value"].sum().items()
        ):
            add(ccy, index_tenor, bucket, v, 1.0)
            add(ccy, "", bucket, v, -1.0)
    out = {
        ccy: {k: s * GIRR_RW[float(k.rpartition(":")[2])] for k, s in items.items()}
        for ccy, items in raw.items()
    }
    xccy = sens[sens["measure"] == "XCCY01"]
    for ccy, v in xccy.groupby("underlying")["value"].sum().items():
        out.setdefault(ccy, {})[XCCY_KEY] = v / 1e-4 * GIRR_XCCY_RW  # parallel basis, per unit
    return out


def _class_charge(
    name: str, ws_fn, rho_fn, gamma: float, vega_ws: dict[str, dict[str, float]], curvature: float
) -> ClassCharge:
    best = None
    for label, scale in CORRELATION_SCENARIOS.items():
        d = _aggregate(ws_fn, rho_fn, gamma, scale)
        vg = _aggregate(vega_ws, lambda b, a, c: VEGA_RHO, gamma, scale) if vega_ws else 0.0
        tot = d + vg + curvature
        if best is None or tot > best[0]:
            best = (tot, label, d, vg)
    return ClassCharge(name, best[2], best[3], curvature, best[1])


def _curvature(sens: pd.DataFrame, measure: str, rw_of) -> float:
    """Curvature from the stored gamma (±1% second difference): CVR ≈ −½ Γ RW²; charge on
    negative gamma (short optionality), summed within the class."""
    g = sens[sens["measure"] == "GAMMA"]
    if g.empty:
        return 0.0
    charge = 0.0
    for u, v in g.groupby("underlying")["value"].sum().items():
        gamma_unit = v / (0.01**2)  # per unit relative move squared
        rw = rw_of(u)
        cvr = -0.5 * gamma_unit * rw**2
        charge += max(cvr, 0.0)
    return float(charge)


def _charges(
    sens: pd.DataFrame,
    valuation: pd.DataFrame,
    base_vols: dict[str, float],
    exotic_notional: pd.Series | None = None,
) -> FRTBSAResult:
    """All charges for one sensitivity set, no attribution."""
    classes, detail = [], []
    is_fx = lambda u: u.endswith("USD") or u.startswith("USD")  # noqa: E731
    is_index = lambda u: u in ("SPX", "NDX", "SX5E", "DAX", "FTSE", "NKY")  # noqa: E731
    is_cmd = lambda u: u in COMMODITY_CODES  # noqa: E731

    def vega_ws_for(pred, rw: float, prefix: str = "VOL:") -> dict[str, dict[str, float]]:
        """Vega rows of one family. Weighted sensitivity = vega × implied vol × RW; surfaces are
        per vol point (×100 to per unit vol), swaption cubes per normal bp (× cube level in bp)."""
        vg = sens[(sens["measure"] == "VEGA") & sens["factor_id"].str.startswith(prefix)]
        vg = vg[vg["underlying"].map(pred).astype(bool)]
        out: dict[str, dict[str, float]] = {}
        for u, v in vg.groupby("underlying")["value"].sum().items():
            if prefix == "SWVOL:":
                out.setdefault("all", {})[u] = v * base_vols.get(f"SWVOL:{u}", 80.0) * rw
            else:
                out.setdefault("all", {})[u] = v * 100 * base_vols.get(u, 0.2) * rw
        return out

    # GIRR: delta ladder plus swaption vega.
    classes.append(
        _class_charge(
            "GIRR",
            _girr_ws(sens),
            _girr_rho,
            GIRR_GAMMA,
            vega_ws_for(lambda u: True, VEGA_RW["GIRR"], "SWVOL:"),
            0.0,
        )
    )

    # CSR non-securitisation.
    cs = sens[sens["measure"] == "CS01"]
    csr: dict[str, dict[str, float]] = {}
    for u, v in cs.groupby("underlying")["value"].sum().items():
        bucket, rw = CSR_BUCKETS.get(u, ("HY", 0.12))
        csr.setdefault(bucket, {})[u] = (v / 1e-4) * rw
    classes.append(_class_charge("CSR", csr, lambda b, a, c: CSR_RHO_SAME_BUCKET, CSR_GAMMA, {}, 0.0))

    # Equity.
    eq = sens[sens["measure"] == "EQ_DELTA"]
    eqws: dict[str, dict[str, float]] = {}
    for u, v in eq.groupby("underlying")["value"].sum().items():
        bucket = "index_developed" if is_index(u) else "large_cap_developed"
        eqws.setdefault(bucket, {})[u] = (v * 100) * EQ_RW[bucket]
    eq_curv = _curvature(
        sens[~sens["underlying"].map(is_fx) & ~sens["underlying"].map(is_cmd)],
        "GAMMA",
        lambda u: EQ_RW["index_developed" if is_index(u) else "large_cap_developed"],
    )
    classes.append(
        _class_charge(
            "EQ",
            eqws,
            lambda b, a, c: EQ_RHO[b],
            EQ_GAMMA,
            vega_ws_for(lambda u: not is_fx(u) and not is_cmd(u), VEGA_RW["EQ"]),
            eq_curv,
        )
    )

    # FX.
    fx = sens[sens["measure"] == "FX_DELTA"]
    fxws: dict[str, dict[str, float]] = {"all": {}}
    for u, v in fx.groupby("underlying")["value"].sum().items():
        fxws["all"][u] = (v * 100) * (FX_RW_LIQUID if u in FX_LIQUID else FX_RW)
    fx_curv = _curvature(
        sens[sens["underlying"].map(is_fx)], "GAMMA", lambda u: FX_RW_LIQUID if u in FX_LIQUID else FX_RW
    )
    classes.append(
        _class_charge("FX", fxws, lambda b, a, c: FX_RHO, 1.0, vega_ws_for(is_fx, VEGA_RW["FX"]), fx_curv)
    )

    # Commodity.
    cm = sens[sens["measure"] == "CMD_DELTA"]
    cmws: dict[str, dict[str, float]] = {}
    for u, v in cm.groupby("underlying")["value"].sum().items():
        bucket, rw = CMD_BUCKET.get(u, ("base", 0.20))
        cmws.setdefault(bucket, {})[u] = (v * 100) * rw
    cmd_curv = _curvature(
        sens[sens["underlying"].map(is_cmd)], "GAMMA", lambda u: CMD_BUCKET.get(u, ("base", 0.20))[1]
    )
    classes.append(
        _class_charge(
            "CMD", cmws, lambda b, a, c: CMD_RHO[b], CMD_GAMMA, vega_ws_for(is_cmd, VEGA_RW["CMD"]), cmd_curv
        )
    )

    # Default risk charge on index protection: JTD from CS01 (5y duration 4.5), hedge benefit ratio.
    drc = 0.0
    for quality in ("IG", "HY"):
        fams = [f for f, (q, _) in CSR_BUCKETS.items() if q == quality]
        jtd_long = jtd_short = 0.0
        for v in cs[cs["underlying"].isin(fams)].groupby("underlying")["value"].sum():
            jtd = abs(v) / 1e-4 / 4.5 * DRC_LGD
            if v > 0:  # gains when spreads widen: protection bought, short credit
                jtd_short += jtd
            else:
                jtd_long += jtd
        hbr = jtd_long / (jtd_long + jtd_short) if (jtd_long + jtd_short) else 0.0
        drc += max(DRC_RW[quality] * jtd_long - hbr * DRC_RW[quality] * jtd_short, 0.0)

    live = valuation[valuation["status"] == "LIVE"]
    crypto = CRYPTO_RW * float(live[live["product_type"] == "CRYPTO_SPOT"]["pv"].abs().sum())
    rrao = 0.0
    if exotic_notional is not None and len(exotic_notional):
        rrao = RRAO_RW * float(exotic_notional.reindex(live["trade_id"]).fillna(0.0).sum())
    for name, ws in (("GIRR", _girr_ws(sens)), ("CSR", csr), ("EQ", eqws), ("FX", fxws), ("CMD", cmws)):
        for b, items in ws.items():
            for f, v in items.items():
                detail.append({"risk_class": name, "bucket": b, "factor": f, "weighted_sensitivity": v})
    return FRTBSAResult(classes, drc, rrao, crypto, detail=pd.DataFrame(detail))


def frtb_sa(
    sens: pd.DataFrame,
    valuation: pd.DataFrame,
    base_vols: dict[str, float] | None = None,
    exotic_notional: pd.Series | None = None,
) -> FRTBSAResult:
    """Firm charge plus its desk allocation (REG-008): Euler contributions, incremental and
    standalone charges, with each desk's standalone breakdown by risk class.
    ``exotic_notional`` is gross notional by trade id for the residual risk add-on."""
    base_vols = base_vols or {}
    res = _charges(sens, valuation, base_vols, exotic_notional)
    desk = desk_map(valuation)
    s2 = sens.assign(desk_id=sens["trade_id"].map(desk).fillna(UNASSIGNED))
    v2 = valuation.assign(desk_id=valuation["trade_id"].map(desk))
    ex2 = exotic_notional if exotic_notional is not None else pd.Series(dtype=float)

    def charge(scales, other: float) -> float:
        ks, kv = s2["desk_id"].map(scales).fillna(other), v2["desk_id"].map(scales).fillna(other)
        kx = ex2.index.map(lambda t: scales.get(desk.get(t, UNASSIGNED), other))
        return _charges(
            s2.assign(value=s2["value"] * ks)[ks != 0],
            v2.assign(pv=v2["pv"] * kv)[kv != 0],
            base_vols,
            ex2 * np.asarray(kx, dtype=float),
        ).total

    rows = []
    for d, g in s2.groupby("desk_id"):
        r = _charges(g, v2[v2["desk_id"] == d], base_vols, exotic_notional)
        rows.append(
            {
                "desk_id": d,
                "sbm": r.sbm,
                "drc": r.drc,
                "rrao": r.rrao,
                "crypto": r.crypto,
                **{f"{c.risk_class}": c.total for c in r.classes},
            }
        )
    desks = sorted(set(s2["desk_id"]) | set(v2.loc[v2["status"] == "LIVE", "desk_id"]))
    by_desk = with_euler(by_bump(charge, desks, res.total), res.total)
    if rows:
        by_desk = by_desk.merge(pd.DataFrame(rows), on="desk_id", how="left")
    res.by_desk = by_desk.sort_values("euler", ascending=False, ignore_index=True)
    return res
