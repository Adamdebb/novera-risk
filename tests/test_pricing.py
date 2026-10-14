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
USD_ZEROS = np.array(
    [0.039, 0.0388, 0.0385, 0.038, 0.0375, 0.0372, 0.0374, 0.038, 0.039, 0.040, 0.0405, 0.0405]
)
EUR_ZEROS = USD_ZEROS - 0.017
TENOR_LABELS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y"]


def _market() -> MarketSnapshot:
    values = {}
    for t, z in zip(
        ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y"], USD_ZEROS, strict=True
    ):
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
    bond = GovernmentBond(
        instrument_id="B",
        currency="USD",
        issuer="UST",
        coupon_rate=0.04,
        issue_date=date(2025, 11, 15),
        maturity_date=date(2035, 11, 15),
        coupon_frequency=Frequency.SEMI_ANNUAL,
        day_count=DayCount.ACT_365,
    )
    trade = Trade(
        trade_id="T",
        instrument=bond,
        direction=BuySell.BUY,
        quantity=10_000_000,
        trade_price=99,
        trade_date=AS_OF,
        book_id="B",
        trader_id="T",
        **_listed("X"),
    )
    res = PRICERS[bond.product_type](trade, market, AS_OF)

    handle = _ql_curve(USD_ZEROS + GOVT_SPREAD["USD"], AS_OF)
    sched = ql.Schedule(
        ql.Date(15, 11, 2025),
        ql.Date(15, 11, 2035),
        ql.Period(ql.Semiannual),
        ql.NullCalendar(),
        ql.Unadjusted,
        ql.Unadjusted,
        ql.DateGeneration.Backward,
        False,
    )
    qb = ql.FixedRateBond(0, 100.0, sched, [0.04], ql.Actual365Fixed())
    qb.setPricingEngine(ql.DiscountingBondEngine(handle))
    assert res.details["dirty_price"] == pytest.approx(qb.dirtyPrice(), rel=2e-4)
    assert res.details["accrued"] == pytest.approx(qb.accruedAmount(), rel=2e-2)
    assert res.pv_local == pytest.approx(res.details["dirty_price"] / 100 * 10_000_000)


def test_bond_sells_negative_and_matured_zero(market: MarketSnapshot) -> None:
    bond = GovernmentBond(
        instrument_id="B",
        currency="USD",
        issuer="UST",
        coupon_rate=0.04,
        issue_date=date(2016, 1, 15),
        maturity_date=date(2026, 1, 15),
    )
    t = Trade(
        trade_id="T",
        instrument=bond,
        direction=BuySell.SELL,
        quantity=1e6,
        trade_price=99,
        trade_date=date(2025, 1, 1),
        book_id="B",
        trader_id="T",
        **_listed("X"),
    )
    r = PRICERS[bond.product_type](t, market, AS_OF)
    assert r.pv_local == 0.0 and r.note == "matured"


# --- swaps ---------------------------------------------------------------------------


def test_par_swap_has_zero_pv_and_matches_quantlib(market: MarketSnapshot) -> None:
    swap = InterestRateSwap(
        instrument_id="S",
        currency="USD",
        effective_date=AS_OF,
        maturity_date=date(2031, 9, 11),
        fixed_rate=0.04,
        float_index="USD-SOFR",
        fixed_frequency=Frequency.SEMI_ANNUAL,
        fixed_day_count=DayCount.THIRTY_360,
        float_frequency=Frequency.QUARTERLY,
        float_day_count=DayCount.ACT_360,
    )
    trade = Trade(
        trade_id="T",
        instrument=swap,
        direction=BuySell.BUY,
        swap_side=SwapSide.RECEIVE_FIXED,
        quantity=100e6,
        trade_price=0.04,
        trade_date=AS_OF,
        book_id="B",
        trader_id="T",
        **_bilateral(),
    )
    res = PRICERS[swap.product_type](trade, market, AS_OF)

    handle = _ql_curve(USD_ZEROS, AS_OF)
    index = ql.IborIndex(
        "SIM",
        ql.Period(3, ql.Months),
        0,
        ql.USDCurrency(),
        ql.NullCalendar(),
        ql.Unadjusted,
        False,
        ql.Actual360(),
        handle,
    )
    fixed = ql.Schedule(
        ql.Date(11, 9, 2026),
        ql.Date(11, 9, 2031),
        ql.Period(ql.Semiannual),
        ql.NullCalendar(),
        ql.Unadjusted,
        ql.Unadjusted,
        ql.DateGeneration.Backward,
        False,
    )
    flt = ql.Schedule(
        ql.Date(11, 9, 2026),
        ql.Date(11, 9, 2031),
        ql.Period(ql.Quarterly),
        ql.NullCalendar(),
        ql.Unadjusted,
        ql.Unadjusted,
        ql.DateGeneration.Backward,
        False,
    )
    qs = ql.VanillaSwap(
        ql.VanillaSwap.Receiver,
        100e6,
        fixed,
        0.04,
        ql.Thirty360(ql.Thirty360.USA),
        flt,
        index,
        0.0,
        ql.Actual360(),
    )
    qs.setPricingEngine(ql.DiscountingSwapEngine(handle))
    assert res.pv_local == pytest.approx(qs.NPV(), abs=2_000)  # 100m notional: within 2k
    assert res.details["par_rate"] == pytest.approx(qs.fairRate(), abs=2e-5)

    par = swap.model_copy(update={"fixed_rate": res.details["par_rate"]})
    par_trade = trade.model_copy(update={"instrument": par})
    assert abs(PRICERS[par.product_type](par_trade, market, AS_OF).pv_local) < 1.0

    payer = trade.model_copy(update={"swap_side": SwapSide.PAY_FIXED})
    assert PRICERS[swap.product_type](payer, market, AS_OF).pv_local == pytest.approx(-res.pv_local)


def test_eur_swap_dual_curve_matches_quantlib(market: MarketSnapshot) -> None:
    """PR-002 dual curve. EUR swaps reference EURIBOR-6M: the engine discounts on the IR:EUR
    (ESTR) curve and projects off ESTR plus the IRBASIS:EUR:6M spread. Benchmarked against
    QuantLib with the same two curves. Without the basis in the snapshot the swap falls back to
    one curve, flags it in ``tenor_basis_applied``, and misses by the gap the upgrade closed."""
    swap = InterestRateSwap(
        instrument_id="S_EUR",
        currency="EUR",
        effective_date=date(2024, 9, 11),
        maturity_date=date(2031, 9, 11),
        fixed_rate=0.03,  # par is ~2.1%: a seasoned receiver, well in the money
        float_index="EUR-EURIBOR-6M",
        fixed_frequency=Frequency.ANNUAL,
        fixed_day_count=DayCount.THIRTY_360,
        float_frequency=Frequency.SEMI_ANNUAL,
        float_day_count=DayCount.ACT_360,
    )
    trade = Trade(
        trade_id="T_EUR",
        instrument=swap,
        direction=BuySell.BUY,
        swap_side=SwapSide.RECEIVE_FIXED,
        quantity=100e6,
        trade_price=0.03,
        trade_date=date(2024, 9, 11),
        book_id="B",
        trader_id="T",
        **_bilateral(),
    )
    basis = 0.0015  # EURIBOR-6M over ESTR, flat
    with_basis = market.with_values({f"IRBASIS:EUR:6M:{t}": basis for t in ("1Y", "2Y", "5Y", "10Y", "30Y")})
    dual = PRICERS[swap.product_type](trade, with_basis, AS_OF)
    single = PRICERS[swap.product_type](trade, market, AS_OF)
    assert dual.details["tenor_basis_applied"] == 1.0
    assert single.details["tenor_basis_applied"] == 0.0

    discounting = _ql_curve(EUR_ZEROS, AS_OF)
    projection = _ql_curve(EUR_ZEROS + basis, AS_OF)
    index = ql.IborIndex(
        "SIM6M",
        ql.Period(6, ql.Months),
        0,
        ql.EURCurrency(),
        ql.NullCalendar(),
        ql.Unadjusted,
        False,
        ql.Actual360(),
        projection,
    )
    fixed = ql.Schedule(
        ql.Date(11, 9, 2024),
        ql.Date(11, 9, 2031),
        ql.Period(ql.Annual),
        ql.NullCalendar(),
        ql.Unadjusted,
        ql.Unadjusted,
        ql.DateGeneration.Backward,
        False,
    )
    flt = ql.Schedule(
        ql.Date(11, 9, 2024),
        ql.Date(11, 9, 2031),
        ql.Period(ql.Semiannual),
        ql.NullCalendar(),
        ql.Unadjusted,
        ql.Unadjusted,
        ql.DateGeneration.Backward,
        False,
    )
    qs = ql.VanillaSwap(
        ql.VanillaSwap.Receiver,
        100e6,
        fixed,
        0.03,
        ql.Thirty360(ql.Thirty360.USA),
        flt,
        index,
        0.0,
        ql.Actual360(),
    )
    # QuantLib needs the fixing of the period already running (Mar to Sep 2026). The engine
    # has no stored fixings and accrues the remaining half period at the curve forward
    # (PR-002), a cashflow of N (1/P(T) - 1); hand QuantLib the fixing that pays the same.
    period_start, period_end = ql.Date(11, 3, 2026), ql.Date(11, 9, 2026)
    tau_full = ql.Actual360().yearFraction(period_start, period_end)
    index.addFixing(period_start, (1.0 / projection.discount(period_end) - 1.0) / tau_full)
    qs.setPricingEngine(ql.DiscountingSwapEngine(discounting))
    dual_ql = qs.NPV()

    assert dual.pv_local == pytest.approx(dual_ql, abs=2_000), "engine must match QuantLib dual-curve"
    assert dual.details["par_rate"] == pytest.approx(qs.fairRate(), abs=2e-5)
    # IR:EUR is ESTR, so dropping the basis lowers every EURIBOR forward by it: the receiver
    # gains about notional x basis x annuity (the legs' day counts differ by ~1.4%).
    gap = single.pv_local - dual.pv_local
    print(f"dual-curve PV {dual.pv_local:,.0f}; single-curve fallback {single.pv_local:,.0f}; gap {gap:,.0f}")
    assert gap == pytest.approx(100e6 * basis * dual.details["annuity"], rel=0.03)


def test_tenor_basis_moves_only_term_rate_indices(market: MarketSnapshot) -> None:
    """The projection curve is the discount curve plus the index's basis, interpolated in
    tenor; an overnight index (SOFR) and an index without basis nodes project off the
    discount curve itself."""
    nodes = {"1Y": 0.0010, "2Y": 0.0012, "5Y": 0.0016, "10Y": 0.0020, "30Y": 0.0024}
    snap = market.with_values({f"IRBASIS:EUR:6M:{t}": v for t, v in nodes.items()})
    disc = snap.zero_curve("EUR")
    proj = snap.projection_curve("EUR-EURIBOR-6M", "EUR")
    spread = proj.zeros - disc.zeros
    assert spread[list(disc.tenors).index(5.0)] == pytest.approx(0.0016)
    assert spread[list(disc.tenors).index(7.0)] == pytest.approx(0.0016 + 0.4 * 0.0004)
    assert spread[0] == pytest.approx(0.0010)  # flat below the first basis node
    assert snap.projection_curve("USD-SOFR", "USD") is snap.zero_curve("USD")
    assert snap.projection_curve("EUR-EURIBOR-3M", "EUR") is disc  # no 3M nodes in this snapshot
    assert not snap.has_tenor_basis("EUR-EURIBOR-3M") and snap.has_tenor_basis("EUR-EURIBOR-6M")


def _swap_trade(swap: InterestRateSwap, side: SwapSide = SwapSide.RECEIVE_FIXED) -> Trade:
    return Trade(
        trade_id="T_" + swap.instrument_id,
        instrument=swap,
        direction=BuySell.BUY,
        swap_side=side,
        quantity=100e6,
        trade_price=swap.fixed_rate,
        trade_date=swap.effective_date,
        book_id="B",
        trader_id="T",
        **_bilateral(),
    )


def test_generated_swaps_follow_market_conventions() -> None:
    """Decision 34.2: each floating index carries its market conventions, and a TIIE swap runs a
    whole number of 28-day periods (13 a year)."""
    from novera.simulation import instruments as inst

    expected = {
        "USD": (Frequency.ANNUAL, DayCount.ACT_360, Frequency.ANNUAL, DayCount.ACT_360),
        "EUR": (Frequency.ANNUAL, DayCount.THIRTY_360, Frequency.SEMI_ANNUAL, DayCount.ACT_360),
        "GBP": (Frequency.ANNUAL, DayCount.ACT_365, Frequency.ANNUAL, DayCount.ACT_365),
        "MXN": (Frequency.EVERY_28_DAYS, DayCount.ACT_360, Frequency.EVERY_28_DAYS, DayCount.ACT_360),
        "BRL": (Frequency.ZERO_COUPON, DayCount.BUS_252, Frequency.ZERO_COUPON, DayCount.BUS_252),
        "ZAR": (Frequency.QUARTERLY, DayCount.ACT_365, Frequency.QUARTERLY, DayCount.ACT_365),
    }
    for ccy, conv in expected.items():
        s = inst.swap(ccy, "5Y", AS_OF, 0.05)
        assert (s.fixed_frequency, s.fixed_day_count, s.float_frequency, s.float_day_count) == conv, ccy
    mxn = inst.swap("MXN", "10Y", AS_OF, 0.08)
    assert (mxn.maturity_date - AS_OF).days == 28 * 130
    assert inst.swaption("EUR", date(2027, 9, 13), "5Y", 0.025, True).fixed_frequency is Frequency.ANNUAL


def test_running_period_accrues_at_the_short_rate(market: MarketSnapshot) -> None:
    """Decision 34.4 fallback: with no stored fixing for the period, the floating period already
    running pays the elapsed part at today's shortest projection rate plus the projected remainder. Benchmarked
    against QuantLib given the fixing that pays the same coupon, so both legs, the schedule and
    the discounting are checked on a seasoned swap."""
    swap = InterestRateSwap(
        instrument_id="S_RUN",
        currency="USD",
        effective_date=date(2024, 7, 11),
        maturity_date=date(2029, 7, 11),
        fixed_rate=0.04,
        float_index="USD-SOFR",
        fixed_frequency=Frequency.QUARTERLY,
        fixed_day_count=DayCount.ACT_360,
        float_frequency=Frequency.QUARTERLY,
        float_day_count=DayCount.ACT_360,
    )
    res = PRICERS[swap.product_type](_swap_trade(swap), market, AS_OF)
    handle = _ql_curve(USD_ZEROS, AS_OF)
    index = ql.IborIndex(
        "SIM3M", ql.Period(3, ql.Months), 0, ql.USDCurrency(), ql.NullCalendar(), ql.Unadjusted, False,
        ql.Actual360(), handle,
    )  # fmt: skip
    sched = ql.Schedule(
        ql.Date(11, 7, 2024), ql.Date(11, 7, 2029), ql.Period(ql.Quarterly), ql.NullCalendar(),
        ql.Unadjusted, ql.Unadjusted, ql.DateGeneration.Backward, False,
    )  # fmt: skip
    qs = ql.VanillaSwap(
        ql.VanillaSwap.Receiver, 100e6, sched, 0.04, ql.Actual360(), sched, index, 0.0, ql.Actual360()
    )
    # Running period 11 Jul to 11 Oct 2026: 62 days elapsed at the short rate, 30 projected.
    start, end = date(2026, 7, 11), date(2026, 10, 11)
    short = float(market.zero_curve("USD").zero(0.0))
    coupon = short * (AS_OF - start).days / 360 + (1 / float(handle.discount(ql.Date(11, 10, 2026))) - 1)
    index.addFixing(ql.Date(11, 7, 2026), coupon / ((end - start).days / 360))
    qs.setPricingEngine(ql.DiscountingSwapEngine(handle))
    assert res.pv_local == pytest.approx(qs.NPV(), abs=2_000)
    # Without the elapsed accrual the receiver would look richer by about N x short x 62/360.
    assert 100e6 * short * 62 / 360 > 500_000


def test_stored_fixings_remove_false_short_rate_risk(market: MarketSnapshot) -> None:
    """Decision 34.4: interest already fixed must not move with today's curve. A seasoned SONIA
    swap (annual periods, 290 days into the running one) bumped at the 1M node: with the stored
    realised rate only the 34 days still to run respond; with the fallback estimate the whole
    elapsed accrual does. A EURIBOR-6M swap's running coupon is the stored fixing exactly."""
    from novera.domain import FloatingFixing

    mk = market.with_values({f"IR:GBP:{t}": float(z) for t, z in zip(TENOR_LABELS, USD_ZEROS, strict=True)})
    gbp = InterestRateSwap(
        instrument_id="SONIA",
        currency="GBP",
        effective_date=date(2025, 11, 25),
        maturity_date=date(2030, 11, 25),
        fixed_rate=0.04,
        float_index="GBP-SONIA",
        fixed_frequency=Frequency.ANNUAL,
        fixed_day_count=DayCount.ACT_365,
        float_frequency=Frequency.ANNUAL,
        float_day_count=DayCount.ACT_365,
    )
    fixed = gbp.model_copy(
        update={"fixing": FloatingFixing(period_start=date(2025, 11, 25), rate=0.0395, through=AS_OF)}
    )
    bumped = mk.with_values({"IR:GBP:1M": mk.values["IR:GBP:1M"] + 1e-4})

    def dv01_1m(s: InterestRateSwap) -> float:
        t = _swap_trade(s)
        return (
            PRICERS[s.product_type](t, bumped, AS_OF).pv_local
            - PRICERS[s.product_type](t, mk, AS_OF).pv_local
        )

    elapsed = (AS_OF - date(2025, 11, 25)).days / 365
    assert abs(dv01_1m(gbp)) > 0.8 * 100e6 * 1e-4 * elapsed  # the fallback moves the whole accrual
    assert abs(dv01_1m(fixed)) < 0.15 * abs(dv01_1m(gbp))
    # The realised part compounds the stored rate; the rest is projected off today's curve.
    res = PRICERS[gbp.product_type](_swap_trade(fixed), mk, AS_OF)
    flt = next(f for f in res.cashflows if f.kind == "FLOAT")
    proj = 1 / float(mk.zero_curve("GBP").df((date(2026, 11, 25) - AS_OF).days / 365))
    assert -flt.amount == pytest.approx(100e6 * (np.exp(0.0395 * elapsed) * proj - 1), rel=1e-9)

    eur = InterestRateSwap(
        instrument_id="E6M",
        currency="EUR",
        effective_date=date(2025, 6, 11),
        maturity_date=date(2030, 6, 11),
        fixed_rate=0.025,
        float_index="EUR-EURIBOR-6M",
        fixed_frequency=Frequency.ANNUAL,
        fixed_day_count=DayCount.THIRTY_360,
        float_frequency=Frequency.SEMI_ANNUAL,
        float_day_count=DayCount.ACT_360,
        fixing=FloatingFixing(period_start=date(2026, 6, 11), rate=0.0231, through=date(2026, 6, 11)),
    )
    r = PRICERS[eur.product_type](_swap_trade(eur), market, AS_OF)
    first = min((f for f in r.cashflows if f.kind == "FLOAT"), key=lambda f: f.pay_date)
    assert first.pay_date == date(2026, 12, 11)
    assert -first.amount == pytest.approx(100e6 * 0.0231 * 183 / 360, rel=1e-12)


def test_generator_attaches_the_running_fixing() -> None:
    """The generated book carries, for every swap mid-period on the business date, the fixing a
    trade system would hold: a term index's rate read off the projection curve on the period
    start, an overnight index's realised rate through the business date."""
    from novera.simulation.fixings import running_period

    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=3.0))
    from novera.market_data.history import MarketHistory

    hist = MarketHistory.from_long(md.history)
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(AS_OF, 400, 7, False, hist))
    swaps = [t for t in gen.snapshot.trades if isinstance(t.instrument, InterestRateSwap)]
    assert swaps
    for t in swaps:
        ins = t.instrument
        period = running_period(ins, AS_OF)
        if period is None:
            assert ins.fixing is None, t.trade_id
            continue
        assert ins.fixing is not None and ins.fixing.period_start == period[0], t.trade_id
        if ins.float_index.startswith(("EUR", "MXN", "ZAR")):
            assert ins.fixing.through == period[0]
        else:
            assert ins.fixing.through == AS_OF
        assert -0.01 < ins.fixing.rate < 0.2


def test_brl_cdi_zero_coupon_swap() -> None:
    """DI pre (decision 34.2): one period, fixed leg N((1+K)^tau - 1) with tau in BUS/252, CDI
    leg the compounded overnight rate N(P(start)/P(T) - 1). A new swap at the par rate is worth
    nothing; a seasoned one adds the CDI accrued since inception at today's short rate."""
    from novera.pricing.schedule import year_fraction

    zeros = {t: 0.11 + 0.002 * i for i, t in enumerate(["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "10Y"])}
    mk = MarketSnapshot(as_of=AS_OF, values={f"IR:BRL:{t}": z for t, z in zeros.items()})
    curve = mk.zero_curve("BRL")
    mat = date(2028, 9, 11)
    tau = year_fraction(AS_OF, mat, DayCount.BUS_252)
    df = float(curve.df((mat - AS_OF).days / 365))
    par = df ** (-1 / tau) - 1
    swap = InterestRateSwap(
        instrument_id="DI",
        currency="BRL",
        effective_date=AS_OF,
        maturity_date=mat,
        fixed_rate=par,
        float_index="BRL-CDI",
        fixed_frequency=Frequency.ZERO_COUPON,
        fixed_day_count=DayCount.BUS_252,
        float_frequency=Frequency.ZERO_COUPON,
        float_day_count=DayCount.BUS_252,
    )
    res = PRICERS[swap.product_type](_swap_trade(swap), mk, AS_OF)
    assert abs(res.pv_local) < 1.0 and res.details["par_rate"] == pytest.approx(par, abs=1e-12)
    assert len(res.cashflows) == 2
    seasoned = swap.model_copy(update={"effective_date": date(2025, 9, 11)})
    r2 = PRICERS[swap.product_type](_swap_trade(seasoned), mk, AS_OF)
    short = float(curve.zero(0.0))
    tau_all = year_fraction(date(2025, 9, 11), mat, DayCount.BUS_252)
    fixed = 100e6 * ((1 + par) ** tau_all - 1) * df
    flt = 100e6 * (np.exp(short * 365 / 365) / df - 1) * df
    assert r2.pv_local == pytest.approx(fixed - flt, rel=1e-9)


def test_mxn_tiie_swap_is_dual_curve_on_28_day_periods(market: MarketSnapshot) -> None:
    """Decision 34.1: a TIIE-28D swap discounts on the IR:MXN (F-TIIE) curve and projects TIIE
    off it plus the 28D basis, on 28-day periods ACT/360. Benchmarked against QuantLib."""
    zeros = USD_ZEROS + 0.045
    values = {f"IR:MXN:{t}": float(z) for t, z in zip(TENOR_LABELS, zeros, strict=True)}
    basis = 0.0028
    values.update({f"IRBASIS:MXN:28D:{t}": basis for t in ("1Y", "2Y", "5Y", "10Y", "30Y")})
    mk = MarketSnapshot(as_of=AS_OF, values=values)
    from novera.simulation import instruments as inst

    swap = inst.swap("MXN", "5Y", AS_OF, 0.09)
    res = PRICERS[swap.product_type](_swap_trade(swap), mk, AS_OF)
    assert res.details["tenor_basis_applied"] == 1.0
    disc, proj = _ql_curve(zeros, AS_OF), _ql_curve(zeros + basis, AS_OF)
    index = ql.IborIndex(
        "TIIE", ql.Period(28, ql.Days), 0, ql.MXNCurrency(), ql.NullCalendar(), ql.Unadjusted, False,
        ql.Actual360(), proj,
    )  # fmt: skip
    m = swap.maturity_date
    sched = ql.Schedule(
        ql.Date(11, 9, 2026), ql.Date(m.day, m.month, m.year), ql.Period(28, ql.Days), ql.NullCalendar(),
        ql.Unadjusted, ql.Unadjusted, ql.DateGeneration.Backward, False,
    )  # fmt: skip
    qs = ql.VanillaSwap(
        ql.VanillaSwap.Receiver, 100e6, sched, 0.09, ql.Actual360(), sched, index, 0.0, ql.Actual360()
    )
    qs.setPricingEngine(ql.DiscountingSwapEngine(disc))
    assert len(res.cashflows) == 2 * 65
    assert res.pv_local == pytest.approx(qs.NPV(), abs=2_000)
    assert res.details["par_rate"] == pytest.approx(qs.fairRate(), abs=2e-5)


# --- FX ------------------------------------------------------------------------------


def test_fx_forward_cip_and_spot(market: MarketSnapshot) -> None:
    fwd = FXForward(
        instrument_id="F",
        currency="USD",
        pair="EUR/USD",
        settlement_date=date(2027, 9, 11),
        forward_rate=1.10,
    )
    t = Trade(
        trade_id="T",
        instrument=fwd,
        direction=BuySell.BUY,
        quantity=10e6,
        trade_price=1.10,
        trade_date=AS_OF,
        book_id="B",
        trader_id="T",
        **_bilateral(),
    )
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
    call = FXOption(
        instrument_id="O",
        currency="USD",
        pair="EUR/USD",
        option_type=OptionType.CALL,
        strike=1.12,
        expiry_date=expiry,
    )
    put = call.model_copy(update={"option_type": OptionType.PUT})
    mk = {
        "direction": BuySell.BUY,
        "quantity": 10e6,
        "trade_price": 0.02,
        "trade_date": AS_OF,
        "book_id": "B",
        "trader_id": "T",
        **_bilateral(),
    }
    rc = PRICERS[call.product_type](Trade(trade_id="C", instrument=call, **mk), market, AS_OF)
    rp = PRICERS[put.product_type](Trade(trade_id="P", instrument=put, **mk), market, AS_OF)
    t = (expiry - AS_OF).days / 365
    df = float(market.zero_curve("USD").df(t))
    fwd = rc.details["forward"]
    ql_call = ql.blackFormula(ql.Option.Call, 1.12, fwd, 0.08 * np.sqrt(t), df)
    assert rc.details["unit_price"] == pytest.approx(ql_call, rel=1e-10)
    # put-call parity: C - P = df (F - K)
    assert rc.details["unit_price"] - rp.details["unit_price"] == pytest.approx(df * (fwd - 1.12), abs=1e-12)
    sold = PRICERS[call.product_type](
        Trade(trade_id="S", instrument=call, **{**mk, "direction": BuySell.SELL}), market, AS_OF
    )
    assert sold.pv_local == pytest.approx(-rc.pv_local)
    assert rc.details["delta_fwd"] > 0 and rp.details["delta_fwd"] < 0 and rc.details["vega"] > 0


# --- equity --------------------------------------------------------------------------


def test_equity_cash_future_option(market: MarketSnapshot) -> None:
    eq = CashEquity(instrument_id="E", currency="USD", ticker="AAPL", exchange="NASDAQ")
    te = Trade(
        trade_id="E",
        instrument=eq,
        direction=BuySell.SELL,
        quantity=1000,
        trade_price=190,
        trade_date=AS_OF,
        book_id="B",
        trader_id="T",
        **_listed("X"),
    )
    assert PRICERS[eq.product_type](te, market, AS_OF).pv_local == pytest.approx(-200_000)

    fut = EquityIndexFuture(
        instrument_id="F",
        currency="USD",
        index="SPX",
        exchange="CME",
        expiry_date=date(2026, 12, 18),
        contract_multiplier=50,
    )
    tf = Trade(
        trade_id="F",
        instrument=fut,
        direction=BuySell.BUY,
        quantity=10,
        trade_price=5000,
        trade_date=AS_OF,
        book_id="B",
        trader_id="T",
        **_listed("X"),
    )
    rf = PRICERS[fut.product_type](tf, market, AS_OF)
    t = (date(2026, 12, 18) - AS_OF).days / 365
    assert rf.details["forward"] == pytest.approx(5000 / float(market.zero_curve("USD").df(t)))
    assert rf.pv_local == pytest.approx(10 * 50 * (rf.details["forward"] - 5000))

    opt = EquityOption(
        instrument_id="O",
        currency="USD",
        underlying="AAPL",
        option_type=OptionType.PUT,
        strike=190,
        expiry_date=date(2027, 3, 19),
        contract_multiplier=100,
    )
    to = Trade(
        trade_id="O",
        instrument=opt,
        direction=BuySell.BUY,
        quantity=20,
        trade_price=8,
        trade_date=AS_OF,
        book_id="B",
        trader_id="T",
        **_listed("X"),
    )
    ro = PRICERS[opt.product_type](to, market, AS_OF)
    t = (date(2027, 3, 19) - AS_OF).days / 365
    df = float(market.zero_curve("USD").df(t))
    fwd = 200 / df
    assert ro.details["unit_price"] == pytest.approx(black_price(OptionType.PUT, fwd, 190, 0.25, t, df))
    assert ro.details["unit_price"] == pytest.approx(
        ql.blackFormula(ql.Option.Put, 190, fwd, 0.25 * np.sqrt(t), df), rel=1e-10
    )
    assert ro.pv_local == pytest.approx(20 * 100 * ro.details["unit_price"])


# --- commodity, credit, crypto -------------------------------------------------------


def test_commodity_future_off_curve(market: MarketSnapshot) -> None:
    fut = CommodityFuture(
        instrument_id="F",
        currency="USD",
        commodity="BRENT",
        exchange="ICE",
        expiry_date=date(2027, 3, 11),
        contract_size=1000,
    )
    t = Trade(
        trade_id="F",
        instrument=fut,
        direction=BuySell.SELL,
        quantity=100,
        trade_price=70,
        trade_date=AS_OF,
        book_id="B",
        trader_id="T",
        **_listed("X"),
    )
    r = PRICERS[fut.product_type](t, market, AS_OF)
    assert 73.0 < r.details["forward"] < 74.0  # 181 days: between the 3M (74) and 6M (73) nodes
    assert r.pv_local == pytest.approx(-100 * 1000 * (r.details["forward"] - 70))


def test_cds_index_matches_quantlib_within_tolerance(market: MarketSnapshot) -> None:
    cds = CDSIndex(
        instrument_id="C",
        currency="USD",
        index_family="CDX.NA.IG",
        series=45,
        maturity_date=date(2031, 12, 20),
        fixed_coupon=0.01,
        recovery_rate=0.4,
    )
    t = Trade(
        trade_id="C",
        instrument=cds,
        direction=BuySell.BUY,
        quantity=100e6,
        trade_price=0.006,
        trade_date=AS_OF,
        book_id="B",
        trader_id="T",
        **_bilateral(),
    )
    r = PRICERS[cds.product_type](t, market, AS_OF)
    # Spread 60bp below the 100bp coupon: protection buyer has negative PV.
    assert r.pv_local < 0
    handle = _ql_curve(USD_ZEROS, AS_OF)
    lam = 0.006 / 0.6
    hazard = ql.DefaultProbabilityTermStructureHandle(
        ql.FlatHazardRate(ql.Date(11, 9, 2026), ql.QuoteHandle(ql.SimpleQuote(lam)), ql.Actual365Fixed())
    )
    sched = ql.Schedule(
        ql.Date(20, 9, 2026),
        ql.Date(20, 12, 2031),
        ql.Period(ql.Quarterly),
        ql.NullCalendar(),
        ql.Unadjusted,
        ql.Unadjusted,
        ql.DateGeneration.CDS,
        False,
    )
    qc = ql.CreditDefaultSwap(ql.Protection.Buyer, 100e6, 0.01, sched, ql.Unadjusted, ql.Actual360())
    qc.setPricingEngine(ql.MidPointCdsEngine(hazard, 0.4, handle))
    assert r.pv_local == pytest.approx(qc.NPV(), rel=0.05)
    wider = market.with_values({"CDS:CDX.NA.IG": 120.0})
    assert PRICERS[cds.product_type](t, wider, AS_OF).pv_local > r.pv_local


def test_crypto_spot(market: MarketSnapshot) -> None:
    c = CryptoSpot(instrument_id="X", currency="USD", symbol="BTC", venue_name="COINBASE")
    t = Trade(
        trade_id="X",
        instrument=c,
        direction=BuySell.BUY,
        quantity=2.5,
        trade_price=50000,
        trade_date=AS_OF,
        book_id="B",
        trader_id="T",
        **_listed("X"),
    )
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
    gen = generate_portfolio(
        org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=200, seed=9, market_history=hist)
    )
    checked = 0
    off_market = {tid for i in gen.injections if i.name == "usd_10y_concentration" for tid in i.trade_ids}
    for t in gen.snapshot.live_trades:
        if t.product_type.value not in ("INTEREST_RATE_SWAP", "FX_FORWARD", "GOVERNMENT_BOND"):
            continue
        if t.trade_id in off_market:
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
