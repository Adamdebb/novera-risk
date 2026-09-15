"""Hedge-fund face: template, modules and the fund EOD branch."""

from datetime import date

import pandas as pd
import pytest

from novera.fund import run_fund
from novera.fund.modules import (
    DEFAULT_REDEMPTION_SCENARIOS,
    RedemptionScenario,
    _next_dealing,
    factor_betas,
    redemption_stress,
)
from novera.market_data.history import MarketHistory
from novera.risk import VaRConfig
from novera.simulation import (
    FUND_TEMPLATE,
    TradeGeneratorConfig,
    build_fund,
    build_fund_counterparties,
    build_fund_limits,
    build_multi_strategy_fund,
    generate_portfolio,
)
from novera.simulation.fund import DealingFrequency
from novera.simulation.market_data import MarketSimConfig, generate_market_data
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.eod import EODConfig, run_eod

AS_OF = date(2026, 9, 11)


@pytest.fixture(scope="module")
def fund_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("fund") / "fund.duckdb"
    org = build_multi_strategy_fund()
    cp = build_fund_counterparties(org)
    fund = build_fund()
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=1.0, seed=51))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(AS_OF, 200, 51, True, hist, FUND_TEMPLATE))
    runs_dir = tmp_path_factory.mktemp("runs")
    with DuckDBRepository(path) as repo:
        repo.init_schema()
        repo.save_organisation(org)
        repo.save_counterparties(cp.counterparties)
        repo.save_netting_sets(cp.netting_sets, cp.csas)
        repo.save_fund(fund)
        repo.save_limits(build_fund_limits(org, cp, fund))
        repo.save_portfolio_snapshot(gen.snapshot)
        repo.save_risk_factors(md.universe)
        repo.save_market_history(md.history)
        repo.save_market_snapshot(md.previous_snapshot)
        repo.save_market_snapshot(md.snapshot)
        res = run_eod(
            repo,
            EODConfig(
                firm_id="MSF",
                counterparty=True,
                exposure_paths=20,
                regulatory=True,
                var=VaRConfig(window_days=150),
                workers=1,
            ),
            runs_dir=runs_dir,
        )
    return {"path": str(path), "run_id": res.run.run_id, "runs_dir": runs_dir, "gen": gen, "fund": fund}


def test_fund_template_and_injections(fund_db):
    gen, fund = fund_db["gen"], fund_db["fund"]
    assert {i.name for i in gen.injections} == {
        "crowded_single_name",
        "pb_concentration",
        "illiquid_vs_redemptions",
        "short_vol",
    }
    assert abs(fund.share_check - 1.0) < 1e-9 and len(fund.prime_brokers) == 4
    bil = [t for t in gen.snapshot.trades if t.clearing.value == "BILATERAL"]
    assert all(t.counterparty_id.startswith("PB_") for t in bil)
    with DuckDBRepository(fund_db["path"], read_only=True) as repo:
        assert repo.load_fund("MSF").nav == fund.nav
        assert repo.load_fund("NOPE") is None


def test_dealing_dates_and_redemptions(fund_db):
    assert _next_dealing(date(2026, 9, 11), DealingFrequency.MONTHLY, 30) == date(2026, 10, 31)
    assert _next_dealing(date(2026, 9, 11), DealingFrequency.QUARTERLY, 90) == date(2026, 12, 31)
    liq = pd.DataFrame(
        {"trade_id": ["a", "b", "c"], "pv": [1e9, 5e8, 5e8], "days_to_liquidate": [1.0, 3.0, 40.0]}
    )
    out = redemption_stress(fund_db["fund"], AS_OF, liq, DEFAULT_REDEMPTION_SCENARIOS)
    assert set(out["scenario"]) == {s.name for s in DEFAULT_REDEMPTION_SCENARIOS}
    assert (out["coverage"].dropna() > 0).all() and (out["gated"] >= 0).all()
    harsh = redemption_stress(fund_db["fund"], AS_OF, liq, (RedemptionScenario("everything", 1.0, 0.5),))
    assert harsh["gated"].iloc[0] > 0  # gates bite when everyone redeems


def test_factor_betas_recover_a_known_loading():
    idx = pd.bdate_range("2025-01-01", periods=300).date
    rng = pd.Series(range(300))
    import numpy as np

    r = np.random.default_rng(0)
    f1, f2 = r.standard_normal(300), r.standard_normal(300)
    moves = pd.DataFrame({"EQIDX:SPX": f1 * 0.01, "IR:USD:10Y": f2 * 0.0005}, index=idx)
    pnl = pd.DataFrame({"S": 3e6 * f1 + 1e5 * r.standard_normal(300)}, index=idx)
    b = factor_betas(pnl, moves, 2e9)
    spx = b[b["factor"] == "EQIDX:SPX"].iloc[0]
    assert spx["beta_per_sigma"] == pytest.approx(3e6, rel=0.05) and spx["t_stat"] > 20 and spx["r2"] > 0.95
    assert abs(b[b["factor"] == "IR:USD:10Y"].iloc[0]["t_stat"]) < 3
    _ = rng


def test_fund_run_outputs(fund_db):
    with DuckDBRepository(fund_db["path"]) as repo:
        run = repo.load_run(fund_db["run_id"])
        assert "fund" in run.summary and "regulatory" not in run.summary, "fund face skips bank capital"
        fr = run_fund(repo, fund_db["run_id"], runs_dir=fund_db["runs_dir"], persist=False)
        t = fr.exposures["totals"]
        assert t["gross"] == pytest.approx(t["long"] + t["short"]) and t["gross_leverage"] > 0.5
        assert fr.margin["total_margin"] > 0 and 0 < fr.margin["largest_pb_share"] <= 1
        assert set(fr.margin["by_pb"]["prime_broker"]) <= {"PB_GS", "PB_MS", "PB_JPM", "PB_BARC"}
        assert not fr.factors.empty and {"beta_per_sigma", "t_stat", "r2"} <= set(fr.factors.columns)
        assert not fr.redemptions.empty and (fr.redemptions["shortfall"] >= 0).all()
        assert fr.attribution["share_of_var"].sum() == pytest.approx(1.0, abs=1e-6)
        assert any("NVDA" in f for f in fr.crowding["flags"]), fr.crowding["flags"]
        lt = repo.load_run_frame(fund_db["run_id"], "limits")
        assert {"LEVERAGE", "MARGIN_USAGE", "PB_CONCENTRATION"} <= set(lt["limit_type"])
        assert lt[lt["limit_type"] == "LEVERAGE"]["current"].iloc[0] == pytest.approx(
            t["gross_leverage"], rel=1e-6
        )
        assert not repo.load_run_frame(fund_db["run_id"], "fund_summary").empty
