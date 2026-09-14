"""Counterparties, netting sets and credit support annexes (CSAs)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CounterpartyType(StrEnum):
    BANK = "BANK"
    BROKER_DEALER = "BROKER_DEALER"
    HEDGE_FUND = "HEDGE_FUND"
    ASSET_MANAGER = "ASSET_MANAGER"
    CORPORATE = "CORPORATE"
    SOVEREIGN = "SOVEREIGN"
    CCP = "CCP"
    EXCHANGE = "EXCHANGE"


class Counterparty(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    counterparty_id: str
    name: str
    counterparty_type: CounterpartyType
    parent_id: str | None = Field(default=None, description="Group parent for concentration")
    country: str = Field(description="ISO 3166 alpha-2")
    sector: str = ""
    rating: str = Field(default="NR", description="External or internal rating, e.g. A+, NR")
    internal_pd: float | None = Field(default=None, ge=0, le=1, description="1y PD")
    on_watchlist: bool = False


class CSA(BaseModel):
    """Credit support annex terms. Amounts in ``collateral_currency``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    csa_id: str
    collateral_currency: str = Field(pattern=r"^[A-Z]{3}$")
    threshold_we_post: float = Field(ge=0, description="Our threshold before we post")
    threshold_they_post: float = Field(ge=0, description="Their threshold before they post")
    minimum_transfer_amount: float = Field(ge=0)
    independent_amount: float = Field(default=0.0, ge=0)
    rounding: float = Field(default=0.0, ge=0)
    haircut: float = Field(default=0.0, ge=0, le=1, description="Applied to posted collateral")
    margin_period_of_risk_days: int = Field(default=10, ge=1)
    call_frequency: str = Field(default="DAILY")


class NettingSet(BaseModel):
    """Trades that net under one agreement with one counterparty and one of our entities."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    netting_set_id: str
    counterparty_id: str
    legal_entity_id: str
    agreement_type: str = Field(default="ISDA", description="ISDA, GMRA, CCP rulebook, ...")
    csa_id: str | None = Field(default=None, description="None means uncollateralised")
