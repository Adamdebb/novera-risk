"""Trades: an instrument plus economics, booking and counterparty attributes."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from novera.domain.enums import (
    AssetClass,
    BuySell,
    ClearingType,
    ProductType,
    SwapSide,
    TradeStatus,
    Venue,
)
from novera.domain.instruments import Instrument, InterestRateSwap


class Trade(BaseModel):
    """A single trade. Frozen: a trade is a fact; lifecycle changes create a new version.

    ``quantity`` semantics by product:
    - bonds, IRS, CDS index, FX: notional in the instrument currency (signed by direction)
    - equities, crypto: number of shares/units
    - futures and options: number of contracts (multiplier lives on the instrument)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    trade_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    instrument: Instrument
    direction: BuySell
    swap_side: SwapSide | None = Field(default=None, description="Required for swaps")
    quantity: float = Field(gt=0, description="Unsigned; sign comes from direction")
    trade_price: float = Field(description="Executed price, rate or spread as quoted")
    trade_date: date
    settlement_date: date | None = None
    booked_at: datetime | None = None

    # Booking hierarchy
    book_id: str
    trader_id: str

    # Counterparty and clearing
    counterparty_id: str
    clearing: ClearingType
    netting_set_id: str | None = Field(default=None, description="Required for bilateral OTC")

    status: TradeStatus = TradeStatus.LIVE
    source_system: str = Field(default="SIM", description="Origin system for reconciliation")
    validation_errors: tuple[str, ...] = Field(default=(), description="Set by data quality")

    @property
    def product_type(self) -> ProductType:
        return self.instrument.product_type

    @property
    def asset_class(self) -> AssetClass:
        return self.instrument.asset_class

    @property
    def venue(self) -> Venue:
        return self.instrument.venue

    @property
    def currency(self) -> str:
        return self.instrument.currency

    @property
    def signed_quantity(self) -> float:
        return self.quantity if self.direction is BuySell.BUY else -self.quantity

    @model_validator(mode="after")
    def _consistency(self) -> Trade:
        if isinstance(self.instrument, InterestRateSwap) and self.swap_side is None:
            raise ValueError("swap_side is required for an interest rate swap")
        if self.venue is Venue.OTC and self.clearing is ClearingType.EXCHANGE:
            raise ValueError("OTC trades cannot be exchange-cleared")
        if (
            self.venue is Venue.OTC
            and self.clearing is ClearingType.BILATERAL
            and self.netting_set_id is None
            and self.status is not TradeStatus.INVALID
        ):
            raise ValueError("bilateral OTC trade requires a netting_set_id")
        if self.settlement_date is not None and self.settlement_date < self.trade_date:
            raise ValueError("settlement_date cannot precede trade_date")
        return self
