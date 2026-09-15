"""Fund look-through: ETF and mutual-fund holdings decomposed into their constituents.
Methodology record MR-014.

Pricing and sensitivities already see through funds (a fund depends on its constituents'
factors, so an ETF on NVDA adds to the NVDA delta). This module makes that explicit for
concentration and reporting: per fund trade, the exposure to each constituent in reporting
currency; per constituent, how much comes directly and how much through funds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from novera.domain.enums import ProductType
from novera.domain.trades import Trade
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing.breadth import leg_price
from novera.pricing.valuation import fx_to_reporting

MODEL_VERSION = "1.0.0"

HOLDING_COLUMNS = [
    "trade_id",
    "book_id",
    "fund",
    "product_type",
    "constituent",
    "kind",
    "leg_currency",
    "units",
    "exposure",
    "share_of_fund",
]
CONSTITUENT_COLUMNS = ["constituent", "kind", "direct", "via_funds", "total", "via_funds_share"]


@dataclass
class LookThrough:
    holdings: pd.DataFrame  # one row per (fund trade, constituent)
    constituents: pd.DataFrame  # direct vs via-fund exposure per constituent
    fund_cash: float = 0.0  # mutual-fund cash sleeves, reporting currency
    flags: list[str] = field(default_factory=list)


def _direct_exposure(t: Trade, market: MarketSnapshot, reporting: str) -> tuple[str, str, float] | None:
    """(constituent, kind, signed exposure) for cash products that a fund could also hold."""
    ins = t.instrument
    pt = t.product_type
    fx = fx_to_reporting(market, t.currency, reporting)
    if pt is ProductType.CASH_EQUITY:
        return ins.ticker, "EQ", t.signed_quantity * market.equity_spot(ins.ticker) * fx  # type: ignore[attr-defined]
    if pt is ProductType.EQUITY_INDEX_FUTURE:
        lvl = market.index_level(ins.index)  # type: ignore[attr-defined]
        return ins.index, "EQIDX", t.signed_quantity * ins.contract_multiplier * lvl * fx  # type: ignore[attr-defined]
    if pt is ProductType.COMMODITY_FUTURE:
        px = float(market.commodity_curve(ins.commodity).price(1.0 / 12.0))  # type: ignore[attr-defined]
        return ins.commodity, "CMD", t.signed_quantity * ins.contract_size * px * fx  # type: ignore[attr-defined]
    if pt is ProductType.CRYPTO_SPOT:
        return ins.symbol, "CRYPTO", t.signed_quantity * market.crypto_spot(ins.symbol) * fx  # type: ignore[attr-defined]
    return None


def look_through(
    trades: list[Trade],
    market: MarketSnapshot,
    reporting: str,
    book_of: dict[str, str] | None = None,
    concentration_share: float = 0.25,
    min_exposure: float = 10e6,
) -> LookThrough:
    """``min_exposure`` (reporting currency) keeps the flags to constituents that matter."""
    rows: list[dict] = []
    direct: dict[tuple[str, str], float] = {}
    via: dict[tuple[str, str], float] = {}
    cash = 0.0
    for t in trades:
        if t.status.value != "LIVE":
            continue
        pt = t.product_type
        if pt in (ProductType.ETF, ProductType.MUTUAL_FUND):
            ins = t.instrument
            fx = fx_to_reporting(market, t.currency, reporting)
            legs = []
            for leg in ins.basket:  # type: ignore[attr-defined]
                leg_fx = 1.0 if leg.currency == t.currency else market.fx_spot(f"{leg.currency}/{t.currency}")
                per_share = leg.units_per_share * leg_price(market, leg) * leg_fx
                legs.append((leg, per_share))
            nav = sum(v for _, v in legs) + getattr(ins, "cash_per_share", 0.0)
            for leg, per_share in legs:
                exposure = t.signed_quantity * per_share * fx
                rows.append(
                    {
                        "trade_id": t.trade_id,
                        "book_id": (book_of or {}).get(t.trade_id, t.book_id),
                        "fund": ins.instrument_id,
                        "product_type": pt.value,
                        "constituent": leg.underlying,
                        "kind": leg.kind,
                        "leg_currency": leg.currency,
                        "units": t.signed_quantity * leg.units_per_share,
                        "exposure": exposure,
                        "share_of_fund": per_share / nav if nav else 0.0,
                    }
                )
                key = (leg.underlying, leg.kind)
                via[key] = via.get(key, 0.0) + exposure
            cash += t.signed_quantity * getattr(ins, "cash_per_share", 0.0) * fx
        else:
            d = _direct_exposure(t, market, reporting)
            if d is not None:
                key = (d[0], d[1])
                direct[key] = direct.get(key, 0.0) + d[2]
    holdings = pd.DataFrame(rows, columns=HOLDING_COLUMNS)
    keys = sorted(set(direct) | set(via))
    cons = pd.DataFrame(
        [
            {
                "constituent": k[0],
                "kind": k[1],
                "direct": direct.get(k, 0.0),
                "via_funds": via.get(k, 0.0),
                "total": direct.get(k, 0.0) + via.get(k, 0.0),
            }
            for k in keys
        ],
        columns=CONSTITUENT_COLUMNS[:-1],
    )
    if len(cons):
        cons["via_funds_share"] = (
            cons["via_funds"].abs() / cons["total"].abs().replace(0.0, float("nan"))
        ).fillna(0.0)
        cons = cons.reindex(cons["total"].abs().sort_values(ascending=False).index).reset_index(drop=True)
    else:
        cons["via_funds_share"] = pd.Series(dtype=float)
    flags: list[str] = []
    for _, r in cons.iterrows():
        if r["via_funds_share"] >= concentration_share and abs(r["via_funds"]) >= min_exposure:
            flags.append(
                f"{r['constituent']}: {r['via_funds_share']:.0%} of the {r['total']:,.0f} exposure comes "
                f"through funds ({r['via_funds']:,.0f})"
            )
    return LookThrough(holdings, cons, cash, flags)
