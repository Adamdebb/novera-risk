"""Instrument definitions for the Phase 2 product set.

Each instrument is a frozen pydantic model carrying only contractual terms. Market data
and valuation live elsewhere. ``Instrument`` is a discriminated union on ``product_type``
so trades can be serialised and parsed without knowing the concrete class.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from novera.domain.enums import (
    PRODUCT_ASSET_CLASS,
    AssetClass,
    DayCount,
    Frequency,
    OptionType,
    ProductType,
    Venue,
)

Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$", description="ISO 4217 code")]


class InstrumentBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument_id: str = Field(min_length=1)
    currency: Currency
    description: str = ""

    @property
    def asset_class(self) -> AssetClass:
        return PRODUCT_ASSET_CLASS[self.product_type]  # type: ignore[attr-defined]

    @property
    def venue(self) -> Venue:
        return VENUE_BY_PRODUCT[self.product_type]  # type: ignore[attr-defined]


# --- Rates -------------------------------------------------------------------------

class GovernmentBond(InstrumentBase):
    product_type: Literal[ProductType.GOVERNMENT_BOND] = ProductType.GOVERNMENT_BOND
    issuer: str
    coupon_rate: float = Field(ge=0, description="Annual coupon as a decimal, e.g. 0.0425")
    issue_date: date
    maturity_date: date
    coupon_frequency: Frequency = Frequency.SEMI_ANNUAL
    day_count: DayCount = DayCount.ACT_ACT
    face_value: float = Field(default=100.0, gt=0)

    @model_validator(mode="after")
    def _dates(self) -> GovernmentBond:
        if self.maturity_date <= self.issue_date:
            raise ValueError("maturity_date must be after issue_date")
        return self


class InterestRateSwap(InstrumentBase):
    product_type: Literal[ProductType.INTEREST_RATE_SWAP] = ProductType.INTEREST_RATE_SWAP
    effective_date: date
    maturity_date: date
    fixed_rate: float = Field(description="Annual fixed rate as a decimal")
    fixed_frequency: Frequency = Frequency.SEMI_ANNUAL
    fixed_day_count: DayCount = DayCount.THIRTY_360
    float_index: str = Field(description="e.g. USD-SOFR, EUR-EURIBOR-3M")
    float_frequency: Frequency = Frequency.QUARTERLY
    float_day_count: DayCount = DayCount.ACT_360
    float_spread: float = 0.0

    @model_validator(mode="after")
    def _dates(self) -> InterestRateSwap:
        if self.maturity_date <= self.effective_date:
            raise ValueError("maturity_date must be after effective_date")
        return self


# --- FX ----------------------------------------------------------------------------

class _FXBase(InstrumentBase):
    pair: str = Field(pattern=r"^[A-Z]{3}/[A-Z]{3}$", description="BASE/QUOTE, e.g. EUR/USD")

    @property
    def base_currency(self) -> str:
        return self.pair[:3]

    @property
    def quote_currency(self) -> str:
        return self.pair[4:]

    @model_validator(mode="after")
    def _currency_is_quote(self) -> _FXBase:
        # FX instruments are denominated in the quote currency by convention.
        if self.currency != self.quote_currency:
            raise ValueError("currency must equal the quote currency of the pair")
        return self


class FXSpot(_FXBase):
    product_type: Literal[ProductType.FX_SPOT] = ProductType.FX_SPOT


class FXForward(_FXBase):
    product_type: Literal[ProductType.FX_FORWARD] = ProductType.FX_FORWARD
    settlement_date: date
    forward_rate: float = Field(gt=0, description="Contracted rate, quote per base")


class FXOption(_FXBase):
    product_type: Literal[ProductType.FX_OPTION] = ProductType.FX_OPTION
    option_type: OptionType
    strike: float = Field(gt=0)
    expiry_date: date
    settlement_date: date | None = None


# --- Equity ------------------------------------------------------------------------

class CashEquity(InstrumentBase):
    product_type: Literal[ProductType.CASH_EQUITY] = ProductType.CASH_EQUITY
    ticker: str
    exchange: str
    sector: str = ""
    country: str = ""


class EquityIndexFuture(InstrumentBase):
    product_type: Literal[ProductType.EQUITY_INDEX_FUTURE] = ProductType.EQUITY_INDEX_FUTURE
    index: str = Field(description="e.g. SPX, SX5E, NKY")
    exchange: str
    expiry_date: date
    contract_multiplier: float = Field(gt=0)


class EquityOption(InstrumentBase):
    product_type: Literal[ProductType.EQUITY_OPTION] = ProductType.EQUITY_OPTION
    underlying: str = Field(description="Ticker or index code")
    option_type: OptionType
    strike: float = Field(gt=0)
    expiry_date: date
    contract_multiplier: float = Field(default=100.0, gt=0)
    exchange: str = ""


# --- Commodities -------------------------------------------------------------------

class CommodityFuture(InstrumentBase):
    product_type: Literal[ProductType.COMMODITY_FUTURE] = ProductType.COMMODITY_FUTURE
    commodity: str = Field(description="e.g. BRENT, WTI, NATGAS, GOLD, COPPER")
    exchange: str
    expiry_date: date
    contract_size: float = Field(gt=0, description="Units per contract, e.g. 1000 bbl")
    unit: str = ""


# --- Credit ------------------------------------------------------------------------

class CDSIndex(InstrumentBase):
    product_type: Literal[ProductType.CDS_INDEX] = ProductType.CDS_INDEX
    index_family: str = Field(description="e.g. CDX.NA.IG, ITRAXX.EUR.MAIN, CDX.NA.HY")
    series: int = Field(ge=1)
    maturity_date: date
    fixed_coupon: float = Field(gt=0, description="Running coupon as a decimal, e.g. 0.01")
    recovery_rate: float = Field(default=0.4, ge=0, le=1)
    premium_frequency: Frequency = Frequency.QUARTERLY


# --- Digital assets ----------------------------------------------------------------

class CryptoSpot(InstrumentBase):
    product_type: Literal[ProductType.CRYPTO_SPOT] = ProductType.CRYPTO_SPOT
    symbol: str = Field(description="e.g. BTC, ETH")
    venue_name: str = Field(description="Exchange or custodian, e.g. COINBASE")

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


Instrument = Annotated[
    GovernmentBond
    | InterestRateSwap
    | FXSpot
    | FXForward
    | FXOption
    | CashEquity
    | EquityIndexFuture
    | EquityOption
    | CommodityFuture
    | CDSIndex
    | CryptoSpot,
    Field(discriminator="product_type"),
]

VENUE_BY_PRODUCT: dict[ProductType, Venue] = {
    ProductType.GOVERNMENT_BOND: Venue.LISTED,
    ProductType.INTEREST_RATE_SWAP: Venue.OTC,
    ProductType.FX_SPOT: Venue.OTC,
    ProductType.FX_FORWARD: Venue.OTC,
    ProductType.FX_OPTION: Venue.OTC,
    ProductType.CASH_EQUITY: Venue.LISTED,
    ProductType.EQUITY_INDEX_FUTURE: Venue.LISTED,
    ProductType.EQUITY_OPTION: Venue.LISTED,
    ProductType.COMMODITY_FUTURE: Venue.LISTED,
    ProductType.CDS_INDEX: Venue.OTC,
    ProductType.CRYPTO_SPOT: Venue.LISTED,
}
