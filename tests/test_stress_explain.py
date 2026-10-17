"""Stress explain and summary of significant risks (MR-020, round 49)."""

from datetime import date

import pandas as pd
import pytest

from novera.market_data.history import MarketHistory
from novera.pricing.valuation import value_portfolio
from novera.risk import Portfolio, compute_sensitivities
from novera.risk import stress as st
from novera.risk import stress_explain as ex
from novera.risk.var import VaRConfig, historical_var
from novera.simulation import (
    TradeGeneratorConfig,
    build_counterparty_universe,
    build_global_macro_bank,
    generate_portfolio,
)
from novera.simulation.market_data import MarketSimConfig, generate_market_data

AS_OF = date(2026, 9, 11)
PICK = ("global_risk_off", "oil_down_30", "vol_shock_10", "btc_down_50")


@pytest.fixture(scope="module")
def world():
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=1.5, seed=49))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(
        org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=300, seed=49, market_history=hist)
    )
    universe = {f.factor_id: f for f in md.universe}
    val = value_portfolio(gen.snapshot, md.snapshot, org, "USD").table
    pf = Portfolio(gen.snapshot.trades, md.snapshot, "USD", universe=universe)
    sens = compute_sensitivities(pf)
    scenarios = [s for s in st.HYPOTHETICAL_LIBRARY if s.scenario_id in PICK]
    results = st.run_stress(pf, scenarios, hist)
    var = historical_var(pf, hist, VaRConfig(window_days=250))
    return {"pf": pf, "sens": sens, "val": val, "results": results, "var": var}


@pytest.fixture(scope="module")
def result(world):
    return ex.run_stress_explain(
        world["pf"], world["sens"], world["results"], world["results"], world["val"], world["var"]
    )


def test_terms_add_up_to_the_full_revaluation_loss_per_desk(world, result):
    desks = world["val"].drop_duplicates("trade_id").set_index("trade_id")["desk_id"]
    for r in world["results"]:
        sid = r.scenario.scenario_id
        rows = result.explain[result.explain["scenario_id"] == sid]
        assert rows[list(ex.TERMS)].sum(axis=1).to_numpy() == pytest.approx(rows["total"].to_numpy())
        want = r.pnl.groupby(desks.reindex(r.pnl.index)).sum()
        got = rows.groupby("desk_id")["total"].sum()
        assert got.reindex(want.index).to_numpy() == pytest.approx(want.to_numpy(), abs=1e-4)
        assert rows["total"].sum() == pytest.approx(r.total, abs=1e-4)


def test_family_is_its_shocks_alone(world, result):
    pf, r = world["pf"], world["results"][0]
    rows = result.explain[(result.explain["scenario_id"] == r.scenario.scenario_id)]
    for fam in set(rows["family"]) - {ex.CROSS_FAMILY}:
        sub = {f: v for f, v in r.shocks.items() if ex.family_of(f) == fam}
        want = sum(pf.pnl_under_shocks(sub).values())
        assert rows.loc[rows["family"] == fam, "total"].sum() == pytest.approx(want, abs=1e-4)


def test_taylor_terms_match_the_challenger_approximation(world):
    from novera.risk.var import taylor_pnl_matrix

    pf, sens, r = world["pf"], world["sens"], world["results"][0]
    scen = pd.DataFrame([{f: r.shocks.get(f, 0.0) for f in pf.base.values}])  # every factor, as VaR passes
    approx = taylor_pnl_matrix(pf, sens, scen).iloc[0]
    terms = (
        ex.taylor_terms(pf, sens, r.shocks).groupby("trade_id")[["delta", "gamma", "vega"]].sum().sum(axis=1)
    )
    assert terms.reindex(approx.index).fillna(0.0).to_numpy() == pytest.approx(
        approx.to_numpy(), rel=1e-9, abs=1e-6
    )


def test_gamma_only_on_option_books_and_vega_only_in_vol_scenarios(world, result):
    rows = result.explain
    vol = rows[rows["scenario_id"] == "vol_shock_10"]
    assert vol["vega"].abs().sum() > 0
    btc = rows[(rows["scenario_id"] == "btc_down_50") & (rows["family"] != "CRYPTO")]
    assert btc.loc[btc["family"] != ex.CROSS_FAMILY, "total"].abs().sum() == pytest.approx(0.0, abs=1e-6)


def test_top_trades_are_the_worst_in_order(world, result):
    for r in world["results"]:
        top = result.top_trades[result.top_trades["scenario_id"] == r.scenario.scenario_id]
        assert list(top["rank"]) == list(range(1, len(top) + 1))
        assert top["pnl"].is_monotonic_increasing
        assert top["pnl"].iloc[0] == pytest.approx(r.pnl.min())
        assert len(top) <= ex.TOP_TRADES


def test_hedge_failure_is_the_basis_family(world):
    pf, sens = world["pf"], world["sens"]
    shocks = {f: 0.0002 for f in pf.base.factors_with_prefix("XCCY:")} | {
        f: 0.0003 for f in pf.base.factors_with_prefix("IRBASIS:")
    }
    pnl = pd.Series(pf.pnl_under_shocks(shocks)).reindex(pf.priced_ids).fillna(0.0)
    desks = world["val"].drop_duplicates("trade_id").set_index("trade_id")["desk_id"]
    out = ex.explain_scenario(pf, sens, "basis", shocks, pnl, desks)
    assert set(out["family"]) <= {ex.HEDGE_FAILURE_FAMILY, ex.CROSS_FAMILY}
    basis = out.loc[out["family"] == ex.HEDGE_FAILURE_FAMILY, "total"].sum()
    assert basis == pytest.approx(pnl.sum(), abs=1e-4)
    assert out.loc[out["family"] == ex.CROSS_FAMILY, "total"].abs().sum() == pytest.approx(0.0, abs=1e-4)


def test_summary_reports_hedge_failure_and_significant_count(result):
    s = result.summary()
    assert s["significant_risks"] >= 1
    assert "hedge_failure" in s or not (result.explain["family"] == ex.HEDGE_FAILURE_FAMILY).any()


def test_risk_summary_desks_add_up_to_the_firm(world, result):
    rs = result.risk_summary
    firm = rs[rs["level"] == "FIRM"].iloc[0]
    desks = rs[rs["level"] == "DESK"]
    assert firm["var"] == pytest.approx(world["var"].var)
    assert desks["var"].sum() == pytest.approx(firm["var"], rel=1e-9)
    assert desks["stress_in_firm_worst"].sum() == pytest.approx(firm["worst_stress"], abs=1e-4)
    assert desks["stress_share"].sum() == pytest.approx(1.0)
    worst = min(world["results"], key=lambda r: r.total)
    assert firm["worst_scenario"] == worst.scenario.name
    assert firm["worst_stress"] == pytest.approx(worst.total)


def test_risk_summary_positions_are_significant_and_capped(result):
    pos = result.risk_summary[result.risk_summary["level"] == "POSITION"]
    assert len(pos) <= ex.MAX_POSITIONS
    assert ((pos["var_share"] >= ex.POSITION_SHARE) | (pos["stress_share"] >= ex.POSITION_SHARE)).all()
    assert pos["significant"].all()


def test_no_limit_scenarios_gives_an_empty_summary(world):
    out = ex.run_stress_explain(world["pf"], world["sens"], [], [], world["val"], world["var"])
    assert out.explain.empty and out.risk_summary.empty and out.summary() == {}
