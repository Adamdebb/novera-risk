"""Monte Carlo VaR, backtesting, concentration, liquidity and the risk pack."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from novera.market_data.history import MarketHistory
from novera.reporting import build_pack
from novera.risk import (
    MonteCarloConfig,
    Portfolio,
    VaRConfig,
    compute_sensitivities,
    concentration,
    historical_var,
    liquidity,
    monte_carlo_var,
    static_backtest,
)
from novera.risk.backtest import christoffersen_independence, kupiec_pof, traffic_light
from novera.risk.concentration import hhi, top_share
from novera.risk.monte_carlo import simulate_factor_moves
from novera.simulation import (
    TradeGeneratorConfig,
    build_counterparty_universe,
    build_global_macro_bank,
    build_limits,
    generate_portfolio,
)
from novera.simulation.market_data import MarketSimConfig, generate_market_data
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.eod import EODConfig, run_eod

AS_OF = date(2026, 9, 11)


@pytest.fixture(scope="module")
def world():
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=2.2, seed=23))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=150, seed=23,
                                                           market_history=hist))
    universe = {f.factor_id: f for f in md.universe}
    from novera.pricing.valuation import value_portfolio
    val = value_portfolio(gen.snapshot, md.snapshot, org, "USD").table
    pf = Portfolio(gen.snapshot.trades, md.snapshot, "USD", universe=universe)
    sens = compute_sensitivities(pf)
    hs = historical_var(pf, hist, VaRConfig(window_days=500))
    return {"pf": pf, "sens": sens, "hist": hist, "hs": hs, "val": val, "org": org, "cp": cp, "md": md,
            "gen": gen}


def test_monte_carlo_reproduces_sample_covariance_and_is_seeded(world):
    from novera.risk.scenarios import historical_shocks
    pf, hist = world["pf"], world["hist"]
    shocks = historical_shocks(hist, pf.as_of, 500, 1, pf.universe, factor_ids=["EQIDX:SPX", "IR:USD:10Y",
                                                                                    "FX:EURUSD"])
    sims = simulate_factor_moves(shocks, MonteCarloConfig(paths=50_000, seed=1))
    assert sims.shape == (50_000, 3)
    np.testing.assert_allclose(sims.cov().to_numpy(), shocks.cov().to_numpy(), rtol=0.08, atol=1e-9)
    again = simulate_factor_moves(shocks, MonteCarloConfig(paths=50_000, seed=1))
    assert np.array_equal(sims.to_numpy(), again.to_numpy())
    mc = monte_carlo_var(pf, world["sens"], hist, VaRConfig(window_days=500), MonteCarloConfig(paths=4000))
    assert mc.method == "monte_carlo_delta_gamma_vega" and mc.var > 0 and mc.pnl.shape[0] == 4000
    assert mc.contributions["var_contribution"].sum() == pytest.approx(mc.var, rel=1e-6)
    assert 0.3 < mc.var / world["hs"].var < 1.5


def test_backtest_statistics():
    lr, p = kupiec_pof(3, 250, 0.99)
    assert lr < 1 and p > 0.5
    lr_bad, p_bad = kupiec_pof(15, 250, 0.99)
    assert lr_bad > 10 and p_bad < 0.01
    hits = np.zeros(250, dtype=bool)
    hits[[10, 11, 12, 100]] = True  # clustered
    lr_c, p_c = christoffersen_independence(hits)
    assert lr_c > 3 and p_c < 0.1
    spread = np.zeros(250, dtype=bool)
    spread[[10, 80, 150, 220]] = True
    _, p_s = christoffersen_independence(spread)
    assert p_s > 0.5
    assert traffic_light(4, 250) == "GREEN" and traffic_light(7, 250) == "AMBER" and traffic_light(10, 250) == "RED"
    assert traffic_light(2, 125) == "GREEN" and traffic_light(5, 125) == "RED"


def test_static_backtest_on_portfolio(world):
    bt = static_backtest(world["hs"].portfolio_pnl, 0.99, test_days=250, lookback=250)
    assert bt.days == 250 and bt.lookback_days == 250 and bt.kind == "STATIC_HYPOTHETICAL"
    assert (bt.series["var"] > 0).all()
    assert bt.exceptions == int((bt.series["pnl"] < -bt.series["var"]).sum())
    assert bt.zone in ("GREEN", "AMBER", "RED") and 0 <= bt.kupiec_pvalue <= 1
    short = static_backtest(world["hs"].portfolio_pnl.iloc[:300], 0.99)
    assert short.days == 50


def test_concentration_measures(world):
    s = pd.Series({"a": 50, "b": 30, "c": 20})
    assert hhi(s) == pytest.approx(0.38) and top_share(s, 1) == pytest.approx(0.5)
    assert hhi(pd.Series({"a": 0, "b": 0})) == 0.0
    rep = concentration(world["val"], world["hs"].contributions, world["sens"])
    dims = set(rep.by_dimension["dimension"])
    assert {"trade_id", "desk_id", "counterparty_id", "factor:DV01"} <= dims
    assert ((rep.by_dimension["hhi"] > 0) & (rep.by_dimension["hhi"] <= 1)).all()
    assert (rep.by_dimension["top1_share"] <= rep.by_dimension["top5_share"]).all()
    assert rep.top_positions["share_of_var"].abs().max() <= 1.0 + 1e-9
    usd = rep.tenor[rep.tenor["currency"] == "USD"]
    assert usd["share_of_abs_dv01"].sum() == pytest.approx(1.0)


def test_liquidity_measures(world):
    far = {t.trade_id for t in world["gen"].snapshot.trades if t.product_type.value == "COMMODITY_FUTURE"
           and (t.instrument.expiry_date - AS_OF).days > 300}
    rep = liquidity(world["val"], world["hs"].var, far)
    assert rep.liquidity_adjusted_var >= rep.var and rep.bidask_cost > 0
    assert rep.by_bucket["share_of_abs_pv"].sum() == pytest.approx(1.0)
    assert (rep.by_trade["days_to_liquidate"] >= 0).all()
    assert set(rep.by_bucket["horizon_bucket"].astype(str)) == {"<= 1 day", "1-5 days", "5-10 days", "> 10 days"}
    brent = rep.by_trade[rep.by_trade["trade_id"].isin(far)]
    if len(brent):
        assert brent["days_to_liquidate"].max() > 1


def test_risk_pack_builds(world, tmp_path):
    path = tmp_path / "pack.duckdb"
    org, cp, md, gen = world["org"], world["cp"], world["md"], world["gen"]
    with DuckDBRepository(path) as repo:
        repo.init_schema()
        repo.save_organisation(org)
        repo.save_counterparties(cp.counterparties)
        repo.save_netting_sets(cp.netting_sets, cp.csas)
        repo.save_limits(build_limits(org, cp))
        repo.save_portfolio_snapshot(gen.snapshot)
        repo.save_risk_factors(md.universe)
        repo.save_market_history(md.history)
        repo.save_market_snapshot(md.previous_snapshot)
        repo.save_market_snapshot(md.snapshot)
        res = run_eod(repo, EODConfig(var=VaRConfig(window_days=300), workers=1), runs_dir=tmp_path / "runs")
        files = build_pack(repo, res.run.run_id, tmp_path / "reports", pdf=False)
    assert files.html.exists() and files.xlsx.exists() and files.pdf is None
    html = files.html.read_text()
    assert res.run.run_id in html and "monte_carlo_delta_gamma_vega" in html and "Backtest" in html
    sheets = pd.ExcelFile(files.xlsx).sheet_names
    assert {"Summary", "VaR", "Backtest", "Concentration", "Liquidity buckets", "Flags"} <= set(sheets)
