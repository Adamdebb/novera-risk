"""SA-CCR exposure at default per netting set and counterparty RWA. Record REG-003.

EAD = 1.4 × (RC + PFE). RC = max(V − C, TH + MTA − NICA, 0) for margined sets, max(V − C, 0)
otherwise. PFE = multiplier × Σ asset-class add-ons, add-ons from supervisory factors on
adjusted notionals with supervisory deltas (±1 linear, Black delta for options) and
maturity factors; hedging sets per currency (rates, three maturity buckets with the
prescribed correlations), per currency pair (FX), per index (credit, equity), per
commodity type. Counterparty risk weights by rating (standardised approach) give RWA.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from novera.domain.counterparties import CSA, Counterparty, NettingSet
from novera.domain.enums import ClearingType, ProductType, Venue
from novera.domain.trades import Trade
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing.valuation import fx_to_reporting, value_trade
from novera.simulation import reference_levels as _ref

MODEL_VERSION = "1.0.0"
ALPHA = 1.4
SF = {
    "IR": 0.005,
    "FX": 0.04,
    "CREDIT_IG": 0.0038,
    "CREDIT_HY": 0.0106,
    "EQ_INDEX": 0.20,
    "EQ_SINGLE": 0.32,
    "CMD_ENERGY": 0.40,
    "CMD_METAL": 0.18,
    "CRYPTO": 0.50,
}
RW_BY_RATING = {
    "AAA": 0.2,
    "AA": 0.2,
    "AA-": 0.2,
    "A+": 0.5,
    "A": 0.5,
    "A-": 0.5,
    "BBB+": 1.0,
    "BBB": 1.0,
    "BB+": 1.0,
    "BB": 1.0,
    "B": 1.5,
    "NR": 1.0,
}
RW_BANK = {
    "AAA": 0.2,
    "AA": 0.2,
    "AA-": 0.2,
    "A+": 0.3,
    "A": 0.3,
    "A-": 0.3,
    "BBB+": 0.5,
    "BBB": 0.5,
    "NR": 0.5,
}


def _years(a: date, b: date) -> float:
    return max((b - a).days / 365.0, 0.0)


def maturity_factor(m_years: float, margined: bool, mpor_days: int = 10) -> float:
    if margined:
        return 1.5 * np.sqrt(mpor_days / 250.0)
    return float(np.sqrt(min(max(m_years, 10 / 250), 1.0)))


def _supervisory_duration(start: float, end: float) -> float:
    return max((np.exp(-0.05 * start) - np.exp(-0.05 * end)) / 0.05, 10 / 250)


@dataclass
class TradeAddOn:
    trade_id: str
    asset_class: str
    hedging_set: str
    bucket: str
    delta: float
    adjusted_notional: float
    maturity_factor: float
    effective: float  # delta * adjusted notional * MF


def _trade_addon(
    t: Trade, market: MarketSnapshot, as_of: date, reporting: str, margined: bool
) -> TradeAddOn | None:
    ins = t.instrument
    pt = t.product_type
    sign = 1.0 if t.direction.value == "BUY" else -1.0
    try:
        r = value_trade(t, market, as_of)
    except Exception:  # noqa: BLE001
        return None
    if r.note:
        return None
    fx = fx_to_reporting(market, t.currency, reporting)
    if pt is ProductType.INTEREST_RATE_SWAP:
        s, e = _years(as_of, ins.effective_date), _years(as_of, ins.maturity_date)
        notional = t.quantity * fx * _supervisory_duration(s, e)
        delta = 1.0 if t.swap_side.value == "PAY_FIXED" else -1.0  # pay fixed gains when rates rise
        bucket = "<1y" if e < 1 else ("1-5y" if e <= 5 else ">5y")
        return TradeAddOn(
            t.trade_id, "IR", ins.currency, bucket, delta, notional, maturity_factor(e, margined), 0.0
        )
    if pt in (ProductType.FX_SPOT, ProductType.FX_FORWARD):
        settle = ins.settlement_date if pt is ProductType.FX_FORWARD else (t.settlement_date or as_of)
        m = _years(as_of, settle)
        notional = t.quantity * fx_to_reporting(market, ins.base_currency, reporting)
        return TradeAddOn(t.trade_id, "FX", ins.pair, "", sign, notional, maturity_factor(m, margined), 0.0)
    if pt is ProductType.FX_OPTION:
        m = _years(as_of, ins.expiry_date)
        notional = t.quantity * fx_to_reporting(market, ins.base_currency, reporting)
        d = r.details.get("delta_fwd", 0.0) / max(t.quantity, 1e-9)  # per unit of base notional
        return TradeAddOn(
            t.trade_id,
            "FX",
            ins.pair,
            "",
            float(np.clip(d, -1, 1)),
            notional,
            maturity_factor(m, margined),
            0.0,
        )
    if pt is ProductType.CDS_INDEX:
        s, e = 0.0, _years(as_of, ins.maturity_date)
        notional = t.quantity * fx * _supervisory_duration(s, e)
        delta = 1.0 if sign < 0 else -1.0  # protection bought = short credit
        cls = "CREDIT_IG" if ("IG" in ins.index_family or "MAIN" in ins.index_family) else "CREDIT_HY"
        return TradeAddOn(
            t.trade_id, cls, ins.index_family, "", delta, notional, maturity_factor(e, margined), 0.0
        )
    if pt is ProductType.CDS_SINGLE_NAME:
        s, e = 0.0, _years(as_of, ins.maturity_date)
        notional = t.quantity * fx * _supervisory_duration(s, e)
        delta = 1.0 if sign < 0 else -1.0
        cls = (
            "CREDIT_IG"
            if _ref.CDS_SINGLE_NAMES.get(ins.reference_entity, ("",) * 6)[5] == "IG"
            else "CREDIT_HY"
        )
        return TradeAddOn(
            t.trade_id, cls, ins.reference_entity, "", delta, notional, maturity_factor(e, margined), 0.0
        )
    if pt is ProductType.SWAPTION:
        # Underlying swap starts at expiry; supervisory delta from the Bachelier rate delta.
        s = _years(as_of, ins.expiry_date)
        e = s + float(ins.swap_tenor.rstrip("Y"))
        notional = t.quantity * fx * _supervisory_duration(s, e)
        d = r.details.get("rate_delta", 0.0) / max(t.quantity * r.details.get("annuity", 1.0), 1e-9)
        delta = -float(np.clip(d, -1, 1))  # payer gains when rates rise = same sign as pay-fixed
        bucket = "<1y" if e < 1 else ("1-5y" if e <= 5 else ">5y")
        return TradeAddOn(
            t.trade_id, "IR", ins.currency, bucket, delta, notional, maturity_factor(e, margined), 0.0
        )
    if pt is ProductType.EQUITY_EXOTIC:
        m = _years(as_of, ins.expiry_date)
        spot = r.details.get("spot", ins.strike)
        notional = t.quantity * ins.contract_multiplier * spot * fx
        # Exotics: supervisory delta ±1 by direction and payoff sign (barrier deltas are not monotone).
        d = sign * (1.0 if ins.option_type.value == "CALL" else -1.0)
        cls = "EQ_INDEX" if ins.underlying in ("SPX", "NDX", "SX5E", "DAX", "FTSE", "NKY") else "EQ_SINGLE"
        return TradeAddOn(t.trade_id, cls, ins.underlying, "", d, notional, maturity_factor(m, margined), 0.0)
    return None


@dataclass
class SACCRResult:
    by_netting_set: pd.DataFrame
    by_counterparty: pd.DataFrame
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def total_ead(self) -> float:
        return float(self.by_counterparty["ead"].sum()) if len(self.by_counterparty) else 0.0

    @property
    def total_rwa(self) -> float:
        return float(self.by_counterparty["rwa"].sum()) if len(self.by_counterparty) else 0.0


def _aggregate_addons(addons: list[TradeAddOn]) -> dict[str, float]:
    """Add-on per asset class from the effective notionals, with the prescribed hedging-set rules."""
    out: dict[str, float] = {}
    df = pd.DataFrame([a.__dict__ for a in addons])
    if df.empty:
        return out
    df["effective"] = df["delta"] * df["adjusted_notional"] * df["maturity_factor"]
    # Rates: per currency, three maturity buckets, correlation 70% adjacent / 30% far.
    ir = df[df["asset_class"] == "IR"]
    ir_total = 0.0
    for _, g in ir.groupby("hedging_set"):
        d = {b: g[g["bucket"] == b]["effective"].sum() for b in ("<1y", "1-5y", ">5y")}
        d1, d2, d3 = d["<1y"], d["1-5y"], d[">5y"]
        eff = np.sqrt(max(d1**2 + d2**2 + d3**2 + 1.4 * d1 * d2 + 1.4 * d2 * d3 + 0.6 * d1 * d3, 0.0))
        ir_total += SF["IR"] * eff
    if ir_total:
        out["IR"] = float(ir_total)
    # FX: per currency pair, absolute net.
    fx = df[df["asset_class"] == "FX"]
    fx_total = sum(abs(g["effective"].sum()) for _, g in fx.groupby("hedging_set")) * SF["FX"]
    if fx_total:
        out["FX"] = float(fx_total)
    # Credit: single hedging set, entity-level correlation 80% for indices (ρ=0.8).
    for cls in ("CREDIT_IG", "CREDIT_HY"):
        c = df[df["asset_class"] == cls]
        if c.empty:
            continue
        per_index = c.groupby("hedging_set")["effective"].sum() * SF[cls]
        # Entity-level correlation: 80% for indices, 50% for single names (MAR/CRE52).
        rhos = np.array([0.8 if ("." in str(h)) else 0.5 for h in per_index.index])
        sys = (rhos * per_index.to_numpy()).sum()
        idio = ((1 - rhos**2) * per_index.to_numpy() ** 2).sum()
        out[cls] = float(np.sqrt(max(sys**2 + idio, 0.0)))
    # Equity: per entity, 80% correlation for indices and 50% for single names.
    for cls in ("EQ_INDEX", "EQ_SINGLE"):
        c = df[df["asset_class"] == cls]
        if c.empty:
            continue
        per = c.groupby("hedging_set")["effective"].sum() * SF[cls]
        rho = 0.8 if cls == "EQ_INDEX" else 0.5
        out[cls] = float(np.sqrt(max((rho * per.sum()) ** 2 + (1 - rho**2) * (per**2).sum(), 0.0)))
    return out


def saccr(
    trades: list[Trade],
    market: MarketSnapshot,
    netting_sets: list[NettingSet],
    csas: list[CSA],
    counterparties: dict[str, Counterparty],
    reporting: str,
    as_of: date | None = None,
    collateral_by_set: dict[str, float] | None = None,
) -> SACCRResult:
    as_of = as_of or market.as_of
    ns_map = {n.netting_set_id: n for n in netting_sets}
    csa_map = {c.csa_id: c for c in csas}
    coll = collateral_by_set or {}
    rows, trade_rows = [], []
    by_set: dict[str, list[Trade]] = {}
    for t in trades:
        if (
            t.status.value == "LIVE"
            and t.venue is Venue.OTC
            and t.clearing is ClearingType.BILATERAL
            and t.netting_set_id in ns_map
        ):
            by_set.setdefault(t.netting_set_id, []).append(t)
    for k, ts in by_set.items():
        ns = ns_map[k]
        csa = csa_map.get(ns.csa_id) if ns.csa_id else None
        margined = csa is not None
        v = 0.0
        addons: list[TradeAddOn] = []
        for t in ts:
            try:
                r = value_trade(t, market, as_of)
                v += r.pv_local * fx_to_reporting(market, r.currency, reporting)
            except Exception:  # noqa: BLE001
                continue
            a = _trade_addon(t, market, as_of, reporting, margined)
            if a is not None:
                a.effective = a.delta * a.adjusted_notional * a.maturity_factor
                addons.append(a)
                trade_rows.append({**a.__dict__, "netting_set_id": k})
        c = coll.get(k)
        if c is None:
            c = 0.0
            if margined:
                they = max(v - csa.threshold_they_post, 0.0)
                we = max(-v - csa.threshold_we_post, 0.0)
                they = 0.0 if they < csa.minimum_transfer_amount else they
                we = 0.0 if we < csa.minimum_transfer_amount else we
                c = they * (1 - csa.haircut) + csa.independent_amount - we * (1 - csa.haircut)
        if margined:
            rc = max(
                v - c, csa.threshold_they_post + csa.minimum_transfer_amount - csa.independent_amount, 0.0
            )
        else:
            rc = max(v - c, 0.0)
        addon_by_class = _aggregate_addons(addons)
        addon_agg = sum(addon_by_class.values())
        mult = min(1.0, 0.05 + 0.95 * np.exp((v - c) / (2 * 0.95 * addon_agg))) if addon_agg > 0 else 1.0
        pfe = mult * addon_agg
        ead = ALPHA * (rc + pfe)
        rows.append(
            {
                "netting_set_id": k,
                "counterparty_id": ns.counterparty_id,
                "margined": margined,
                "value": v,
                "collateral": c,
                "replacement_cost": rc,
                "addon": addon_agg,
                "multiplier": mult,
                "pfe": pfe,
                "ead": ead,
                **{f"addon_{cls}": val for cls, val in addon_by_class.items()},
            }
        )
    by_ns = pd.DataFrame(rows)
    if by_ns.empty:
        return SACCRResult(by_ns, pd.DataFrame(), pd.DataFrame(trade_rows))
    by_cp = by_ns.groupby("counterparty_id", as_index=False).agg(
        ead=("ead", "sum"),
        rc=("replacement_cost", "sum"),
        pfe=("pfe", "sum"),
        netting_sets=("netting_set_id", "count"),
    )

    def rw(cid: str) -> float:
        cp = counterparties.get(cid)
        if cp is None:
            return 1.0
        table = RW_BANK if cp.counterparty_type.value in ("BANK", "BROKER_DEALER") else RW_BY_RATING
        return table.get(cp.rating, 1.0)

    by_cp["risk_weight"] = by_cp["counterparty_id"].map(rw)
    by_cp["rwa"] = by_cp["ead"] * by_cp["risk_weight"]
    by_cp["capital"] = by_cp["rwa"] * 0.08
    return SACCRResult(by_ns, by_cp.sort_values("ead", ascending=False), pd.DataFrame(trade_rows))
