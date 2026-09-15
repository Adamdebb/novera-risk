"""Headless run of the Streamlit dashboard over a stored run: every page must render
without exceptions and show figures from the run."""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

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
APP = Path(__file__).resolve().parents[1] / "src" / "novera" / "ui" / "app.py"


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("ui") / "ui.duckdb"
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=1.0, seed=8))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(
        org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=100, seed=8, market_history=hist)
    )
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
        res = run_eod(
            repo,
            EODConfig(counterparty=False, regulatory=False, var=VaRConfig(window_days=120), workers=1),
            runs_dir=tmp_path_factory.mktemp("runs"),
        )
        from novera.reconciliation import reconcile
        from novera.simulation.vendor_feed import VendorFeedConfig, generate_vendor_feed

        csv, meta = generate_vendor_feed(
            repo, res.run.run_id, tmp_path_factory.mktemp("feed"), VendorFeedConfig(var_window_days=100)
        )
        reconcile(repo, res.run.run_id, csv, meta)
        from novera.counterparty_risk import ExposureSimConfig, run_counterparty
        from novera.regulatory import run_regulatory

        run_regulatory(repo, res.run.run_id, runs_dir=tmp_path_factory.mktemp("runs2"))
        run_counterparty(
            repo,
            res.run.run_id,
            ExposureSimConfig(paths=20),
            runs_dir=tmp_path_factory.mktemp("cpr"),
            workers=1,
        )
    return path


@pytest.fixture
def app(db_path, monkeypatch):
    from novera.config import get_settings

    monkeypatch.setenv("NOVERA_DB_PATH", str(db_path))
    monkeypatch.setenv("NOVERA_FUND_DB_PATH", str(db_path.parent / "no_fund.duckdb"))
    monkeypatch.delenv("NOVERA_API_URL", raising=False)
    get_settings.cache_clear()
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    yield at
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "page",
    [
        "Overview",
        "Copilot",
        "Drill-down",
        "VaR",
        "Stress",
        "Limits",
        "Breaches",
        "Counterparty",
        "Capital",
        "P&L explain",
        "Data quality",
        "Concentration & liquidity",
        "Compare runs",
        "Challenger",
        "Risk pack",
        "Alerts & jobs",
        "Runs & audit",
    ],
)
def test_every_page_renders(app, page):
    radio = next(r for r in app.sidebar.radio if r.label == "View")
    radio.set_value(page).run()
    assert not app.exception, [e.value for e in app.exception]
    assert app.title[0].value
    assert "run_" in app.caption[1].value or any("run_" in c.value for c in app.caption)


def test_overview_shows_var_and_breaches(app):
    assert next(r for r in app.sidebar.radio if r.label == "View").value == "Overview"
    metrics = {m.label: m.value for m in app.metric}
    assert "VaR 99% 1d" in metrics and metrics["VaR 99% 1d"].endswith("m")
    assert any("BREACH" in e.value or "—" in e.value for e in app.error) or app.success
