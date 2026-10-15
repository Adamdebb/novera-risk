"""Regulatory capital by desk: Euler contributions, the official allocation, with incremental and
standalone capital beside them for decisions. Record REG-008.

Euler: a desk's contribution is the derivative of the firm charge along the desk's own positions,
x_d · ∂K/∂x_d. For a charge that is homogeneous of degree one (scaling every position by λ scales
the charge by λ) the contributions add up to the charge exactly. FRTB IMA (expected shortfall
through the liquidity-horizon cascade) and BA-CVA are differentiated analytically; FRTB SA,
SA-CCR per netting set and the margin posted at a CCP by a central difference, the desk's
positions scaled by 1 ± ``EULER_STEP``. Where a charge is not homogeneous (the SA-CCR floors, CSA
thresholds and PFE multiplier) the contributions are rescaled to the charge and the scale is
reported with them (decision 41.1).

Incremental: the charge less the charge without the desk, what closing the desk would free. It
does not add up to the charge; the gap is the diversification the desk receives from the rest of
the firm. Standalone: the desk's charge on its own.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from novera.counterparty_risk.ccp import QCCP_TRADE_RW
from novera.regulatory.cva_capital import RHO, ba_cva_charge
from novera.regulatory.saccr import SACCRResult
from novera.regulatory.simm import simm_for_sensitivities

MODEL_VERSION = "1.0.0"
EULER_STEP = 1e-4  # relative bump of one desk's positions for a numerical derivative
UNASSIGNED = "UNASSIGNED"  # trades whose book has no desk
COMPONENTS = {
    "frtb_sa": "Market risk, FRTB SA",
    "frtb_ima": "Market risk, FRTB IMA",
    "saccr": "Counterparty credit risk, SA-CCR",
    "ba_cva": "CVA risk, BA-CVA",
    "ccp_trade": "CCP trade exposure",
    "ccp_default_fund": "CCP default fund",
}
STANDARDISED = ("frtb_sa", "saccr", "ba_cva", "ccp_trade", "ccp_default_fund")  # FRTB IMA is the alternative
TOTAL = "total_sa"

Charge = Callable[[Mapping[str, float], float], float]  # (scale by desk, scale of other desks) -> K


def desk_map(valuation: pd.DataFrame) -> dict[str, str]:
    """Trade id -> desk, ``UNASSIGNED`` where the book has none."""
    keys = valuation[["trade_id", "desk_id"]].drop_duplicates("trade_id")
    return dict(zip(keys["trade_id"], keys["desk_id"].fillna(UNASSIGNED), strict=True))


def by_bump(charge: Charge, desks: list[str], total: float) -> pd.DataFrame:
    """Euler (central difference), incremental and standalone charge of each desk."""
    rows = []
    for d in desks:
        up, down = charge({d: 1 + EULER_STEP}, 1.0), charge({d: 1 - EULER_STEP}, 1.0)
        rows.append(
            {
                "desk_id": d,
                "euler_raw": (up - down) / (2 * EULER_STEP),
                "incremental": total - charge({d: 0.0}, 1.0),
                "standalone": charge({d: 1.0}, 0.0),
            }
        )
    return pd.DataFrame(rows, columns=["desk_id", "euler_raw", "incremental", "standalone"])


def rescaled(raw: pd.Series, total: float, fallback: pd.Series) -> pd.Series:
    """Contributions scaled to add up to ``total``; by ``fallback`` shares when they add up to
    nothing (a charge set by a floor, whose derivative is zero)."""
    s = float(raw.sum())
    if abs(s) > 1e-9 * max(abs(total), 1.0):
        return raw * (total / s)
    f = float(fallback.sum())
    return fallback * (total / f) if f else raw * 0.0


def with_euler(frame: pd.DataFrame, total: float) -> pd.DataFrame:
    """``frame`` (desk_id, euler_raw, incremental, standalone) with the official ``euler`` column."""
    out = frame.copy()
    out["euler"] = rescaled(out["euler_raw"], total, out["standalone"]) if len(out) else []
    return out


# --- SA-CCR ----------------------------------------------------------------------------------------
def saccr_by_set(sc: SACCRResult, desk: Mapping[str, str]) -> pd.DataFrame:
    """EAD of each netting set by desk: Euler (rescaled to the set's EAD), incremental, standalone.
    The scaled and reduced sets take their collateral from the CSA terms (NettingSetInputs.ead)."""
    rows = []
    for k, inp in sc.inputs.items():
        ead = inp.ead()["ead"]
        groups: dict[str, list[str]] = {}
        for t in inp.pv:
            groups.setdefault(desk.get(t, UNASSIGNED), []).append(t)
        if not groups:
            continue
        model = inp.ead({})["ead"]  # today's set, collateral from the CSA terms

        def charge(scales: Mapping[str, float], other: float, inp=inp, groups=groups) -> float:
            return inp.ead({t: scales.get(d, other) for d, ts in groups.items() for t in ts})["ead"]

        part = by_bump(charge, sorted(groups), model)
        part["euler"] = rescaled(part["euler_raw"], ead, part["standalone"])
        part["euler_raw"] = part["euler_raw"] * (ead / model if model else 1.0)  # same scale as EAD
        rows.append(part.assign(netting_set_id=k, counterparty_id=inp.counterparty_id))
    cols = ["netting_set_id", "counterparty_id", "desk_id", "euler", "euler_raw", "incremental", "standalone"]
    return pd.concat(rows, ignore_index=True)[cols] if rows else pd.DataFrame(columns=cols)


def saccr_capital(by_set: pd.DataFrame, sc: SACCRResult) -> pd.DataFrame:
    """Desk SA-CCR capital: each set's EAD measures × the counterparty risk weight × 8%."""
    if by_set.empty or sc.by_counterparty.empty:
        return pd.DataFrame(columns=["desk_id", "euler", "euler_raw", "incremental", "standalone"])
    rw = sc.by_counterparty.set_index("counterparty_id")["risk_weight"]
    k = by_set["counterparty_id"].map(rw).fillna(1.0) * 0.08
    cols = ["euler", "euler_raw", "incremental", "standalone"]
    return (
        by_set[cols].mul(k, axis=0).assign(desk_id=by_set["desk_id"]).groupby("desk_id", as_index=False).sum()
    )


# --- BA-CVA ----------------------------------------------------------------------------------------
def ba_cva_by_desk(cva_k: float, detail: pd.DataFrame, by_set: pd.DataFrame) -> pd.DataFrame:
    """K = 2.33·sqrt((ρΣS)² + (1−ρ²)ΣS²) with S_c = k_c·EAD_c: ∂K/∂S_c is analytic, and each desk's
    Euler EAD at the counterparty carries its share. Incremental and standalone re-measure K with
    the counterparty EADs less, or only, the desk's."""
    cols = ["desk_id", "euler_raw", "incremental", "standalone"]
    if cva_k <= 0 or detail.empty or by_set.empty:
        return pd.DataFrame(columns=cols)
    d = detail.set_index("counterparty_id")
    k = (d["scva"] / d["ead"].where(d["ead"] > 0)).fillna(0.0)
    s = d["scva"]
    grad = 2.33**2 * (RHO**2 * s.sum() + (1 - RHO**2) * s) / cva_k
    per = by_set[by_set["counterparty_id"].isin(d.index)]
    per = per.groupby(["desk_id", "counterparty_id"])[["euler", "incremental", "standalone"]].sum()
    rows = []
    for desk, g in per.groupby(level="desk_id"):
        g = g.droplevel("desk_id")
        euler = float((grad[g.index] * k[g.index] * g["euler"]).sum())
        less = d["ead"].sub(g["incremental"], fill_value=0.0).clip(lower=0.0)
        alone = g["standalone"].reindex(d.index, fill_value=0.0).clip(lower=0.0)
        rows.append(
            {
                "desk_id": desk,
                "euler_raw": euler,
                "incremental": cva_k - ba_cva_charge((k * less).to_numpy()),
                "standalone": ba_cva_charge((k * alone).to_numpy()),
            }
        )
    return pd.DataFrame(rows, columns=cols)


# --- qualifying CCPs ---------------------------------------------------------------------------------
def ccp_by_desk(
    sc_ccp: SACCRResult,
    ccp_cap: pd.DataFrame,
    sens: pd.DataFrame,
    valuation: pd.DataFrame,
    base_vols: dict[str, float],
    ccp_sets: set[str],
    desk: Mapping[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Trade exposure at each CCP (SA-CCR EAD plus the margin posted, which is SIMM-lite on the
    set's sign-flipped sensitivities) by desk, at 2% × 8%; the default-fund charge K_CM follows
    the desk's Euler share of the trade exposure. Leaving a CCP's business frees none of K_CM
    while the firm stays a clearing member, so its incremental is zero; standalone it is whole."""
    cols = ["desk_id", "euler_raw", "incremental", "standalone"]
    if ccp_cap.empty:
        return pd.DataFrame(columns=cols), pd.DataFrame(columns=cols)
    parts = [saccr_by_set(sc_ccp, desk)]
    live = valuation[(valuation["status"] == "LIVE") & valuation["netting_set_id"].isin(ccp_sets)]
    ns_of = dict(zip(live["trade_id"], live["netting_set_id"], strict=True))
    cp_of = dict(zip(live["netting_set_id"], live["counterparty_id"], strict=False))
    s2 = sens[sens["trade_id"].isin(ns_of)].assign(
        netting_set_id=lambda x: x["trade_id"].map(ns_of), desk_id=lambda x: x["trade_id"].map(desk)
    )
    for ns, g in s2.groupby("netting_set_id"):

        def posted(scales: Mapping[str, float], other: float, g=g) -> float:
            k = g["desk_id"].map(scales).fillna(other)
            return float(sum(simm_for_sensitivities(g.assign(value=-g["value"] * k), base_vols).values()))

        im = posted({}, 1.0)
        part = by_bump(posted, sorted(g["desk_id"].unique()), im)
        part["euler"] = rescaled(part["euler_raw"], im, part["standalone"])
        parts.append(part.assign(netting_set_id=ns, counterparty_id=cp_of.get(ns)))
    te = pd.concat(parts, ignore_index=True)
    te = te.groupby(["counterparty_id", "desk_id"])[["euler", "euler_raw", "incremental", "standalone"]].sum()
    trade = (te * QCCP_TRADE_RW * 0.08).groupby(level="desk_id").sum().reset_index()
    k_cm = ccp_cap.set_index("counterparty_id")["default_fund_capital"]
    share = te["euler"] / te["euler"].groupby(level="counterparty_id").transform("sum")
    by_ccp = share.reset_index(name="share")
    by_ccp["k_cm"] = by_ccp["counterparty_id"].map(k_cm).fillna(0.0)
    by_ccp["euler_raw"] = by_ccp["share"].fillna(0.0) * by_ccp["k_cm"]
    by_ccp["incremental"] = 0.0
    by_ccp["standalone"] = by_ccp["k_cm"]
    fund = by_ccp.groupby("desk_id", as_index=False)[["euler_raw", "incremental", "standalone"]].sum()
    return trade[cols + ["euler"]], fund[cols]


# --- the whole stack -------------------------------------------------------------------------------
@dataclass
class DeskAllocation:
    rows: pd.DataFrame  # desk_id, business_id, component, euler, euler_raw, incremental, standalone
    summary: pd.DataFrame  # component: capital, the sums of the three measures, the Euler scale
    by_desk: pd.DataFrame  # Euler by desk, one column per component and the standardised total


def assemble(
    parts: dict[str, pd.DataFrame], capital: dict[str, float], business: Mapping[str, str]
) -> DeskAllocation:
    """One frame per component (desk_id, euler_raw, incremental, standalone, and euler where the
    rescaling was done per netting set) into the stored tables."""
    long = []
    for comp, frame in parts.items():
        f = frame.copy()
        if "euler" not in f:
            f = with_euler(f, capital[comp])
        else:  # rescaled per set: the firm-level sum already equals the charge
            f["euler"] = rescaled(f["euler"], capital[comp], f["standalone"]) if len(f) else f["euler"]
        long.append(f.assign(component=comp))
    cols = ["desk_id", "euler", "euler_raw", "incremental", "standalone"]
    rows = pd.concat(long, ignore_index=True) if long else pd.DataFrame(columns=[*cols, "component"])
    std = rows[rows["component"].isin(STANDARDISED)].groupby("desk_id", as_index=False)[cols[1:]].sum()
    rows = pd.concat([rows, std.assign(component=TOTAL)], ignore_index=True)
    rows["business_id"] = rows["desk_id"].map(business)
    rows = rows[["desk_id", "business_id", "component", *cols[1:]]]

    capital = {**capital, TOTAL: sum(capital[c] for c in STANDARDISED)}
    summ = []
    for comp, g in rows.groupby("component", sort=False):
        raw = float(g["euler_raw"].sum())
        summ.append(
            {
                "component": comp,
                "label": COMPONENTS.get(comp, "Standardised total"),
                "capital": capital[comp],
                "euler": float(g["euler"].sum()),
                "euler_raw": raw,
                "euler_scale": capital[comp] / raw if raw else np.nan,
                "incremental": float(g["incremental"].sum()),
                "standalone": float(g["standalone"].sum()),
            }
        )
    wide = rows.pivot_table(index="desk_id", columns="component", values="euler", aggfunc="sum").fillna(0.0)
    wide = wide.reindex(columns=[*COMPONENTS, TOTAL], fill_value=0.0).reset_index()
    wide.columns.name = None
    wide["business_id"] = wide["desk_id"].map(business)
    return DeskAllocation(
        rows, pd.DataFrame(summ), wide.sort_values(TOTAL, ascending=False, ignore_index=True)
    )
