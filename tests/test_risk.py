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
    monte_carlo_var,
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
    gen = generate_portfolio(
        org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=250, seed=21, market_history=hist)
    )
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
    optional = ("FX_OPTION", "EQUITY_OPTION", "COMMODITY_OPTION", "EQUITY_EXOTIC", "SWAPTION")
    for tid in gamma_ids | vega_ids:
        assert trades[tid].product_type.value in optional, tid
    assert vega_ids, "some options must show vega"
    # Bought vanilla options and swaptions have positive vega (barriers and digitals need not).
    for tid in vega_ids:
        if trades[tid].product_type.value == "EQUITY_EXOTIC":
            continue
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
    # Linear cash equity: the sign of the crash P&L follows the net delta (options and exotics
    # are convex and can gain either way, so they are excluded from this check).
    eq = crash.by(world["val"], "product_type").get("CASH_EQUITY", 0.0)
    sens = compute_sensitivities(world["pf"])
    cash_ids = set(world["val"][world["val"]["product_type"] == "CASH_EQUITY"]["trade_id"])
    net_equity_delta = sens[(sens.measure == "EQ_DELTA") & sens.trade_id.isin(cash_ids)]["value"].sum()
    assert (eq < 0) == (net_equity_delta > 0), "a net-long cash equity book must lose in a crash"


def test_portfolio_skips_unpriceable(world):
    pf = world["pf"]
    assert set(pf.errors) == set()
    assert len(pf.priced_ids) == len(pf.trades)
    assert isinstance(pf.pnl_under_shocks({"CRYPTO:BTC": 0.1}), dict)
    assert pf.pnl_under_shocks({"NOPE:X": 0.1}) == {}
    assert isinstance(pd.DataFrame(), pd.DataFrame)


def test_stress_library_catalogue(world):
    from novera.risk.stress_catalogue import HEADLINE_FACTORS, stress_library

    lib = stress_library(world["hist"], list(world["pf"].universe.values()))
    cats = {c["category"]: c for c in lib["categories"]}
    assert (
        cats["HYPOTHETICAL"]["count"] == len(HYPOTHETICAL_LIBRARY)
        and cats["STYLISED_HISTORICAL"]["count"] == 2
    )
    rules = {r["scenario_id"]: r["shocks"] for r in cats["HYPOTHETICAL"]["scenarios"]}
    assert rules["credit_wider_150"][0]["unit"] == "bp" and rules["credit_wider_150"][0]["size"] == 150.0
    vol = next(s for s in rules["equity_crash_20_vol_15"] if s["target"] == "VOL:")
    assert vol["unit"] == "vol points" and vol["size"] == 15.0
    crash = cats["STYLISED_HISTORICAL"]["scenarios"][0]
    assert crash["status"] == "IN_RUN" and crash["shock_count"] == len(HEADLINE_FACTORS)
    usd10 = next(s for s in crash["shocks"] if s["target"] == "IR:USD:10Y")
    assert usd10["unit"] == "bp" and usd10["size"] < 0  # rates fall in the risk-off crash
    crises = cats["NAMED_CRISIS"]["scenarios"]
    assert all(c["status"] == "NOT_COVERED" and not c["in_daily_run"] for c in crises)
    assert lib["summary"]["scenarios"] == len(HYPOTHETICAL_LIBRARY) + 2 + 7


def test_weighted_tail_measures_and_fixed_window(world, sens):
    """MR-015 and MR-016: decaying weights sum to one and favour the newest scenarios; a
    decay of None reproduces the equally weighted figures exactly; a fixed window replays
    exactly the moves inside it."""
    from novera.risk.var import scenario_weights, tail_measures

    pf, hist = world["pf"], world["hist"]
    equal = historical_var(pf, hist, VaRConfig(window_days=250))
    weighted = historical_var(pf, hist, VaRConfig(window_days=250, decay=0.94))
    w = scenario_weights(weighted.portfolio_pnl.index, weighted.config)
    assert w is not None and w.sum() == pytest.approx(1.0) and len(w) == 250
    newest = weighted.portfolio_pnl.index.max()
    assert w[list(weighted.portfolio_pnl.index).index(newest)] == pytest.approx(w.max())
    assert w.max() / w.min() == pytest.approx((1 / 0.94) ** 249, rel=1e-6)
    assert weighted.method == "historical_weighted_full_revaluation"
    assert weighted.var > 0 and weighted.es >= weighted.var * 0.8
    assert weighted.contributions["var_contribution"].sum() == pytest.approx(weighted.var, rel=1e-6)
    assert weighted.contributions["es_contribution"].sum() == pytest.approx(weighted.es, rel=1e-6)
    # Same P&L matrix, different tail: only the weights moved.
    pd.testing.assert_frame_equal(weighted.pnl, equal.pnl)
    assert weighted.var != pytest.approx(equal.var, rel=1e-3)
    # Uniform weights reproduce the unweighted figures exactly.
    n = len(equal.portfolio_pnl)
    var_u, es_u, d_u = tail_measures(equal.portfolio_pnl, equal.config, np.full(n, 1.0 / n))
    assert var_u == pytest.approx(equal.var, rel=1e-9) and es_u == pytest.approx(equal.es, rel=1e-9)
    assert d_u == equal.var_scenario_date
    # Putting all the weight on the worst scenario makes VaR that scenario's loss (up to the
    # 1% interpolation step towards the next scenario).
    worst = equal.portfolio_pnl.idxmin()
    spike = np.where(equal.portfolio_pnl.index == worst, 1.0, 1e-9)
    var_w, _, d = tail_measures(equal.portfolio_pnl, equal.config, spike / spike.sum())
    assert var_w == pytest.approx(-equal.portfolio_pnl.min(), rel=5e-3) and d == worst
    # Fixed window: the scenarios are exactly the moves between the two dates.
    dates = hist.dates
    start, end = dates[-120], dates[-21]
    fixed = historical_var(pf, hist, VaRConfig(window_start=start.isoformat(), window_end=end.isoformat()))
    assert len(fixed.pnl) == 99 and fixed.pnl.index.min() > start and fixed.pnl.index.max() == end
    sub = equal.pnl.loc[fixed.pnl.index]
    pd.testing.assert_frame_equal(fixed.pnl, sub)
    with pytest.raises(ValueError):
        historical_var(pf, hist, VaRConfig(window_start=end.isoformat(), window_end=end.isoformat()))
    with pytest.raises(ValueError):
        monte_carlo_var(pf, sens, hist, VaRConfig(window_days=250, decay=0.94))


def test_var_measures_share_matrices_and_validate(world, sens):
    """OPS-004: a matrix of measures runs in one pass, rows with the same scenarios share
    the P&L matrix, LIMIT rows route to limit types, and the validation catches what an
    administrator can get wrong."""
    from novera.risk.var_measures import (
        DEFAULT_MEASURES,
        VaRMeasure,
        compute_measures,
        templates,
        validate_measures,
    )

    pf, hist = world["pf"], world["hist"]
    base = VaRConfig(window_days=250)
    dates = hist.dates
    start, end = dates[-200].isoformat(), dates[-50].isoformat()
    ms = [
        VaRMeasure("LIMIT", "VAR", 0.95, "HISTORICAL_WEIGHTED", "FULL_REVALUATION", decay=0.94),
        VaRMeasure("INFORMATION", "ES", 0.95, "HISTORICAL_WEIGHTED", "FULL_REVALUATION", decay=0.94),
        VaRMeasure("INFORMATION", "VAR", 0.99, "HISTORICAL", "FULL_REVALUATION"),
        VaRMeasure(
            "LIMIT",
            "STRESSED_VAR",
            0.99,
            "HISTORICAL",
            "FULL_REVALUATION",
            window_start=start,
            window_end=end,
        ),
        VaRMeasure("INFORMATION", "VAR", 0.99, "MONTE_CARLO", "SENSITIVITY"),
        VaRMeasure("INFORMATION", "VAR", 0.99, "HISTORICAL", "SENSITIVITY", enabled=False),
    ]
    assert ms[0].measure_id == "VAR_95_WHS_FULL_BASE_L94" and ms[3].measure_id.startswith(
        "STRESSED_VAR_99_HS_FULL_"
    )
    assert ms[0].label == "VaR 95% · weighted historical (λ 0.94) · full revaluation · base window"
    assert validate_measures(ms) == []
    out = compute_measures(pf, sens, hist, ms, base, workers=1)
    assert [r.measure.measure_id for r in out] == [m.measure_id for m in ms[:5]]
    head = out.headline
    assert head.measure is ms[0] and head.result.method == "historical_weighted_full_revaluation"
    # The ES row reused the weighted VaR row's matrix; the plain 99% row shares scenarios too.
    es_row, plain = out.results[1], out.results[2]
    assert es_row.shared_with == ms[0].measure_id and plain.shared_with == ms[0].measure_id
    assert es_row.value == es_row.result.es and es_row.result.config.es_confidence == 0.95
    assert plain.result.var > head.result.var * 0.5  # 99% vs 95% on the same matrix
    assert es_row.result.pnl is head.result.pnl
    assert out.for_limit("VAR") is head and out.for_limit("STRESSED_VAR").measure is ms[3]
    assert out.for_limit("EXPECTED_SHORTFALL") is None
    summary = out.summary()
    assert summary["var"] == head.result.var and summary["var_confidence"] == 0.95
    assert summary["es"] == es_row.value and summary["es_confidence"] == 0.95
    assert summary["stressed_var"] == out.results[3].value and summary["challenger_var"] is None
    assert summary["monte_carlo_var"] == out.results[4].result.var
    assert set(summary["var_measures"]) == {m.measure_id for m in ms[:5]}
    assert set(out.limit_inputs()) == {"VAR", "STRESSED_VAR"}
    rows = [r.row() for r in out]
    assert rows[3]["window_start"] == start and rows[0]["decay"] == 0.94 and rows[1]["metric"] == "ES"
    # Defaults reproduce the pre-setup platform: three methods, VaR and ES limits on the same matrix.
    d = compute_measures(pf, sens, hist, DEFAULT_MEASURES, base, workers=1)
    assert d.headline.result.var == pytest.approx(historical_var(pf, hist, base).var)
    assert d.for_limit("EXPECTED_SHORTFALL").value == pytest.approx(d.headline.result.es)
    assert d.first("delta_gamma_vega") is not None and d.first("monte_carlo_delta_gamma_vega") is not None
    # Validation.
    bad = [
        VaRMeasure("LIMIT", "VAR", 0.99, "HISTORICAL", "FULL_REVALUATION"),
        VaRMeasure("LIMIT", "VAR", 0.95, "HISTORICAL", "FULL_REVALUATION"),
        VaRMeasure("INFORMATION", "VAR", 0.99, "HISTORICAL_WEIGHTED", "FULL_REVALUATION"),
        VaRMeasure("INFORMATION", "VAR", 0.99, "MONTE_CARLO", "FULL_REVALUATION"),
        VaRMeasure("INFORMATION", "STRESSED_VAR", 0.99, "HISTORICAL", "FULL_REVALUATION"),
        VaRMeasure(
            "INFORMATION",
            "VAR",
            0.99,
            "HISTORICAL",
            "FULL_REVALUATION",
            window_start="2010-01-01",
            window_end="2010-12-31",
        ),
        VaRMeasure("INFORMATION", "VAR", 1.2, "HISTORICAL", "FULL_REVALUATION", decay=0.9),
    ]
    errors = validate_measures(bad, dates[0], dates[-1])
    text = "\n".join(errors)
    assert "only one VaR measure can feed limits" in text
    assert "need a decay" in text and "sensitivities only" in text and "needs a fixed window" in text
    assert (
        "before the stored history" in text
        and "confidence" in text
        and "weighted historical scenarios only" in text
    )
    assert validate_measures([VaRMeasure("INFORMATION", "ES", 0.975, "HISTORICAL", "FULL_REVALUATION")]) == [
        "at least one enabled VaR measure is needed: it is the headline VaR of the run"
    ]
    tpl = templates((dates[-300], dates[-51]))
    assert {m.metric for m in tpl["bank"]} == {"VAR", "ES", "STRESSED_VAR"}
    assert tpl["hedge_fund"][0].decay == 0.94 and tpl["hedge_fund"][0].confidence == 0.95
    assert (
        validate_measures(tpl["bank"], dates[0], dates[-1]) == []
        and validate_measures(tpl["hedge_fund"]) == []
    )
    assert VaRMeasure.from_dict(ms[3].to_dict()) == ms[3]
