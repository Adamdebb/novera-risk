from datetime import date

import pytest

from novera.market_data.history import MarketHistory
from novera.risk import VaRConfig
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
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("eod") / "eod.duckdb"
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=1.2, seed=77))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=150, seed=77,
                                                           market_history=hist))
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
    return path


@pytest.fixture(scope="module")
def result(db_path, tmp_path_factory):
    with DuckDBRepository(db_path) as repo:
        return run_eod(repo, EODConfig(counterparty=False, var=VaRConfig(window_days=200), workers=1),
                       runs_dir=tmp_path_factory.mktemp("runs"))


def test_run_record_and_verdict(result):
    r = result.run
    assert r.status == "COMPLETED" and r.run_id.startswith("run_")
    assert r.verdict == "AMBER", [f.code for f in result.dq.findings]
    codes = {f.code for f in result.dq.findings}
    assert {"MD_MISSING_FACTOR", "MD_STALE_FACTOR", "TRADE_INVALID", "TRADE_UNKNOWN_BOOK",
            "TRADE_UNKNOWN_COUNTERPARTY"} <= codes
    missing = next(f for f in result.dq.findings if f.code == "MD_MISSING_FACTOR")
    assert missing.subject == "IR:USD:7Y" and missing.affected_trade_ids
    assert {"valuation", "sensitivities", "var", "stress", "pnl_attribution", "monte_carlo", "backtest",
            "concentration", "liquidity"} <= set(r.model_versions)
    assert r.summary["monte_carlo_var"] > 0 and r.summary["backtest_zone"] in ("GREEN", "AMBER", "RED")
    assert r.summary["liquidity_adjusted_var"] >= r.summary["var"]
    assert r.summary["var"] > 0 and r.summary["breaches"] >= 1
    assert "var" in r.timings and "persist" in r.timings


def test_pnl_waterfall_is_exact(result):
    pnl = result.pnl
    steps = pnl.steps.set_index("step")["pnl"]
    assert steps.sum() == pytest.approx(pnl.total)
    # For trades present both days, steps telescope exactly from yesterday's PV to today's.
    by_trade = pnl.by_trade.groupby("trade_id")["pnl"].sum()
    common = [t for t in by_trade.index if t in result.pnl.challenger["trade_id"].values]
    assert len(common) > 50
    # Missing USD 7Y node shows up in the DATA step for USD rates trades, never silently.
    assert steps["DATA"] != 0.0
    ch = pnl.challenger.dropna()
    assert len(ch) > 50
    corr = ch[["actual", "predicted"]].corr().iloc[0, 1]
    assert corr > 0.8, corr


def test_results_persisted_and_reloadable(result, db_path):
    rid = result.run.run_id
    with DuckDBRepository(db_path) as repo:
        loaded = repo.load_run(rid)
        assert loaded.summary["var"] == pytest.approx(result.run.summary["var"])
        assert repo.latest_run().run_id == rid
        val = repo.load_run_frame(rid, "valuation")
        assert len(val) == len(result.valuation)
        assert len(repo.load_run_frame(rid, "sensitivities")) == len(result.sensitivities)
        summary = repo.load_run_frame(rid, "var_summary")
        assert set(summary["method"]) == {"historical_full_revaluation", "delta_gamma_vega",
                                          "monte_carlo_delta_gamma_vega"}
        assert len(repo.load_run_frame(rid, "backtest_summary")) == 2
        assert not repo.load_run_frame(rid, "concentration").empty
        assert not repo.load_run_frame(rid, "liquidity_buckets").empty
        assert len(repo.load_run_frame(rid, "stress_summary")) == len(result.stress)
        assert (repo.load_run_frame(rid, "limits")["status"] == "BREACH").sum() == result.run.summary["breaches"]
        assert len(repo.load_run_frame(rid, "dq_findings")) == len(result.dq.findings)
        assert repo.load_run_frame(rid, "nothing_here").empty
        events = repo.load_audit_events(subject=rid)
        assert {"RUN_STARTED", "RUN_FINISHED", "DQ_FINDING"} <= set(events["event_type"])
        breaches = repo.load_audit_events()
        assert (breaches["event_type"] == "LIMIT_BREACH").sum() == result.run.summary["breaches"]


def test_rerun_is_reproducible(db_path, result, tmp_path_factory):
    with DuckDBRepository(db_path) as repo:
        again = run_eod(repo, EODConfig(counterparty=False, var=VaRConfig(window_days=200), workers=1), persist=False)
    assert again.run.run_id != result.run.run_id
    assert again.run.summary["var"] == pytest.approx(result.run.summary["var"])
    assert again.run.config_hash == result.run.config_hash
    assert again.run.portfolio_snapshot_id == result.run.portfolio_snapshot_id


def test_coupon_between_dates_is_not_a_loss():
    from datetime import date as _d

    from novera.domain import BuySell, ClearingType, GovernmentBond, Trade
    from novera.market_data import MarketSnapshot
    from novera.risk import Portfolio, explain_pnl

    tenors = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y"]
    values = {f"IR:USD:{t}": 0.04 for t in tenors}
    prev = MarketSnapshot(as_of=_d(2026, 5, 14), values=values)
    today = MarketSnapshot(as_of=_d(2026, 5, 16), values=values)
    bond = GovernmentBond(instrument_id="B", currency="USD", issuer="UST", coupon_rate=0.05,
                          issue_date=_d(2025, 11, 15), maturity_date=_d(2035, 11, 15))
    t = Trade(trade_id="T", instrument=bond, direction=BuySell.BUY, quantity=100e6, trade_price=100,
              trade_date=_d(2026, 1, 5), book_id="B", trader_id="T", counterparty_id="X",
              clearing=ClearingType.EXCHANGE)
    pf = Portfolio([t], today, "USD")
    out = explain_pnl(pf, prev, [t])
    carry = out.steps.set_index("step")["pnl"]["CARRY"]
    # A 2.5m coupon paid on 15 May crosses the window: carry must be small and positive, not -2.5m.
    assert 0 < carry < 200_000, carry
