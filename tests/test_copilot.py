"""Risk Copilot: tool loop, scripted planner, what-if engine, governance record."""
import json
from datetime import date

import pytest

from novera.ai import Copilot, ScriptedProvider
from novera.ai.provider import ProviderResponse, TextBlock, ToolUseBlock, _plan, _plan_what_if
from novera.ai.tools import build_tools, execute
from novera.ai.whatif import Shock, what_if
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
from novera.simulation.market_data import MarketSimConfig, generate_market_data
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.eod import EODConfig, run_eod

D1, D2 = date(2026, 9, 11), date(2026, 9, 14)


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("copilot") / "c.duckdb"
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=D2, years=1.0, seed=13, problem_date=D1, snapshot_days=3))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(business_date=D1, n_trades=120, seed=13,
                                                           market_history=hist))
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
        run_eod(repo, EODConfig(var=VaRConfig(window_days=120), workers=1), D1, runs_dir=runs_dir)
        run_eod(repo, EODConfig(var=VaRConfig(window_days=120), workers=1), D2, runs_dir=runs_dir)
    return str(path)


class FakeProvider:
    """Emits a fixed tool call, then answers with the number it received."""

    name = "fake"
    model = "fake-1"

    def __init__(self):
        self.calls = 0

    def complete(self, system, messages, tools):
        self.calls += 1
        assert "Every number you state must come from a tool result" in system
        if self.calls == 1:
            return ProviderResponse([TextBlock("Let me check."), ToolUseBlock("t1", "run_summary", {})],
                                    "tool_use", self.model, {"input_tokens": 10, "output_tokens": 5})
        last = messages[-1]["content"][0]
        assert last["type"] == "tool_result" and last["tool_use_id"] == "t1"
        payload = json.loads(last["content"])
        return ProviderResponse([TextBlock(f"VaR is {payload['var_99_1d_m']}m (run {payload['run_id']}).")],
                                "end_turn", self.model, {"input_tokens": 20, "output_tokens": 8})


def test_loop_feeds_tool_results_and_stores_answer(db_path):
    c = Copilot(db_path, provider=FakeProvider())
    a = c.ask("What is VaR?")
    assert a.turns == 2 and len(a.tool_calls) == 1 and a.tool_calls[0].name == "run_summary"
    assert a.answer.startswith("VaR is ") and a.run_ids_cited == [a.run_id]
    assert a.usage == {"input_tokens": 30, "output_tokens": 13}
    hist = c.history()
    assert hist[0]["answer_id"] == a.answer_id and hist[0]["tool_calls"][0]["name"] == "run_summary"
    with DuckDBRepository(db_path, read_only=True) as repo:
        ev = repo.load_audit_events(subject=a.answer_id)
    assert list(ev["event_type"]) == ["COPILOT_ANSWER"]


def test_tools_execute_and_errors_are_returned_not_raised(db_path):
    tools = build_tools(db_path)
    names = {t.name for t in tools}
    assert {"run_summary", "var_by", "sensitivities", "stress", "limits", "breaches", "pnl", "data_quality",
            "trade", "positions", "compare_runs", "what_if"} <= names
    out, err = execute(tools, "run_summary", {})
    assert not err and json.loads(out)["var_99_1d_m"] > 0
    out, err = execute(tools, "trade", {"trade_id": "NOPE"})
    assert err and "error" in json.loads(out)
    out, err = execute(tools, "nothing", {})
    assert err
    out, err = execute(tools, "compare_runs", {})
    d = json.loads(out)
    assert not err and d["run_a"]["business_date"] == "2026-09-11" and d["run_b"]["business_date"] == "2026-09-14"
    out, err = execute(tools, "limits", {"status": "BREACH"})
    assert not err and all(x["status"] == "BREACH" for x in json.loads(out)["limits"])


def test_what_if_reprices_through_engine(db_path):
    with DuckDBRepository(db_path, read_only=True) as repo:
        rid = repo.latest_run().run_id
    res = what_if(db_path, rid, [Shock("btc", -50, "pct")])
    assert set(k for k, v in res["by"].items() if v != 0) <= {"DIGITAL_ASSET"}
    assert res["total_pnl"] < 0
    res2 = what_if(db_path, rid, [Shock("rates_usd", 100, "bp", tenors=("10Y", "30Y"))])
    assert res2["factors_shocked"] == 2
    res3 = what_if(db_path, rid, [Shock("vol", 15, "vol_points")])
    assert res3["factors_shocked"] > 100
    with pytest.raises(ValueError, match="unknown target"):
        what_if(db_path, rid, [Shock("moon", 1)])


def test_scripted_planner_and_answers(db_path):
    assert _plan_what_if("What if equities fall 20%, vol up 15 points and oil drops 30%?") == [
        {"target": "equities", "size": -20.0, "unit": "pct"},
        {"target": "vol", "size": 15.0, "unit": "vol_points"},
        {"target": "oil", "size": -30.0, "unit": "pct"},
    ]
    assert _plan_what_if("rates +100bp") is None  # not phrased as a scenario
    assert _plan("Why did VaR increase?")[0][0] == "compare_runs"
    assert _plan("which desks are closest to limits")[0][0] == "limits"
    assert _plan("show me IRS_000201")[0] == ("trade", {"trade_id": "IRS_000201"})
    c = Copilot(db_path, provider=ScriptedProvider())
    for q in ["Why did VaR change since yesterday?", "Which books are closest to their limits?",
              "What happens if equities fall 20% and BTC falls 40%?", "Can I trust today's run?",
              "Draft the morning commentary", "Explain today's P&L", "What is the DV01 by desk?"]:
        a = c.ask(q, persist=False)
        assert a.answer and "Source: run" in a.answer, q
        assert a.tool_calls and not any(t.is_error for t in a.tool_calls), q
    a = c.commentary(persist=False)
    assert "VaR" in a.answer and a.run_ids_cited
