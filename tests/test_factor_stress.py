"""Standalone factor stress, sliding scales, jump to default and worst windows (MR-019, round 48)."""

from datetime import date

import numpy as np
import pytest

from novera.domain.enums import BuySell, ProductType
from novera.market_data.history import MarketHistory
from novera.pricing.valuation import fx_to_reporting, value_portfolio
from novera.risk import Portfolio, compute_sensitivities
from novera.risk import factor_stress as fs
from novera.risk.scenarios import historical_shocks
from novera.simulation import (
    TradeGeneratorConfig,
    build_counterparty_universe,
    build_global_macro_bank,
    generate_portfolio,
)
from novera.simulation.market_data import MarketSimConfig, generate_market_data

AS_OF = date(2026, 9, 11)


@pytest.fixture(scope="module")
def world():
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=1.5, seed=48))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(
        org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=300, seed=48, market_history=hist)
    )
    universe = {f.factor_id: f for f in md.universe}
    val = value_portfolio(gen.snapshot, md.snapshot, org, "USD").table
    pf = Portfolio(gen.snapshot.trades, md.snapshot, "USD", universe=universe)
    sens = compute_sensitivities(pf)
    return {"pf": pf, "hist": hist, "val": val, "sens": sens, "desks": val.set_index("trade_id")["desk_id"]}


@pytest.fixture(scope="module")
def result(world):
    return fs.run_factor_stress(world["pf"], world["sens"], world["hist"], world["val"])


def test_sizes_are_each_directions_quantile_of_ten_day_moves(world, result):
    pf, hist = world["pf"], world["hist"]
    sizes = result.sizes.set_index("factor_id")
    assert (sizes["down"] <= 0).all() and (sizes["up"] >= 0).all() and (sizes["sigma"] > 0).all()
    days = len([d for d in hist.dates if d <= pf.as_of]) - fs.SIZE_HORIZON
    moves = historical_shocks(hist, pf.as_of, days, fs.SIZE_HORIZON, pf.universe, factor_ids=["EQIDX:SPX"])
    x = moves["EQIDX:SPX"].to_numpy()
    assert sizes.loc["EQIDX:SPX", "down"] == pytest.approx(np.quantile(x, 1 - fs.SIZE_QUANTILE))
    assert sizes.loc["EQIDX:SPX", "up"] == pytest.approx(np.quantile(x, fs.SIZE_QUANTILE))
    # 99.9% of 10-day moves is beyond the 99% move: the standalone shock is severe.
    assert sizes.loc["EQIDX:SPX", "down"] < np.quantile(x, 0.01)


def test_standalone_is_full_revaluation_of_one_factor_split_by_desk(world, result):
    pf = world["pf"]
    st = result.standalone
    assert set(st["direction"]) == {"DOWN", "UP"}
    fid = "EQIDX:SPX"
    shock = float(result.sizes.set_index("factor_id").loc[fid, "down"])
    direct = pf.pnl_under_shocks({fid: shock})
    mine = st[(st.factor_id == fid) & (st.direction == "DOWN")]
    assert mine["pnl"].sum() == pytest.approx(sum(direct.values()))
    by_desk = {}
    for tid, v in direct.items():
        by_desk[world["desks"][tid]] = by_desk.get(world["desks"][tid], 0.0) + v
    assert dict(zip(mine["desk_id"], mine["pnl"], strict=True)) == pytest.approx(by_desk)


def test_sliding_scales_cover_the_headline_basket_and_desk_worst(result):
    sc = result.scales
    firm = sc[sc.desk_id == fs.FIRM]
    factors = list(dict.fromkeys(firm["factor_id"]))
    assert "EQIDX:SPX" in factors and "IR:USD:10Y" in factors
    for _, grp in firm.groupby("factor_id"):
        assert sorted(grp["sigmas"]) == list(fs.SCALE_STEPS)
        assert grp.set_index("sigmas").loc[0.0, "pnl"] == 0.0
    # The firm row is the sum of the desk rows at every point.
    desks = sc[sc.desk_id != fs.FIRM].groupby(["factor_id", "sigmas"])["pnl"].sum()
    totals = firm.set_index(["factor_id", "sigmas"])["pnl"]
    assert desks.reindex(totals.index, fill_value=0.0).to_numpy() == pytest.approx(totals.to_numpy())
    # Each desk's worst standalone factor is scaled.
    worst = result.standalone.groupby(["desk_id", "factor_id"])["pnl"].min().reset_index()
    for _, grp in worst[worst.pnl < 0].groupby("desk_id"):
        assert grp.sort_values(["pnl", "factor_id"])["factor_id"].iloc[0] in factors


def test_issuers_join_cds_and_stock_and_leave_out_indices(world):
    names = {i: (eq, cds) for i, eq, cds in fs.issuers(world["pf"])}
    assert names["BOEING"] == ("EQ:BA", "BOEING") and "BA" not in names
    assert names["TEVA"] == (None, "TEVA") and names["NVDA"] == ("EQ:NVDA", None)
    assert not any(i.startswith(fs.INDEX_PREFIXES) for i in names)


def test_jump_to_default_settles_cds_at_recovery_and_zeros_the_stock(world, result):
    pf, desks = world["pf"], world["desks"]
    jtd = result.jump_to_default
    cds = [
        pf.by_id[t]
        for t in pf.priced_ids
        if pf.by_id[t].product_type is ProductType.CDS_SINGLE_NAME
        and pf.by_id[t].instrument.maturity_date > pf.as_of
    ]
    assert cds, "fixture holds no single-name CDS"
    entity = cds[0].instrument.reference_entity
    expected = 0.0
    for t in cds:
        if t.instrument.reference_entity != entity:
            continue
        sign = 1.0 if t.direction is BuySell.BUY else -1.0
        fx = fx_to_reporting(pf.base, t.instrument.currency, "USD")
        expected += sign * t.quantity * (1 - t.instrument.recovery_rate) * fx - pf.base_pv[t.trade_id]
    issuer = next(i for i, _, c in fs.issuers(pf) if c == entity)
    assert jtd[jtd.issuer == issuer]["cds_pnl"].sum() == pytest.approx(expected)
    # A cash equity loses all but 0.01% of its value.
    cash = next(pf.by_id[t] for t in pf.priced_ids if pf.by_id[t].product_type is ProductType.CASH_EQUITY)
    ticker = cash.instrument.ticker
    one = fs.jump_to_default(Portfolio([cash], pf.base, "USD", universe=pf.universe), desks)
    eq = one[one.equity == f"EQ:{ticker}"]["equity_pnl"].sum()
    assert eq == pytest.approx(-pf.base_pv[cash.trade_id] * (1 - fs.EQUITY_DEFAULT_PRICE), rel=1e-6)


def test_worst_windows_are_distinct_confirmed_and_ranked(world, result):
    pf = world["pf"]
    w = result.windows
    assert set(w["horizon_days"]) == set(fs.WINDOW_HORIZONS) and set(w["version"]) == {"RAW", "VOL_RESCALED"}
    dates = [d for d in world["hist"].dates if d <= pf.as_of]
    pos = {d: i for i, d in enumerate(dates)}
    for (h, _), grp in w.groupby(["horizon_days", "version"]):
        assert list(grp.sort_values("rank")["pnl"]) == sorted(grp["pnl"])
        ends = [pos[d] for d in grp["window_end"]]
        assert all(abs(a - b) >= h for i, a in enumerate(ends) for b in ends[i + 1 :])
        assert all(pos[e] - pos[s] == h for s, e in zip(grp["window_start"], grp["window_end"], strict=True))
    # The confirmation is full revaluation of the raw window's moves.
    raw = w[(w.version == "RAW") & (w.horizon_days == 5) & (w["rank"] == 1)].iloc[0]
    moves = historical_shocks(
        world["hist"], raw["window_end"], 1, 5, pf.universe, factor_ids=list(pf.base.values)
    )
    assert pf.pnl_matrix(moves.tail(1), workers=1).sum(axis=1).iloc[0] == pytest.approx(raw["pnl"])


def test_summary_names_the_worst_of_each_view(result):
    s = result.summary()
    tot = result.standalone.groupby(["factor_id", "direction"])["pnl"].sum()
    assert s["worst_factor"] == pytest.approx(tot.min())
    assert s["worst_window"] == pytest.approx(result.windows["pnl"].min())
    assert s["worst_jtd_issuer"] in set(result.jump_to_default["issuer"])
