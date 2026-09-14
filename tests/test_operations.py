"""Phase 3 operations: alerts, scheduler, day advance, challenger reconciliation, adapters."""
import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest

from novera.market_data.adapters import (
    CoinbaseAdapter,
    FredAdapter,
    YahooAdapter,
    apply_real_history,
    fetch_all,
)
from novera.market_data.crises import named_crisis_scenarios
from novera.market_data.history import MarketHistory
from novera.reconciliation import reconcile
from novera.risk import VaRConfig
from novera.simulation import (
    TradeGeneratorConfig,
    build_counterparty_universe,
    build_global_macro_bank,
    build_limits,
    generate_portfolio,
)
from novera.simulation.market_data import MarketSimConfig, generate_market_data
from novera.simulation.vendor_feed import VendorFeedConfig, generate_vendor_feed
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.alerts import Alert, alerts_from_run, dispatch, run_failed_alert
from novera.workflows.eod import EODConfig, run_eod
from novera.workflows.scheduler import next_fire_time, run_once, serve

D1 = date(2026, 9, 11)
CFG = EODConfig(var=VaRConfig(window_days=120), workers=1)


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("ops") / "ops.duckdb"
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=D1, years=1.0, seed=17))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(business_date=D1, n_trades=120, seed=17,
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
    return str(path)


class FakeChannel:
    def __init__(self, fail=False, name="fake"):
        self.sent, self.fail, self.name = [], fail, name

    def send(self, alert):
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append(alert.title)


def test_alerts_from_run_and_dedupe(db_path, tmp_path, monkeypatch):
    monkeypatch.setenv("NOVERA_ALERTS_ENABLED", "false")
    from novera.config import get_settings
    get_settings.cache_clear()
    with DuckDBRepository(db_path) as repo:
        res = run_eod(repo, CFG, D1, runs_dir=tmp_path)
        sync = res.sync
        assert sync is not None and sync.raised
        alerts = alerts_from_run(res, sync)
        kinds = {a.kind for a in alerts}
        assert "RUN_SUMMARY" in kinds and "RUN_VERDICT" in kinds and "NEW_BREACH" in kinds
        good, bad = FakeChannel(), FakeChannel(fail=True, name="broken")
        sent = dispatch(repo, alerts, [good, bad])
        crit = [a for a in sent if a.severity != "INFO"]
        assert all(a.status == "PARTIAL" for a in crit) and all(a.deliveries["fake"] == "ok" for a in crit)
        assert all(a.deliveries["broken"].startswith("RuntimeError") for a in crit)
        assert all(a.status == "STORED" for a in sent if a.severity == "INFO")  # below min severity
        again = dispatch(repo, alerts_from_run(res, sync), [good])
        assert all(a.status == "SUPPRESSED" for a in again)
        stored = repo.load_alerts()
        assert len(stored) == len(alerts) + len(again)
        failed = dispatch(repo, [run_failed_alert(D1, "kaboom", 3)], [good])
        assert failed[0].status == "SENT" and "kaboom" in failed[0].body
    get_settings.cache_clear()


def test_next_fire_time_skips_weekends():
    fri = datetime(2026, 9, 11, 19, 0)  # Friday after 18:30
    assert next_fire_time(fri, "18:30") == datetime(2026, 9, 14, 18, 30)
    assert next_fire_time(datetime(2026, 9, 14, 9, 0), "18:30") == datetime(2026, 9, 14, 18, 30)


def test_scheduler_advances_and_runs(db_path, tmp_path, monkeypatch):
    monkeypatch.setenv("NOVERA_ALERTS_ENABLED", "false")
    monkeypatch.setenv("NOVERA_DATA_DIR", str(tmp_path))
    from novera.config import get_settings
    get_settings.cache_clear()
    with DuckDBRepository(db_path) as repo:
        before = repo.list_market_snapshots()[-1][1]
        skipped = run_once(repo, advance=False, cfg=CFG)
        assert skipped.status == "SKIPPED" and skipped.business_date == before
        job = run_once(repo, advance=True, cfg=CFG)
        assert job.status == "COMPLETED", job.error
        assert job.business_date == date(2026, 9, 14) and job.run_id
        assert repo.list_market_snapshots()[-1][1] == date(2026, 9, 14)
        assert repo.list_portfolio_snapshots()[-1][1] == date(2026, 9, 14)
        assert repo.load_jobs()[0]["job_id"] == job.job_id
        # History extended by one day, the earlier days untouched.
        hist = MarketHistory.from_long(repo.load_market_history())
        assert hist.dates[-1] == date(2026, 9, 14) and hist.dates[-2] == D1
        assert "IR:USD:7Y" in repo.load_market_snapshot(repo.list_market_snapshots()[-1][0]).values
    slept = []
    jobs = list(serve(db_path, "18:30", advance=False, cfg=CFG, iterations=1, sleep=slept.append,
                      clock=lambda: datetime(2026, 9, 14, 18, 0)))
    assert slept == [1800.0] and jobs[0].status == "SKIPPED"
    get_settings.cache_clear()


def test_vendor_feed_and_reconciliation(db_path, tmp_path):
    with DuckDBRepository(db_path) as repo:
        latest = repo.latest_run() or run_eod(repo, CFG, D1, runs_dir=tmp_path).run
        rid = latest.run_id
        cfg = VendorFeedConfig()
        csv, meta = generate_vendor_feed(repo, rid, tmp_path / "feeds", cfg)
        assert csv.exists() and meta.exists() and len(cfg.planted) == 4
        feed = pd.read_csv(csv)
        assert "official_pv" in feed and "official_var_contribution" in feed
        rec = reconcile(repo, rid, csv, meta)
        att = rec.attribution
        assert set(att) == {"SCOPE", "MARKET_DATA", "PRICING_MODEL", "METHODOLOGY", "RESIDUAL"}
        assert sum(att.values()) == pytest.approx(rec.gap, abs=1.0)
        only = rec.detail[rec.detail["presence"] == "NOVERA_ONLY"]
        assert len(only) > 0 and att["SCOPE"] == pytest.approx(-only["var_contribution"].sum(), abs=1.0)
        assert (rec.detail["cause"] == "MARKET_DATA").sum() > 0, "stale FX valuation should be detected"
        assert any(f.startswith("METHODOLOGY") for f in rec.findings)
        assert "methodology_rerun" in rec.meta
        assert not repo.load_run_frame(rid, "recon_summary").empty
        # A feed identical to Novera reconciles to zero.
        cfg0 = VendorFeedConfig(plant=False, var_window_days=120)
        csv0, meta0 = generate_vendor_feed(repo, rid, tmp_path / "feeds0", cfg0)
        rec0 = reconcile(repo, rid, csv0, meta0, persist=False)
        assert abs(rec0.gap) < 1.0 and (rec0.detail["cause"] == "").all()


def _mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "stlouisfed" in url:
            sid = request.url.params["series_id"]
            base = {"DGS10": 4.1, "DGS20": 4.4}.get(sid, 4.0)
            return httpx.Response(200, json={"observations": [
                {"date": "2020-03-02", "value": str(base)}, {"date": "2020-03-03", "value": "."},
                {"date": "2020-03-04", "value": str(base + 0.1)}]})
        if "finance.yahoo" in url:
            return httpx.Response(200, json={"chart": {"result": [{
                "timestamp": [1583107200, 1583193600], "indicators": {"quote": [{"close": [3000.0, None]}]}}]}})
        if "coinbase" in url:
            return httpx.Response(200, json=[[1583107200, 8500, 8900, 8600, 8800.5, 10.0]])
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_adapters_with_recorded_responses(db_path):
    client = httpx.Client(transport=_mock_transport())
    results = fetch_all([FredAdapter("key", client=client), YahooAdapter(client=client, symbols={"^GSPC": "EQIDX:SPX"}),
                         CoinbaseAdapter(client=client)], date(2020, 3, 1), date(2020, 3, 5))
    fred, yahoo, cb = results
    assert fred.fetched["IR:USD:10Y"] == 2 and fred.fetched["IR:USD:15Y"] == 2 and not fred.errors
    ten = fred.frame[fred.frame.factor_id == "IR:USD:10Y"]["value"].tolist()
    assert ten == pytest.approx([0.041, 0.042])
    assert yahoo.fetched == {"EQIDX:SPX": 1} and yahoo.frame.iloc[0]["value"] == 3000.0
    assert cb.fetched == {"CRYPTO:BTC": 1, "CRYPTO:ETH": 1}
    with DuckDBRepository(db_path) as repo:
        summary = apply_real_history(repo, results)
        assert summary["rows"] > 0 and not summary["errors"]
        prov = repo.load_market_provenance()
        assert set(prov["source"]) == {"fred", "yahoo", "coinbase"}
        rows = repo.load_market_history(["EQIDX:SPX"], start=date(2020, 3, 1), end=date(2020, 3, 5))
        assert len(rows) == 1
    failing = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    bad = fetch_all([CoinbaseAdapter(client=failing)], date(2020, 3, 1), date(2020, 3, 2))
    assert bad[0].errors and bad[0].frame.empty


def test_named_crises_require_coverage():
    long = MarketHistory(pd.DataFrame({"EQIDX:SPX": range(6000)},
                                      index=pd.bdate_range("2005-01-03", periods=6000).date))
    names = {s.scenario_id for s in named_crisis_scenarios(long)}
    assert {"gfc_2008", "covid_2020", "rates_2022", "banks_2023"} <= names
    short = MarketHistory(pd.DataFrame({"EQIDX:SPX": range(300)},
                                       index=pd.bdate_range("2025-01-01", periods=300).date))
    assert named_crisis_scenarios(short) == []


def test_alert_serialises_round_trip():
    a = Alert("a1", datetime.now(UTC), D1, "INFO", "RUN_SUMMARY", "run", "t", "b", ["x"], "run")
    d = a.to_dict()
    assert json.loads(json.dumps(d))["kind"] == "RUN_SUMMARY" and a.dedupe_key.endswith("2026-09-11")
    assert Path(".").exists()
