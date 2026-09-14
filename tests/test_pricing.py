"""Pricer tests: closed-form identities and QuantLib benchmarks."""
from datetime import date

import numpy as np
import pytest
import QuantLib as ql

from novera.domain import (
    BuySell,
    CashEquity,
    CDSIndex,
    ClearingType,
    CommodityFuture,
    CryptoSpot,
    DayCount,
    EquityIndexFuture,
    EquityOption,
    Frequency,
    FXForward,
    FXOption,
    GovernmentBond,
    InterestRateSwap,
    OptionType,
    SwapSide,
    Trade,
)
from novera.market_data import MarketSnapshot
from novera.pricing import PRICERS
from novera.pricing.black import black_price
from novera.pricing.rates import GOVT_SPREAD
from novera.pricing.valuation import value_portfolio
from novera.simulation import (
    TradeGeneratorConfig,
    build_counterparty_universe,
    build_global_macro_bank,
    generate_portfolio,
)
from novera.simulation.market_data import MarketSimConfig, generate_market_data

AS_OF = date(2026, 9, 11)
TENORS = np.array([1 / 12, 0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30])
USD_ZEROS = np.array([.039, .0388, .0385, .038, .0375, .0372, .0374, .038, .039, .040, .0405, .0405])
EUR_ZEROS = USD_ZEROS - 0.017


def _market() -> MarketSnapshot:
    values = {}
    for t, z in zip(["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y"], USD_ZEROS,
                    strict=True):
        values[f"IR:USD:{t}"] = float(z)
        values[f"IR:EUR:{t}"] = float(z - 0.017)
    values["FX:EURUSD"] = 1.10
    values["EQ:AAPL"] = 200.0
    values["EQIDX:SPX"] = 5000.0
    values["CDS:CDX.NA.IG"] = 60.0
    values["CRYPTO:BTC"] = 60000.0
    for t in ["1M", "3M", "6M", "1Y", "2Y"]:
        for m in [0.8, 0.9, 1.0, 1.1, 1.2]:
            values[f"VOL:AAPL:{t}:{m:.2f}"] = 0.25
            values[f"VOL:SPX:{t}:{m:.2f}"] = 0.18
            values[f"VOL:EURUSD:{t}:{m:.2f}"] = 0.08
    for t, p in [("1M", 75.0), ("3M", 74.0), ("6M", 73.0), ("1Y", 71.5), ("2Y", 69.0), ("3Y", 67.0)]:
        values[f"CMD:BRENT:{t}"] = p
    return MarketSnapshot(as_of=AS_OF, values=values)


@pytest.fixture(scope="module")
def market() -> MarketSnapshot:
    return _market()


def _ql_curve(zeros: np.ndarray, as_of: date) -> ql.YieldTermStructureHandle:
    """Discount curve with log-linear DF interpolation on ACT/365, matching ZeroCurve."""
    ql.Settings.instance().evaluationDate = ql.Date(as_of.day, as_of.month, as_of.year)
    dates = [ql.Date(as_of.day, as_of.month, as_of.year)]
    dfs = [1.0]
    for t, z in zip(TENORS, zeros, strict=True):
        dates.append(dates[0] + ql.Period(int(round(t * 365)), ql.Days))
        dfs.append(float(np.exp(-z * round(t * 365) / 365)))
    curve = ql.DiscountCurve(dates, dfs, ql.Actual365Fixed())
    curve.enableExtrapolation()
    return ql.YieldTermStructureHandle(curve)


def _listed(cpty: str) -> dict:
    return {"counterparty_id": cpty, "clearing": ClearingType.EXCHANGE}


def _bilateral() -> dict:
    return {"counterparty_id": "BANK_A", "clearing": ClearingType.BILATERAL, "netting_set_id": "NS"}


# --- bonds ---------------------------------------------------------------------------

def test_bond_matches_quantlib(market: MarketSnapshot) -> None:
    bond = GovernmentBond(instrument_id="B", currency="USD", issuer="UST", coupon_rate=0.04,
                          issue_date=date(2025, 11, 15), maturity_date=date(2035, 11, 15),
                          coupon_frequency=Frequency.SEMI_ANNUAL, day_count=DayCount.ACT_365)
    trade = Trade(trade_id="T", instrument=bond, direction=BuySell.BUY, quantity=10_000_000, trade_price=99,
                  trade_date=AS_OF, book_id="B", trader_id="T", **_listed("X"))
    res = PRICERS[bond.product_type](trade, market, AS_OF)

    handle = _ql_curve(USD_ZEROS + GOVT_SPREAD["USD"], AS_OF)
    sched = ql.Schedule(ql.Date(15, 11, 2025), ql.Date(15, 11, 2035), ql.Period(ql.Semiannual), ql.NullCalendar(),
                        ql.Unadjusted, ql.Unadjusted, ql.DateGeneration.Backward, False)
    qb = ql.FixedRateBond(0, 100.0, sched, [0.04], ql.Actual365Fixed())
    qb.setPricingEngine(ql.DiscountingBondEngine(handle))
    assert res.details["dirty_price"] == pytest.approx(qb.dirtyPrice(), rel=2e-4)
    assert res.details["accrued"] == pytest.approx(qb.accruedAmount(), rel=2e-2)
    assert res.pv_local == pytest.approx(res.details["dirty_price"] / 100 * 10_000_000)


def test_bond_sells_negative_and_matured_zero(market: MarketSnapshot) -> None:
    bond = GovernmentBond(instrument_id="B", currency="USD", issuer="UST", coupon_rate=0.04,
                          issue_date=date(2016, 1, 15), maturity_date=date(2026, 1, 15))
    t = Trade(trade_id="T", instrument=bond, direction=BuySell.SELL, quantity=1e6, trade_price=99,
              trade_date=date(2025, 1, 1), book_id="B", trader_id="T", **_listed("X"))
    r = PRICERS[bond.product_type](t, market, AS_OF)
    assert r.pv_local == 0.0 and r.note == "matured"


# --- swaps ---------------------------------------------------------------------------

def test_par_swap_has_zero_pv_and_matches_quantlib(market: MarketSnapshot) -> None:
    swap = InterestRateSwap(instrument_id="S", currency="USD", effective_date=AS_OF,
                            maturity_date=date(2031, 9, 11), fixed_rate=0.04, float_index="USD-SOFR",
                            fixed_frequency=Frequency.SEMI_ANNUAL, fixed_day_count=DayCount.THIRTY_360,
                            float_frequency=Frequency.QUARTERLY, float_day_count=DayCount.ACT_360)
    trade = Trade(trade_id="T", instrument=swap, direction=BuySell.BUY, swap_side=SwapSide.RECEIVE_FIXED,
                  quantity=100e6, trade_price=0.04, trade_date=AS_OF, book_id="B", trader_id="T", **_bilateral())
    res = PRICERS[swap.product_type](trade, market, AS_OF)

    handle = _ql_curve(USD_ZEROS, AS_OF)
    index = ql.IborIndex("SIM", ql.Period(3, ql.Months), 0, ql.USDCurrency(), ql.NullCalendar(), ql.Unadjusted,
                         False, ql.Actual360(), handle)
    fixed = ql.Schedule(ql.Date(11, 9, 2026), ql.Date(11, 9, 2031), ql.Period(ql.Semiannual), ql.NullCalendar(),
                        ql.Unadjusted, ql.Unadjusted, ql.DateGeneration.Backward, False)
    flt = ql.Schedule(ql.Date(11, 9, 2026), ql.Date(11, 9, 2031), ql.Period(ql.Quarterly), ql.NullCalendar(),
                      ql.Unadjusted, ql.Unadjusted, ql.DateGeneration.Backward, False)
    qs = ql.VanillaSwap(ql.VanillaSwap.Receiver, 100e6, fixed, 0.04, ql.Thirty360(ql.Thirty360.USA), flt, index,
                        0.0, ql.Actual360())
    qs.setPricingEngine(ql.DiscountingSwapEngine(handle))
    assert res.pv_local == pytest.approx(qs.NPV(), abs=2_000)  # 100m notional: within 2k
    assert res.details["par_rate"] == pytest.approx(qs.fairRate(), abs=2e-5)

    par = swap.model_copy(update={"fixed_rate": res.details["par_rate"]})
    par_trade = trade.model_copy(update={"instrument": par})
    assert abs(PRICERS[par.product_type](par_trade, market, AS_OF).pv_local) < 1.0

    payer = trade.model_copy(update={"swap_side": SwapSide.PAY_FIXED})
    assert PRICERS[swap.product_type](payer, market, AS_OF).pv_local == pytest.approx(-res.pv_local)


# --- FX ------------------------------------------------------------------------------

def test_fx_forward_cip_and_spot(market: MarketSnapshot) -> None:
    fwd = FXForward(instrument_id="F", currency="USD", pair="EUR/USD", settlement_date=date(2027, 9, 11),
                    forward_rate=1.10)
    t = Trade(trade_id="T", instrument=fwd, direction=BuySell.BUY, quantity=10e6, trade_price=1.10,
              trade_date=AS_OF, book_id="B", trader_id="T", **_bilateral())
    r = PRICERS[fwd.product_type](t, market, AS_OF)
    usd, eur = market.zero_curve("USD"), market.zero_curve("EUR")
    t1 = (date(2027, 9, 11) - AS_OF).days / 365
    expected_fwd = 1.10 * float(eur.df(t1)) / float(usd.df(t1))
    assert r.details["forward"] == pytest.approx(expected_fwd)
    assert expected_fwd > 1.10, "EUR rates below USD: EUR/USD forward above spot"
    assert r.pv_local == pytest.approx(10e6 * (expected_fwd - 1.10) * float(usd.df(t1)))
    settled = fwd.model_copy(update={"settlement_date": date(2026, 9, 1)})
    r2 = PRICERS[fwd.product_type](t.model_copy(update={"instrument": settled}), market, AS_OF)
    assert r2.pv_local == 0.0 and r2.note == "settled"


def test_fx_option_matches_quantlib_black_and_parity(market: MarketSnapshot) -> None:
    expiry = date(2027, 3, 11)
    call = FXOption(instrument_id="O", currency="USD", pair="EUR/USD", option_type=OptionType.CALL, strike=1.12,
                    expiry_date=expiry)
    put = call.model_copy(update={"option_type": OptionType.PUT})
    mk = {"direction": BuySell.BUY, "quantity": 10e6, "trade_price": 0.02, "trade_date": AS_OF, "book_id": "B",
          "trader_id": "T", **_bilateral()}
    rc = PRICERS[call.product_type](Trade(trade_id="C", instrument=call, **mk), market, AS_OF)
    rp = PRICERS[put.product_type](Trade(trade_id="P", instrument=put, **mk), market, AS_OF)
    t = (expiry - AS_OF).days / 365
    df = float(market.zero_curve("USD").df(t))
    fwd = rc.details["forward"]
    ql_call = ql.blackFormula(ql.Option.Call, 1.12, fwd, 0.08 * np.sqrt(t), df)
    assert rc.details["unit_price"] == pytest.approx(ql_call, rel=1e-10)
    # put-call parity: C - P = df (F - K)
    assert rc.details["unit_price"] - rp.details["unit_price"] == pytest.approx(df * (fwd - 1.12), abs=1e-12)
    sold = PRICERS[call.product_type](Trade(trade_id="S", instrument=call, **{**mk, "direction": BuySell.SELL}),
                                      market, AS_OF)
    assert sold.pv_local == pytest.approx(-rc.pv_local)
    assert rc.details["delta_fwd"] > 0 and rp.details["delta_fwd"] < 0 and rc.details["vega"] > 0


# --- equity --------------------------------------------------------------------------

def test_equity_cash_future_option(market: MarketSnapshot) -> None:
    eq = CashEquity(instrument_id="E", currency="USD", ticker="AAPL", exchange="NASDAQ")
    te = Trade(trade_id="E", instrument=eq, direction=BuySell.SELL, quantity=1000, trade_price=190,
               trade_date=AS_OF, book_id="B", trader_id="T", **_listed("X"))
    assert PRICERS[eq.product_type](te, market, AS_OF).pv_local == pytest.approx(-200_000)

    fut = EquityIndexFuture(instrument_id="F", currency="USD", index="SPX", exchange="CME",
                            expiry_date=date(2026, 12, 18), contract_multiplier=50)
    tf = Trade(trade_id="F", instrument=fut, direction=BuySell.BUY, quantity=10, trade_price=5000,
               trade_date=AS_OF, book_id="B", trader_id="T", **_listed("X"))
    rf = PRICERS[fut.product_type](tf, market, AS_OF)
    t = (date(2026, 12, 18) - AS_OF).days / 365
    assert rf.details["forward"] == pytest.approx(5000 / float(market.zero_curve("USD").df(t)))
    assert rf.pv_local == pytest.approx(10 * 50 * (rf.details["forward"] - 5000))

    opt = EquityOption(instrument_id="O", currency="USD", underlying="AAPL", option_type=OptionType.PUT, strike=190,
                       expiry_date=date(2027, 3, 19), contract_multiplier=100)
    to = Trade(trade_id="O", instrument=opt, direction=BuySell.BUY, quantity=20, trade_price=8,
               trade_date=AS_OF, book_id="B", trader_id="T", **_listed("X"))
    ro = PRICERS[opt.product_type](to, market, AS_OF)
    t = (date(2027, 3, 19) - AS_OF).days / 365
    df = float(market.zero_curve("USD").df(t))
    fwd = 200 / df
    assert ro.details["unit_price"] == pytest.approx(black_price(OptionType.PUT, fwd, 190, 0.25, t, df))
    assert ro.details["unit_price"] == pytest.approx(ql.blackFormula(ql.Option.Put, 190, fwd, 0.25 * np.sqrt(t), df),
                                                    rel=1e-10)
    assert ro.pv_local == pytest.approx(20 * 100 * ro.details["unit_price"])


# --- commodity, credit, crypto -------------------------------------------------------

def test_commodity_future_off_curve(market: MarketSnapshot) -> None:
    fut = CommodityFuture(instrument_id="F", currency="USD", commodity="BRENT", exchange="ICE",
                          expiry_date=date(2027, 3, 11), contract_size=1000)
    t = Trade(trade_id="F", instrument=fut, direction=BuySell.SELL, quantity=100, trade_price=70,
              trade_date=AS_OF, book_id="B", trader_id="T", **_listed("X"))
    r = PRICERS[fut.product_type](t, market, AS_OF)
    assert 73.0 < r.details["forward"] < 74.0  # 181 days: between the 3M (74) and 6M (73) nodes
    assert r.pv_local == pytest.approx(-100 * 1000 * (r.details["forward"] - 70))


def test_cds_index_matches_quantlib_within_tolerance(market: MarketSnapshot) -> None:
    cds = CDSIndex(instrument_id="C", currency="USD", index_family="CDX.NA.IG", series=45,
                   maturity_date=date(2031, 12, 20), fixed_coupon=0.01, recovery_rate=0.4)
    t = Trade(trade_id="C", instrument=cds, direction=BuySell.BUY, quantity=100e6, trade_price=0.006,
              trade_date=AS_OF, book_id="B", trader_id="T", **_bilateral())
    r = PRICERS[cds.product_type](t, market, AS_OF)
    # Spread 60bp below the 100bp coupon: protection buyer has negative PV.
    assert r.pv_local < 0
    handle = _ql_curve(USD_ZEROS, AS_OF)
    lam = 0.006 / 0.6
    hazard = ql.DefaultProbabilityTermStructureHandle(
        ql.FlatHazardRate(ql.Date(11, 9, 2026), ql.QuoteHandle(ql.SimpleQuote(lam)), ql.Actual365Fixed()))
    sched = ql.Schedule(ql.Date(20, 9, 2026), ql.Date(20, 12, 2031), ql.Period(ql.Quarterly), ql.NullCalendar(),
                        ql.Unadjusted, ql.Unadjusted, ql.DateGeneration.CDS, False)
    qc = ql.CreditDefaultSwap(ql.Protection.Buyer, 100e6, 0.01, sched, ql.Unadjusted, ql.Actual360())
    qc.setPricingEngine(ql.MidPointCdsEngine(hazard, 0.4, handle))
    assert r.pv_local == pytest.approx(qc.NPV(), rel=0.05)
    wider = market.with_values({"CDS:CDX.NA.IG": 120.0})
    assert PRICERS[cds.product_type](t, wider, AS_OF).pv_local > r.pv_local


def test_crypto_spot(market: MarketSnapshot) -> None:
    c = CryptoSpot(instrument_id="X", currency="USD", symbol="BTC", venue_name="COINBASE")
    t = Trade(trade_id="X", instrument=c, direction=BuySell.BUY, quantity=2.5, trade_price=50000,
              trade_date=AS_OF, book_id="B", trader_id="T", **_listed("X"))
    assert PRICERS[c.product_type](t, market, AS_OF).pv_local == pytest.approx(150_000)


# --- portfolio valuation on the simulated bank ----------------------------------------

def test_value_simulated_portfolio() -> None:
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=300, seed=5))
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=0.3, seed=5))
    out = value_portfolio(gen.snapshot, md.snapshot, org, "USD")
    table = out.table
    assert len(table) == len(gen.snapshot)
    live = table[table["status"] == "LIVE"]
    priced = live[live["error"].isna()]
    assert len(priced) >= 0.98 * len(live), out.errors
    assert priced["pv"].notna().all()
    assert (priced["fx_to_reporting"] > 0).all()
    assert set(priced["asset_class"]) == {"RATES", "FX", "EQUITY", "CREDIT", "COMMODITY", "DIGITAL_ASSET"}
    # Missing USD 7Y node must not stop USD pricing (curve interpolates across it).
    assert priced[priced["currency"] == "USD"]["pv"].notna().all()
    assert table[table["status"] == "INVALID"]["note"].eq("not live").all()


def test_generator_strikes_trades_at_fair_value() -> None:
    from novera.market_data.history import MarketHistory

    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=2.0, seed=9))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=200, seed=9,
                                                           market_history=hist))
    checked = 0
    for t in gen.snapshot.live_trades:
        if t.product_type.value not in ("INTEREST_RATE_SWAP", "FX_FORWARD", "GOVERNMENT_BOND"):
            continue
        snap = hist.snapshot_at(t.trade_date)
        r = PRICERS[t.product_type](t, snap, snap.as_of)
        if r.note:
            continue
        if t.product_type.value == "GOVERNMENT_BOND":
            assert abs(t.trade_price / r.details["dirty_price"] - 1) < 0.01, t.trade_id
        else:
            assert abs(r.pv_local) < 0.005 * t.quantity, (t.trade_id, r.pv_local / t.quantity)
        checked += 1
    assert checked > 50
    out = value_portfolio(gen.snapshot, md.snapshot, org, "USD")
    settled = out.table[out.table["note"] == "settled"]
    assert len(settled) <= 0.05 * len(out.table), "few forwards should already be settled"
