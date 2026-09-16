from datetime import date

import pandas as pd
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
    gen = generate_portfolio(
        org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=150, seed=77, market_history=hist)
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
    return path


@pytest.fixture(scope="module")
def result(db_path, tmp_path_factory):
    with DuckDBRepository(db_path) as repo:
        return run_eod(
            repo,
            EODConfig(counterparty=False, regulatory=False, var=VaRConfig(window_days=200), workers=1),
            runs_dir=tmp_path_factory.mktemp("runs"),
        )


def test_run_record_and_verdict(result):
    r = result.run
    assert r.status == "COMPLETED" and r.run_id.startswith("run_")
    assert r.verdict == "AMBER", [f.code for f in result.dq.findings]
    codes = {f.code for f in result.dq.findings}
    assert {
        "MD_MISSING_FACTOR",
        "MD_STALE_FACTOR",
        "TRADE_INVALID",
        "TRADE_UNKNOWN_BOOK",
        "TRADE_UNKNOWN_COUNTERPARTY",
    } <= codes
    missing = next(f for f in result.dq.findings if f.code == "MD_MISSING_FACTOR")
    assert missing.subject == "IR:USD:7Y" and missing.affected_trade_ids
    assert {
        "valuation",
        "sensitivities",
        "var",
        "stress",
        "pnl_attribution",
        "monte_carlo",
        "backtest",
        "concentration",
        "liquidity",
    } <= set(r.model_versions)
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
        assert set(summary["method"]) == {
            "historical_full_revaluation",
            "delta_gamma_vega",
            "monte_carlo_delta_gamma_vega",
        }
        assert len(repo.load_run_frame(rid, "backtest_summary")) == 2
        assert not repo.load_run_frame(rid, "concentration").empty
        assert not repo.load_run_frame(rid, "liquidity_buckets").empty
        assert len(repo.load_run_frame(rid, "stress_summary")) == len(result.stress)
        assert (repo.load_run_frame(rid, "limits")["status"] == "BREACH").sum() == result.run.summary[
            "breaches"
        ]
        assert len(repo.load_run_frame(rid, "dq_findings")) == len(result.dq.findings)
        assert repo.load_run_frame(rid, "nothing_here").empty
        events = repo.load_audit_events(subject=rid)
        assert {"RUN_STARTED", "RUN_FINISHED", "DQ_FINDING"} <= set(events["event_type"])
        breaches = repo.load_audit_events()
        assert (breaches["event_type"] == "LIMIT_BREACH").sum() == result.run.summary["breaches"]


def test_rerun_is_reproducible(db_path, result, tmp_path_factory):
    with DuckDBRepository(db_path) as repo:
        again = run_eod(
            repo,
            EODConfig(counterparty=False, regulatory=False, var=VaRConfig(window_days=200), workers=1),
            persist=False,
        )
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
    bond = GovernmentBond(
        instrument_id="B",
        currency="USD",
        issuer="UST",
        coupon_rate=0.05,
        issue_date=_d(2025, 11, 15),
        maturity_date=_d(2035, 11, 15),
    )
    t = Trade(
        trade_id="T",
        instrument=bond,
        direction=BuySell.BUY,
        quantity=100e6,
        trade_price=100,
        trade_date=_d(2026, 1, 5),
        book_id="B",
        trader_id="T",
        counterparty_id="X",
        clearing=ClearingType.EXCHANGE,
    )
    pf = Portfolio([t], today, "USD")
    out = explain_pnl(pf, prev, [t])
    carry = out.steps.set_index("step")["pnl"]["CARRY"]
    # A 2.5m coupon paid on 15 May crosses the window: carry must be small and positive, not -2.5m.
    assert 0 < carry < 200_000, carry


def test_partial_rerun_of_a_stage(db_path, result, tmp_path):
    from novera.workflows.rerun import STAGE_BY_NAME, rerun_stage

    parent = result.run
    with DuckDBRepository(db_path) as repo:
        before_summary = repo.load_run(parent.run_id).summary
        before_stress = repo.load_run_frame(parent.run_id, "stress_summary")
        breaches_before = len(repo.load_breaches(open_only=False))
        res = rerun_stage(repo, parent.run_id, "stress", "Risk Control", "fixture check", runs_dir=tmp_path)
        run = res.run
        assert run.run_type == "RERUN" and run.status == "COMPLETED" and run.run_id != parent.run_id
        assert run.config["rerun"]["parent_run_id"] == parent.run_id and run.config_hash != parent.config_hash
        assert (
            run.business_date == parent.business_date and run.market_snapshot_id == parent.market_snapshot_id
        )
        # The parent is untouched and the breach workflow was not touched either.
        again = repo.load_run(parent.run_id)
        assert again.summary == before_summary and again.config_hash == parent.config_hash
        assert len(repo.load_breaches(open_only=False)) == breaches_before
        assert repo.latest_run().run_id == parent.run_id  # a re-run never becomes the latest EOD run
        # Every result table was copied under the new id; the stage reproduced the parent exactly.
        for name in ("valuation", "sensitivities", "limits", "var_summary", "dq_findings"):
            assert len(repo.load_run_frame(run.run_id, name)) == len(
                repo.load_run_frame(parent.run_id, name)
            ), name
        after = repo.load_run_frame(run.run_id, "stress_summary")
        key = lambda d: d.sort_values("scenario_id").reset_index(drop=True)  # noqa: E731
        pd.testing.assert_frame_equal(key(after), key(before_stress))
        assert res.changed == {} and res.stale_stages == STAGE_BY_NAME["stress"].dependents
        assert run.summary["rerun"]["stage"] == "stress" and run.summary["rerun"]["copied_tables"] > 10
        events = repo.load_audit_events(subject=run.run_id)
        assert {"RERUN_STARTED", "RERUN_FINISHED"} <= set(events["event_type"])
        with pytest.raises(ValueError):
            rerun_stage(repo, parent.run_id, "alerts", "x")
        with pytest.raises(ValueError):
            rerun_stage(repo, parent.run_id, "fund", "x")  # bank face
        # A limits re-run recomputes VaR and stress in memory and reproduces the stored table.
        lim = rerun_stage(repo, parent.run_id, "limits", "Risk Control", runs_dir=tmp_path)
        assert lim.changed == {} and lim.run.summary["breaches"] == parent.summary["breaches"]
        assert len(repo.load_breaches(open_only=False)) == breaches_before


def test_signoff_policy_release_and_override(db_path, result):
    from novera.limits.workflow import WorkflowError
    from novera.workflows import signoff

    run = result.run
    with DuckDBRepository(db_path) as repo:
        pol = signoff.policy(repo)
        assert pol["source"] == "defaults" and "VAR" in pol["required"] and "BACKTEST" not in pol["required"]
        st = signoff.status(repo, run)
        assert st["release_status"] == "PENDING" and st["face"] == "bank"
        assert "FUND" not in {m["metric_id"] for m in st["metrics"]}
        with pytest.raises(WorkflowError):
            signoff.sign(repo, run, "VAR", "")
        with pytest.raises(WorkflowError):
            signoff.sign(repo, run, "FUND", "CRO")  # fund metric on the bank face
        with pytest.raises(WorkflowError):
            signoff.reject(repo, run, "VAR", "Head of Market Risk", "")  # a rejection needs a comment
        st = signoff.sign(repo, run, "VAR", "Head of Market Risk", "reviewed against yesterday")
        var = next(m for m in st["metrics"] if m["metric_id"] == "VAR")
        assert var["status"] == "SIGNED" and var["actor"] == "Head of Market Risk"
        assert var["value"]["var"] == run.summary["var"]  # the value seen is frozen with the signature
        with pytest.raises(WorkflowError):
            signoff.sign(repo, run, "VAR", "Someone else")  # already signed
        st = signoff.reject(repo, run, "STRESS", "Head of Market Risk", "worst scenario looks wrong")
        assert st["release_status"] == "BLOCKED"
        st = signoff.sign(repo, run, "STRESS", "Head of Market Risk", "re-checked, fine")
        assert st["release_status"] == "PENDING"
        with pytest.raises(WorkflowError):
            signoff.set_policy(repo, "CRO", ["NOPE"])
        pol = signoff.set_policy(repo, "CRO", ["VAR", "STRESS", "LIMITS"], "demo policy")
        assert pol["source"] == "stored" and pol["required"] == ["VAR", "STRESS", "LIMITS"]
        st = signoff.status(repo, run)
        assert st["release_status"] == "PENDING" and st["pending"] == ["LIMITS"]
        st = signoff.sign(repo, run, "LIMITS", "Head of Market Risk")
        assert st["release_status"] == "RELEASED" and st["released_by"] == "Head of Market Risk"
        assert repo.load_release(run.run_id) is not None
        types = set(repo.load_audit_events(subject=run.run_id)["event_type"])
        assert {"METRIC_SIGNED", "METRIC_REJECTED", "RUN_RELEASED"} <= types
        # Withdrawing a required signature withdraws the release.
        st = signoff.reject(repo, run, "VAR", "CRO", "found an issue in the USD book")
        assert st["release_status"] == "BLOCKED" and repo.load_release(run.run_id) is None
        assert "RUN_RELEASE_WITHDRAWN" in set(repo.load_audit_events(subject=run.run_id)["event_type"])
        # A RED verdict makes the data-quality sign-off an override that needs a comment.
        red = repo.load_run(run.run_id)
        red.verdict = "RED"
        repo.save_run(red)
        with pytest.raises(WorkflowError):
            signoff.sign(repo, red, "DATA_QUALITY", "Head of Market Risk Control")
        st = signoff.sign(
            repo, red, "DATA_QUALITY", "Head of Market Risk Control", "stale surface proxied; accepted"
        )
        assert next(m for m in st["metrics"] if m["metric_id"] == "DATA_QUALITY")["override"]
        red.verdict = run.verdict
        repo.save_run(red)
        q = signoff.queue(repo)
        assert q and q[0]["run_id"] == run.run_id and q[0]["release_status"] == "BLOCKED"
        assert repo.load_run(run.run_id).summary == run.summary  # sign-off never edits the run
        assert "SIGNOFF_POLICY_CHANGED" in set(repo.load_audit_events(subject="signoff_policy")["event_type"])


def test_var_setup_drives_the_run_and_limits(db_path, result, tmp_path):
    """OPS-004: a stored VaR setup decides what the EOD run produces, which measures feed
    the limits, and is recorded in the run's config so a re-run reproduces it."""
    from novera.limits.workflow import WorkflowError
    from novera.risk.var_measures import DEFAULT_MEASURES
    from novera.workflows import var_setup
    from novera.workflows.rerun import rerun_stage

    with DuckDBRepository(db_path) as repo:
        st = var_setup.setup(repo)
        assert st["source"] == "defaults" and st["record"] == "OPS-004"
        assert [m["measure_id"] for m in st["measures"]] == [m.measure_id for m in DEFAULT_MEASURES]
        assert st["history_start"] < st["history_end"] and st["stress_window"] is not None
        assert {"bank", "hedge_fund"} <= set(st["templates"])
        # The default run recorded the default measures and their values.
        parent = result.run
        assert [m["measure_id"] for m in parent.config["var_measures"]] == [
            m.measure_id for m in DEFAULT_MEASURES
        ]
        assert parent.summary["var_measure_id"] == DEFAULT_MEASURES[0].measure_id
        assert set(parent.summary["var_measures"]) == {m.measure_id for m in DEFAULT_MEASURES}
        vs = repo.load_run_frame(parent.run_id, "var_summary")
        assert list(vs["goal"]) == ["LIMIT", "LIMIT", "INFORMATION", "INFORMATION"]
        assert vs.iloc[1]["value"] == pytest.approx(parent.summary["es"])
        assert set(repo.load_run_frame(parent.run_id, "var_measure_contributions")["measure_id"]) == set(
            vs["measure_id"]
        )
        # Validation errors reach the caller as workflow errors.
        with pytest.raises(WorkflowError):
            var_setup.set_setup(repo, "", [m.to_dict() for m in DEFAULT_MEASURES])
        with pytest.raises(WorkflowError):
            var_setup.set_setup(repo, "CRO", [])
        with pytest.raises(WorkflowError):
            var_setup.set_setup(
                repo,
                "CRO",
                [
                    {
                        "goal": "LIMIT",
                        "metric": "STRESSED_VAR",
                        "confidence": 0.99,
                        "shocks": "HISTORICAL",
                        "compute": "FULL_REVALUATION",
                        "window_start": "1999-01-01",
                        "window_end": "1999-12-31",
                    }
                ],
            )
        # A hedge-fund style matrix plus a stressed VaR that feeds limits.
        start, end = st["stress_window"]
        new = [
            {
                "goal": "LIMIT",
                "metric": "VAR",
                "confidence": 0.95,
                "shocks": "HISTORICAL_WEIGHTED",
                "compute": "FULL_REVALUATION",
                "window_years": 0.8,
                "decay": 0.94,
            },
            {
                "goal": "INFORMATION",
                "metric": "ES",
                "confidence": 0.95,
                "shocks": "HISTORICAL_WEIGHTED",
                "compute": "FULL_REVALUATION",
                "window_years": 0.8,
                "decay": 0.94,
            },
            {
                "goal": "LIMIT",
                "metric": "STRESSED_VAR",
                "confidence": 0.99,
                "shocks": "HISTORICAL",
                "compute": "FULL_REVALUATION",
                "window_start": start,
                "window_end": end,
            },
            {
                "goal": "INFORMATION",
                "metric": "VAR",
                "confidence": 0.99,
                "shocks": "HISTORICAL",
                "compute": "SENSITIVITY",
                "window_years": 0.8,
                "enabled": False,
            },
        ]
        st = var_setup.set_setup(repo, "CRO", new, "fund style")
        assert st["source"] == "stored" and st["headline"] == "VAR_95_WHS_FULL_0P8Y_L94"
        assert st["measures"][0]["updated_by"] == "CRO" and not st["measures"][3]["enabled"]
        ev = repo.load_audit_events(subject="var_setup")
        assert "VAR_SETUP_CHANGED" in set(ev["event_type"])
        assert [m.measure_id for m in var_setup.measures_for_run(repo)] == [
            m["measure_id"] for m in st["measures"][:3]
        ]

        res = run_eod(
            repo,
            EODConfig(counterparty=False, regulatory=False, var=VaRConfig(window_days=200), workers=1),
            runs_dir=tmp_path,
        )
        r = res.run
        assert r.summary["var_confidence"] == 0.95 and r.summary["es_confidence"] == 0.95
        assert r.summary["var_measure_id"] == "VAR_95_WHS_FULL_0P8Y_L94"
        assert r.summary["stressed_var"] is not None and r.summary["challenger_var"] is None
        assert r.summary["monte_carlo_var"] is None
        assert [m["measure_id"] for m in r.config["var_measures"]] == [
            m["measure_id"] for m in st["measures"][:3]
        ]
        vs = repo.load_run_frame(r.run_id, "var_summary")
        assert len(vs) == 3 and vs.iloc[0]["decay"] == 0.94 and vs.iloc[2]["window_start"] == start
        assert (
            vs.iloc[0]["scenarios"] == 200 and vs.iloc[0]["method"] == "historical_weighted_full_revaluation"
        )
        ws = repo.load_run_frame(r.run_id, "var_measure_scenarios")
        w0 = ws[ws["measure_id"] == vs.iloc[0]["measure_id"]]["weight"]
        assert w0.sum() == pytest.approx(1.0) and w0.max() > 10 * w0.min()
        assert repo.load_run_frame(r.run_id, "var_contributions_challenger").empty
        # Limits read the LIMIT rows: the firm VaR limit is on the 95% weighted figure.
        lim = repo.load_run_frame(r.run_id, "limits")
        firm_var = lim[lim["limit_id"] == "FIRM_VAR"].iloc[0]
        assert firm_var["current"] == pytest.approx(r.summary["var"], rel=1e-6)
        assert firm_var["current"] != pytest.approx(parent.summary["var"], rel=1e-3)
        firm_es = lim[lim["limit_id"] == "FIRM_ES"].iloc[
            0
        ]  # no ES limit row: falls back to the headline's ES
        assert firm_es["current"] == pytest.approx(res.var.es, rel=1e-6)
        assert r.timings["var"] > 0 and any(k.startswith("var:") for k in r.timings)
        # The parent's re-run keeps the parent's matrix, not today's setup.
        rr = rerun_stage(repo, parent.run_id, "var", "Risk Control", runs_dir=tmp_path)
        assert rr.changed == {} and rr.run.summary["var_measure_id"] == DEFAULT_MEASURES[0].measure_id
        assert len(repo.load_run_frame(rr.run.run_id, "var_summary")) == 4
        # Restore the defaults for the tests that follow.
        var_setup.set_setup(repo, "CRO", [m.to_dict() for m in DEFAULT_MEASURES], "back to defaults")
