from datetime import date

import pytest
from fastapi.testclient import TestClient

from novera.api.client import LocalClient
from novera.api.service import RiskService, RunNotFoundError
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
    path = tmp_path_factory.mktemp("api") / "api.duckdb"
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=1.0, seed=5))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=120, seed=5,
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
        run_eod(repo, EODConfig(var=VaRConfig(window_days=150), workers=1),
                runs_dir=tmp_path_factory.mktemp("runs"))
    return path


@pytest.fixture(scope="module")
def client(db_path):
    import novera.api.app as app_module

    app_module.settings.db_path = db_path
    return TestClient(app_module.app)


def test_service_summary_and_frames(db_path):
    with DuckDBRepository(db_path, read_only=True) as repo:
        svc = RiskService(repo)
        s = svc.summary()
        assert s["status"] == "COMPLETED" and s["verdict"] in ("GREEN", "AMBER", "RED")
        assert s["var_by_asset_class"] and s["summary"]["var"] > 0
        assert svc.positions(by="desk_id")
        assert svc.var_by("desk_id")
        assert {r["method"] for r in svc.var_summary()} == {"historical_full_revaluation", "delta_gamma_vega"}
        assert svc.sensitivities(measure="DV01", by="desk_id", desk_id="USD_RATES")
        assert svc.stress(by="asset_class") and svc.stress(by=None)
        assert svc.limits() and all(r["status"] for r in svc.limits())
        assert svc.dq()
        p = svc.pnl(by="desk_id")
        assert p["steps"] and "by" in p and "challenger" in p
        tid = svc.valuation().iloc[0]["trade_id"]
        t = svc.trade(tid)
        assert t["trade"]["trade_id"] == tid and t["valuation"]["trade_id"] == tid
        assert svc.audit()
        with pytest.raises(RunNotFoundError):
            svc.resolve("nope")


def test_http_endpoints(client):
    assert client.get("/health").json()["status"] == "ok"
    runs = client.get("/runs").json()
    assert runs and runs[0]["run_id"].startswith("run_")
    rid = runs[0]["run_id"]
    assert client.get(f"/runs/{rid}/summary").json()["run_id"] == rid
    assert client.get("/runs/latest/summary").json()["run_id"] == rid
    assert client.get(f"/runs/{rid}/positions", params={"by": "asset_class"}).json()
    assert client.get(f"/runs/{rid}/var", params={"by": "desk_id"}).json()
    assert client.get(f"/runs/{rid}/var/summary").json()
    assert len(client.get(f"/runs/{rid}/var/scenarios").json()) == 150
    assert client.get(f"/runs/{rid}/sensitivities", params={"measure": "DV01"}).json()
    assert client.get(f"/runs/{rid}/stress").json()
    breaches = client.get(f"/runs/{rid}/limits", params={"status": "BREACH"}).json()
    assert all(b["status"] == "BREACH" for b in breaches)
    assert client.get(f"/runs/{rid}/dq").json()
    assert client.get(f"/runs/{rid}/pnl", params={"by": "asset_class"}).json()["by"]
    assert client.get("/audit").json()
    assert client.get("/organisation").json()["firm"]["firm_id"] == "GMB"
    assert client.get("/runs/nope/summary").status_code == 404
    assert client.get(f"/runs/{rid}/trades/NOPE").status_code == 404


def test_local_client_matches_service(db_path):
    c = LocalClient(db_path)
    s = c.summary()
    assert s["run_id"].startswith("run_")
    assert c.limits(status="BREACH") == [r for r in c.limits() if r["status"] == "BREACH"]
