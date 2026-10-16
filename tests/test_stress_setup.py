"""The stress setup (OPS-005, decision round 46): a versioned, approved scenario library that
reproduces the built-in results exactly, splits scenarios into a LIMIT set and an INFORMATION
set, enforces four eyes and the desk heads' scope, and flags overdue reviews."""

from datetime import date

import pandas as pd
import pytest

from novera.api.errors import ForbiddenError
from novera.limits.workflow import WorkflowError
from novera.market_data.history import MarketHistory
from novera.risk import HYPOTHETICAL_LIBRARY, VaRConfig
from novera.risk.stress import historical_episodes
from novera.risk.stress_definitions import (
    ScenarioDefinition,
    built_in_definitions,
    episode_windows,
    resolve,
    validate,
)
from novera.security import seed_users
from novera.simulation import (
    TradeGeneratorConfig,
    build_counterparty_universe,
    build_global_macro_bank,
    build_limits,
    generate_portfolio,
)
from novera.simulation.market_data import MarketSimConfig, generate_market_data
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows import stress_setup
from novera.workflows.eod import EODConfig, run_eod

AS_OF = date(2026, 9, 11)
CFG = dict(counterparty=False, regulatory=False, var=VaRConfig(window_days=150), workers=1)

EXTRA = {
    "scenario_id": "jpy_carry_unwind",
    "name": "Yen carry unwind",
    "kind": "HYPOTHETICAL",
    "goal": "INFORMATION",
    "description": "USD/JPY -12%, equity -8%, vol +8",
    "rules": [
        {"prefix": "FX:USDJPY", "size": -0.12},
        {"prefix": "EQIDX:", "size": -0.08},
        {"prefix": "VOL:", "vol_points": 0.08},
    ],
    "tags": ["CROSS_ASSET", "FX", "CROWDING"],
    "narrative": "Leveraged yen-funded positions are cut at once after a hawkish surprise",
    "catalyst": "Bank of Japan hike with a hawkish guidance",
}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    path = tmp_path_factory.mktemp("stress_setup") / "s.duckdb"
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=1.0, seed=11))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(
        org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=110, seed=11, market_history=hist)
    )
    with DuckDBRepository(path) as repo:
        repo.init_schema()
        repo.save_organisation(org)
        repo.save_counterparties(cp.counterparties)
        repo.save_netting_sets(cp.netting_sets, cp.csas)
        repo.save_limits(build_limits(org, cp))
        seed_users(repo, org.firm.firm_id)
        repo.save_portfolio_snapshot(gen.snapshot)
        repo.save_risk_factors(md.universe)
        repo.save_market_history(md.history)
        repo.save_market_episodes(md.episodes)
        repo.save_market_snapshot(md.previous_snapshot)
        repo.save_market_snapshot(md.snapshot)
    return {"path": path, "hist": hist, "md": md, "runs": tmp_path_factory.mktemp("runs")}


def _frames(repo, run_id):
    st = repo.load_run_frame(run_id, "stress").sort_values(["scenario_id", "trade_id"]).reset_index(drop=True)
    lim = repo.load_run_frame(run_id, "limits")[["limit_id", "current", "status"]]
    return st, lim.sort_values("limit_id").reset_index(drop=True)


def test_built_ins_are_the_library_the_engine_always_ran(world):
    """The built-in definitions resolve to exactly the scenarios of the old code path."""
    from novera.risk.stress_definitions import CONSISTENT_BUILT_INS, Expander

    hist, md = world["hist"], world["md"]
    universe = {f.factor_id: f for f in md.universe}
    windows = episode_windows(hist, md.episodes)
    ex = Expander(hist, md.snapshot, universe, windows)
    resolved, unresolved = resolve(built_in_definitions(list(windows)), windows, expander=ex)
    old = list(HYPOTHETICAL_LIBRARY) + historical_episodes(hist, md.episodes)
    assert not unresolved and len(resolved) == len(old)
    assert all(r.definition.goal == "LIMIT" and r.definition.source == "BUILT_IN" for r in resolved)
    for r, o in zip(resolved, old, strict=True):
        if r.definition.scenario_id in CONSISTENT_BUILT_INS:
            # Round 47: the rules are unchanged and the scenario now gives every shock explicitly.
            assert r.scenario.rules == o.rules and r.scenario.shocks is not None and r.definition.version == 2
        else:
            assert r.scenario == o  # curve and basis sensitivities and the episodes: as always
    # A database without stored windows falls back to the offsets, as before (decision 42.4).
    fb, _ = resolve(
        built_in_definitions(list(episode_windows(hist, None))), episode_windows(hist, None), expander=ex
    )
    assert [r.scenario for r in fb][len(HYPOTHETICAL_LIBRARY) :] == historical_episodes(hist, {})
    # Round trip through the stored form.
    d = resolved[-1].definition
    assert ScenarioDefinition.from_dict(d.to_dict()) == d


def test_validation_names_what_is_wrong(world):
    fids = [f.factor_id for f in world["md"].universe]
    eps = list(world["md"].episodes)
    rng = (world["hist"].dates[0], world["hist"].dates[-1])
    ok = ScenarioDefinition.from_dict(EXTRA)
    assert validate(ok, fids, eps, rng) == []
    bad = ScenarioDefinition.from_dict(
        {
            **EXTRA,
            "scenario_id": "X",
            "goal": "SOMETIMES",
            "tags": ["NOPE"],
            "rules": [
                {"prefix": "ZZZ:", "size": 0.1},
                {"prefix": "EQIDX:", "vol_points": 0.1},
                {"prefix": "IR:USD:", "size": 0.01, "tenor_filter": ["4Y"]},
                {"prefix": "EQIDX:", "size": 0.0},
            ],
        }
    )
    errs = " | ".join(validate(bad, fids, eps, rng))
    for text in (
        "scenario_id",
        "goal",
        "unknown tags",
        "matches no risk factor",
        "VOL: surfaces",
        "tenors",
        "zero",
    ):
        assert text in errs, text
    hist_bad = ScenarioDefinition(
        "old_window", "Old", "HISTORICAL", window_start="1999-01-01", window_end="1999-06-30"
    )
    assert "inside the stored history" in " ".join(validate(hist_bad, fids, eps, rng))
    assert "unknown episode" in " ".join(
        validate(ScenarioDefinition("e1", "E", "HISTORICAL", episode_id="nope"), fids, eps, rng)
    )
    with pytest.raises(ValueError):
        ScenarioDefinition.from_dict({**EXTRA, "colour": "blue"})


def test_store_reproduces_the_run_and_governs_the_library(world):
    from novera.api.service import RiskService, RiskWriteService

    with DuckDBRepository(world["path"]) as repo:
        users = {u.user_id: u for u in repo.load_users()}
        base = run_eod(repo, EODConfig(**CFG), runs_dir=world["runs"])
        st0 = stress_setup.setup(repo)
        assert st0["source"] == "defaults" and st0["summary"]["limit"] == st0["summary"]["scenarios"]
        assert base.run.summary["worst_stress"] == base.run.summary["worst_stress_all"]
        recorded = base.run.config["stress_scenarios"]
        assert [d["scenario_id"] for d in recorded][-1] in world["md"].episodes
        assert all(d["review_due"] is None for d in recorded) and recorded[-1]["resolved_window"]
        summ = repo.load_run_frame(base.run.run_id, "stress_summary")
        assert set(summ["goal"]) == {"LIMIT"} and set(summ["version"]) == {1, 2}  # v2: consistent (round 47)

        # Seeding stores the built-ins as approved version 1; the next run is identical.
        hmr = RiskWriteService(repo, users["head-of-market-risk"])
        hmr.seed_stress_library("adopt")
        st1 = stress_setup.setup(repo)
        assert st1["source"] == "stored" and all(s["status"] == "APPROVED" for s in st1["scenarios"])
        assert st1["scenarios"][0]["review_due"] == "2026-12-11"
        seeded = run_eod(repo, EODConfig(**CFG), runs_dir=world["runs"])
        a, b = _frames(repo, base.run.run_id), _frames(repo, seeded.run.run_id)
        pd.testing.assert_frame_equal(a[0], b[0])
        pd.testing.assert_frame_equal(a[1], b[1])
        for k in ("worst_stress", "worst_stress_id", "breaches", "warnings", "dq_verdict"):
            assert seeded.run.summary[k] == base.run.summary[k], k

        # A desk head may propose information scenarios only; the risk function decides.
        desk_head = next(u for u in users.values() if u.role.value == "DESK_HEAD")
        dh = RiskWriteService(repo, desk_head)
        with pytest.raises(ForbiddenError):
            dh.propose_stress_scenario({**EXTRA, "goal": "LIMIT"})
        with pytest.raises(ForbiddenError):
            dh.propose_stress_scenario({**built_in_definitions([])[0].to_dict(), "goal": "INFORMATION"})
        draft = dh.propose_stress_scenario(EXTRA, "what would hurt my desk")
        assert (
            draft["status"] == "DRAFT"
            and draft["version"] == 1
            and draft["area"] == ",".join(desk_head.desks)
        )
        with pytest.raises(WorkflowError, match="open draft"):
            RiskWriteService(repo, users["risk-control"]).propose_stress_scenario(EXTRA)
        with pytest.raises(ForbiddenError):
            RiskWriteService(repo, users["risk-control"]).decide_stress_scenario("jpy_carry_unwind", 1, True)
        # Drafts never run.
        assert "jpy_carry_unwind" not in [d["scenario_id"] for d in stress_setup.definitions_for_run(repo)]

        cro = RiskWriteService(repo, users["cro"])
        ok = cro.decide_stress_scenario("jpy_carry_unwind", 1, True, "fits the desk's carry book")
        assert ok["status"] == "APPROVED" and ok["decided_by"] == "CRO"
        # Four eyes: the CRO's own proposal needs the Head of Market Risk.
        cro.propose_stress_scenario(
            {**EXTRA, "rules": EXTRA["rules"][:2], "description": "no vol"}, "simpler"
        )
        with pytest.raises(WorkflowError, match="four eyes"):
            cro.decide_stress_scenario("jpy_carry_unwind", 2, True)
        hmr.decide_stress_scenario("jpy_carry_unwind", 2, False, "keep the vol leg")
        # A risk-function change to a built-in: credit shock as information only.
        credit = {**built_in_definitions([])[8].to_dict(), "goal": "INFORMATION"}
        assert credit["scenario_id"] == "credit_wider_150"
        cv = hmr.propose_stress_scenario(credit, "information until recalibrated")["version"]
        assert cv == 3  # seeded at version 2, the consistent built-in of round 47
        cro.decide_stress_scenario("credit_wider_150", cv, True)

        st2 = stress_setup.setup(repo)
        by = {s["scenario_id"]: s for s in st2["scenarios"]}
        assert by["jpy_carry_unwind"]["version"] == 1 and by["credit_wider_150"]["goal"] == "INFORMATION"
        assert {(h["scenario_id"], h["status"]) for h in st2["history"]} == {
            ("jpy_carry_unwind", "REJECTED"),
            ("credit_wider_150", "SUPERSEDED"),
        }
        assert st2["summary"]["information"] == 2
        events = set(repo.load_audit_events(subject="stress_setup")["event_type"])
        assert {"STRESS_LIBRARY_SEEDED", "STRESS_SCENARIO_PROPOSED", "STRESS_SCENARIO_APPROVED"} <= events
        assert "STRESS_SCENARIO_REJECTED" in events

        # The run reports every scenario but limits read only the LIMIT set.
        r = run_eod(repo, EODConfig(**CFG), runs_dir=world["runs"])
        summ = repo.load_run_frame(r.run.run_id, "stress_summary").set_index("scenario_id")
        assert summ.loc["jpy_carry_unwind", "goal"] == "INFORMATION"
        assert summ.loc["credit_wider_150", "version"] == 3
        assert r.run.summary["stress_scenarios"] == r.run.summary["stress_limit_scenarios"] + 2
        lim_ids = {x.scenario.scenario_id for x in r.stress} - {"jpy_carry_unwind", "credit_wider_150"}
        firm = repo.load_run_frame(r.run.run_id, "limits").set_index("limit_id").loc["FIRM_STRESS", "current"]
        worst_limit = min(x.total for x in r.stress if x.scenario.scenario_id in lim_ids)
        assert firm == pytest.approx(max(-worst_limit, 0.0))
        worst_all = min(x.total for x in r.stress)
        assert r.run.summary["worst_stress_all"] == pytest.approx(worst_all)
        assert r.run.summary["worst_stress"] == pytest.approx(worst_limit)

        # The re-run of the stage replays the parent's definitions, not today's library.
        from novera.workflows.rerun import rerun_stage

        hmr.propose_stress_scenario({**EXTRA, "enabled": False}, "retire")
        cro.decide_stress_scenario("jpy_carry_unwind", 3, True)
        assert "jpy_carry_unwind" not in [d["scenario_id"] for d in stress_setup.definitions_for_run(repo)]
        rr = rerun_stage(repo, r.run.run_id, "stress", "Risk Control", "check", runs_dir=world["runs"])
        assert rr.changed == {}
        assert "jpy_carry_unwind" in set(repo.load_run_frame(rr.run.run_id, "stress_summary")["scenario_id"])

        # The library page lists the stored scenarios with their governance facts.
        lib = RiskService(repo).stress_library()
        hyp = next(c for c in lib["categories"] if c["category"] == "HYPOTHETICAL")["scenarios"]
        row = next(s for s in hyp if s["scenario_id"] == "jpy_carry_unwind")
        assert row["status"] == "DISABLED" and not row["in_daily_run"] and row["version"] == 3


def test_overdue_review_is_a_minor_finding(world):
    from novera.api.service import RiskWriteService

    with DuckDBRepository(world["path"]) as repo:
        users = {u.user_id: u for u in repo.load_users()}
        defs = stress_setup.definitions_for_run(repo)
        late = [{**d, "review_due": "2026-09-01"} if i == 0 else d for i, d in enumerate(defs)]
        res = run_eod(repo, EODConfig(**CFG, stress_scenarios=late), runs_dir=world["runs"])
        f = [x for x in res.dq.findings if x.code == "STRESS_REVIEW_OVERDUE"]
        assert len(f) == 1 and f[0].severity == "MINOR" and f[0].subject == defs[0]["scenario_id"]
        assert res.run.verdict in ("AMBER", "RED")
        # A review moves the due date three months on the business calendar.
        with pytest.raises(ForbiddenError):
            RiskWriteService(repo, users["risk-control"]).review_stress_scenario(defs[0]["scenario_id"])
        out = RiskWriteService(repo, users["cro"]).review_stress_scenario(
            defs[0]["scenario_id"], "still fits"
        )
        assert out["review_due"] == "2026-12-11" and out["reviewed_by"] == "CRO" and not out["review_overdue"]
        assert stress_setup.add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


def test_a_run_made_before_the_store_reruns_on_the_built_ins(world):
    """A parent whose config predates OPS-005 carries no definitions: its re-run uses the
    built-ins, which is what it ran."""
    from novera.workflows.rerun import rerun_stage

    with DuckDBRepository(world["path"]) as repo:
        r = run_eod(repo, EODConfig(**CFG, stress_scenarios=[]), runs_dir=world["runs"])
        assert r.stress == [] and r.run.summary["worst_stress"] is None
        run = repo.load_run(r.run.run_id)
        run.config.pop("stress_scenarios")
        repo.save_run(run)
        rr = rerun_stage(repo, run.run_id, "stress", "Risk Control", "legacy", runs_dir=world["runs"])
        assert rr.run.summary["stress_scenarios"] == len(HYPOTHETICAL_LIBRARY) + len(world["md"].episodes)
