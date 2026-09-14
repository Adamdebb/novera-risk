"""Commodity futures marked off the commodity curve. Methodology record PR-007."""
from __future__ import annotations

from datetime import date

from novera.domain.instruments import CommodityFuture
from novera.domain.trades import Trade
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing.base import PricingResult, year_fraction_act365

COMMODITY_MODEL_VERSION = "1.0.0"


def price_commodity_future(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    ins = trade.instrument
    assert isinstance(ins, CommodityFuture)
    if ins.expiry_date <= as_of:
        return PricingResult(trade.trade_id, ins.currency, 0.0, "commodity_future_curve",
                             COMMODITY_MODEL_VERSION, note="expired")
    t = year_fraction_act365(as_of, ins.expiry_date)
    fwd = float(market.commodity_curve(ins.commodity).price(t))
    exposure = trade.signed_quantity * ins.contract_size
    return PricingResult(trade.trade_id, ins.currency, exposure * (fwd - trade.trade_price),
                         "commodity_future_curve", COMMODITY_MODEL_VERSION,
                         details={"forward": fwd, "notional_exposure": exposure * fwd, "years_to_expiry": t,
                                  "units": exposure})
