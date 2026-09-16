from datetime import date

import pytest
from fastapi.testclient import TestClient

from novera.api.client import LocalClient
from novera.api.errors import InvalidRequestError
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
    gen = generate_portfolio(
        org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=120, seed=5, market_history=hist)
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
            EODConfig(counterparty=False, regulatory=False, var=VaRConfig(window_days=150), workers=1),
            runs_dir=tmp_path_factory.mktemp("runs"),
        )
        from novera.counterparty_risk import ExposureSimConfig, run_counterparty

        data_dir = path.parent / "data"
        run_counterparty(
            repo, res.run.run_id, ExposureSimConfig(paths=20), runs_dir=data_dir / "runs", workers=1
        )
    return path


@pytest.fixture(scope="module")
def client(db_path):
    import novera.api.app as app_module

    app_module.settings.db_path = db_path
    app_module.settings.data_dir = db_path.parent / "data"
    return TestClient(app_module.app)


def test_service_summary_and_frames(db_path):
    with DuckDBRepository(db_path, read_only=True) as repo:
        svc = RiskService(repo)
        s = svc.summary()
        assert s["status"] == "COMPLETED" and s["verdict"] in ("GREEN", "AMBER", "RED")
        assert s["var_by_asset_class"] and s["summary"]["var"] > 0
        assert svc.positions(by="desk_id")
        assert svc.var_by("desk_id")
        methods = {r["method"] for r in svc.var_summary()}
        assert methods == {"historical_full_revaluation", "delta_gamma_vega", "monte_carlo_delta_gamma_vega"}
        for method in methods:
            rows = svc.var_by("asset_class", method=method)
            assert rows and all(r["trades"] > 0 for r in rows), method
        with pytest.raises(InvalidRequestError):
            svc.var_by("asset_class", method="parametric")
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
    assert client.get("/organisation", params={"firm_id": "GMB"}).json()["desks"]
    assert client.get("/organisation", params={"firm_id": "NOPE"}).status_code == 404
    opts = client.get(f"/runs/{rid}/trade-extract/options").json()
    assert opts["dims"]["desk_id"] and opts["dates"]["trade_date"]["min"]
    ext = client.get(f"/runs/{rid}/trade-extract").json()
    assert ext["count"] == len(ext["rows"]) > 50 and ext["columns"][0] == "trade_id"
    assert {"pv", "model", "trade_date", "maturity_date", "instrument_id"} <= set(ext["columns"])
    desk = opts["dims"]["desk_id"][0]
    sub = client.get(f"/runs/{rid}/trade-extract", params={"desk_id": [desk]}).json()
    assert 0 < sub["count"] < ext["count"] and all(r["desk_id"] == desk for r in sub["rows"])
    two = [r["trade_id"] for r in ext["rows"][:2]]
    assert client.get(f"/runs/{rid}/trade-extract", params={"trade_ids": two}).json()["count"] == 2
    assert client.get(f"/runs/{rid}/trade-extract", params={"min_abs_pv": 1e15}).json()["count"] == 0
    r = client.get(f"/runs/{rid}/trade-extract.csv", params={"desk_id": [desk]})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].endswith(f'{rid}.csv"')
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("trade_id,") and len(lines) == sub["count"] + 1
    lh = client.get("/limits/hierarchy").json()
    assert lh["run_id"] == rid and lh["rows"]
    ranks = [r["level_rank"] for r in lh["rows"]]
    assert ranks == sorted(ranks), "rows are ordered firm, business, desk, counterparty"
    desk = next(r for r in lh["rows"] if r["level"] == "desk_id")
    assert desk["path"].count("›") == 2 and desk["status"] in ("OK", "WARNING", "BREACH", "NO_DATA")
    assert desk["effective_amount"] == desk["amount"] or desk["increase_id"]
    prods = client.get("/reference/products").json()["asset_classes"]
    assert {a["asset_class"] for a in prods} == {
        "RATES",
        "FX",
        "EQUITY",
        "CREDIT",
        "COMMODITY",
        "DIGITAL_ASSET",
    }
    swaption = next(p for a in prods for p in a["products"] if p["product_type"] == "SWAPTION")
    assert swaption["methodology"] == "PR-012" and swaption["venue"] == "OTC"
    assert {f["name"] for f in swaption["fields"]} >= {"expiry_date", "swap_tenor", "strike", "payer"}
    assert swaption["appropriateness"] == "known_weakness" and "single-curve" in swaption["simplifications"]
    inv = client.get("/reference/model-inventory").json()
    assert inv["record"] == "MV-001" and len(inv["rows"]) == 19
    assert set(inv["summary"]) == {"market_standard", "acceptable_simplification", "known_weakness"}
    areas = client.get("/reference/measures").json()["areas"]
    assert [a["area"] for a in areas] == ["MARKET", "COUNTERPARTY", "REGULATORY", "FUND", "CONTROL"]
    var = next(m for a in areas for m in a["measures"] if m["methodology"] == "MR-002")
    assert var["screen"] == "VaR" and var["version"]
    factors = client.get("/reference/risk-factors").json()["factors"]
    assert factors and {f["factor_type"] for f in factors} >= {"IR_ZERO", "FX_SPOT", "IMPLIED_VOL"}
    srcs = client.get("/reference/market-data-sources").json()
    assert srcs["record"] == "MD-001" and len(srcs["rows"]) == len(factors)
    assert srcs["summary"]["real"] == 0 and srcs["summary"]["available"] > 0  # nothing fetched in tests
    by_id = {r["factor_id"]: r for r in srcs["rows"]}
    assert by_id["IR:USD:10Y"]["status"] == "AVAILABLE" and by_id["IR:USD:10Y"]["adapter"] == "fred"
    assert by_id["IR:EUR:10Y"]["status"] == "SYNTHETIC" and by_id["IR:EUR:10Y"]["source"].startswith(
        "simulator"
    )
    assert all(r["free_source"] and r["paid_source"] for r in srcs["rows"])
    fam = {f["family"]: f for f in srcs["families"]}
    assert fam["CMD:BRENT"]["status"] == "PARTIAL" and fam["CMD:BRENT"]["available"] == 1
    assert fam["SWVOL:USD"]["status"] == "SYNTHETIC"
    lib = client.get("/reference/stress-library").json()
    assert lib["record"] == "MR-005" and [c["category"] for c in lib["categories"]] == [
        "HYPOTHETICAL",
        "STYLISED_HISTORICAL",
        "NAMED_CRISIS",
    ]
    hyp = {x["scenario_id"]: x for x in lib["categories"][0]["scenarios"]}
    assert len(hyp) == 12 and hyp["usd_rates_up_100"]["shocks"][0]["size"] == 100.0
    assert hyp["usd_rates_up_100"]["shocks"][0]["unit"] == "bp" and hyp["usd_rates_up_100"]["in_daily_run"]
    steep = hyp["usd_steepener"]["shocks"]
    assert steep[0]["tenors"] == "1M, 3M, 6M, 1Y, 2Y" and steep[0]["size"] == -50.0
    styl = lib["categories"][1]["scenarios"]
    assert [x["scenario_id"] for x in styl] == ["stylised_risk_off_crash", "stylised_rates_shock"]
    spx = next(sh for sh in styl[0]["shocks"] if sh["target"] == "EQIDX:SPX")
    assert spx["kind"] == "REALISED" and spx["unit"] == "%" and spx["size"] < -15
    crises = {x["scenario_id"]: x for x in lib["categories"][2]["scenarios"]}
    assert len(crises) == 7 and not any(x["in_daily_run"] for x in crises.values())
    assert crises["covid_2020"]["status"] == "NOT_COVERED" and crises["covid_2020"]["shocks"] == []
    assert lib["summary"]["replayable_real"] == 0 and lib["history"]["days"] > 200
    ref = client.get("/reference/counterparties").json()
    assert ref["counterparties"] and ref["netting_sets"] and ref["csas"]
    assert {n["csa_id"] for n in ref["netting_sets"] if n["csa_id"]} <= {c["csa_id"] for c in ref["csas"]}
    assert client.get("/runs/nope/summary").status_code == 404
    assert client.get(f"/runs/{rid}/trades/NOPE").status_code == 404


def test_local_client_matches_service(db_path):
    c = LocalClient(db_path)
    s = c.summary()
    assert s["run_id"].startswith("run_")
    assert c.limits(status="BREACH") == [r for r in c.limits() if r["status"] == "BREACH"]


def test_breach_endpoints_round_trip(client):
    breaches = client.get("/breaches").json()
    assert breaches, "the EOD run should have raised at least one breach"
    bid = breaches[0]["breach_id"]
    detail = client.get(f"/breaches/{bid}").json()
    assert detail["actions"][0]["action"] == "RAISED"
    r = client.post(f"/breaches/{bid}/acknowledge", json={"actor": "Head of Desk", "comment": "on it"})
    assert r.status_code == 200 and r.json()["status"] == "ACKNOWLEDGED"
    r = client.post(f"/breaches/{bid}/acknowledge", json={"actor": "Head of Desk"})
    assert r.status_code == 409  # workflow rule violated
    r = client.post(f"/breaches/{bid}/close", json={"actor": "Head of Desk", "reason": "RISK_REDUCED"})
    assert r.status_code == 409
    lid = breaches[0]["limit_id"]
    lim = next(x for x in client.get("/runs/latest/limits").json() if x["limit_id"] == lid)
    r = client.post(
        "/increases",
        json={
            "limit_id": lid,
            "new_amount": lim["base_amount"] * 1.1,
            "expires_on": "2026-10-10",
            "requested_by": "Head of Desk",
            "rationale": "unwind scheduled",
            "effective_from": "2026-09-11",
            "breach_id": bid,
        },
    )
    assert r.status_code == 200, r.text
    iid = r.json()["increase_id"]
    inc = next(i for i in client.get("/increases").json() if i["increase_id"] == iid)
    assert inc["allowed_approvers"]
    r = client.post(f"/increases/{iid}/decide", json={"approver": "Head of Desk", "approve": True})
    assert r.status_code == 409
    r = client.post(
        f"/increases/{iid}/decide", json={"approver": inc["allowed_approvers"][0], "approve": True}
    )
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    r = client.post(
        f"/breaches/{bid}/close", json={"actor": "Head of Desk", "reason": "TEMPORARY_INCREASE_APPROVED"}
    )
    assert r.status_code == 200 and r.json()["status"] == "CLOSED"
    rid = client.get("/runs").json()[0]["run_id"]
    assert (
        client.get("/compare", params={"run_a": rid, "run_b": rid}).json()["headline"]["var"]["change"] == 0
    )


def test_analyst_endpoints(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert client.get("/analyst/provider").json()["provider"] == "scripted"
    r = client.post("/analyst/ask", json={"question": "Which limits are breached?", "session_id": "t"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["answer"] and d["tool_calls"] and d["run_ids_cited"]
    assert client.get("/analyst/history").json()[0]["answer_id"] == d["answer_id"]
    r = client.post("/analyst/commentary")
    assert r.status_code == 200 and "VaR" in r.json()["answer"]


def test_ops_endpoints(client):
    assert client.get("/alerts").status_code == 200
    assert client.get("/jobs").status_code == 200
    assert client.get("/market-data/provenance").status_code == 200
    assert client.get("/runs/latest/reconciliation").status_code == 404


def test_measure_endpoints(client):
    assert client.get("/runs/latest/concentration").json()["by_dimension"]
    liq = client.get("/runs/latest/liquidity").json()
    assert liq["liquidity_adjusted_var"] >= liq["var"]
    assert client.get("/runs/latest/backtest").json()["summary"]
    r = client.post("/runs/latest/risk-pack", params={"pdf": False})
    assert r.status_code == 200 and r.json()["html"].endswith(".html")


def test_counterparty_endpoints(client):
    cps = client.get("/runs/latest/counterparties").json()
    assert cps["summary"] and cps["notes"]["paths"] == "20"
    cid = cps["summary"][0]["counterparty_id"]
    d = client.get(f"/runs/latest/counterparties/{cid}").json()
    assert d["profile"] and d["netting_sets"]
    ns = d["netting_sets"][0]["netting_set_id"]
    r = client.post("/runs/latest/csa-what-if", json={"netting_set_id": ns, "uncollateralised": True})
    assert (
        r.status_code == 200 and r.json()["what_if_csa"] is None and r.json()["after"] and r.json()["before"]
    )
    assert client.get("/runs/latest/counterparties/NOPE").status_code == 404


def test_capital_endpoint(client):
    r = client.get("/runs/latest/capital")
    assert r.status_code == 200


# --- contract: response models, problem details, one error hierarchy -----------------------------
def _walk_extras(obj, path="", out=None):
    """Collect (path, extra keys) for every model in the tree that carries undeclared fields."""
    from pydantic import BaseModel

    from novera.api.schemas import Row

    out = [] if out is None else out
    if isinstance(obj, BaseModel):
        extra = obj.model_extra or {}
        if extra and not isinstance(obj, Row):
            out.append((path or type(obj).__name__, sorted(extra)))
        for name in type(obj).model_fields:
            _walk_extras(getattr(obj, name), f"{path}.{name}" if path else name, out)
    elif isinstance(obj, list):
        for i, x in enumerate(obj[:3]):
            _walk_extras(x, f"{path}[{i}]", out)
    elif isinstance(obj, dict):
        for k, x in obj.items():
            _walk_extras(x, f"{path}.{k}", out)
    return out


def test_every_route_declares_its_response(client):
    from fastapi.routing import APIRoute

    import novera.api.app as app_module

    file_routes = {"/runs/{run_id}/risk-pack/{fmt}", "/runs/{run_id}/trade-extract.csv"}
    missing = [
        r.path
        for r in app_module.app.routes
        if isinstance(r, APIRoute) and r.path not in file_routes and r.response_model is None
    ]
    assert not missing, f"routes without a response model: {missing}"
    schema = client.get("/openapi.json").json()
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            if path in file_routes:
                continue
            ok = op["responses"]["200"]["content"]["application/json"]["schema"]
            assert ok, f"{method.upper()} {path} has no 200 schema"


def test_schemas_declare_every_field_the_engine_returns(client):
    """Extra fields pass through so nothing is lost, but the schema must not fall behind."""
    from novera.api import schemas as s

    rid = client.get("/runs").json()[0]["run_id"]
    pairs = [
        (s.RunSummary, client.get(f"/runs/{rid}/summary").json()),
        (s.PnLExplain, client.get(f"/runs/{rid}/pnl", params={"by": "desk_id"}).json()),
        (s.RunComparison, client.get("/compare", params={"run_a": rid, "run_b": rid}).json()),
        (s.ConcentrationReport, client.get(f"/runs/{rid}/concentration").json()),
        (s.LiquidityReport, client.get(f"/runs/{rid}/liquidity").json()),
        (s.BacktestReport, client.get(f"/runs/{rid}/backtest").json()),
        (s.CounterpartyExposures, client.get(f"/runs/{rid}/counterparties").json()),
        (s.CapitalReport, client.get(f"/runs/{rid}/capital").json()),
        (s.LookthroughReport, client.get(f"/runs/{rid}/lookthrough").json()),
        (s.MarketDataProxies, client.get(f"/runs/{rid}/market-data-proxies").json()),
        (s.CounterpartyReference, client.get("/reference/counterparties").json()),
        (s.ProductReference, client.get("/reference/products").json()),
        (s.ModelInventory, client.get("/reference/model-inventory").json()),
        (s.LimitHierarchy, client.get("/limits/hierarchy").json()),
        (s.TradeExtractOptions, client.get(f"/runs/{rid}/trade-extract/options").json()),
        (s.MeasureReference, client.get("/reference/measures").json()),
        (s.RiskFactorReference, client.get("/reference/risk-factors").json()),
        (s.MarketDataSources, client.get("/reference/market-data-sources").json()),
        (s.StressLibrary, client.get("/reference/stress-library").json()),
        (s.RerunOptions, client.get("/admin/rerun/options").json()),
        (s.SignoffStatus, client.get("/runs/latest/signoff").json()),
        (s.SignoffPolicy, client.get("/admin/signoff/policy").json()),
        (s.VarSetup, client.get("/admin/var/setup").json()),
    ]
    for model, payload in pairs:
        pairs_extra = _walk_extras(model.model_validate(payload))
        assert not pairs_extra, f"{model.__name__} is behind the engine: {pairs_extra}"
    for model, payload in [
        (s.LimitRow, client.get(f"/runs/{rid}/limits").json()),
        (s.DQFinding, client.get(f"/runs/{rid}/dq").json()),
        (s.BreachRecord, client.get("/breaches", params={"open_only": False}).json()),
        (s.IncreaseRecord, client.get("/increases").json()),
        (s.AuditEvent, client.get("/audit").json()),
        (s.VarSummaryRow, client.get(f"/runs/{rid}/var/summary").json()),
        (s.VarScenarioRow, client.get(f"/runs/{rid}/var/scenarios").json()),
        (s.AlertRecord, client.get("/alerts").json()),
        (s.JobRecord, client.get("/jobs").json()),
    ]:
        for row in payload[:5]:
            extra = _walk_extras(model.model_validate(row))
            assert not extra, f"{model.__name__} is behind the engine: {extra}"
    cid = client.get(f"/runs/{rid}/counterparties").json()["summary"][0]["counterparty_id"]
    extra = _walk_extras(
        s.CounterpartyDetail.model_validate(client.get(f"/runs/{rid}/counterparties/{cid}").json())
    )
    assert not extra, extra
    tid = client.get(f"/runs/{rid}/positions", params={"by": "trade_id"}).json()[0]["trade_id"]
    extra = _walk_extras(s.TradeDetail.model_validate(client.get(f"/runs/{rid}/trades/{tid}").json()))
    assert not extra, extra


def test_errors_are_problem_details(client):
    r = client.get("/runs/nope/summary")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/problem+json")
    p = r.json()
    assert p["code"] == "RUN_NOT_FOUND" and p["status"] == 404 and p["title"] == "Not found"
    assert p["instance"] == "/runs/nope/summary" and p["context"] == {"run_id": "nope"}
    rid = client.get("/runs").json()[0]["run_id"]
    assert client.get(f"/runs/{rid}/trades/NOPE").json()["code"] == "NOT_FOUND"
    assert client.get("/runs/latest/reconciliation").json()["code"] == "RECONCILIATION_NOT_FOUND"
    assert client.get("/lab/nope").json()["code"] == "LAB_NOT_FOUND"
    r = client.get("/runs/latest/risk-pack/docx")
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"
    bid = client.get("/breaches", params={"open_only": False}).json()[0]["breach_id"]
    r = client.post(f"/breaches/{bid}/acknowledge", json={"comment": "no actor"})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED" and "actor" in r.json()["detail"]
    r = client.post(f"/breaches/{bid}/acknowledge", json={"actor": "Head of Desk"})
    assert r.status_code == 409 and r.json()["code"] == "WORKFLOW_CONFLICT"
    assert r.json()["title"] == "Workflow rule violated" and r.json()["detail"]
    r = client.post(f"/breaches/{bid}/close", json={"actor": "Head of Desk"})
    assert r.status_code == 422 and r.json()["context"] == {"field": "reason"}


def test_both_clients_raise_the_same_errors(db_path, client):
    from novera.api.client import HttpClient
    from novera.api.errors import ConflictError, NotFoundError, RunNotFoundError

    local = LocalClient(db_path)
    http = HttpClient("http://testserver")
    http.http = client  # the TestClient is an httpx client
    for c in (local, http):
        with pytest.raises(RunNotFoundError) as e:
            c.summary(run_id="nope")
        assert e.value.code == "RUN_NOT_FOUND" and e.value.run_id == "nope" and e.value.status == 404
        with pytest.raises(NotFoundError):
            c.trade("NOPE")
        assert c.reconciliation() is None
        bid = c.breaches(open_only=False)[0]["breach_id"]
        with pytest.raises(ConflictError) as e:
            c.acknowledge(bid, "Head of Desk")
        assert e.value.code == "WORKFLOW_CONFLICT" and e.value.status == 409
        csv = c.trade_extract_csv(asset_class=["RATES"])
        assert isinstance(csv, bytes) and csv.startswith(b"trade_id,")
    with pytest.raises(NotFoundError) as e:
        http.risk_pack_content(fmt="pdf")
    assert e.value.code == "RISK_PACK_NOT_BUILT"
    # the typed response always carries every declared key (null when the run lacks it)
    strip = lambda d: {k: v for k, v in d.items() if v is not None}  # noqa: E731
    assert strip(local.summary()["summary"]) == strip(http.summary()["summary"])
    assert local.limits() == http.limits()


def test_risk_pack_download(client):
    r = client.post("/runs/latest/risk-pack", params={"pdf": False})
    assert r.status_code == 200
    r = client.get("/runs/latest/risk-pack/html")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html") and b"<" in r.content
    assert client.get("/runs/latest/risk-pack/pdf").json()["code"] == "RISK_PACK_NOT_BUILT"


def test_openapi_schema_is_committed():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(
        [sys.executable, str(root / "scripts" / "export_openapi.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_cors_setting_parses_a_comma_list():
    from novera.config import Settings

    assert Settings(cors_origins=" http://localhost:5173, http://127.0.0.1:5173 ").cors_origin_list == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    assert Settings(cors_origins="").cors_origin_list == []


def test_product_catalogue_matches_pricers_and_methodology(db_path):
    """The catalogue is reference data read from code: it must name every product, the model
    each pricer actually writes on valuation rows, and a methodology record that exists."""
    from pathlib import Path

    from novera.domain.enums import ProductType
    from novera.pricing import PRICERS
    from novera.pricing.catalogue import (
        PRODUCT_CATALOGUE,
        Appropriateness,
        instrument_classes,
        model_inventory,
    )

    assert set(PRODUCT_CATALOGUE) == set(ProductType) == set(PRICERS) == set(instrument_classes())
    docs = Path(__file__).resolve().parents[1] / "docs" / "methodology"
    for spec in PRODUCT_CATALOGUE.values():
        assert list(docs.glob(f"{spec.methodology}-*.md")), f"{spec.methodology} has no methodology record"
        for col in ("market_standard", "simplifications", "validation"):
            assert getattr(spec, col).strip(), f"{spec.product_type}: inventory column {col} is empty"
        assert isinstance(spec.appropriateness, Appropriateness)
    # The inventory (MV-001) is rendered from the same catalogue; the committed table must be current.
    import subprocess
    import sys

    inv = model_inventory()
    assert inv["record"] == "MV-001" and len(inv["rows"]) == len(ProductType)
    assert sum(inv["summary"].values()) == len(ProductType)
    assert list(docs.glob("MV-001-*.md")), "MV-001 has no methodology record"
    r = subprocess.run(
        [sys.executable, str(docs.parents[1] / "scripts" / "export_model_inventory.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    with DuckDBRepository(db_path, read_only=True) as repo:
        v = RiskService(repo).valuation().dropna(subset=["model"])
    seen = v.groupby("product_type")["model"].agg(lambda s: set(s))
    assert len(seen) >= 10, "the test portfolio should span most products"
    for pt, models in seen.items():
        assert models == {PRODUCT_CATALOGUE[ProductType(pt)].model}, (pt, models)


def test_measure_catalogue_matches_methodology_records():
    """Every catalogued measure points at a record whose title and version match, and every
    market, counterparty, regulatory, fund and data-quality record is catalogued."""
    import re
    from pathlib import Path

    from novera.risk.catalogue import MEASURE_CATALOGUE

    docs = Path(__file__).resolve().parents[1] / "docs" / "methodology"
    catalogued = {m.methodology: m for a in MEASURE_CATALOGUE for m in a.measures}
    assert len(catalogued) == sum(len(a.measures) for a in MEASURE_CATALOGUE), "duplicate measure id"
    for mid, spec in catalogued.items():
        files = list(docs.glob(f"{mid}-*.md"))
        assert files, f"{mid} has no methodology record"
        text = files[0].read_text(encoding="utf-8")
        head = re.match(r"# (.+?)\s+\(ID: (\S+)\)", text)
        assert head and head.group(2) == mid, files[0].name
        assert head.group(1).strip() == spec.title, (mid, head.group(1), spec.title)
        version = re.search(r"\|\s*Version\s*\|\s*([0-9.]+)\s*\|", text)
        assert version and version.group(1) == spec.version, (mid, spec.version)
    ids = (re.match(r"^(MR|CR|REG|HF|DQ)-(\d{3})-", f.name) for f in docs.glob("*.md"))
    expected = {f"{m.group(1)}-{m.group(2)}" for m in ids if m}
    assert expected <= set(catalogued), f"records not catalogued: {sorted(expected - set(catalogued))}"


def test_admin_rerun_endpoints(client):
    opts = client.get("/admin/rerun/options").json()
    assert opts["record"] == "OPS-002" and {x["name"] for x in opts["stages"]} >= {
        "valuation",
        "var",
        "stress",
        "limits",
    }
    r = client.post("/admin/rerun", json={"run_id": "latest", "stage": "nope", "actor": "Risk Control"})
    assert r.status_code == 422 and "unknown stage" in r.text
    r = client.post(
        "/admin/rerun", json={"run_id": "latest", "stage": "stress", "actor": "Risk Control", "reason": "t"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (
        body["run_type"] == "RERUN" and body["rerun"]["stage"] == "stress" and body["rerun"]["changed"] == {}
    )
    assert body["rerun"]["stale_stages"] == ["limits", "counterparty"]
    runs = client.get("/runs").json()
    assert any(x["run_id"] == body["run_id"] and x["run_type"] == "RERUN" for x in runs)
    assert client.get("/runs/latest/summary").json()["run_id"] != body["run_id"]  # latest stays the EOD
    assert client.get("/admin/rerun/options").json()["reruns"][0]["run_id"] == body["run_id"]
    assert client.get(f"/runs/{body['run_id']}/stress").json()


def test_signoff_endpoints(client):
    st = client.get("/runs/latest/signoff").json()
    assert st["record"] == "OPS-003" and st["release_status"] == "PENDING" and st["required_total"] >= 5
    r = client.post("/runs/latest/signoff/VAR/sign", json={"actor": "Head of Market Risk", "comment": "ok"})
    assert r.status_code == 200, r.text
    assert next(m for m in r.json()["metrics"] if m["metric_id"] == "VAR")["status"] == "SIGNED"
    assert client.post("/runs/latest/signoff/VAR/sign", json={"actor": "x"}).status_code == 409
    assert client.post("/runs/latest/signoff/STRESS/reject", json={"actor": "x"}).status_code == 409
    assert client.post("/runs/latest/signoff/NOPE/sign", json={"actor": "x"}).status_code == 409
    assert client.get("/admin/signoff/policy").json()["source"] == "defaults"
    r = client.post("/admin/signoff/policy", json={"actor": "CRO", "required": ["VAR"], "comment": "narrow"})
    assert r.status_code == 200 and r.json()["required"] == ["VAR"] and r.json()["source"] == "stored"
    st = client.get("/runs/latest/signoff").json()
    assert st["release_status"] == "RELEASED" and st["released_by"] == "Head of Market Risk"
    q = client.get("/signoff/queue").json()
    assert q and q[0]["run_id"] == st["run_id"] and q[0]["release_status"] == "RELEASED"
    events = client.get("/audit", params={"subject": st["run_id"]}).json()
    assert any(e["event_type"] == "METRIC_SIGNED" for e in events)


def test_var_setup_endpoints(client):
    st = client.get("/admin/var/setup").json()
    assert (
        st["record"] == "OPS-004" and st["source"] == "defaults" and st["headline"] == "VAR_99_HS_FULL_BASE"
    )
    assert st["options"]["metrics"] == ["VAR", "ES", "STRESSED_VAR"] and st["templates"]["hedge_fund"]
    bad = client.post(
        "/admin/var/setup",
        json={
            "actor": "CRO",
            "measures": [
                {
                    "goal": "LIMIT",
                    "metric": "ES",
                    "confidence": 0.975,
                    "shocks": "HISTORICAL",
                    "compute": "FULL_REVALUATION",
                }
            ],
        },
    )
    assert bad.status_code == 409 and "headline" in bad.json()["detail"]
    r = client.post("/admin/var/template", json={"actor": "CRO", "template": "hedge_fund"})
    assert r.status_code == 200 and r.json()["source"] == "stored"
    assert r.json()["measures"][0]["decay"] == 0.94 and r.json()["measures"][0]["limit_type"] == "VAR"
    assert client.post("/admin/var/template", json={"actor": "CRO", "template": "nope"}).status_code == 409
    events = client.get("/audit", params={"subject": "var_setup"}).json()
    assert any(e["event_type"] == "VAR_SETUP_CHANGED" for e in events)
    # The stored run keeps its own matrix: measure-level reads work on it.
    vs = client.get("/runs/latest/var/summary").json()
    mid = vs[0]["measure_id"]
    assert vs[0]["goal"] == "LIMIT" and vs[0]["value"] == vs[0]["var"]
    by = client.get("/runs/latest/var", params={"by": "asset_class", "measure_id": mid}).json()
    assert by and sum(x["component_var"] for x in by) == pytest.approx(vs[0]["var"], rel=1e-6)
    sc = client.get("/runs/latest/var/measure-scenarios", params={"measure_id": mid}).json()
    assert sc and sum(x["weight"] for x in sc) == pytest.approx(1.0)
    restore = client.post(
        "/admin/var/setup", json={"actor": "CRO", "measures": st["measures"], "comment": "back"}
    )
    assert restore.status_code == 200 and restore.json()["headline"] == st["headline"]
