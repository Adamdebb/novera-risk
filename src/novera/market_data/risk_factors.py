"""The shared risk-factor universe.

Every instrument maps to factors in this universe. Sensitivities, VaR and stress all key
off the same ``factor_id`` so results reconcile (docs/02-architecture.md).

Factor id conventions:
    IR:{CCY}:{TENOR}             continuously compounded zero rate, decimal
    FX:{BASE}{QUOTE}             spot, quote per base
    EQ:{TICKER}                  share price in the share currency
    EQIDX:{INDEX}                index level
    CMD:{CODE}:{TENOR}           futures price at the tenor, USD
    CDS:{FAMILY}                 index spread in basis points
    CRYPTO:{SYMBOL}              price in USD
    VOL:{UNDERLYING}:{EXPIRY}:{MONEYNESS}   implied vol, decimal, K/F moneyness
    SWVOL:{CCY}:{EXPIRY}:{TENOR}   swaption normal (Bachelier) vol in basis points per year
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RiskFactorType(StrEnum):
    IR_ZERO = "IR_ZERO"
    FX_SPOT = "FX_SPOT"
    EQUITY_SPOT = "EQUITY_SPOT"
    EQUITY_INDEX = "EQUITY_INDEX"
    COMMODITY_CURVE = "COMMODITY_CURVE"
    CREDIT_SPREAD = "CREDIT_SPREAD"
    CRYPTO_SPOT = "CRYPTO_SPOT"
    IMPLIED_VOL = "IMPLIED_VOL"
    SWAPTION_VOL = "SWAPTION_VOL"


class RiskFactor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    factor_id: str
    factor_type: RiskFactorType
    asset_class: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    underlying: str = Field(description="Currency, pair, ticker, index, commodity or family")
    tenor: str | None = Field(default=None, description="Curve tenor label, e.g. 10Y")
    tenor_years: float | None = None
    expiry_years: float | None = None
    moneyness: float | None = Field(default=None, description="K/F for vol nodes")
    unit: str = Field(description="rate, price, level, bp, vol")
    shock_type: str = Field(description="ABSOLUTE (rates, spreads) or RELATIVE (prices, vols)")


TENOR_YEARS: dict[str, float] = {
    "1W": 7 / 365,
    "1M": 1 / 12,
    "2M": 2 / 12,
    "3M": 0.25,
    "6M": 0.5,
    "9M": 0.75,
    "1Y": 1.0,
    "18M": 1.5,
    "2Y": 2.0,
    "3Y": 3.0,
    "5Y": 5.0,
    "7Y": 7.0,
    "10Y": 10.0,
    "15Y": 15.0,
    "20Y": 20.0,
    "30Y": 30.0,
}


def ir_id(ccy: str, tenor: str) -> str:
    return f"IR:{ccy}:{tenor}"


def fx_id(pair: str) -> str:
    return f"FX:{pair.replace('/', '')}"


def eq_id(ticker: str) -> str:
    return f"EQ:{ticker}"


def eqidx_id(index: str) -> str:
    return f"EQIDX:{index}"


def cmd_id(code: str, tenor: str) -> str:
    return f"CMD:{code}:{tenor}"


def cds_id(family: str) -> str:
    return f"CDS:{family}"


def crypto_id(symbol: str) -> str:
    return f"CRYPTO:{symbol}"


def vol_id(underlying: str, expiry: str, moneyness: float) -> str:
    return f"VOL:{underlying.replace('/', '')}:{expiry}:{moneyness:.2f}"


def swvol_id(ccy: str, expiry: str, tenor: str) -> str:
    return f"SWVOL:{ccy}:{expiry}:{tenor}"
