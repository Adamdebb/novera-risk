"""Cash equity, index futures and vanilla equity options. Methodology records PR-005, PR-006."""

from __future__ import annotations

from datetime import date

from novera.domain.enums import BuySell
from novera.domain.instruments import CashEquity, EquityIndexFuture, EquityOption
from novera.domain.trades import Trade
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing.base import PricingResult, year_fraction_act365
from novera.pricing.black import black_greeks, black_price

EQUITY_MODEL_VERSION = "1.0.0"
EQUITY_OPTION_MODEL_VERSION = "1.0.0"


def _underlying_spot(market: MarketSnapshot, code: str) -> float:
    if market.has(f"EQIDX:{code}"):
        return market.index_level(code)
    return market.equity_spot(code)


def equity_forward(market: MarketSnapshot, code: str, currency: str, t: float) -> tuple[float, float]:
    """(forward, df) with cost of carry at the zero rate and no dividend yield."""
    spot = _underlying_spot(market, code)
    df = float(market.zero_curve(currency).df(t))
    return spot / df, df


def price_cash_equity(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    ins = trade.instrument
    assert isinstance(ins, CashEquity)
    spot = market.equity_spot(ins.ticker)
    return PricingResult(
        trade.trade_id,
        ins.currency,
        trade.signed_quantity * spot,
        "equity_mtm",
        EQUITY_MODEL_VERSION,
        details={"spot": spot, "shares": trade.signed_quantity},
    )


def price_equity_index_future(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    ins = trade.instrument
    assert isinstance(ins, EquityIndexFuture)
    if ins.expiry_date <= as_of:
        return PricingResult(
            trade.trade_id, ins.currency, 0.0, "index_future_carry", EQUITY_MODEL_VERSION, note="expired"
        )
    t = year_fraction_act365(as_of, ins.expiry_date)
    fwd, df = equity_forward(market, ins.index, ins.currency, t)
    exposure = trade.signed_quantity * ins.contract_multiplier
    pv = exposure * (fwd - trade.trade_price)  # daily-settled: variation margin value
    return PricingResult(
        trade.trade_id,
        ins.currency,
        pv,
        "index_future_carry",
        EQUITY_MODEL_VERSION,
        details={
            "forward": fwd,
            "spot": _underlying_spot(market, ins.index),
            "notional_exposure": exposure * fwd,
            "years_to_expiry": t,
        },
    )


def price_equity_option(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    ins = trade.instrument
    assert isinstance(ins, EquityOption)
    if ins.expiry_date <= as_of:
        return PricingResult(
            trade.trade_id,
            ins.currency,
            0.0,
            "equity_option_black_scholes",
            EQUITY_OPTION_MODEL_VERSION,
            note="expired",
        )
    t = year_fraction_act365(as_of, ins.expiry_date)
    fwd, df = equity_forward(market, ins.underlying, ins.currency, t)
    vol = market.vol_surface(ins.underlying).vol(t, ins.strike / fwd)
    unit = black_price(ins.option_type, fwd, ins.strike, vol, t, df)
    sign = 1.0 if trade.direction is BuySell.BUY else -1.0
    scale = sign * trade.quantity * ins.contract_multiplier
    greeks = black_greeks(ins.option_type, fwd, ins.strike, vol, t, df)
    return PricingResult(
        trade.trade_id,
        ins.currency,
        scale * unit,
        "equity_option_black_scholes",
        EQUITY_OPTION_MODEL_VERSION,
        details={
            "forward": fwd,
            "spot": _underlying_spot(market, ins.underlying),
            "vol": vol,
            "unit_price": unit,
            "years_to_expiry": t,
            "moneyness": ins.strike / fwd,
            **{k: scale * v for k, v in greeks.items() if k in ("delta_fwd", "gamma_fwd", "vega", "theta")},
        },
    )
