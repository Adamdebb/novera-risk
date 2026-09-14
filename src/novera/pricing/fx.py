"""FX spot, forwards and vanilla options. Methodology records PR-003 and PR-004."""

from __future__ import annotations

from datetime import date

from novera.domain.enums import BuySell
from novera.domain.instruments import FXForward, FXOption, FXSpot
from novera.domain.trades import Trade
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing.base import Cashflow, PricingResult, year_fraction_act365
from novera.pricing.black import black_greeks, black_price

FX_FORWARD_MODEL_VERSION = "1.0.0"
FX_OPTION_MODEL_VERSION = "1.0.0"


def fx_forward_rate(market: MarketSnapshot, pair: str, t: float) -> tuple[float, float, float]:
    """(forward, df_quote, df_base) from covered interest parity on the zero curves."""
    base, quote = pair[:3], pair[4:]
    spot = market.fx_spot(pair)
    df_b = float(market.zero_curve(base).df(t))
    df_q = float(market.zero_curve(quote).df(t))
    return spot * df_b / df_q, df_q, df_b


def price_fx_spot(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    """Spot trade: value of the base-currency amount against the contracted rate, in quote ccy.
    Once settled it is a cash position; we keep marking the P&L against the trade rate so
    the FX delta stays visible until the position is closed."""
    ins = trade.instrument
    assert isinstance(ins, FXSpot)
    spot = market.fx_spot(ins.pair)
    pv = trade.signed_quantity * (spot - trade.trade_price)
    return PricingResult(
        trade.trade_id,
        ins.currency,
        pv,
        "fx_spot_mtm",
        FX_FORWARD_MODEL_VERSION,
        details={"spot": spot, "base_amount": trade.signed_quantity},
    )


def price_fx_forward(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    ins = trade.instrument
    assert isinstance(ins, FXForward)
    if ins.settlement_date <= as_of:
        return PricingResult(
            trade.trade_id, ins.currency, 0.0, "fx_forward_cip", FX_FORWARD_MODEL_VERSION, note="settled"
        )
    t = year_fraction_act365(as_of, ins.settlement_date)
    fwd, df_q, df_b = fx_forward_rate(market, ins.pair, t)
    pv = trade.signed_quantity * (fwd - ins.forward_rate) * df_q
    flows = (Cashflow(ins.settlement_date, trade.signed_quantity * (fwd - ins.forward_rate), "SETTLEMENT"),)
    return PricingResult(
        trade.trade_id,
        ins.currency,
        pv,
        "fx_forward_cip",
        FX_FORWARD_MODEL_VERSION,
        cashflows=flows,
        details={
            "forward": fwd,
            "spot": market.fx_spot(ins.pair),
            "df_quote": df_q,
            "df_base": df_b,
            "years_to_settlement": t,
        },
    )


def price_fx_option(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    """Garman–Kohlhagen as Black on the CIP forward, vol read at K/F from the surface."""
    ins = trade.instrument
    assert isinstance(ins, FXOption)
    if ins.expiry_date <= as_of:
        return PricingResult(
            trade.trade_id,
            ins.currency,
            0.0,
            "fx_option_garman_kohlhagen",
            FX_OPTION_MODEL_VERSION,
            note="expired",
        )
    t = year_fraction_act365(as_of, ins.expiry_date)
    fwd, df_q, _ = fx_forward_rate(market, ins.pair, t)
    vol = market.vol_surface(ins.pair).vol(t, ins.strike / fwd)
    unit = black_price(ins.option_type, fwd, ins.strike, vol, t, df_q)
    sign = 1.0 if trade.direction is BuySell.BUY else -1.0
    greeks = black_greeks(ins.option_type, fwd, ins.strike, vol, t, df_q)
    return PricingResult(
        trade.trade_id,
        ins.currency,
        sign * trade.quantity * unit,
        "fx_option_garman_kohlhagen",
        FX_OPTION_MODEL_VERSION,
        details={
            "forward": fwd,
            "vol": vol,
            "unit_price": unit,
            "years_to_expiry": t,
            "moneyness": ins.strike / fwd,
            **{
                k: sign * trade.quantity * v
                for k, v in greeks.items()
                if k in ("delta_fwd", "gamma_fwd", "vega", "theta")
            },
        },
    )
