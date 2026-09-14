"""Portfolio valuation: price every trade, convert to reporting currency, keep failures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from novera.domain.organisation import Organisation
from novera.domain.snapshots import PortfolioSnapshot
from novera.domain.trades import Trade
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing import PRICERS, PricingResult

VALUATION_VERSION = "1.0.0"

RESULT_COLUMNS = [
    "trade_id",
    "book_id",
    "desk_id",
    "business_id",
    "legal_entity_id",
    "trader_id",
    "counterparty_id",
    "asset_class",
    "product_type",
    "currency",
    "quantity",
    "direction",
    "status",
    "pv_local",
    "fx_to_reporting",
    "pv",
    "model",
    "model_version",
    "note",
    "error",
]


@dataclass
class ValuationOutput:
    table: pd.DataFrame  # one row per trade (RESULT_COLUMNS)
    results: dict[str, PricingResult]
    errors: dict[str, str]

    @property
    def total_pv(self) -> float:
        return float(self.table["pv"].fillna(0.0).sum())


def fx_to_reporting(market: MarketSnapshot, currency: str, reporting_currency: str) -> float:
    return 1.0 if currency == reporting_currency else market.fx_spot(f"{currency}/{reporting_currency}")


def value_trade(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    pricer = PRICERS[trade.product_type]
    return pricer(trade, market, as_of)


def value_portfolio(
    snapshot: PortfolioSnapshot,
    market: MarketSnapshot,
    org: Organisation | None,
    reporting_currency: str,
    as_of: date | None = None,
) -> ValuationOutput:
    """Price every LIVE trade. Trades that fail are kept with ``error`` set and PV NaN, so
    the data-quality module can report them and totals stay honest."""
    as_of = as_of or market.as_of
    rows: list[dict] = []
    results: dict[str, PricingResult] = {}
    errors: dict[str, str] = {}
    for t in snapshot.trades:
        hierarchy = {"desk_id": None, "business_id": None, "legal_entity_id": None}
        if org is not None:
            try:
                h = org.hierarchy_for_book(t.book_id)
                hierarchy = {k: h[k] for k in hierarchy}
            except KeyError:
                pass
        base = {
            "trade_id": t.trade_id,
            "book_id": t.book_id,
            **hierarchy,
            "trader_id": t.trader_id,
            "counterparty_id": t.counterparty_id,
            "asset_class": t.asset_class.value,
            "product_type": t.product_type.value,
            "currency": t.currency,
            "quantity": t.signed_quantity,
            "direction": t.direction.value,
            "status": t.status.value,
        }
        if t.status.value != "LIVE":
            rows.append(
                {
                    **base,
                    "pv_local": None,
                    "fx_to_reporting": None,
                    "pv": None,
                    "model": None,
                    "model_version": None,
                    "note": "not live",
                    "error": None,
                }
            )
            continue
        try:
            r = value_trade(t, market, as_of)
            fx = fx_to_reporting(market, r.currency, reporting_currency)
            results[t.trade_id] = r
            rows.append(
                {
                    **base,
                    "pv_local": r.pv_local,
                    "fx_to_reporting": fx,
                    "pv": r.pv_local * fx,
                    "model": r.model,
                    "model_version": r.model_version,
                    "note": r.note,
                    "error": None,
                }
            )
        except Exception as e:  # noqa: BLE001 - we want every failure captured, not raised
            errors[t.trade_id] = f"{type(e).__name__}: {e}"
            rows.append(
                {
                    **base,
                    "pv_local": None,
                    "fx_to_reporting": None,
                    "pv": None,
                    "model": None,
                    "model_version": None,
                    "note": "",
                    "error": errors[t.trade_id],
                }
            )
    table = pd.DataFrame(rows, columns=RESULT_COLUMNS)
    return ValuationOutput(table, results, errors)
