"""Instrument universe for the simulated bank, built relative to a business date."""

from __future__ import annotations

from datetime import date

from dateutil.relativedelta import relativedelta

from novera.domain import (
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
    InterestRateSwap,
    OptionType,
)
from novera.simulation import reference_levels as ref

BOND_TENORS = ("2Y", "5Y", "10Y", "30Y")
SWAP_TENORS = ("2Y", "5Y", "10Y", "30Y")


def _add_tenor(d: date, tenor: str) -> date:
    n, unit = int(tenor[:-1]), tenor[-1]
    return d + (relativedelta(years=n) if unit == "Y" else relativedelta(months=n))


def _imm_dates(start: date, count: int) -> list[date]:
    """Quarterly IMM-style expiries (third Friday of Mar/Jun/Sep/Dec) after ``start``."""
    out: list[date] = []
    y, m = start.year, start.month
    while len(out) < count:
        m = ((m - 1) // 3 + 1) * 3
        if m > 12:
            m, y = 3, y + 1
        first = date(y, m, 1)
        third_friday = first + relativedelta(days=(4 - first.weekday()) % 7 + 14)
        if third_friday > start:
            out.append(third_friday)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def government_bonds(currency: str, business_date: date) -> list[GovernmentBond]:
    """One benchmark bond per tenor, issued at par-ish coupons around a year ago."""
    curve = ref.SWAP_CURVES[currency]
    out = []
    for tenor in BOND_TENORS:
        issue = business_date - relativedelta(months=9)
        issue = issue.replace(day=15)
        coupon = round(curve[tenor] + ref.GOVT_SPREAD[currency], 4)
        mat = _add_tenor(issue, tenor)
        out.append(
            GovernmentBond(
                instrument_id=f"{currency}_GOVT_{tenor}_{mat:%Y%m}",
                currency=currency,
                issuer=ref.GOVT_ISSUER[currency],
                coupon_rate=coupon,
                issue_date=issue,
                maturity_date=mat,
                description=f"{ref.GOVT_ISSUER[currency]} {coupon:.2%} {mat:%b %Y}",
            )
        )
    return out


def swap(currency: str, tenor: str, effective: date, fixed_rate: float) -> InterestRateSwap:
    mat = _add_tenor(effective, tenor)
    return InterestRateSwap(
        instrument_id=f"{currency}_IRS_{tenor}_{effective:%Y%m%d}_{fixed_rate * 1e4:.0f}",
        currency=currency,
        effective_date=effective,
        maturity_date=mat,
        fixed_rate=fixed_rate,
        float_index=ref.FLOAT_INDEX[currency],
        description=f"{tenor} {currency} IRS {fixed_rate:.3%}",
    )


def fx_spot(pair: str) -> FXSpot:
    return FXSpot(instrument_id=f"FX_{pair.replace('/', '')}_SPOT", currency=pair[4:], pair=pair)


def fx_forward(pair: str, settlement: date, rate: float) -> FXForward:
    return FXForward(
        instrument_id=f"FX_{pair.replace('/', '')}_FWD_{settlement:%Y%m%d}_{rate:.4f}",
        currency=pair[4:],
        pair=pair,
        settlement_date=settlement,
        forward_rate=rate,
    )


def fx_option(pair: str, expiry: date, strike: float, kind: OptionType) -> FXOption:
    return FXOption(
        instrument_id=f"FX_{pair.replace('/', '')}_{kind.value[0]}_{strike:.4f}_{expiry:%Y%m%d}",
        currency=pair[4:],
        pair=pair,
        option_type=kind,
        strike=strike,
        expiry_date=expiry,
        settlement_date=expiry + relativedelta(days=2),
    )


def cash_equity(ticker: str) -> CashEquity:
    exch, ccy, sector, country, _, _ = ref.EQUITIES[ticker]
    return CashEquity(
        instrument_id=f"EQ_{ticker}",
        currency=ccy,
        ticker=ticker,
        exchange=exch,
        sector=sector,
        country=country,
    )


def index_futures(index: str, business_date: date, count: int = 3) -> list[EquityIndexFuture]:
    exch, ccy, _, _, mult = ref.EQUITY_INDICES[index]
    return [
        EquityIndexFuture(
            instrument_id=f"FUT_{index}_{exp:%Y%m}",
            currency=ccy,
            index=index,
            exchange=exch,
            expiry_date=exp,
            contract_multiplier=mult,
        )
        for exp in _imm_dates(business_date, count)
    ]


def equity_option(
    underlying: str,
    expiry: date,
    strike: float,
    kind: OptionType,
    currency: str,
    exchange: str,
    multiplier: float = 100.0,
) -> EquityOption:
    return EquityOption(
        instrument_id=f"OPT_{underlying}_{kind.value[0]}_{strike:.0f}_{expiry:%Y%m%d}",
        currency=currency,
        underlying=underlying,
        option_type=kind,
        strike=strike,
        expiry_date=expiry,
        contract_multiplier=multiplier,
        exchange=exchange,
    )


def commodity_futures(code: str, business_date: date, count: int = 6) -> list[CommodityFuture]:
    exch, ccy, _, _, size, unit = ref.COMMODITIES[code]
    first = business_date.replace(day=1) + relativedelta(months=2)
    return [
        CommodityFuture(
            instrument_id=f"FUT_{code}_{d:%Y%m}",
            currency=ccy,
            commodity=code,
            exchange=exch,
            expiry_date=d - relativedelta(days=5),
            contract_size=size,
            unit=unit,
        )
        for d in (first + relativedelta(months=2 * k) for k in range(count))
    ]


def cds_index(family: str, business_date: date) -> CDSIndex:
    ccy, series, coupon, _, rec = ref.CDS_INDICES[family]
    # On-the-run 5Y: matures on the 20 Dec following five years out.
    mat = date(business_date.year + 5, 12, 20)
    return CDSIndex(
        instrument_id=f"CDS_{family}_S{series}_5Y",
        currency=ccy,
        index_family=family,
        series=series,
        maturity_date=mat,
        fixed_coupon=coupon,
        recovery_rate=rec,
    )


def crypto_spot(symbol: str) -> CryptoSpot:
    venue, _, _ = ref.CRYPTO[symbol]
    return CryptoSpot(instrument_id=f"CRYPTO_{symbol}", currency="USD", symbol=symbol, venue_name=venue)
