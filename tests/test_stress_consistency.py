"""Consistent scenarios, PCA shapes, plausibility and calibrated crises (MR-018, round 47)."""

from datetime import date

import numpy as np
import pytest

from novera.market_data.history import MarketHistory
from novera.risk import scenario_covariance as sc_cov
from novera.risk.crisis_tables import CRISIS_TABLES
from novera.risk.stress_definitions import (
    CONSISTENT_BUILT_INS,
    Expander,
    ScenarioDefinition,
    built_in_definitions,
    episode_windows,
    validate,
)
from novera.simulation.market_data import MarketSimConfig, generate_market_data

AS_OF = date(2026, 9, 11)


@pytest.fixture(scope="module")
def mkt():
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=2.0, seed=21))
    hist = MarketHistory.from_long(md.history)
    universe = {f.factor_id: f for f in md.universe}
    windows = episode_windows(hist, md.episodes)
    return {
        "md": md,
        "hist": hist,
        "universe": universe,
        "windows": windows,
        "ex": Expander(hist, md.snapshot, universe, windows),
    }


def _def(**kw):
    base = dict(scenario_id="t_scn", name="Test", kind="HYPOTHETICAL", expand=True)
    return ScenarioDefinition(**{**base, **kw})


def test_expansion_keeps_the_rules_and_fills_the_rest(mkt):
    ex = mkt["ex"]
    d = _def(rules=({"prefix": "EQIDX:", "size": -0.2},))
    shocks, info = ex.explain(d)
    # Every equity index carries exactly the rule's shock; everything else is filled.
    for f in mkt["md"].snapshot.factors_with_prefix("EQIDX:"):
        assert shocks[f] == pytest.approx(-0.2)
    assert info["anchors"] == {"EQIDX:SPX": pytest.approx(-0.2)} and info["covariance"] == "STRESSED"
    assert info["filled"] > 1000 and len(shocks) == len(mkt["universe"])
    # Crisis correlations: credit widens and single stocks fall when indices fall.
    assert shocks["CDS:CDX.NA.HY"] > 0 and shocks["EQ:AAPL"] < 0
    # Linear in the anchor: half the shock, half the fill.
    half, _ = ex.explain(_def(rules=({"prefix": "EQIDX:", "size": -0.1},)))
    assert half["CDS:CDX.NA.HY"] == pytest.approx(shocks["CDS:CDX.NA.HY"] / 2, rel=1e-6)
    # Fills never exceed 1.5 times the factor's largest historical horizon move.
    ms = ex.moves(20)
    big, _ = ex.explain(_def(rules=({"prefix": "EQIDX:SPX", "size": -0.9},)))
    for f in ("CDS:CDX.NA.HY", "IR:USD:10Y", "CRYPTO:BTC"):
        assert abs(big[f]) <= sc_cov.FILL_CAP * ms.max_abs[ms.index[f]] + 1e-12
    # A standalone scenario leaves unnamed factors alone.
    alone, _ = ex.explain(_def(expand=False, rules=({"prefix": "EQIDX:", "size": -0.2},)))
    assert "CDS:CDX.NA.HY" not in alone


def test_plausibility_scores_size_and_consistency(mkt):
    ex = mkt["ex"]
    risk_off = _def(
        rules=({"prefix": "EQIDX:SPX", "size": -0.15}, {"prefix": "CDS:CDX.NA.HY", "size": 150.0})
    )
    _, a = ex.explain(risk_off)
    _, b = ex.explain(
        _def(rules=({"prefix": "EQIDX:SPX", "size": -0.30}, {"prefix": "CDS:CDX.NA.HY", "size": 300.0}))
    )
    # Twice the shocks: twice the z-score, the same correlation z-score.
    assert b["z_score"] == pytest.approx(2 * a["z_score"], rel=1e-6)
    assert b["correlation_z"] == pytest.approx(a["correlation_z"], rel=1e-6)
    assert b["exceedance"] <= a["exceedance"]
    # Credit tightening while equities crash fights the correlations: correlation z-score above 1,
    # and the credit anchor carries most of the inconsistency.
    _, odd = ex.explain(
        _def(rules=({"prefix": "EQIDX:SPX", "size": -0.15}, {"prefix": "CDS:CDX.NA.HY", "size": -150.0}))
    )
    assert odd["correlation_z"] > 1.0 > a["correlation_z"]
    assert odd["z_score"] > a["z_score"]
    deltas = {x["factor_id"]: x["z_delta"] for x in odd["anchor_detail"]}
    assert deltas["CDS:CDX.NA.HY"] > 0
    # The ruler places the global risk-off above a lone vol shock.
    defs = {d.scenario_id: d for d in built_in_definitions(list(mkt["windows"]))}
    assert ex.explain(defs["global_risk_off"])[1]["ruler_z"] > ex.explain(defs["vol_shock_10"])[1]["ruler_z"]


def test_similar_days_fall_back_when_too_few_resemble(mkt):
    hist, ex = mkt["hist"], mkt["ex"]
    ms = ex.moves(20)
    cols = ms.cols(["EQIDX:SPX"])
    reg = sc_cov.regime("SIMILAR", hist, ms, mkt["windows"], cols, np.array([-0.2]))
    assert reg.method in ("SIMILAR", "STRESSED")
    impossible = sc_cov.regime(
        "SIMILAR", hist, ms, mkt["windows"], ms.cols(["EQIDX:SPX", "CDS:CDX.NA.HY"]), np.array([0.0, 0.0])
    )
    assert impossible.method == "STRESSED"
    assert sc_cov.regime("RECENT", hist, ms, None).weights[-1] == pytest.approx(1.0)


def test_pca_shapes_have_fixed_signs(mkt):
    ex = mkt["ex"]
    ms = ex.moves(20)
    reg = sc_cov.regime("STRESSED", mkt["hist"], ms, mkt["windows"])
    level = sc_cov.pca_shape(ms, reg, "IR:USD:", 1, 2.0)
    slope = sc_cov.pca_shape(ms, reg, "IR:USD:", 2, 2.0)
    curv = sc_cov.pca_shape(ms, reg, "IR:USD:", 3, 2.0)
    assert level.explained > slope.explained > curv.explained > 0 and level.nodes >= 10
    assert sum(level.shocks.values()) > 0
    assert slope.shocks["IR:USD:30Y"] > slope.shocks["IR:USD:1M"]
    neg = sc_cov.pca_shape(ms, reg, "IR:USD:", 1, -2.0)
    assert neg.shocks["IR:USD:10Y"] == pytest.approx(-level.shocks["IR:USD:10Y"])
    shocks, info = ex.explain(_def(expand=False, rules=({"pca": "IR:USD:", "component": 2, "sigmas": 3.0},)))
    assert shocks["IR:USD:30Y"] - shocks["IR:USD:1M"] == pytest.approx(
        1.5 * (slope.shocks["IR:USD:30Y"] - slope.shocks["IR:USD:1M"])
    )
    assert info["pca"][0]["nodes"] == level.nodes
    errs = " ".join(
        validate(
            _def(rules=({"pca": "EQIDX:SPX", "component": 4, "sigmas": 0.0},)),
            list(mkt["universe"]),
            [],
            None,
        )
    )
    assert "at least 3 nodes" in errs and "component" in errs and "standard deviations" in errs


def test_built_ins_and_crisis_tables_are_valid(mkt):
    fids = list(mkt["universe"])
    defs = built_in_definitions(list(mkt["windows"]))
    assert {d.scenario_id for d in defs if d.expand} == set(CONSISTENT_BUILT_INS)
    for d in defs:
        assert validate(d, fids, list(mkt["windows"]), None) == [], d.scenario_id
    assert len(CRISIS_TABLES) == 14
    for c in CRISIS_TABLES:
        d = ScenarioDefinition.from_dict(c.definition())
        assert d.source == "CALIBRATED" and d.expand and d.goal == "INFORMATION"
        assert validate(d, fids, [], None) == [], c.scenario_id
        shocks, info = mkt["ex"].explain(d)
        assert info["z_score"] > 0 and len(shocks) == len(fids)


def test_calibrated_crises_and_built_in_upgrades_through_four_eyes(tmp_path):
    """Crises enter as INFORMATION drafts from Risk Control and run once approved; a library
    stored before round 47 receives the consistent built-ins as drafts."""
    from novera.api.service import RiskService, RiskWriteService
    from novera.risk.stress_definitions import _rule_dict
    from novera.security import seed_users
    from novera.simulation import build_global_macro_bank
    from novera.storage.duckdb_repository import DuckDBRepository
    from novera.workflows import stress_setup

    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=1.0, seed=3))
    org = build_global_macro_bank()
    with DuckDBRepository(tmp_path / "c.duckdb") as repo:
        repo.init_schema()
        repo.save_organisation(org)
        seed_users(repo, org.firm.firm_id)
        repo.save_risk_factors(md.universe)
        repo.save_market_history(md.history)
        repo.save_market_episodes(md.episodes)
        repo.save_market_snapshot(md.snapshot)
        users = {u.user_id: u for u in repo.load_users()}
        rc, cro = RiskWriteService(repo, users["risk-control"]), RiskWriteService(repo, users["cro"])
        # A library stored before round 47: the built-ins as standalone version 1.
        cro.seed_stress_library("round 46 library")
        rows = repo.load_stress_scenarios()
        for r in rows:
            if r["scenario_id"] in CONSISTENT_BUILT_INS:
                r["definition"] = {**r["definition"], "expand": False, "version": 1}
                r["version"] = 1
        repo._conn.execute("DELETE FROM stress_scenario")  # noqa: SLF001 - rewrite the fixture rows
        repo.save_stress_scenarios(rows)
        ups = rc.upgrade_stress_built_ins()
        assert {u["scenario_id"] for u in ups} == set(CONSISTENT_BUILT_INS)
        assert all(u["status"] == "DRAFT" and u["expand"] and u["version"] == 2 for u in ups)
        assert rc.upgrade_stress_built_ins() == []  # open drafts are not proposed twice
        cro.decide_stress_scenario("global_risk_off", 2, True, "consistent")
        # Calibrated crises: drafts only, approximate, run once approved.
        drafts = rc.draft_stress_crises()
        assert len(drafts) == 14 and all(d["source"] == "CALIBRATED" for d in drafts)
        assert all(d["goal"] == "INFORMATION" and "approximate" in d["note"] for d in drafts)
        assert rc.draft_stress_crises() == []
        cro.decide_stress_scenario("covid_2020", 1, True, "verified against our data")
        lib = RiskService(repo).stress_library()
        named = next(c for c in lib["categories"] if c["category"] == "NAMED_CRISIS")["scenarios"]
        covid = next(s for s in named if s["scenario_id"] == "covid_2020")
        assert covid["status"] == "CALIBRATED" and covid["in_daily_run"] and covid["consistent"]
        hyp = next(c for c in lib["categories"] if c["category"] == "HYPOTHETICAL")["scenarios"]
        assert "covid_2020" not in {s["scenario_id"] for s in hyp}
        assert "covid_2020" in {d["scenario_id"] for d in stress_setup.definitions_for_run(repo)}
        # Plausibility for the approver: every active scenario, the drafts and the ruler.
        p = RiskService(repo).stress_plausibility()
        assert p["available"] and p["scenarios"]["covid_2020"]["z_score"] > 0
        assert "gfc_2008:1" in p["drafts"] and any(r["kind"] == "EPISODE" for r in p["ruler"])
        assert [r["ruler_z"] for r in p["ruler"]] == sorted(r["ruler_z"] for r in p["ruler"])
        assert _rule_dict({"pca": "IR:USD:", "component": 2, "sigmas": 1}) == {
            "pca": "IR:USD:",
            "component": 2,
            "sigmas": 1.0,
        }
