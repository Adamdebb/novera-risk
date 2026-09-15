"""Phase 6 pricers: repo, interest-rate future, swaption, single-name CDS, commodity option,
ETF and mutual fund look-through, equity barrier and digital options.
Methodology records PR-010 to PR-016."""

from __future__ import annotations

import math
from datetime import date

from dateutil.relativedelta import relativedelta

from novera.domain.enums import BuySell, ExoticStyle
from novera.domain.instruments import (
    ETF,
    BasketLeg,
    CDSSingleName,
    CommodityOption,
    EquityExotic,
    InterestRateFuture,
    MutualFund,
    Repo,
    Swaption,
)
from novera.domain.trades import Trade
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing.base import Cashflow, PricingResult, year_fraction_act365
from novera.pricing.black import black_greeks, black_price
from novera.pricing.credit import CDS_MODEL_VERSION, cds_legs
from novera.pricing.exotic_formulas import bachelier_price, barrier_price, cash_or_nothing
from novera.pricing.schedule import remaining_periods, year_fraction

REPO_MODEL_VERSION = "1.0.0"
IR_FUTURE_MODEL_VERSION = "1.0.0"
SWAPTION_MODEL_VERSION = "1.0.0"
COMMODITY_OPTION_MODEL_VERSION = "1.0.0"
FUND_LOOKTHROUGH_MODEL_VERSION = "1.0.0"
EXOTIC_MODEL_VERSION = "1.0.0"


# --- Repo / reverse repo (PR-010) ----------------------------------------------------------------


def price_repo(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    """Cash leg marked against the zero curve. BUY = reverse repo: we lent ``quantity`` of cash
    and receive it back with interest; PV is the receivable minus the principal outstanding, so
    it is zero at a fair rate and carries DV01 on the term. The collateral stays on the
    counterparty's balance sheet and is reported for exposure purposes only."""
    ins = trade.instrument
    assert isinstance(ins, Repo)
    if ins.end_date <= as_of:
        return PricingResult(
            trade.trade_id, ins.currency, 0.0, "repo_cash_leg", REPO_MODEL_VERSION, note="matured"
        )
    curve = market.zero_curve(ins.currency)
    tau = year_fraction(ins.start_date, ins.end_date, ins.day_count)
    t_end = year_fraction_act365(as_of, ins.end_date)
    t_start = max(year_fraction_act365(as_of, ins.start_date), 0.0)
    df_end, df_start = float(curve.df(t_end)), float(curve.df(t_start))
    n = trade.quantity
    repayment = n * (1.0 + ins.repo_rate * tau)
    unit_pv = repayment * df_end - n * df_start  # per reverse repo
    fair_rate = (df_start / df_end - 1.0) / tau if tau > 0 else ins.repo_rate
    sign = 1.0 if trade.direction is BuySell.BUY else -1.0
    return PricingResult(
        trade.trade_id,
        ins.currency,
        sign * unit_pv,
        "repo_cash_leg",
        REPO_MODEL_VERSION,
        cashflows=(
            Cashflow(ins.start_date, -sign * n, "PRINCIPAL"),
            Cashflow(ins.end_date, sign * repayment, "PRINCIPAL"),
        ),
        details={
            "repo_rate": ins.repo_rate,
            "fair_rate": fair_rate,
            "term_years": tau,
            "years_to_end": t_end,
            "cash_principal": sign * n,
            "collateral_required": n / (1.0 - ins.haircut),
            "haircut": ins.haircut,
        },
    )


# --- Interest-rate future (PR-011) ---------------------------------------------------------------


def price_interest_rate_future(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    """Price = 100 − 100 × simple forward over the contract period, no convexity adjustment.
    Daily-settled: PV is the variation margin against the trade price."""
    ins = trade.instrument
    assert isinstance(ins, InterestRateFuture)
    if ins.expiry_date <= as_of:
        return PricingResult(
            trade.trade_id, ins.currency, 0.0, "ir_future_forward", IR_FUTURE_MODEL_VERSION, note="expired"
        )
    curve = market.zero_curve(ins.currency)
    t1 = year_fraction_act365(as_of, ins.expiry_date)
    t2 = t1 + ins.tenor_years
    fwd = (float(curve.df(t1)) / float(curve.df(t2)) - 1.0) / ins.tenor_years
    price = 100.0 - 100.0 * fwd
    per_point = ins.contract_notional * ins.tenor_years / 100.0  # value of one price point
    pv = trade.signed_quantity * per_point * (price - trade.trade_price)
    return PricingResult(
        trade.trade_id,
        ins.currency,
        pv,
        "ir_future_forward",
        IR_FUTURE_MODEL_VERSION,
        details={
            "forward_rate": fwd,
            "price": price,
            "forward": price,
            "years_to_expiry": t1,
            "notional_exposure": trade.signed_quantity * ins.contract_notional,
            "bp_value": trade.signed_quantity * per_point / 100.0,
        },
    )


# --- Swaption (PR-012) ---------------------------------------------------------------------------


def forward_swap(market: MarketSnapshot, ins: Swaption, as_of: date) -> tuple[float, float, date]:
    """(forward swap rate, annuity per unit notional, swap maturity) off the single curve."""
    curve = market.zero_curve(ins.currency)
    years = int(ins.swap_tenor.rstrip("Y"))
    maturity = ins.expiry_date + relativedelta(years=years)
    annuity = 0.0
    for start, end in remaining_periods(ins.expiry_date, maturity, ins.fixed_frequency, as_of):
        tau = year_fraction(start, end, ins.fixed_day_count)
        annuity += tau * float(curve.df(year_fraction_act365(as_of, end)))
    df_start = float(curve.df(year_fraction_act365(as_of, ins.expiry_date)))
    df_end = float(curve.df(year_fraction_act365(as_of, maturity)))
    fwd = (df_start - df_end) / annuity if annuity > 0 else 0.0
    return fwd, annuity, maturity


def price_swaption(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    """European swaption under the normal (Bachelier) model on the forward swap rate."""
    ins = trade.instrument
    assert isinstance(ins, Swaption)
    if ins.expiry_date <= as_of:
        return PricingResult(
            trade.trade_id, ins.currency, 0.0, "swaption_bachelier", SWAPTION_MODEL_VERSION, note="expired"
        )
    fwd, annuity, maturity = forward_swap(market, ins, as_of)
    t = year_fraction_act365(as_of, ins.expiry_date)
    tenor_years = int(ins.swap_tenor.rstrip("Y"))
    vol_bp = market.swaption_normal_vol_bp(ins.currency, t, tenor_years)
    unit = annuity * bachelier_price(ins.payer, fwd, ins.strike, vol_bp / 1e4, t)
    sign = 1.0 if trade.direction is BuySell.BUY else -1.0
    sd = vol_bp / 1e4 * math.sqrt(t)
    d = (fwd - ins.strike) / sd if sd > 0 else 0.0
    from novera.pricing.black import norm_cdf, norm_pdf

    delta = annuity * (norm_cdf(d) if ins.payer else norm_cdf(d) - 1.0)  # per unit rate
    vega_bp = annuity * math.sqrt(t) * norm_pdf(d) / 1e4  # per +1bp normal vol
    return PricingResult(
        trade.trade_id,
        ins.currency,
        sign * trade.quantity * unit,
        "swaption_bachelier",
        SWAPTION_MODEL_VERSION,
        details={
            "forward_swap_rate": fwd,
            "annuity": annuity,
            "normal_vol_bp": vol_bp,
            "unit_price": unit,
            "years_to_expiry": t,
            "swap_years": float((maturity - ins.expiry_date).days / 365.0),
            "rate_delta": sign * trade.quantity * delta,
            "vega_1bp": sign * trade.quantity * vega_bp,
        },
    )


# --- Single-name CDS (PR-013) --------------------------------------------------------------------


def price_cds_single_name(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    ins = trade.instrument
    assert isinstance(ins, CDSSingleName)
    if ins.maturity_date <= as_of:
        return PricingResult(
            trade.trade_id, ins.currency, 0.0, "cds_flat_hazard", CDS_MODEL_VERSION, note="matured"
        )
    curve = market.zero_curve(ins.currency)
    spread = market.cds_spread_bp(ins.reference_entity)
    periods = remaining_periods(as_of.replace(day=1), ins.maturity_date, ins.premium_frequency, as_of)
    prot, prem, annuity = cds_legs(
        trade.quantity, ins.fixed_coupon, spread, ins.recovery_rate, periods, as_of, curve.df
    )
    sign = 1.0 if trade.direction is BuySell.BUY else -1.0  # BUY = buy protection
    return PricingResult(
        trade.trade_id,
        ins.currency,
        sign * (prot - prem),
        "cds_flat_hazard",
        CDS_MODEL_VERSION,
        details={
            "spread_bp": spread,
            "hazard_rate": (spread / 1e4) / (1 - ins.recovery_rate),
            "protection_leg_pv": sign * prot,
            "premium_leg_pv": -sign * prem,
            "risky_annuity": annuity,
            "jump_to_default": -sign * trade.quantity * (1 - ins.recovery_rate) * -1.0,
            "years_to_maturity": year_fraction_act365(as_of, ins.maturity_date),
        },
    )


# --- Commodity option (PR-014) -------------------------------------------------------------------


def price_commodity_option(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    """Black (1976) on the futures price at expiry, vol from the commodity's surface."""
    ins = trade.instrument
    assert isinstance(ins, CommodityOption)
    if ins.expiry_date <= as_of:
        return PricingResult(
            trade.trade_id,
            ins.currency,
            0.0,
            "commodity_option_black76",
            COMMODITY_OPTION_MODEL_VERSION,
            note="expired",
        )
    t = year_fraction_act365(as_of, ins.expiry_date)
    fwd = float(market.commodity_curve(ins.commodity).price(t))
    df = float(market.zero_curve(ins.currency).df(t))
    vol = market.vol_surface(ins.commodity).vol(t, ins.strike / fwd)
    unit = black_price(ins.option_type, fwd, ins.strike, vol, t, df)
    sign = 1.0 if trade.direction is BuySell.BUY else -1.0
    scale = sign * trade.quantity * ins.contract_size
    greeks = black_greeks(ins.option_type, fwd, ins.strike, vol, t, df)
    return PricingResult(
        trade.trade_id,
        ins.currency,
        scale * unit,
        "commodity_option_black76",
        COMMODITY_OPTION_MODEL_VERSION,
        details={
            "forward": fwd,
            "vol": vol,
            "unit_price": unit,
            "years_to_expiry": t,
            "moneyness": ins.strike / fwd,
            "units": scale,
            **{k: scale * v for k, v in greeks.items() if k in ("delta_fwd", "gamma_fwd", "vega", "theta")},
        },
    )


# --- Funds by look-through (PR-015) --------------------------------------------------------------


def leg_price(market: MarketSnapshot, leg: BasketLeg) -> float:
    if leg.kind == "EQ":
        return market.equity_spot(leg.underlying)
    if leg.kind == "EQIDX":
        return market.index_level(leg.underlying)
    if leg.kind == "CMD":
        return float(market.commodity_curve(leg.underlying).price(1.0 / 12.0))
    if leg.kind == "CRYPTO":
        return market.crypto_spot(leg.underlying)
    raise KeyError(f"unknown basket leg kind {leg.kind}")


def fund_nav(
    market: MarketSnapshot, basket: tuple[BasketLeg, ...], currency: str, cash: float = 0.0
) -> float:
    nav = cash
    for leg in basket:
        fx = 1.0 if leg.currency == currency else market.fx_spot(f"{leg.currency}/{currency}")
        nav += leg.units_per_share * leg_price(market, leg) * fx
    return nav


def price_etf(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    ins = trade.instrument
    assert isinstance(ins, ETF)
    nav = fund_nav(market, ins.basket, ins.currency)
    price = nav * (1.0 + ins.tracking_spread)
    return PricingResult(
        trade.trade_id,
        ins.currency,
        trade.signed_quantity * price,
        "fund_lookthrough",
        FUND_LOOKTHROUGH_MODEL_VERSION,
        details={
            "nav": nav,
            "spot": price,
            "shares": trade.signed_quantity,
            "constituents": float(len(ins.basket)),
        },
    )


def price_mutual_fund(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    ins = trade.instrument
    assert isinstance(ins, MutualFund)
    nav = fund_nav(market, ins.basket, ins.currency, ins.cash_per_share)
    return PricingResult(
        trade.trade_id,
        ins.currency,
        trade.signed_quantity * nav,
        "fund_lookthrough",
        FUND_LOOKTHROUGH_MODEL_VERSION,
        details={
            "nav": nav,
            "spot": nav,
            "shares": trade.signed_quantity,
            "cash_per_share": ins.cash_per_share,
            "constituents": float(len(ins.basket)),
        },
    )


# --- Equity barrier and digital options (PR-016) -------------------------------------------------


def _spot(market: MarketSnapshot, code: str) -> float:
    return market.index_level(code) if market.has(f"EQIDX:{code}") else market.equity_spot(code)


def price_equity_exotic(trade: Trade, market: MarketSnapshot, as_of: date) -> PricingResult:
    ins = trade.instrument
    assert isinstance(ins, EquityExotic)
    if ins.expiry_date <= as_of:
        return PricingResult(
            trade.trade_id, ins.currency, 0.0, "exotic_closed_form_bs", EXOTIC_MODEL_VERSION, note="expired"
        )
    t = year_fraction_act365(as_of, ins.expiry_date)
    spot = _spot(market, ins.underlying)
    df = float(market.zero_curve(ins.currency).df(t))
    rate = -math.log(df) / t if t > 0 else 0.0
    fwd = spot / df
    vol = market.vol_surface(ins.underlying).vol(t, ins.strike / fwd)
    if ins.style is ExoticStyle.BARRIER:
        assert ins.barrier is not None and ins.barrier_type is not None
        unit = barrier_price(
            ins.option_type, ins.barrier_type, spot, ins.strike, ins.barrier, rate, vol, t, ins.rebate
        )
        vanilla = black_price(ins.option_type, fwd, ins.strike, vol, t, df)
        extra = {
            "vanilla_price": vanilla,
            "barrier": ins.barrier,
            "barrier_distance": ins.barrier / spot - 1.0,
        }
    else:
        assert ins.cash_payout is not None
        unit = cash_or_nothing(ins.option_type, spot, ins.strike, rate, vol, t, ins.cash_payout)
        extra = {"payout": ins.cash_payout}
    sign = 1.0 if trade.direction is BuySell.BUY else -1.0
    scale = sign * trade.quantity * ins.contract_multiplier
    return PricingResult(
        trade.trade_id,
        ins.currency,
        scale * unit,
        "exotic_closed_form_bs",
        EXOTIC_MODEL_VERSION,
        details={
            "spot": spot,
            "forward": fwd,
            "vol": vol,
            "rate": rate,
            "unit_price": unit,
            "years_to_expiry": t,
            "moneyness": ins.strike / fwd,
            **extra,
        },
    )


__all__ = [
    "price_repo",
    "price_interest_rate_future",
    "price_swaption",
    "price_cds_single_name",
    "price_commodity_option",
    "price_etf",
    "price_mutual_fund",
    "price_equity_exotic",
    "forward_swap",
    "fund_nav",
    "leg_price",
]
