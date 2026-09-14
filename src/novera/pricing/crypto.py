"""Digital-asset spot marked to market. Methodology record PR-009."""
from __future__ import annotations

from datetime import date

from novera.domain.instruments import CryptoSpot
from novera.domain.trades import Trade
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing.base import PricingResult

CRYPTO_MODEL_VERSION = "1.0.0"


def price_crypto_spot(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    ins = trade.instrument
    assert isinstance(ins, CryptoSpot)
    spot = market.crypto_spot(ins.symbol)
    return PricingResult(trade.trade_id, ins.currency, trade.signed_quantity * spot, "crypto_mtm",
                         CRYPTO_MODEL_VERSION, details={"spot": spot, "units": trade.signed_quantity})
