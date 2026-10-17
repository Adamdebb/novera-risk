"""Stress paths with management actions, hedging and liquidation (MR-023, round 51)."""

from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from novera.market_data.history import MarketHistory
from novera.pricing.valuation import value_portfolio
from novera.risk import Portfolio, compute_sensitivities
from novera.risk import pca_space as ps
from novera.risk import stress_paths as sp
from novera.risk.liquidity import liquidity
from novera.simulation import (
    TradeGeneratorConfig,
    build_counterparty_universe,
    build_global_macro_bank,
    generate_portfolio,
)
from novera.simulation.market_data import MarketSimConfig, generate_market_data

AS_OF = date(2026, 9, 11)
H = sp.HORIZON


@pytest.fixture(scope="module")
def world():
    return build_world()


def build_world():
    """A 300-trade bank with its sensitivities, PCA space and liquidity (shared with test_funding_chain)."""
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=2.5, seed=51))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(
        org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=300, seed=51, market_history=hist)
    )
    universe = {f.factor_id: f for f in md.universe}
    val = value_portfolio(gen.snapshot, md.snapshot, org, "USD").table
    pf = Portfolio(gen.snapshot.trades, md.snapshot, "USD", universe=universe)
    sens = compute_sensitivities(pf)
    book = ps.quad_book(pf, sens, list(pf.base.values))
    space = ps.build_space(pf, hist, book, None, list(pf.priced_ids))
    liq = liquidity(val, 1.0).by_trade
    desks = sorted(val["desk_id"].dropna().unique())
    return {
        "pf": pf,
        "sens": sens,
        "val": val,
        "book": book,
        "space": space,
        "hist": hist,
        "liq": liq,
        "desks": desks,
    }


def _usd_rates_result(world, sign: float = 1.0, bp: float = 100.0):
    """A stand-in for a stored stress result: USD rates parallel by ``bp``."""
    pf = world["pf"]
    shocks = {f: sign * bp * 1e-4 for f in pf.base.values if f.startswith("IR:USD:")}
    total = float(sum(pf.pnl_under_shocks(shocks).values()))
    return SimpleNamespace(
        scenario=SimpleNamespace(scenario_id=f"usd_parallel_{int(sign * bp)}", name="USD rates parallel"),
        shocks=shocks,
        total=total,
    )


def _run(world, rules, results, desk_var=None):
    dv = desk_var if desk_var is not None else dict.fromkeys(world["desks"], 1e6)
    return sp.run_stress_paths(
        world["pf"],
        world["book"],
        world["space"],
        world["sens"],
        world["hist"],
        world["val"],
        world["liq"],
        dv,
        [],
        results,
        rules,
        workers=1,
    )


def test_shapes_reach_their_points():
    g, v, b = (sp.shape_weights(s) for s in sp.SHAPES)
    assert g[0] == v[0] == b[0] == 0.0
    assert g[1] == pytest.approx(sp.GAP) and g[-1] == pytest.approx(1.0)
    assert v[sp.V_BOTTOM_DAY] == pytest.approx(1.0) and v[-1] == pytest.approx(sp.V_END)
    assert np.allclose(np.diff(b), 1.0 / H)
    with pytest.raises(ValueError):
        sp.shape_weights("ZIGZAG")


def test_rules_validate_and_desk_rows_replace_the_default():
    d = sp.ActionRule()
    own = sp.ActionRule(scope="USD_RATES", cut_fraction=1.0)
    assert sp.validate_rules([d, own], ["USD_RATES", "EUR_RATES"]) == []
    assert "exactly one" in sp.validate_rules([own], ["USD_RATES"])[0]
    assert any("twice" in e for e in sp.validate_rules([d, own, own], ["USD_RATES"]))
    assert any(
        "unknown desks" in e for e in sp.validate_rules([d, replace(own, scope="NOPE")], ["USD_RATES"])
    )
    assert any("cut fraction" in e for e in sp.validate_rules([replace(d, cut_fraction=1.5)]))
    assert any("participation" in e for e in sp.validate_rules([replace(d, participation=0.0)]))
    by = sp.rules_by_desk([d, own], ["USD_RATES", "EUR_RATES"])
    assert by["USD_RATES"].cut_fraction == 1.0 and by["EUR_RATES"] is d
    with pytest.raises(ValueError):
        sp.ActionRule.from_dict({"scope": "(default)", "colour": "red"})


def test_frozen_path_is_full_revaluation_every_day(world):
    """The frozen book's cumulative P&L on each day is the full revaluation of that day's moves plus
    theta; a scenario path that ends on the scenario ends on its stored loss."""
    pf = world["pf"]
    r = _usd_rates_result(world)
    res = _run(world, [sp.ActionRule(enabled=False)], [r])
    theta = float(world["sens"][world["sens"]["measure"] == "THETA"]["value"].sum()) * sp.THETA_DAYS
    days = res.days[(res.days["path_id"] == "usd_parallel_100:GAP_DRIFT") & (res.days["book"] == "FROZEN")]
    assert days["cumulative"].iloc[-1] == pytest.approx(r.total + theta * H, rel=1e-9, abs=1.0)
    for d, w in ((1, sp.GAP), (10, sp.shape_weights("GAP_DRIFT")[10])):
        shocks = {f: v * w for f, v in r.shocks.items()}
        full = float(sum(pf.pnl_under_shocks(shocks).values()))
        got = float(days[days["day"] == d]["cumulative"].iloc[0])
        assert got == pytest.approx(full + theta * d, rel=1e-9, abs=1.0)
    # Every source is present: three shapes, the worst window, the Monte Carlo tail.
    fr = res.summary_table[res.summary_table["book"] == "FROZEN"]
    assert set(fr["source"]) == {"SCENARIO", "HISTORICAL", "MONTE_CARLO"}
    assert len(fr[fr["source"] == "MONTE_CARLO"]) == round(sp.MC_TAIL * sp.MC_PATHS)
    assert fr["taylor_end_pnl"].notna().all()


def test_disabled_rules_leave_the_managed_book_frozen(world):
    res = _run(world, [sp.ActionRule(enabled=False)], [_usd_rates_result(world)])
    s = res.summary_table.set_index(["path_id", "book"])["end_pnl"].unstack()
    assert np.allclose(s["FROZEN"], s["MANAGED"])
    assert res.actions.empty and (res.summary_table["action_cost"] == 0).all()


def test_cut_is_bounded_by_volume_and_stops_at_its_target(world):
    """Every desk acts on day one (a tiny stop-loss): the held share falls day by day, never below the
    cut, and a desk that finishes holds exactly the target; costs are paid."""
    rule = sp.ActionRule(stop_loss_var_multiple=1e-9, close_short_options=False, hedge_delta=False)
    r = _usd_rates_result(world, -1.0)
    r2 = _usd_rates_result(world, 1.0)
    res = _run(world, [rule], [r, r2])
    days = res.days[res.days["book"] == "MANAGED"]
    for _, g in days.groupby("path_id"):
        held = g.sort_values("day")["risk_held"].to_numpy()
        assert np.all(np.diff(held) <= 1e-12) and held.min() >= 0.5 - 1e-9
    mg = res.summary_table[res.summary_table["book"] == "MANAGED"]
    acted = mg[mg["desks_triggered"] > 0]
    assert len(acted) and (acted["action_cost"] > 0).all()
    desks = res.desks[(res.desks["book"] == "MANAGED") & res.desks["trigger_day"].notna()]
    assert (
        desks["completion_day"].dropna() > desks.loc[desks["completion_day"].notna(), "trigger_day"]
    ).all()
    assert set(res.actions["action"]) == {"CUT"}


def test_hedging_takes_most_of_the_rates_loss_away(world):
    """A desk that loses on a slow USD rates move and only hedges keeps a fraction of the frozen loss:
    the rate future hedges the curve, what is left is curve shape, gamma and the days before acting."""
    sens = world["sens"]
    usd = sens[(sens["measure"] == "DV01") & sens["factor_id"].str.startswith("IR:USD:")]
    desk_of = world["val"].drop_duplicates("trade_id").set_index("trade_id")["desk_id"]
    dv01 = usd.assign(desk=usd["trade_id"].map(desk_of)).groupby("desk")["value"].sum()
    desk = str(dv01.abs().idxmax())
    sign = -1.0 if dv01[desk] > 0 else 1.0  # the direction in which the desk loses
    rule = sp.ActionRule(stop_loss_var_multiple=1e-9, cut_fraction=0.0, close_short_options=False)
    res = _run(world, [rule], [_usd_rates_result(world, sign, 200.0)])
    d = res.desks[
        (res.desks["path_id"] == f"usd_parallel_{int(sign * 200)}:SLOW_BLEED")
        & (res.desks["desk_id"] == desk)
    ]
    frozen = float(d[d["book"] == "FROZEN"]["end_pnl"].iloc[0])
    managed = float(d[d["book"] == "MANAGED"]["end_pnl"].iloc[0])
    assert frozen < 0 and abs(managed) < 0.5 * abs(frozen)
    acts = res.actions[
        (res.actions["path_id"] == f"usd_parallel_{int(sign * 200)}:SLOW_BLEED")
        & (res.actions["desk_id"] == desk)
    ]
    assert set(acts["action"]) == {"HEDGE"}


def test_hedge_instruments_map_names_to_regional_indices(world):
    h = sp.build_hedges(world["pf"], world["book"], world["space"])
    assert set(h.kinds) <= set(sp.HEDGE_VOLUME)
    names = set(h.names)
    assert any(n.endswith("rate future") for n in names)
    rows = dict(zip([r[0] for r in world["book"].rows], range(len(world["book"].rows)), strict=False))
    if "EQ:AAPL" in rows:
        col = h.R[rows["EQ:AAPL"]].indices
        assert len(col) == 1 and h.names[col[0]] == "SPX index future"
    if "IR:ARS:10Y" in rows:  # no listed rate future: not hedged
        assert h.R[rows["IR:ARS:10Y"]].nnz == 0
    assert (h.sigma > 0).all()


def test_monte_carlo_compounds_prices_as_log_returns(world):
    space = world["space"]
    uni = world["pf"].universe
    rel = np.array([f in uni and uni[f].shock_type != "ABSOLUTE" for f in world["book"].factors])
    x = np.full((2, len(rel)), -5.0)
    out = sp._compound(x.copy(), rel)
    assert np.all(out[:, rel] >= sp.PRICE_FLOOR) and np.allclose(out[:, ~rel], -5.0)
    assert np.allclose(sp._compound(np.full((1, len(rel)), 0.1), rel)[:, rel], np.expm1(0.1))
    assert space.k > 0


def test_summary_names_the_worst_path(world):
    res = _run(world, [sp.ActionRule()], [_usd_rates_result(world)])
    s = res.summary()
    fr = res.summary_table[res.summary_table["book"] == "FROZEN"]
    assert s["stress_paths"] == len(fr)
    assert s["path_worst_drawdown_frozen"] == pytest.approx(fr["max_drawdown"].min())
    assert isinstance(res.parameters["rules"], list) and res.parameters["horizon_days"] == H
    assert list(res.summary_table.columns) == sp.SUMMARY_COLUMNS
    assert pd.api.types.is_numeric_dtype(res.days["cumulative"])
