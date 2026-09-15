"""Enumerations shared across the platform."""

from __future__ import annotations

from enum import StrEnum


class AssetClass(StrEnum):
    RATES = "RATES"
    FX = "FX"
    EQUITY = "EQUITY"
    CREDIT = "CREDIT"
    COMMODITY = "COMMODITY"
    DIGITAL_ASSET = "DIGITAL_ASSET"


class ProductType(StrEnum):
    """Phase 2 scope: one liquid product per asset class (docs/03-roadmap.md)."""

    GOVERNMENT_BOND = "GOVERNMENT_BOND"
    INTEREST_RATE_SWAP = "INTEREST_RATE_SWAP"
    FX_SPOT = "FX_SPOT"
    FX_FORWARD = "FX_FORWARD"
    FX_OPTION = "FX_OPTION"
    CASH_EQUITY = "CASH_EQUITY"
    EQUITY_INDEX_FUTURE = "EQUITY_INDEX_FUTURE"
    EQUITY_OPTION = "EQUITY_OPTION"
    COMMODITY_FUTURE = "COMMODITY_FUTURE"
    CDS_INDEX = "CDS_INDEX"
    CRYPTO_SPOT = "CRYPTO_SPOT"
    # Phase 6 breadth
    REPO = "REPO"
    INTEREST_RATE_FUTURE = "INTEREST_RATE_FUTURE"
    SWAPTION = "SWAPTION"
    CDS_SINGLE_NAME = "CDS_SINGLE_NAME"
    COMMODITY_OPTION = "COMMODITY_OPTION"
    ETF = "ETF"
    MUTUAL_FUND = "MUTUAL_FUND"
    EQUITY_EXOTIC = "EQUITY_EXOTIC"


PRODUCT_ASSET_CLASS: dict[ProductType, AssetClass] = {
    ProductType.GOVERNMENT_BOND: AssetClass.RATES,
    ProductType.INTEREST_RATE_SWAP: AssetClass.RATES,
    ProductType.FX_SPOT: AssetClass.FX,
    ProductType.FX_FORWARD: AssetClass.FX,
    ProductType.FX_OPTION: AssetClass.FX,
    ProductType.CASH_EQUITY: AssetClass.EQUITY,
    ProductType.EQUITY_INDEX_FUTURE: AssetClass.EQUITY,
    ProductType.EQUITY_OPTION: AssetClass.EQUITY,
    ProductType.COMMODITY_FUTURE: AssetClass.COMMODITY,
    ProductType.CDS_INDEX: AssetClass.CREDIT,
    ProductType.CRYPTO_SPOT: AssetClass.DIGITAL_ASSET,
    ProductType.REPO: AssetClass.RATES,
    ProductType.INTEREST_RATE_FUTURE: AssetClass.RATES,
    ProductType.SWAPTION: AssetClass.RATES,
    ProductType.CDS_SINGLE_NAME: AssetClass.CREDIT,
    ProductType.COMMODITY_OPTION: AssetClass.COMMODITY,
    ProductType.ETF: AssetClass.EQUITY,
    ProductType.MUTUAL_FUND: AssetClass.EQUITY,
    ProductType.EQUITY_EXOTIC: AssetClass.EQUITY,
}


class Venue(StrEnum):
    LISTED = "LISTED"
    OTC = "OTC"


class ClearingType(StrEnum):
    BILATERAL = "BILATERAL"
    CLEARED = "CLEARED"  # via a CCP such as LCH or CME
    EXCHANGE = "EXCHANGE"  # listed products settled through an exchange/clearing house


class BuySell(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class SwapSide(StrEnum):
    PAY_FIXED = "PAY_FIXED"
    RECEIVE_FIXED = "RECEIVE_FIXED"


class OptionType(StrEnum):
    CALL = "CALL"
    PUT = "PUT"


class BarrierType(StrEnum):
    UP_AND_OUT = "UP_AND_OUT"
    DOWN_AND_OUT = "DOWN_AND_OUT"
    UP_AND_IN = "UP_AND_IN"
    DOWN_AND_IN = "DOWN_AND_IN"


class ExoticStyle(StrEnum):
    BARRIER = "BARRIER"
    DIGITAL = "DIGITAL"  # cash-or-nothing


class DayCount(StrEnum):
    ACT_360 = "ACT/360"
    ACT_365 = "ACT/365"
    THIRTY_360 = "30/360"
    ACT_ACT = "ACT/ACT"


class Frequency(StrEnum):
    ANNUAL = "ANNUAL"
    SEMI_ANNUAL = "SEMI_ANNUAL"
    QUARTERLY = "QUARTERLY"
    MONTHLY = "MONTHLY"


class TradeStatus(StrEnum):
    LIVE = "LIVE"
    CANCELLED = "CANCELLED"
    MATURED = "MATURED"
    INVALID = "INVALID"  # failed validation; kept for the data-quality module


class HierarchyLevel(StrEnum):
    """Aggregation levels, ordered from finest to coarsest."""

    TRADE = "trade_id"
    BOOK = "book_id"
    DESK = "desk_id"
    BUSINESS = "business_id"
    FIRM = "firm_id"
    # Cross-cutting dimensions
    LEGAL_ENTITY = "legal_entity_id"
    TRADER = "trader_id"
    ASSET_CLASS = "asset_class"
    PRODUCT_TYPE = "product_type"
    CURRENCY = "currency"
    COUNTERPARTY = "counterparty_id"
