"""Pure domain models. No I/O, no calculations beyond validation.

Hierarchy for aggregation: trade -> book -> desk -> business -> firm.
Legal entity is a separate dimension carried by the book (a desk can book into
several legal entities). Counterparty and netting set are carried by the trade.
"""
from novera.domain.counterparties import CSA, Counterparty, CounterpartyType, NettingSet
from novera.domain.enums import (
    AssetClass,
    BuySell,
    ClearingType,
    DayCount,
    Frequency,
    HierarchyLevel,
    OptionType,
    ProductType,
    SwapSide,
    TradeStatus,
    Venue,
)
from novera.domain.instruments import (
    CashEquity,
    CDSIndex,
    CommodityFuture,
    CryptoSpot,
    EquityIndexFuture,
    EquityOption,
    FXForward,
    FXOption,
    FXSpot,
    GovernmentBond,
    Instrument,
    InterestRateSwap,
)
from novera.domain.limits import Limit, LimitScope, LimitStatus, LimitType
from novera.domain.organisation import Book, Business, Desk, Firm, LegalEntity, Organisation, Trader
from novera.domain.snapshots import PortfolioSnapshot, content_hash
from novera.domain.trades import Trade

__all__ = [
    "CSA", "Counterparty", "CounterpartyType", "NettingSet",
    "AssetClass", "BuySell", "ClearingType", "DayCount", "Frequency", "HierarchyLevel",
    "OptionType", "ProductType", "SwapSide", "TradeStatus", "Venue",
    "CashEquity", "CDSIndex", "CommodityFuture", "CryptoSpot", "EquityIndexFuture",
    "EquityOption", "FXForward", "FXOption", "FXSpot", "GovernmentBond", "Instrument",
    "InterestRateSwap",
    "Limit", "LimitScope", "LimitStatus", "LimitType",
    "Book", "Business", "Desk", "Firm", "LegalEntity", "Organisation", "Trader",
    "PortfolioSnapshot", "content_hash", "Trade",
]
