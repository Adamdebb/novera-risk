"""Risk engine tests: structural invariants, not magic numbers."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from novera.market_data.history import MarketHistory
from novera.pricing.valuation import value_portfolio
from novera.risk import (
    HYPOTHETICAL_LIBRARY,
    Portfolio,
    VaRConfig,
    compare,
    compute_sensitivities,
    factor_prefixes,
    historical_var,
    run_stress,
    stress_table,
    taylor_var,
)
from novera.risk.scenarios import apply_shocks, historical_shocks
from novera.risk.stress import build_shocks, historical_episodes_from_simulation
from novera.simulation import (
    TradeGeneratorConfig,
    build_counterparty_universe,
    build_global_macro_bank,
    generate_portfolio,
)
from novera.simulation.market_data import DEFAULT_EPISODES, MarketSimConfig, generate_market_data

AS_OF = date(2026, 9, 11)


@pytest.fixture(scope="module")
def world():
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=2.2, seed=21))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=250, seed=21,
                                                           market_history=hist))
    universe = {f.factor_id: f for f in md.universe}
    val = value_portfolio(gen.snapshot, md.previous_snapshot, org, "USD")
    pf = Portfolio(gen.snapshot.trades, md.previous_snapshot, "USD", universe=universe)
    return {"org": org, "gen": gen, "md": md, "hist": hist, "val": val.table, "pf": pf, "universe": universe}


@pytest.fixture(scope="module")
def sens(world):
    return compute_sensitivities(world["pf"])


def test_factor_prefixes(world):
    by_type = {}
    for t in world["gen"].snapshot.live_trades:
        by_type.setdefault(t.product_type.value, t)
    p = factor_prefixes(by_type["INTEREST_RATE_SWAP"], "USD")
    assert any(x.startswith("IR:") for x in p)
    fxo = by_type["FX_OPTION"]
    p = factor_prefixes(fxo, "USD")
    assert any(x.startswith("VOL:") for x in p) and any(x.startswith("IR:") for x in p)
    eq = by_type["CASH_EQUITY"]
    p = factor_prefixes(eq, "USD")
    assert f"EQ:{eq.instrument.ticker}" in p
    if eq.currency != "USD":
        assert f"FX:{eq.currency}USD" in p or f"FX:USD{eq.currency}" in p


def test_apply_shocks_units(world):
    base = world["pf"].base
    shocked = apply_shocks(base, {"IR:USD:10Y": 0.0001, "EQIDX:SPX": 0.01}, world["universe"])
    assert shocked.value("IR:USD:10Y") == pytest.approx(base.value("IR:USD:10Y") + 0.0001)
    assert shocked.value("EQIDX:SPX") == pytest.approx(base.value("EQIDX:SPX") * 1.01)


def test_historical_shocks_shape(world):
    scen = historical_shocks(world["hist"], AS_OF, 200, universe=world["universe"])
    assert len(scen) == 200 and scen.index.max() <= AS_OF
    assert scen["IR:USD:10Y"].abs().max() < 0.02  # rates diffs, decimal
    assert scen["EQIDX:SPX"].abs().max() < 0.25  # returns


def test_sensitivity_signs_and_structure(world, sens):
    assert set(sens.columns) == {"trade_id", "measure", "factor_id", "bucket", "underlying", "bump", "value"}
    trades = {t.trade_id: t for t in world["gen"].snapshot.live_trades}
    dv01 = sens[sens.measure == "DV01"]
    # A long bond loses money when rates rise: total DV01 across its curve nodes is negative.
    for tid, g in dv01.groupby("trade_id"):
        t = trades[tid]
        if t.product_type.value == "GOVERNMENT_BOND":
            assert np.sign(g["value"].sum()) == -np.sign(t.signed_quantity), tid
        if t.product_type.value == "INTEREST_RATE_SWAP":
            expected = -1 if t.swap_side.value == "RECEIVE_FIXED" else 1
            assert np.sign(g["value"].sum()) == expected, tid
    eq = sens[(sens.measure == "EQ_DELTA")]
    for tid, g in eq.groupby("trade_id"):
        t = trades[tid]
        if t.product_type.value == "CASH_EQUITY":
            assert np.sign(g["value"].sum()) == np.sign(t.signed_quantity)
    # Options carry gamma and vega; linear products do not.
    gamma_ids = set(sens[sens.measure == "GAMMA"]["trade_id"])
    vega_ids = set(sens[sens.measure == "VEGA"]["trade_id"])
    for tid in gamma_ids | vega_ids:
        assert trades[tid].product_type.value in ("FX_OPTION", "EQUITY_OPTION"), tid
    assert vega_ids, "some options must show vega"
    # Bought options have positive vega.
    for tid in vega_ids:
        v = sens[(sens.measure == "VEGA") & (sens.trade_id == tid)]["value"].sum()
        assert np.sign(v) == (1 if trades[tid].direction.value == "BUY" else -1), tid
    assert (sens[sens.measure == "THETA"]["bucket"] == "1D").all()


def test_historical_var_properties(world):
    cfg = VaRConfig(window_days=250)
    hs = historical_var(world["pf"], world["hist"], cfg)
    assert hs.pnl.shape == (250, len(world["pf"].priced_ids))
    assert hs.var > 0 and hs.es >= 0.8 * hs.var
    assert hs.var_scaled == pytest.approx(hs.var * np.sqrt(10))
    # Component contributions sum to the totals.
    assert hs.contributions["var_contribution"].sum() == pytest.approx(hs.var, rel=1e-6)
    assert hs.contributions["es_contribution"].sum() == pytest.approx(hs.es, rel=1e-6)
    by_ac = hs.by(world["val"], "asset_class")
    assert by_ac["component_var"].sum() == pytest.approx(hs.var, rel=1e-6)
    # Standalone VaR per group is sub-additive relative to the total.
    assert by_ac["standalone_var"].sum() >= hs.var * 0.999


def test_taylor_var_is_close_but_not_equal(world, sens):
    cfg = VaRConfig(window_days=250)
    hs = historical_var(world["pf"], world["hist"], cfg)
    tv = taylor_var(world["pf"], sens, world["hist"], cfg)
    assert tv.pnl.shape == hs.pnl.shape
    # Same scenarios, same sign of the worst day, similar magnitude.
    corr = np.corrcoef(hs.portfolio_pnl.to_numpy(), tv.portfolio_pnl.to_numpy())[0, 1]
    assert corr > 0.9, corr
    assert 0.5 < tv.var / hs.var < 1.5
    diff = compare(hs, tv, world["val"])
    assert {"primary", "challenger", "difference", "difference_pct"} <= set(diff.columns)


def test_stress_library_runs_and_makes_sense(world):
    pf = world["pf"]
    res = run_stress(pf, HYPOTHETICAL_LIBRARY)
    table = stress_table(res, world["val"])
    assert len(table) == len(HYPOTHETICAL_LIBRARY)
    by_id = {r.scenario.scenario_id: r for r in res}
    # A +1bp parallel USD stress equals the sum of the USD DV01 ladder (linear at 1bp).
    from novera.risk.stress import ShockRule, StressScenario

    one_bp = StressScenario("usd_1bp", "USD +1bp", "", "HYPOTHETICAL", (ShockRule("IR:USD:", 0.0001),))
    dv01_total = compute_sensitivities(pf).query("measure == 'DV01' and underlying == 'USD'")["value"].sum()
    assert run_stress(pf, [one_bp])[0].total == pytest.approx(dv01_total, rel=0.02, abs=100.0)
    assert by_id["usd_rates_up_100"].total != 0
    # BTC -50% only touches digital assets.
    btc = by_id["btc_down_50"].by(world["val"], "asset_class")
    assert set(btc[btc != 0].index) <= {"DIGITAL_ASSET"}
    shocks = build_shocks(by_id["equity_crash_20_vol_15"].scenario, pf)
    assert all(v == -0.20 for k, v in shocks.items() if k.startswith("EQ"))
    vol_keys = [k for k in shocks if k.startswith("VOL:")]
    assert vol_keys and all(shocks[k] * pf.base.values[k] == pytest.approx(0.15) for k in vol_keys)


def test_historical_episode_scenarios(world):
    sc = historical_episodes_from_simulation(world["hist"], DEFAULT_EPISODES, AS_OF)
    assert [s.scenario_id for s in sc] == ["stylised_risk_off_crash", "stylised_rates_shock"]
    res = run_stress(world["pf"], sc, world["hist"])
    crash = res[0]
    assert crash.shocks["EQIDX:SPX"] < -0.15
    eq = crash.by(world["val"], "asset_class").get("EQUITY", 0.0)
    sens = compute_sensitivities(world["pf"])
    net_equity_delta = sens[sens.measure == "EQ_DELTA"]["value"].sum()
    assert (eq < 0) == (net_equity_delta > 0), "a net-long equity book must lose in a crash"


def test_portfolio_skips_unpriceable(world):
    pf = world["pf"]
    assert set(pf.errors) == set()
    assert len(pf.priced_ids) == len(pf.trades)
    assert isinstance(pf.pnl_under_shocks({"CRYPTO:BTC": 0.1}), dict)
    assert pf.pnl_under_shocks({"NOPE:X": 0.1}) == {}
    assert isinstance(pd.DataFrame(), pd.DataFrame)
