"""Daily P&L explain. Methodology record MR-007.

Official: a full-revaluation waterfall from yesterday's market and portfolio to today's,
one factor group at a time, then carry, new and dead trades, with a final "data" step
that captures factors present yesterday but missing today (so a missing node shows up as
a residual instead of disappearing). Each step is exact by construction.

Challenger: yesterday's sensitivities times today's factor moves, plus theta. Its residual
against the official trade-level P&L is the "unexplained" a risk manager investigates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from novera.domain.enums import ProductType
from novera.domain.trades import Trade
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing.valuation import fx_to_reporting, value_trade
from novera.risk.revaluation import Portfolio
from novera.risk.sensitivities import BUMPS

MODEL_VERSION = "1.0.0"

FACTOR_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("RATES", ("IR:",)),
    ("CREDIT", ("CDS:",)),
    ("FX", ("FX:",)),
    ("EQUITY", ("EQ:", "EQIDX:")),
    ("COMMODITY", ("CMD:",)),
    ("DIGITAL_ASSET", ("CRYPTO:",)),
    ("VOLATILITY", ("VOL:",)),
)
STEP_ORDER = ["CARRY", *[g for g, _ in FACTOR_GROUPS], "DATA", "NEW_TRADES", "DEAD_TRADES"]


def inception_cash(trade: Trade) -> float:
    """Cash paid at inception in the instrument currency, signed from our side (positive =
    we paid). Needed because PV of cash products is market value, not P&L."""
    q, p = trade.signed_quantity, trade.trade_price
    pt = trade.product_type
    ins = trade.instrument
    if pt is ProductType.GOVERNMENT_BOND:
        return q * p / 100.0
    if pt in (ProductType.CASH_EQUITY, ProductType.CRYPTO_SPOT):
        return q * p
    if pt is ProductType.EQUITY_OPTION:
        return q * ins.contract_multiplier * p  # type: ignore[attr-defined]
    if pt is ProductType.FX_OPTION:
        return q * p
    return 0.0  # swaps, forwards, futures, CDS, FX spot: PV already measures P&L


@dataclass
class PnLExplain:
    business_date: date
    steps: pd.DataFrame  # step, pnl (official waterfall, reporting currency)
    by_trade: pd.DataFrame  # trade_id, step, pnl
    challenger: pd.DataFrame  # trade_id, actual, predicted, residual
    total: float
    pv_previous: float
    pv_today: float
    notes: dict[str, float] = field(default_factory=dict)

    def by(self, valuation: pd.DataFrame, column: str) -> pd.DataFrame:
        keys = valuation[["trade_id", column]].drop_duplicates("trade_id").set_index("trade_id")[column]
        t = self.by_trade.assign(group=self.by_trade["trade_id"].map(keys))
        return (
            t.pivot_table(index="group", columns="step", values="pnl", aggfunc="sum", fill_value=0.0)
            .reindex(columns=[s for s in STEP_ORDER if s in set(t["step"])], fill_value=0.0)
            .assign(TOTAL=lambda d: d.sum(axis=1))
            .sort_values("TOTAL")
        )


def _pv_map(trades: list[Trade], market: MarketSnapshot, as_of: date, reporting: str) -> dict[str, float]:
    return {tid: pv for tid, (pv, _) in _price_map(trades, market, as_of, reporting).items()}


def _price_map(
    trades: list[Trade], market: MarketSnapshot, as_of: date, reporting: str, cash_until: date | None = None
) -> dict[str, tuple[float, float]]:
    """trade_id -> (PV, cash received on pay dates in (as_of, cash_until]) in reporting currency."""
    out: dict[str, tuple[float, float]] = {}
    for t in trades:
        try:
            r = value_trade(t, market, as_of)
        except Exception:  # noqa: BLE001
            continue
        fx = fx_to_reporting(market, r.currency, reporting)
        cash = 0.0
        if cash_until is not None:
            cash = sum(cf.amount for cf in r.cashflows if as_of < cf.pay_date <= cash_until)
        out[t.trade_id] = (r.pv_local * fx, cash * fx)
    return out


def explain_pnl(
    pf_today: Portfolio,
    prev_market: MarketSnapshot,
    prev_trades: list[Trade],
    prev_sens: pd.DataFrame | None = None,
) -> PnLExplain:
    today, reporting = pf_today.as_of, pf_today.reporting_currency
    prev_date = prev_market.as_of
    prev_live = [t for t in prev_trades if t.status.value == "LIVE"]
    today_ids = set(pf_today.priced_ids)
    prev_ids = {t.trade_id for t in prev_live}

    priced_prev = _price_map(prev_live, prev_market, prev_date, reporting, cash_until=today)
    pv_prev = {tid: pv for tid, (pv, _) in priced_prev.items()}
    cash_prev = {tid: cash for tid, (_, cash) in priced_prev.items()}
    # Trades that stop having value today (matured, expired, settled) get their own step.
    dead_ids = set()
    for tid in prev_ids & today_ids:
        t = pf_today.by_id[tid]
        try:
            if value_trade(t, pf_today.base, today).note:
                dead_ids.add(tid)
        except Exception:  # noqa: BLE001
            continue
    common = [pf_today.by_id[tid] for tid in sorted((prev_ids & today_ids) - dead_ids) if tid in pv_prev]
    rows: list[tuple[str, str, float]] = []

    # Carry: roll the valuation date on yesterday's market. Cashflows that paid between the two
    # dates leave the PV but are received, so they are added back here (P&L = dPV + cash).
    cur_pv = pv_prev
    pv_carry = _pv_map(common, prev_market, today, reporting)
    rows += [(tid, "CARRY", pv_carry[tid] - cur_pv[tid] + cash_prev.get(tid, 0.0)) for tid in pv_carry]
    cur_pv = pv_carry

    # Factor groups, sequential full revaluation.
    values = dict(prev_market.values)
    today_values = pf_today.base.values
    for group, prefixes in FACTOR_GROUPS:
        changed = {f: today_values[f] for f in values if f.startswith(prefixes) and f in today_values}
        values.update(changed)
        snap = MarketSnapshot(as_of=today, values=values, source="PNL_STEP")
        pv_g = _pv_map(common, snap, today, reporting)
        rows += [(tid, group, pv_g[tid] - cur_pv[tid]) for tid in pv_g]
        cur_pv = pv_g

    # Data step: whatever remains between the stepped snapshot and today's official one
    # (factors missing today, stale observations, new factors).
    pv_today_common = {tid: pf_today.base_pv[tid] for tid in cur_pv}
    rows += [(tid, "DATA", pv_today_common[tid] - cur_pv[tid]) for tid in cur_pv]

    # New trades: today's PV less cash paid at inception.
    new_ids = sorted(today_ids - prev_ids)
    for tid in new_ids:
        t = pf_today.by_id[tid]
        cash = inception_cash(t) * fx_to_reporting(pf_today.base, t.currency, reporting)
        rows.append((tid, "NEW_TRADES", pf_today.base_pv[tid] - cash))
    # Dead trades: yesterday's PV goes to zero against the cash they paid on the way out
    # (principal, final coupon, forward settlement). Options with no cashflow model lose
    # their remaining PV, a documented approximation.
    for tid in sorted(dead_ids):
        rows.append((tid, "DEAD_TRADES", cash_prev.get(tid, 0.0) - pv_prev.get(tid, 0.0)))
    # Trades that vanished from the feed entirely (cancelled or dropped) are reported as notes.
    vanished = sorted(prev_ids - today_ids)
    notes = {
        "vanished_trades_pv": float(sum(pv_prev.get(t, 0.0) for t in vanished)),
        "vanished_trades": len(vanished),
    }

    by_trade = pd.DataFrame(rows, columns=["trade_id", "step", "pnl"])
    steps = by_trade.groupby("step")["pnl"].sum().reindex(STEP_ORDER, fill_value=0.0).reset_index()
    total = float(by_trade["pnl"].sum())

    # Challenger: yesterday's Greeks times today's moves.
    challenger = _challenger(pf_today, prev_market, prev_sens, common, by_trade)
    return PnLExplain(
        today,
        steps,
        by_trade,
        challenger,
        total,
        float(sum(pv_prev.values())),
        float(sum(pf_today.base_pv.values())),
        notes,
    )


def _challenger(
    pf_today: Portfolio,
    prev_market: MarketSnapshot,
    prev_sens: pd.DataFrame | None,
    common: list[Trade],
    by_trade: pd.DataFrame,
) -> pd.DataFrame:
    actual = (
        by_trade[by_trade["trade_id"].isin({t.trade_id for t in common})].groupby("trade_id")["pnl"].sum()
    )
    if prev_sens is None or prev_sens.empty:
        return pd.DataFrame(
            {"trade_id": actual.index, "actual": actual.to_numpy(), "predicted": np.nan, "residual": np.nan}
        )
    today_v, prev_v = pf_today.base.values, prev_market.values

    def move(fid: str) -> float | None:
        if fid.endswith(":"):
            members = [f for f in prev_v if f.startswith(fid) and f in today_v]
            if not members:
                return None
            if fid.startswith("VOL:"):
                return float(np.mean([today_v[f] - prev_v[f] for f in members]))  # vol points
            return float(np.mean([today_v[f] / prev_v[f] - 1.0 for f in members]))
        if fid not in today_v or fid not in prev_v:
            return None
        if fid.startswith(("IR:", "CDS:")):
            return today_v[fid] - prev_v[fid]
        return today_v[fid] / prev_v[fid] - 1.0

    predicted: dict[str, float] = dict.fromkeys(actual.index, 0.0)
    for (measure, fid), grp in prev_sens.groupby(["measure", "factor_id"]):
        if measure == "THETA":
            for _, r in grp.iterrows():
                if r["trade_id"] in predicted:
                    predicted[r["trade_id"]] += r["value"]
            continue
        x = move(fid)
        if x is None:
            continue
        units = x / BUMPS.get(measure, 1.0)
        for _, r in grp.iterrows():
            tid = r["trade_id"]
            if tid not in predicted:
                continue
            predicted[tid] += 0.5 * r["value"] * units**2 if measure == "GAMMA" else r["value"] * units
    out = pd.DataFrame({"trade_id": actual.index, "actual": actual.to_numpy()})
    out["predicted"] = out["trade_id"].map(predicted)
    out["residual"] = out["actual"] - out["predicted"]
    return out
