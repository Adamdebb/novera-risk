"""Phase 7: agents (breach investigation, scenario suggestion, model validation, CSA ingestion)
and the Portfolio Lab. Evidence is deterministic; drafts come from the scripted provider."""

from datetime import date
from pathlib import Path

import pytest

from novera.ai.agents import (
    approve_csa,
    draft_validation_report,
    extract_fields,
    investigate_breach,
    propose_csa,
    reject_csa,
    suggest_scenarios,
)
from novera.ai.agents.validation import parse_record
from novera.ai.provider import ScriptedProvider, _plan
from novera.ai.tools import build_tools, execute
from novera.lab import PROBLEM_CATALOGUE, LabSpec, run_lab
from novera.market_data.history import MarketHistory
from novera.risk import VaRConfig
from novera.simulation import (
    TradeGeneratorConfig,
    build_counterparty_universe,
    build_global_macro_bank,
    build_limits,
    evolve_portfolio,
    generate_portfolio,
)
from novera.simulation.documents import csa_term_sheet
from novera.simulation.market_data import MARKET_PROBLEMS, MarketSimConfig, generate_market_data
from novera.simulation.trades import BANK_PROBLEMS, FUND_PROBLEMS
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.eod import EODConfig, run_eod

D1, D2 = date(2026, 9, 11), date(2026, 9, 14)


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("agents") / "a.duckdb"
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(
        MarketSimConfig(end_date=D2, years=1.0, seed=13, problem_date=D1, snapshot_days=3)
    )
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(
        org, cp, TradeGeneratorConfig(business_date=D1, n_trades=120, seed=13, market_history=hist)
    )
    day2, _ = evolve_portfolio(gen.snapshot, D2, org, cp, gen.injections, hist)
    with DuckDBRepository(path) as repo:
        repo.init_schema()
        repo.save_organisation(org)
        repo.save_counterparties(cp.counterparties)
        repo.save_netting_sets(cp.netting_sets, cp.csas)
        repo.save_limits(build_limits(org, cp))
        repo.save_portfolio_snapshot(gen.snapshot)
        repo.save_portfolio_snapshot(day2)
        repo.save_risk_factors(md.universe)
        repo.save_market_history(md.history)
        for m in md.snapshots.values():
            repo.save_market_snapshot(m)
        runs_dir = tmp_path_factory.mktemp("runs")
        cfg = EODConfig(counterparty=False, regulatory=False, var=VaRConfig(window_days=120), workers=1)
        run_eod(repo, cfg, D1, runs_dir=runs_dir)
        run_eod(repo, cfg, D2, runs_dir=runs_dir)
    return str(path)


def test_breach_investigation_attaches_note_with_evidence(db_path):
    with DuckDBRepository(db_path, read_only=True) as repo:
        breaches = repo.load_breaches(open_only=True)
    assert breaches, "the planted problems must produce at least one breach"
    b = next((x for x in breaches if x.limit_id == "USD_RATES_DV01_USD_10Y"), breaches[0])
    note = investigate_breach(db_path, b.breach_id, attach=True)
    ev = note.evidence
    assert ev["breach"]["breach_id"] == b.breach_id
    assert ev["utilisation_history"] and ev["utilisation_history"][0]["run_id"]
    assert ev["top_contributors"], "contributors must be identified"
    tid = ev["top_contributors"][0]["trade_id"]
    assert tid in note.text and b.limit_id in note.text
    if "reduce_to_limit" in ev["remediation"]:
        assert ev["remediation"]["reduce_to_limit"] >= 0
    with DuckDBRepository(db_path, read_only=True) as repo:
        actions = repo.load_breach_actions(b.breach_id)
        notes = repo.load_agent_notes(kind="BREACH_INVESTIGATION")
        audit = repo.load_audit_events(subject=note.note_id)
    assert any(a.actor == "breach-investigator" and note.note_id in a.comment for a in actions)
    assert notes and notes[0]["status"] == "ATTACHED"
    assert len(audit) and (audit["event_type"] == "AGENT_NOTE").any()


def test_scenario_suggestion_runs_engine(db_path):
    note = suggest_scenarios(db_path, n=3)
    ev = note.evidence
    assert ev["largest_exposures"] and ev["largest_recent_moves"]
    props = [p for p in ev["proposals"] if "error" not in p]
    assert len(props) >= 3
    # Loss-direction sizing: the top-exposure scenarios lose money, and they are ranked worst first.
    losses = [p["total_pnl_m"] for p in props]
    assert losses == sorted(losses)
    assert props[0]["total_pnl_m"] < 0
    assert props[0]["name"] in note.text


def test_validation_report_reads_records_and_live_evidence(db_path, tmp_path):
    note, path = draft_validation_report(db_path, records=["PR-016", "MR-002"], out_dir=tmp_path)
    ids = [r["record_id"] for r in note.evidence["records"]]
    assert ids == ["MR-002", "PR-016"]
    rec = next(r for r in note.evidence["records"] if r["record_id"] == "PR-016")
    assert all(t["in_suite"] for t in rec["tests"]) and rec["limitations"]
    assert note.evidence["live_evidence"]["backtest_zone"] in ("GREEN", "AMBER", "RED")
    assert path is not None and path.exists() and "PR-016" in path.read_text()


def test_parse_record_template_shape():
    d = parse_record(Path("docs/methodology/PR-012-swaption.md"))
    assert d["record_id"] == "PR-012" and d["version"] == "1.1.0"
    assert "test_swaption_matches_quantlib_bachelier" in d["tests"]


def test_csa_ingestion_round_trip(db_path, tmp_path):
    with DuckDBRepository(db_path, read_only=True) as repo:
        org = repo.load_organisation("GMB")
        before, _ = repo.load_netting_sets()
    doc = tmp_path / "csa.txt"
    doc.write_text(csa_term_sheet(org, "Northsea Energy plc", "GMB_LN", threshold_they_post=7.5e6, mta=0.5e6))
    fields = extract_fields(doc.read_text())
    assert fields["threshold_they_post"]["value"] == 7.5e6 and fields["threshold_they_post"]["found"]
    assert fields["minimum_transfer_amount"]["value"] == 0.5e6
    assert fields["haircut"]["value"] == pytest.approx(0.02)
    assert "\n" not in fields["rounding"]["source"]
    note = propose_csa(db_path, str(doc))
    p = note.evidence["proposal"]
    assert note.evidence["valid"]
    assert (
        p["netting_set"]["counterparty_id"] == "CORP_ENERGY"
        and p["netting_set"]["legal_entity_id"] == "GMB_LN"
    )
    assert p["csa"]["threshold_they_post"] == 7.5e6
    assert any("already exists" in w for w in note.evidence["warnings"])
    with pytest.raises(ValueError):
        approve_csa(db_path, note.note_id, "")
    out = approve_csa(db_path, note.note_id, "Head of Counterparty Risk")
    with DuckDBRepository(db_path, read_only=True) as repo:
        ns, csas = repo.load_netting_sets()
        saved = next(n for n in ns if n.netting_set_id == out["netting_set_id"])
        csa = next(c for c in csas if c.csa_id == saved.csa_id)
        audit = repo.load_audit_events(subject=out["netting_set_id"])
    assert csa.threshold_they_post == 7.5e6 and csa.minimum_transfer_amount == 0.5e6
    assert (audit["event_type"] == "CSA_INGESTED").any()
    # A document naming an unknown counterparty is not valid and cannot be approved.
    bad = tmp_path / "bad.txt"
    bad.write_text(csa_term_sheet(org, "Unknown Corp Ltd", "GMB_LN"))
    note2 = propose_csa(db_path, str(bad))
    assert not note2.evidence["valid"]
    with pytest.raises(ValueError):
        approve_csa(db_path, note2.note_id, "someone")
    reject_csa(db_path, note2.note_id, "someone", "unknown counterparty")


def test_copilot_routes_to_agents(db_path):
    assert _plan("suggest scenarios I should run today")[0][0] == "agent"
    assert _plan("investigate brc_abc123_def456 for me")[0] == (
        "agent",
        {"agent": "investigate_breach", "breach_id": "brc_abc123_def456"},
    )
    tools = build_tools(db_path)
    out, err = execute(tools, "agent", {"agent": "suggest_scenarios", "n": 2})
    assert not err and "note_id" in out
    out, err = execute(tools, "agent", {"agent": "investigate_breach"})
    assert "breach_id is required" in out


def test_scripted_provider_draft_is_templated():
    text, usage = ScriptedProvider().draft(
        "SCENARIO_SUGGESTION", {"run_id": "r", "business_date": "d", "proposals": []}, ""
    )
    assert "Suggested scenarios" in text and usage == {}


# --- Portfolio Lab -------------------------------------------------------------------------------


def test_problem_catalogue_covers_every_plantable_problem():
    for p in (*BANK_PROBLEMS, *FUND_PROBLEMS, *MARKET_PROBLEMS):
        assert p in PROBLEM_CATALOGUE, p


def test_lab_spec_validation():
    with pytest.raises(ValueError):
        LabSpec(name="x", template="bank", problems=("crowded_single_name",)).validate()
    with pytest.raises(ValueError):
        LabSpec(name="bad name!", template="bank").validate()
    LabSpec(name="ok_1", template="hedge_fund", problems=("short_vol",), scale=2.0).validate()


def test_lab_runs_and_detects_selected_problems(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVERA_DATA_DIR", str(tmp_path))
    from novera.config import get_settings

    get_settings.cache_clear()
    try:
        spec = LabSpec(
            name="t_lab",
            template="bank",
            problems=("usd_10y_concentration", "invalid_trades"),
            market_problems=("missing_usd_7y_node",),
            scale=2.0,
            n_trades=150,
            years=1.0,
            seed=5,
            workers=1,
        )
        res = run_lab(spec)
        assert Path(res.db_path).exists() and res.db_path.startswith(str(tmp_path))
        by = {d.problem: d for d in res.detections}
        assert set(by) == {"usd_10y_concentration", "invalid_trades", "missing_usd_7y_node"}
        assert by["invalid_trades"].detected and any("TRADE_" in e for e in by["invalid_trades"].evidence)
        assert by["missing_usd_7y_node"].detected
        assert by["usd_10y_concentration"].detected
        assert all("MD_MISSING" not in e for e in by["usd_10y_concentration"].evidence)
        with DuckDBRepository(res.db_path, read_only=True) as repo:
            rec = repo.load_lab("t_lab")
            trades = repo.load_portfolio_snapshot(repo.list_portfolio_snapshots()[0][0]).trades
        assert rec["run_id"] == res.run_id and rec["spec"]["scale"] == 2.0
        assert {i["name"] for i in rec["injections"]} == {"usd_10y_concentration", "invalid_trades"}
        # Scaled problem: 800m swaps instead of 400m.
        big = [t for t in trades if t.trade_id in rec["injections"][0]["trade_ids"]]
        assert big and all(t.quantity == 800e6 for t in big)
    finally:
        get_settings.cache_clear()
