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
        "Trade extract",
        "VaR",
        "Stress",
        "Stress library",
        "Limit management",
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
        "Reference data",
        "Admin",
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


def test_reference_page_shows_both_trees(app):
    next(r for r in app.sidebar.radio if r.label == "View").set_value("Reference data").run()
    assert not app.exception, [e.value for e in app.exception]
    text = " ".join(m.value for m in app.markdown)
    assert "legal entities" in text and "desks" in text and "books" in text
    labels = [e.label for e in app.expander]
    assert any("MACRO" in lab for lab in labels) and any("USD_RATES" in lab for lab in labels)
    assert "GMB_NY" in text and "USD_MACRO_RV" in text
    next(r for r in app.radio if r.label == "Group by").set_value("Legal entity").run()
    assert not app.exception, [e.value for e in app.exception]
    assert any("GMB" in e.label for e in app.expander)
    next(r for r in app.radio if r.label == "Show").set_value("Counterparties").run()
    assert not app.exception, [e.value for e in app.exception]
    text = " ".join(m.value for m in app.markdown)
    assert "netting sets" in text and "CSA" in text and "MPoR" in text
    next(t for t in app.text_input if t.label == "Filter").set_value("BANK_A").run()
    assert not app.exception, [e.value for e in app.exception]
    labels = [e.label for e in app.expander]
    assert labels and all("BANK_A" in lab for lab in labels)
    next(t for t in app.text_input if t.label == "Filter").set_value("").run()
    next(r for r in app.radio if r.label == "Show").set_value("Products").run()
    assert not app.exception, [e.value for e in app.exception]
    labels = [e.label for e in app.expander]
    assert any("Rates · RATES" in lab for lab in labels) and any("SWAPTION" in lab for lab in labels)
    text = " ".join(m.value for m in app.markdown)
    assert "PR-012" in text and "swap_tenor" in text
    next(r for r in app.radio if r.label == "Show").set_value("Risk measures").run()
    assert not app.exception, [e.value for e in app.exception]
    labels = [e.label for e in app.expander]
    assert any("Market risk" in lab for lab in labels) and any("MR-002" in lab for lab in labels)
    next(r for r in app.radio if r.label == "Show").set_value("Risk factors").run()
    assert not app.exception, [e.value for e in app.exception]
    labels = [e.label for e in app.expander]
    assert any("Zero curves" in lab for lab in labels)
    text = " ".join(m.value for m in app.markdown)
    assert "USD" in text and "nodes" in text and "moneyness" in text


def test_limit_management_hierarchy_table(app):
    next(r for r in app.sidebar.radio if r.label == "View").set_value("Limit management").run()
    assert not app.exception, [e.value for e in app.exception]
    table = app.dataframe[0].value
    assert {"hierarchy", "limit", "type", "utilisation", "owner", "approval"} <= set(table.columns)
    assert len(table) > 10 and table["hierarchy"].str.contains("›").any()
    assert table["status"].str.contains("BREACH").any()
    next(r for r in app.radio if r.label == "Group by").set_value("Limit type").run()
    assert not app.exception, [e.value for e in app.exception]
    types = list(app.dataframe[0].value["type"])
    assert types == sorted(types)
    next(t for t in app.multiselect if t.label == "Run status").set_value(["BREACH"]).run()
    assert not app.exception, [e.value for e in app.exception]
    assert app.dataframe[0].value["status"].str.contains("BREACH").all()
    # Utilisation tab: the exceptions view, highest utilisation first, trades in scope as a column
    util = next(d.value for d in app.dataframe if "trades in scope" in d.value.columns)
    assert util["status"].str.contains("BREACH|WARNING").all()
    assert list(util["utilisation"]) == sorted(util["utilisation"], reverse=True)


def test_trade_extract_filters_and_preview(app):
    next(r for r in app.sidebar.radio if r.label == "View").set_value("Trade extract").run()
    assert not app.exception, [e.value for e in app.exception]
    metrics = {m.label: m.value for m in app.metric}
    total = int(metrics["Trades selected"].replace(",", ""))
    assert total > 50 and app.dataframe[0].value.shape[0] <= 200
    desk = next(m for m in app.multiselect if m.label == "Desk")
    desk.set_value([desk.options[0]]).run()
    assert not app.exception, [e.value for e in app.exception]
    metrics = {m.label: m.value for m in app.metric}
    picked = int(metrics["Trades selected"].replace(",", ""))
    assert 0 < picked < total
    assert (app.dataframe[0].value["desk_id"] == desk.options[0]).all()
