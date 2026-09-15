"""Phase 6 instrument breadth: pricers benchmarked to QuantLib or closed form, generator
coverage, fund look-through and market-data proxies."""

from datetime import date

import numpy as np
import pytest
import QuantLib as ql
from dateutil.relativedelta import relativedelta

from novera.domain import ETF as ETFModel  # noqa: N811
from novera.domain import (
    BarrierType,
    BasketLeg,
    BuySell,
    CDSSingleName,
    ClearingType,
    CommodityOption,
    EquityExotic,
    ExoticStyle,
    InterestRateFuture,
    MutualFund,
    OptionType,
    Repo,
    Swaption,
    Trade,
)
from novera.market_data import MarketSnapshot
from novera.market_data.proxies import apply_proxies, apply_stored_proxies
from novera.pricing import PRICERS
from novera.pricing.black import black_price
from novera.pricing.breadth import forward_swap
from novera.pricing.exotic_formulas import bachelier_price, barrier_price, cash_or_nothing
from novera.pricing.valuation import value_portfolio
from novera.risk import DependencyIndex, compute_sensitivities, factor_prefixes, look_through
from novera.risk.revaluation import Portfolio
from novera.simulation import (
    BANK_TEMPLATE,
    FUND_TEMPLATE,
    TradeGeneratorConfig,
    build_counterparty_universe,
    build_fund_counterparties,
    build_global_macro_bank,
    build_multi_strategy_fund,
    generate_portfolio,
)
from novera.simulation import instruments as inst
from novera.simulation.market_data import MarketSimConfig, build_risk_factor_universe, generate_market_data
from tests.test_pricing import USD_ZEROS, _bilateral, _listed, _market, _ql_curve

AS_OF = date(2026, 9, 11)


@pytest.fixture(scope="module")
def market() -> MarketSnapshot:
    base = _market()
    values = dict(base.values)
    for e in ["3M", "1Y", "2Y", "5Y"]:
        for t in ["2Y", "5Y", "10Y", "30Y"]:
            values[f"SWVOL:USD:{e}:{t}"] = 90.0
    values["CDS:FORD"] = 150.0
    for t in ["1M", "3M", "6M", "1Y", "2Y"]:
        for m in [0.8, 0.9, 1.0, 1.1, 1.2]:
            values[f"VOL:BRENT:{t}:{m:.2f}"] = 0.35
    values["EQ:MSFT"] = 400.0
    return MarketSnapshot(as_of=AS_OF, values=values)


# --- closed-form formulas against QuantLib --------------------------------------------------


def test_barrier_and_digital_formulas_match_quantlib() -> None:
    today = ql.Date(11, 9, 2026)
    ql.Settings.instance().evaluationDate = today
    s, r, vol, t = 100.0, 0.04, 0.25, 1.0
    proc = ql.BlackScholesMertonProcess(
        ql.QuoteHandle(ql.SimpleQuote(s)),
        ql.YieldTermStructureHandle(ql.FlatForward(today, 0.0, ql.Actual365Fixed())),
        ql.YieldTermStructureHandle(ql.FlatForward(today, r, ql.Actual365Fixed())),
        ql.BlackVolTermStructureHandle(
            ql.BlackConstantVol(today, ql.NullCalendar(), vol, ql.Actual365Fixed())
        ),
    )
    exp = ql.EuropeanExercise(today + ql.Period(365, ql.Days))
    kinds = [(OptionType.CALL, ql.Option.Call), (OptionType.PUT, ql.Option.Put)]
    barriers = [
        (BarrierType.DOWN_AND_OUT, ql.Barrier.DownOut, [80.0, 95.0]),
        (BarrierType.DOWN_AND_IN, ql.Barrier.DownIn, [80.0, 95.0]),
        (BarrierType.UP_AND_OUT, ql.Barrier.UpOut, [105.0, 130.0]),
        (BarrierType.UP_AND_IN, ql.Barrier.UpIn, [105.0, 130.0]),
    ]
    for bt, qbt, hs in barriers:
        for h in hs:
            for kind, qk in kinds:
                for k in (90.0, 100.0, 110.0):
                    for rebate in (0.0, 2.0):
                        o = ql.BarrierOption(qbt, h, rebate, ql.PlainVanillaPayoff(qk, k), exp)
                        o.setPricingEngine(ql.AnalyticBarrierEngine(proc))
                        mine = barrier_price(kind, bt, s, k, h, r, vol, t, rebate)
                        assert mine == pytest.approx(o.NPV(), abs=1e-8), (bt, h, kind, k, rebate)
    for kind, qk in kinds:
        o = ql.VanillaOption(ql.CashOrNothingPayoff(qk, 100.0, 10.0), exp)
        o.setPricingEngine(ql.AnalyticEuropeanEngine(proc))
        assert cash_or_nothing(kind, s, 100.0, r, vol, t, 10.0) == pytest.approx(o.NPV(), abs=1e-10)
    # In/out parity: knock-in + knock-out = vanilla when there is no rebate.
    fwd, df = s * np.exp(r * t), np.exp(-r * t)
    vanilla = black_price(OptionType.CALL, fwd, 100.0, vol, t, df)
    di = barrier_price(OptionType.CALL, BarrierType.DOWN_AND_IN, s, 100.0, 90.0, r, vol, t)
    do = barrier_price(OptionType.CALL, BarrierType.DOWN_AND_OUT, s, 100.0, 90.0, r, vol, t)
    assert di + do == pytest.approx(vanilla, rel=1e-10)


def test_bachelier_matches_quantlib() -> None:
    for payer, qk in [(True, ql.Option.Call), (False, ql.Option.Put)]:
        for k in (0.025, 0.035, 0.045):
            mine = bachelier_price(payer, 0.035, k, 0.0095, 2.0)
            ref = ql.bachelierBlackFormula(qk, k, 0.035, 0.0095 * np.sqrt(2.0), 1.0)
            assert mine == pytest.approx(ref, rel=1e-12)


# --- pricers ---------------------------------------------------------------------------------


def test_repo_zero_at_fair_rate_and_dv01_sign(market: MarketSnapshot) -> None:
    bond = inst.government_bonds("USD", AS_OF)[1]
    start, end = AS_OF - relativedelta(days=10), AS_OF + relativedelta(months=3)
    rp = inst.repo("USD", bond, start, end, 0.038)
    t = Trade(
        trade_id="R1",
        instrument=rp,
        direction=BuySell.BUY,
        quantity=100e6,
        trade_price=0.038,
        trade_date=start,
        book_id="USD_REPO",
        trader_id="T1",
        **_bilateral(),
    )
    r = PRICERS[rp.product_type](t, market, AS_OF)
    fair = r.details["fair_rate"]
    at_fair = t.model_copy(update={"instrument": rp.model_copy(update={"repo_rate": fair})})
    r2 = PRICERS[rp.product_type](at_fair, market, AS_OF)
    assert abs(r2.pv_local) < 1.0
    # Reverse repo (cash lent) loses when rates rise: the fixed receivable is discounted harder.
    bumped = market.with_values(
        {f"IR:USD:{k}": market.values[f"IR:USD:{k}"] + 0.01 for k in ["1M", "3M", "6M"]}
    )
    assert PRICERS[rp.product_type](t, bumped, AS_OF).pv_local < r.pv_local
    assert r.details["collateral_required"] == pytest.approx(100e6 / 0.98)


def test_ir_future_price_from_curve(market: MarketSnapshot) -> None:
    fut = inst.ir_futures("USD", AS_OF)[2]
    t = Trade(
        trade_id="F1",
        instrument=fut,
        direction=BuySell.BUY,
        quantity=100,
        trade_price=96.0,
        trade_date=AS_OF,
        book_id="USD_STIR",
        trader_id="T1",
        **_listed("EXCH_CME"),
    )
    r = PRICERS[fut.product_type](t, market, AS_OF)
    curve = market.zero_curve("USD")
    t1 = (fut.expiry_date - AS_OF).days / 365.0
    fwd = (curve.df(t1) / curve.df(t1 + 0.25) - 1.0) / 0.25
    assert r.details["price"] == pytest.approx(100 - 100 * fwd)
    assert r.pv_local == pytest.approx(100 * 1e6 * 0.25 / 100 * (r.details["price"] - 96.0))
    # Long futures lose when rates rise.
    bumped = market.with_values({k: v + 0.01 for k, v in market.values.items() if k.startswith("IR:USD:")})
    assert PRICERS[fut.product_type](t, bumped, AS_OF).pv_local < r.pv_local


def test_swaption_matches_quantlib_bachelier(market: MarketSnapshot) -> None:
    expiry = AS_OF + relativedelta(years=1)
    sw = inst.swaption("USD", expiry, "5Y", 0.038, payer=True)
    t = Trade(
        trade_id="S1",
        instrument=sw,
        direction=BuySell.BUY,
        quantity=100e6,
        trade_price=0.01,
        trade_date=AS_OF,
        book_id="USD_STIR",
        trader_id="T1",
        **_bilateral(),
    )
    r = PRICERS[sw.product_type](t, market, AS_OF)
    fwd, annuity, maturity = forward_swap(market, sw, AS_OF)
    # QuantLib: same discount curve, an index forwarding off it, Bachelier engine at 90bp.
    curve = _ql_curve(USD_ZEROS, AS_OF)
    index = ql.IborIndex(
        "SIM",
        ql.Period(6, ql.Months),
        0,
        ql.USDCurrency(),
        ql.NullCalendar(),
        ql.Unadjusted,
        False,
        ql.Actual360(),
        curve,
    )
    start = ql.Date(expiry.day, expiry.month, expiry.year)
    end = ql.Date(maturity.day, maturity.month, maturity.year)
    fixed = ql.Schedule(
        start,
        end,
        ql.Period(6, ql.Months),
        ql.NullCalendar(),
        ql.Unadjusted,
        ql.Unadjusted,
        ql.DateGeneration.Backward,
        False,
    )
    swap = ql.VanillaSwap(
        ql.Swap.Payer, 100e6, fixed, 0.038, ql.Thirty360(ql.Thirty360.USA), fixed, index, 0.0, ql.Actual360()
    )
    swaption = ql.Swaption(swap, ql.EuropeanExercise(start))
    swaption.setPricingEngine(ql.BachelierSwaptionEngine(curve, ql.QuoteHandle(ql.SimpleQuote(0.0090))))
    assert r.pv_local == pytest.approx(swaption.NPV(), rel=2e-3)
    # Payer/receiver parity: payer - receiver = annuity x (forward - strike).
    rec = t.model_copy(update={"instrument": sw.model_copy(update={"payer": False})})
    rr = PRICERS[sw.product_type](rec, market, AS_OF)
    assert r.pv_local - rr.pv_local == pytest.approx(100e6 * annuity * (fwd - 0.038), rel=1e-9)
    assert r.details["vega_1bp"] > 0


def test_single_name_cds_and_index_share_the_model(market: MarketSnapshot) -> None:
    cds = inst.cds_single_name("FORD", AS_OF)
    t = Trade(
        trade_id="C1",
        instrument=cds,
        direction=BuySell.BUY,
        quantity=10e6,
        trade_price=0.015,
        trade_date=AS_OF,
        book_id="SN_CDS_HY",
        trader_id="T1",
        **_bilateral(),
    )
    r = PRICERS[cds.product_type](t, market, AS_OF)
    assert r.model == "cds_flat_hazard"
    assert r.details["spread_bp"] == 150.0
    # Buying protection at a 100bp coupon on a 150bp name is worth money to the buyer.
    assert r.pv_local > 0
    wider = market.with_values({"CDS:FORD": 200.0})
    assert PRICERS[cds.product_type](t, wider, AS_OF).pv_local > r.pv_local


def test_commodity_option_black76_and_parity(market: MarketSnapshot) -> None:
    expiry = AS_OF + relativedelta(months=6)
    call = inst.commodity_option("BRENT", expiry, 72.0, OptionType.CALL)
    put = inst.commodity_option("BRENT", expiry, 72.0, OptionType.PUT)
    mk = dict(
        direction=BuySell.BUY,
        quantity=100,
        trade_price=3.0,
        trade_date=AS_OF,
        book_id="ENERGY_OPTIONS",
        trader_id="T1",
        **_listed("EXCH_ICE"),
    )
    rc = PRICERS[call.product_type](Trade(trade_id="O1", instrument=call, **mk), market, AS_OF)
    rp = PRICERS[put.product_type](Trade(trade_id="O2", instrument=put, **mk), market, AS_OF)
    t = (expiry - AS_OF).days / 365.0
    fwd = float(market.commodity_curve("BRENT").price(t))
    df = float(market.zero_curve("USD").df(t))
    ref = ql.blackFormula(ql.Option.Call, 72.0, fwd, 0.35 * np.sqrt(t), df)
    assert rc.details["unit_price"] == pytest.approx(ref, rel=1e-10)
    assert rc.pv_local - rp.pv_local == pytest.approx(100 * 1000 * df * (fwd - 72.0), rel=1e-9)


def test_fund_lookthrough_pricing(market: MarketSnapshot) -> None:
    basket = (
        BasketLeg(underlying="AAPL", kind="EQ", units_per_share=0.1, currency="USD"),
        BasketLeg(underlying="MSFT", kind="EQ", units_per_share=0.05, currency="USD"),
    )
    etf = ETFModel(
        instrument_id="ETF_T",
        currency="USD",
        ticker="T",
        exchange="NYSE",
        basket=basket,
        tracking_spread=-0.001,
    )
    mf = MutualFund(instrument_id="MF_T", currency="USD", fund_code="T", basket=basket, cash_per_share=2.0)
    mk = dict(
        direction=BuySell.BUY,
        quantity=1000,
        trade_price=40.0,
        trade_date=AS_OF,
        book_id="FUNDS_ETF",
        trader_id="T1",
    )
    re = PRICERS[etf.product_type](
        Trade(trade_id="E1", instrument=etf, **mk, **_listed("EXCH_NYSE")), market, AS_OF
    )
    rm = PRICERS[mf.product_type](
        Trade(trade_id="M1", instrument=mf, **mk, counterparty_id="AM_ONE", clearing=ClearingType.BILATERAL),
        market,
        AS_OF,
    )
    nav = 0.1 * 200.0 + 0.05 * 400.0
    assert re.details["nav"] == pytest.approx(nav)
    assert re.pv_local == pytest.approx(1000 * nav * 0.999)
    assert rm.pv_local == pytest.approx(1000 * (nav + 2.0))
    # Look-through: constituents' factors drive the fund, and the report splits direct vs via fund.
    t_etf = Trade(trade_id="E1", instrument=etf, **mk, **_listed("EXCH_NYSE"))
    prefixes = factor_prefixes(t_etf, "USD")
    assert "EQ:AAPL" in prefixes and "EQ:MSFT" in prefixes
    cash = Trade(
        trade_id="Q1",
        instrument=inst.cash_equity("AAPL"),
        direction=BuySell.BUY,
        quantity=100,
        trade_price=200.0,
        trade_date=AS_OF,
        book_id="US_CASH_EQ",
        trader_id="T1",
        **_listed("EXCH_NYSE"),
    )
    lt = look_through([t_etf, cash], market, "USD", min_exposure=10_000.0)
    aapl = lt.constituents.set_index("constituent").loc["AAPL"]
    assert aapl["direct"] == pytest.approx(100 * 200.0)
    assert aapl["via_funds"] == pytest.approx(1000 * 0.1 * 200.0)
    assert aapl["via_funds_share"] == pytest.approx(20000 / 40000)
    assert lt.flags and "AAPL" in lt.flags[0]


def test_equity_exotic_pricer_uses_surface_and_curve(market: MarketSnapshot) -> None:
    expiry = AS_OF + relativedelta(years=1)
    uo = inst.equity_barrier("AAPL", expiry, 200.0, 240.0, BarrierType.UP_AND_OUT, OptionType.CALL, "USD")
    dig = inst.equity_digital("SPX", expiry, 5000.0, 100.0, OptionType.PUT, "USD")
    mk = dict(
        direction=BuySell.BUY,
        quantity=10,
        trade_price=5.0,
        trade_date=AS_OF,
        book_id="EQ_EXOTICS",
        trader_id="T1",
        **_bilateral(),
    )
    ru = PRICERS[uo.product_type](Trade(trade_id="X1", instrument=uo, **mk), market, AS_OF)
    rd = PRICERS[dig.product_type](Trade(trade_id="X2", instrument=dig, **mk), market, AS_OF)
    t = (expiry - AS_OF).days / 365.0
    df = float(market.zero_curve("USD").df(t))
    rate = -np.log(df) / t
    assert ru.details["unit_price"] == pytest.approx(
        barrier_price(OptionType.CALL, BarrierType.UP_AND_OUT, 200.0, 200.0, 240.0, rate, 0.25, t)
    )
    assert ru.details["unit_price"] < ru.details["vanilla_price"]
    assert rd.details["unit_price"] == pytest.approx(
        cash_or_nothing(OptionType.PUT, 5000.0, 5000.0, rate, 0.18, t, 100.0)
    )
    assert ru.pv_local == pytest.approx(10 * 100 * ru.details["unit_price"])
    # A knocked-out option is worth the rebate only.
    knocked = market.with_values({"EQ:AAPL": 250.0})
    assert PRICERS[uo.product_type](Trade(trade_id="X1", instrument=uo, **mk), knocked, AS_OF).pv_local == 0.0


def test_domain_validators() -> None:
    with pytest.raises(ValueError):
        EquityExotic(
            instrument_id="bad",
            currency="USD",
            underlying="AAPL",
            style=ExoticStyle.BARRIER,
            option_type=OptionType.CALL,
            strike=100.0,
            expiry_date=AS_OF,
        )
    with pytest.raises(ValueError):
        Repo(
            instrument_id="bad",
            currency="USD",
            collateral_instrument_id="B",
            start_date=AS_OF,
            end_date=AS_OF,
            repo_rate=0.03,
        )
    assert Swaption(
        instrument_id="s", currency="USD", expiry_date=AS_OF, swap_tenor="5Y", strike=0.03
    ).swap_side
    assert InterestRateFuture(
        instrument_id="f", currency="USD", index="SOFR", exchange="CME", expiry_date=AS_OF
    )
    assert (
        CDSSingleName(
            instrument_id="c", currency="USD", reference_entity="FORD", maturity_date=AS_OF, fixed_coupon=0.01
        ).recovery_rate
        == 0.4
    )
    assert (
        CommodityOption(
            instrument_id="o",
            currency="USD",
            commodity="BRENT",
            exchange="ICE",
            option_type=OptionType.PUT,
            strike=70.0,
            expiry_date=AS_OF,
            contract_size=1000,
        ).unit
        == ""
    )


# --- generator, universe and sensitivities ------------------------------------------------------


@pytest.fixture(scope="module")
def sim():
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=1.5, seed=7))
    from novera.market_data.history import MarketHistory

    hist = MarketHistory.from_long(md.history)
    return md, hist


def test_universe_has_new_families() -> None:
    ids = {f.factor_id for f in build_risk_factor_universe()}
    assert "SWVOL:USD:1Y:5Y" in ids and "CDS:FORD" in ids and "VOL:BRENT:3M:1.00" in ids
    types = {f.factor_type.value for f in build_risk_factor_universe() if f.factor_id.startswith("SWVOL:")}
    assert types == {"SWAPTION_VOL"}


def test_generator_covers_all_eight_products_and_prices(sim) -> None:
    md, hist = sim
    for org, cp, tmpl in [
        (build_global_macro_bank(), None, BANK_TEMPLATE),
        (build_multi_strategy_fund(), None, FUND_TEMPLATE),
    ]:
        cp = build_counterparty_universe(org) if tmpl is BANK_TEMPLATE else build_fund_counterparties(org)
        gen = generate_portfolio(org, cp, TradeGeneratorConfig(AS_OF, 600, 7, True, hist, tmpl))
        kinds = {t.product_type.value for t in gen.snapshot.trades}
        expected = {
            "SWAPTION",
            "INTEREST_RATE_FUTURE",
            "CDS_SINGLE_NAME",
            "COMMODITY_OPTION",
            "ETF",
            "EQUITY_EXOTIC",
        }
        if tmpl is BANK_TEMPLATE:
            expected |= {"REPO", "MUTUAL_FUND"}
        assert expected <= kinds, expected - kinds
        out = value_portfolio(gen.snapshot, md.snapshot, org, "USD")
        assert not out.errors, list(out.errors.items())[:3]
        # Struck at fair value: inception PV of the new products is small next to their size.
        for t in gen.snapshot.trades:
            if t.product_type.value not in expected:
                continue
            snap = hist.snapshot_at(t.trade_date)
            r = PRICERS[t.product_type](t, snap, snap.as_of)
            if t.product_type.value == "REPO":
                assert abs(r.pv_local) < 1e-4 * t.quantity, t.trade_id  # rate struck at fair
            if t.product_type.value == "INTEREST_RATE_FUTURE":
                assert abs(r.pv_local) < 2e-5 * t.quantity * 1e6, t.trade_id  # under 2bp of notional
            if t.product_type.value == "CDS_SINGLE_NAME":  # spread struck at market; PV is the upfront
                assert abs(t.trade_price * 1e4 - r.details["spread_bp"]) < 0.1 * r.details["spread_bp"]
        if tmpl is BANK_TEMPLATE:
            reserved = {
                "USD_REPO",
                "USD_STIR",
                "EUR_STIR",
                "SN_CDS_IG",
                "SN_CDS_HY",
                "ENERGY_OPTIONS",
                "METALS_OPTIONS",
                "FUNDS_ETF",
                "EQ_EXOTICS",
            }
            for t in gen.snapshot.trades:
                if t.book_id in reserved:
                    assert t.product_type.value in expected, (t.trade_id, t.book_id)


def test_sensitivities_for_new_products(sim) -> None:
    md, hist = sim
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(AS_OF, 300, 7, False, hist, BANK_TEMPLATE))
    universe = {f.factor_id: f for f in md.universe}
    pf = Portfolio(gen.snapshot.trades, md.snapshot, "USD", universe=universe)
    sens = compute_sensitivities(pf)
    by = {t.trade_id: t for t in gen.snapshot.trades}
    swvol = sens[sens.factor_id.str.startswith("SWVOL:")]
    assert len(swvol) and set(swvol["measure"]) == {"VEGA"} and (swvol["bump"] == 1.0).all()
    assert all(by[tid].product_type.value == "SWAPTION" for tid in swvol["trade_id"])
    for tid in swvol["trade_id"]:
        v = swvol[swvol.trade_id == tid]["value"].sum()
        assert np.sign(v) == (1 if by[tid].direction.value == "BUY" else -1), tid
    cs = sens[
        (sens.measure == "CS01") & sens.factor_id.isin([f"CDS:{e}" for e in ["FORD", "GM", "DBK", "CASINO"]])
    ]
    assert len(cs) and all(by[tid].product_type.value == "CDS_SINGLE_NAME" for tid in cs["trade_id"])
    cmd_gamma = sens[(sens.measure == "GAMMA") & sens.factor_id.str.startswith("CMD:")]
    assert len(cmd_gamma) and all(
        by[tid].product_type.value == "COMMODITY_OPTION" for tid in cmd_gamma["trade_id"]
    )
    etf_ids = [tid for tid, t in by.items() if t.product_type.value == "ETF"]
    eq = sens[(sens.measure == "EQ_DELTA") & sens.trade_id.isin(etf_ids)]
    assert len(eq), "ETFs must show delta on their constituents"
    idx = DependencyIndex.build(gen.snapshot.trades, "USD")
    assert idx.trades_for(["SWVOL:USD:1Y:5Y"]) <= {
        tid for tid, t in by.items() if t.product_type.value == "SWAPTION"
    }


# --- market-data proxies -----------------------------------------------------------------------


def test_proxies_interpolate_roll_and_relevel(sim) -> None:
    md, hist = sim
    raw = md.snapshot  # planted: USD 7Y missing, EUR/USD vol surface stale
    prev = md.previous_snapshot
    res = apply_proxies(raw, md.universe, prev)
    assert res.raw is raw and res.applied
    tbl = res.table()
    kinds = dict(zip(tbl["factor_id"], tbl["kind"], strict=True))
    assert kinds["IR:USD:7Y"] == "INTERPOLATED"
    assert kinds["VOL:EURUSD:1M:1.00"] == "RELEVELLED"
    lo, hi = raw.values["IR:USD:5Y"], raw.values["IR:USD:10Y"]
    assert min(lo, hi) <= res.market.values["IR:USD:7Y"] <= max(lo, hi)
    # Re-levelled surface moves with GBP/USD's move since the stale date.
    move = np.mean([raw.values[k] / prev.values[k] - 1 for k in raw.factors_with_prefix("VOL:GBPUSD:")])
    assert res.market.values["VOL:EURUSD:1M:1.00"] == pytest.approx(
        raw.values["VOL:EURUSD:1M:1.00"] * (1 + move)
    )
    assert "VOL:EURUSD:1M:1.00" not in res.market.observed_at
    # Missing single factor rolls from the previous day; stored actions rebuild the same snapshot.
    values = {k: v for k, v in raw.values.items() if k != "EQ:AAPL"}
    raw2 = MarketSnapshot(as_of=raw.as_of, values=values, observed_at=raw.observed_at)
    res2 = apply_proxies(raw2, md.universe, prev)
    assert res2.market.values["EQ:AAPL"] == prev.values["EQ:AAPL"]
    rebuilt = apply_stored_proxies(raw2, res2.table())
    assert rebuilt.snapshot_id == res2.market.snapshot_id
    # A clean snapshot is returned untouched.
    clean = apply_proxies(prev, md.universe, None)
    assert clean.market is prev and not clean.actions
