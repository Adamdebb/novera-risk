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
    BarrierType,
    DayCount,
    ExoticStyle,
    Frequency,
    OptionType,
    ProductType,
    SwapSide,
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


# --- Phase 6: repos, rates futures, swaptions, single-name CDS, commodity options, funds, exotics ---


class Repo(InstrumentBase):
    """Repo (we borrow cash against collateral) or reverse repo (we lend cash). ``direction``
    on the trade decides: BUY = reverse repo (cash lent, asset), SELL = repo (cash borrowed)."""

    product_type: Literal[ProductType.REPO] = ProductType.REPO
    collateral_instrument_id: str = Field(description="Government bond pledged")
    collateral_issuer: str = ""
    start_date: date
    end_date: date
    repo_rate: float = Field(description="Annual simple rate, decimal")
    haircut: float = Field(default=0.02, ge=0, le=0.5)
    day_count: DayCount = DayCount.ACT_360

    @model_validator(mode="after")
    def _dates(self) -> Repo:
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class InterestRateFuture(InstrumentBase):
    """Three-month money-market future quoted 100 − rate; 1bp = notional × 0.25 × 0.0001."""

    product_type: Literal[ProductType.INTEREST_RATE_FUTURE] = ProductType.INTEREST_RATE_FUTURE
    index: str = Field(description="e.g. USD-SOFR-3M, EUR-EURIBOR-3M")
    exchange: str
    expiry_date: date
    contract_notional: float = Field(default=1_000_000.0, gt=0)
    tenor_years: float = Field(default=0.25, gt=0)


class Swaption(InstrumentBase):
    """European swaption on a vanilla swap; payer pays fixed on exercise."""

    product_type: Literal[ProductType.SWAPTION] = ProductType.SWAPTION
    expiry_date: date
    swap_tenor: str = Field(description="e.g. 5Y")
    strike: float = Field(description="Fixed rate, decimal")
    payer: bool = True
    fixed_frequency: Frequency = Frequency.SEMI_ANNUAL
    fixed_day_count: DayCount = DayCount.THIRTY_360
    float_index: str = "USD-SOFR"
    cash_settled: bool = True

    @property
    def swap_side(self) -> SwapSide:
        return SwapSide.PAY_FIXED if self.payer else SwapSide.RECEIVE_FIXED


class CDSSingleName(InstrumentBase):
    product_type: Literal[ProductType.CDS_SINGLE_NAME] = ProductType.CDS_SINGLE_NAME
    reference_entity: str = Field(description="Factor key, e.g. FORD")
    entity_name: str = ""
    sector: str = ""
    maturity_date: date
    fixed_coupon: float = Field(gt=0)
    recovery_rate: float = Field(default=0.4, ge=0, le=1)
    premium_frequency: Frequency = Frequency.QUARTERLY


class CommodityOption(InstrumentBase):
    """Vanilla option on a commodity future, Black on the curve price at expiry."""

    product_type: Literal[ProductType.COMMODITY_OPTION] = ProductType.COMMODITY_OPTION
    commodity: str
    exchange: str
    option_type: OptionType
    strike: float = Field(gt=0)
    expiry_date: date
    contract_size: float = Field(gt=0)
    unit: str = ""


class BasketLeg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    underlying: str = Field(description="Equity ticker, index code or commodity code")
    kind: str = Field(description="EQ, EQIDX or CMD")
    units_per_share: float = Field(gt=0, description="Units of the underlying per fund share")
    currency: Currency


class ETF(InstrumentBase):
    """Exchange-traded fund priced by look-through: NAV per share = Σ units × price, times
    one plus the tracking spread."""

    product_type: Literal[ProductType.ETF] = ProductType.ETF
    ticker: str
    exchange: str
    basket: tuple[BasketLeg, ...] = Field(min_length=1)
    tracking_spread: float = 0.0
    expense_ratio: float = Field(default=0.002, ge=0)


class MutualFund(InstrumentBase):
    """Open-ended fund priced by look-through with a cash sleeve; dealt through a transfer agent."""

    product_type: Literal[ProductType.MUTUAL_FUND] = ProductType.MUTUAL_FUND
    fund_code: str
    manager: str = ""
    basket: tuple[BasketLeg, ...] = Field(min_length=1)
    cash_per_share: float = Field(default=0.0, ge=0, description="Cash sleeve per share in fund currency")
    dealing_frequency: str = "DAILY"
    notice_days: int = 1


class EquityExotic(InstrumentBase):
    """Continuously monitored barrier option or cash-or-nothing digital on an equity or index."""

    product_type: Literal[ProductType.EQUITY_EXOTIC] = ProductType.EQUITY_EXOTIC
    underlying: str
    style: ExoticStyle
    option_type: OptionType
    strike: float = Field(gt=0)
    expiry_date: date
    barrier: float | None = Field(default=None, gt=0)
    barrier_type: BarrierType | None = None
    rebate: float = Field(default=0.0, ge=0)
    cash_payout: float | None = Field(default=None, gt=0, description="Digital payout per unit")
    contract_multiplier: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def _shape(self) -> EquityExotic:
        if self.style is ExoticStyle.BARRIER and (self.barrier is None or self.barrier_type is None):
            raise ValueError("barrier options need barrier and barrier_type")
        if self.style is ExoticStyle.DIGITAL and self.cash_payout is None:
            raise ValueError("digital options need cash_payout")
        return self


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
    | CryptoSpot
    | Repo
    | InterestRateFuture
    | Swaption
    | CDSSingleName
    | CommodityOption
    | ETF
    | MutualFund
    | EquityExotic,
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
    ProductType.REPO: Venue.OTC,
    ProductType.INTEREST_RATE_FUTURE: Venue.LISTED,
    ProductType.SWAPTION: Venue.OTC,
    ProductType.CDS_SINGLE_NAME: Venue.OTC,
    ProductType.COMMODITY_OPTION: Venue.LISTED,
    ProductType.ETF: Venue.LISTED,
    ProductType.MUTUAL_FUND: Venue.LISTED,
    ProductType.EQUITY_EXOTIC: Venue.OTC,
}
