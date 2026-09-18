"""Analyst: tool loop, scripted planner, what-if engine, governance record."""

import json
from datetime import date

import pytest

from novera.ai import Analyst, ScriptedProvider
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
    path = tmp_path_factory.mktemp("analyst") / "c.duckdb"
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
        run_eod(
            repo,
            EODConfig(counterparty=False, regulatory=False, var=VaRConfig(window_days=120), workers=1),
            D1,
            runs_dir=runs_dir,
        )
        run_eod(
            repo,
            EODConfig(counterparty=False, regulatory=False, var=VaRConfig(window_days=120), workers=1),
            D2,
            runs_dir=runs_dir,
        )
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
            return ProviderResponse(
                [TextBlock("Let me check."), ToolUseBlock("t1", "run_summary", {})],
                "tool_use",
                self.model,
                {"input_tokens": 10, "output_tokens": 5},
            )
        last = messages[-1]["content"][0]
        assert last["type"] == "tool_result" and last["tool_use_id"] == "t1"
        payload = json.loads(last["content"])
        return ProviderResponse(
            [TextBlock(f"VaR is {payload['var_99_1d_m']}m (run {payload['run_id']}).")],
            "end_turn",
            self.model,
            {"input_tokens": 20, "output_tokens": 8},
        )


def test_loop_feeds_tool_results_and_stores_answer(db_path):
    c = Analyst(db_path, provider=FakeProvider())
    a = c.ask("What is VaR?")
    assert a.turns == 2 and len(a.tool_calls) == 1 and a.tool_calls[0].name == "run_summary"
    assert a.answer.startswith("VaR is ") and a.run_ids_cited == [a.run_id]
    assert a.usage == {"input_tokens": 30, "output_tokens": 13}
    hist = c.history()
    assert hist[0]["answer_id"] == a.answer_id and hist[0]["tool_calls"][0]["name"] == "run_summary"
    with DuckDBRepository(db_path, read_only=True) as repo:
        ev = repo.load_audit_events(subject=a.answer_id)
    assert list(ev["event_type"]) == ["ANALYST_ANSWER"]


def test_tools_execute_and_errors_are_returned_not_raised(db_path):
    tools = build_tools(db_path)
    names = {t.name for t in tools}
    assert {
        "run_summary",
        "var_by",
        "sensitivities",
        "stress",
        "limits",
        "breaches",
        "pnl",
        "data_quality",
        "trade",
        "positions",
        "compare_runs",
        "what_if",
    } <= names
    out, err = execute(tools, "run_summary", {})
    assert not err and json.loads(out)["var_99_1d_m"] > 0
    out, err = execute(tools, "trade", {"trade_id": "NOPE"})
    assert err and "error" in json.loads(out)
    out, err = execute(tools, "nothing", {})
    assert err
    out, err = execute(tools, "compare_runs", {})
    d = json.loads(out)
    assert (
        not err
        and d["run_a"]["business_date"] == "2026-09-11"
        and d["run_b"]["business_date"] == "2026-09-14"
    )
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
    c = Analyst(db_path, provider=ScriptedProvider())
    for q in [
        "Why did VaR change since yesterday?",
        "Which books are closest to their limits?",
        "What happens if equities fall 20% and BTC falls 40%?",
        "Can I trust today's run?",
        "Draft the morning commentary",
        "Explain today's P&L",
        "What is the DV01 by desk?",
    ]:
        a = c.ask(q, persist=False)
        assert a.answer and "Source: run" in a.answer, q
        assert a.tool_calls and not any(t.is_error for t in a.tool_calls), q
    a = c.commentary(persist=False)
    assert "VaR" in a.answer and a.run_ids_cited


def _chat_completion(text=None, tool_calls=None, finish="stop", model="gemini-3.6-flash"):
    msg = {"role": "assistant", "content": text}
    if tool_calls:
        msg["tool_calls"] = [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            for cid, name, args in tool_calls
        ]
        finish = "tool_calls"
    return {
        "model": model,
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }


def test_openai_compat_provider_through_the_analyst_loop(db_path):
    """Gemini, Groq, OpenRouter and Ollama share one adapter: Messages-API turns go out as
    chat-completions messages with function tools, tool calls come back as tool_use blocks,
    and the tool results are returned under the tool role with the call id (Round 29)."""
    import httpx

    from novera.ai.provider import OpenAICompatProvider, ProviderError

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": body})
        if len(seen) == 1:
            data = _chat_completion(None, [("call_1", "run_summary", {})])
            data["choices"][0]["message"]["tool_calls"][0]["extra_content"] = {
                "google": {"thought_signature": "sig"}
            }
            return httpx.Response(200, json=data)
        return httpx.Response(200, json=_chat_completion("VaR is 1.2m (run x)."))

    prov = OpenAICompatProvider(
        "gemini",
        "https://example.test/v1beta/openai/",
        "gemini-3.6-flash",
        "k",
        transport=httpx.MockTransport(handler),
    )
    ans = Analyst(db_path, provider=prov).ask("What is the VaR?", persist=False)
    assert (
        ans.answer == "VaR is 1.2m (run x)." and ans.provider == "gemini" and ans.model == "gemini-3.6-flash"
    )
    assert [c.name for c in ans.tool_calls] == ["run_summary"]
    first, second = seen
    assert first["url"] == "https://example.test/v1beta/openai/chat/completions"
    assert first["auth"] == "Bearer k"
    assert first["body"]["messages"][0]["role"] == "system" and first["body"]["messages"][1] == {
        "role": "user",
        "content": "What is the VaR?",
    }
    assert first["body"]["tool_choice"] == "auto"
    tools = {t["function"]["name"]: t["function"]["parameters"] for t in first["body"]["tools"]}
    assert "run_summary" in tools and "additionalProperties" not in json.dumps(tools)
    roles = [m["role"] for m in second["body"]["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    call = second["body"]["messages"][2]["tool_calls"][0]
    assert call["function"]["name"] == "run_summary"
    assert call["extra_content"] == {"google": {"thought_signature": "sig"}}  # Gemini 3 needs it back
    tool_msg = second["body"]["messages"][3]
    assert tool_msg["tool_call_id"] == "call_1" and "run_id" in tool_msg["content"]
    assert ans.usage["input_tokens"] == 240 and ans.usage["output_tokens"] == 60

    def limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "12"}, json={"error": {"message": "quota"}})

    prov2 = OpenAICompatProvider(
        "groq", "https://example.test/v1", "m", "k", transport=httpx.MockTransport(limited)
    )
    with pytest.raises(ProviderError, match="rate limit.*12s"):
        prov2.complete("s", [{"role": "user", "content": "q"}], [])


def test_openai_compat_parsing_edge_cases():
    from novera.ai.provider import from_chat_completion, strip_provider_extras, to_chat_messages

    hist = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "c", "name": "t", "input": {}, "extra": {"x": 1}}],
        }
    ]
    assert strip_provider_extras(hist)[0]["content"][0] == {
        "type": "tool_use",
        "id": "c",
        "name": "t",
        "input": {},
    }

    # a tool call without an id gets one, bad JSON arguments are kept raw, content parts join
    data = _chat_completion(None, [("", "limits", {"status": "BREACH"})])
    data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{not json"
    data["choices"][0]["message"]["content"] = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    resp = from_chat_completion(data, "fallback")
    assert resp.stop_reason == "tool_use" and resp.text == "ab"
    tu = resp.tool_uses[0]
    assert tu.id.startswith("call_") and tu.input == {"_raw": "{not json"}
    assert (
        from_chat_completion(
            {"choices": [{"message": {"content": "x"}, "finish_reason": "length"}]}, "m"
        ).stop_reason
        == "max_tokens"
    )
    # an error result is marked so the model sees it failed
    msgs = to_chat_messages(
        "sys",
        [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "looking"},
                    {"type": "tool_use", "id": "c9", "name": "t", "input": {"a": 1}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "c9", "content": {"k": 1}, "is_error": True}
                ],
            },
        ],
    )
    assert msgs[2]["content"] == "looking" and json.loads(
        msgs[2]["tool_calls"][0]["function"]["arguments"]
    ) == {"a": 1}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c9", "content": 'ERROR: {"k": 1}'}


def test_make_provider_follows_the_settings(monkeypatch):
    from novera.ai import make_provider
    from novera.config import Settings

    for var in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    for var in ("NOVERA_LLM_PROVIDER", "NOVERA_LLM_MODEL", "NOVERA_LLM_BASE_URL", "NOVERA_LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert make_provider(Settings(_env_file=None)).name == "scripted"
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    p = make_provider(Settings(_env_file=None))
    assert (p.name, p.model, p.base_url) == (
        "gemini",
        "gemini-3.6-flash",
        "https://generativelanguage.googleapis.com/v1beta/openai",
    )
    assert p.client.headers["authorization"] == "Bearer g"
    monkeypatch.setenv("NOVERA_LLM_MODEL", "gemini-2.5-pro")
    assert make_provider(Settings(_env_file=None)).model == "gemini-2.5-pro"
    monkeypatch.setenv("NOVERA_LLM_PROVIDER", "groq")
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        make_provider(Settings(_env_file=None))
    monkeypatch.setenv("NOVERA_LLM_PROVIDER", "ollama")
    assert make_provider(Settings(_env_file=None)).base_url == "http://localhost:11434/v1"
    monkeypatch.setenv("NOVERA_LLM_PROVIDER", "openai_compat")
    with pytest.raises(ValueError, match="NOVERA_LLM_BASE_URL"):
        make_provider(Settings(_env_file=None))
    monkeypatch.setenv("NOVERA_LLM_PROVIDER", "scripted")
    assert make_provider(Settings(_env_file=None)).name == "scripted"
    monkeypatch.setenv("NOVERA_LLM_PROVIDER", "nope")
    with pytest.raises(ValueError, match="unknown"):
        make_provider(Settings(_env_file=None))


def test_fallback_chain_skips_failed_providers_and_rests_them(monkeypatch):
    """gemini,groq,openrouter,ollama: a member that fails is skipped for the next one and
    rested; the answer names the member that produced it (Round 30)."""
    import httpx

    from novera.ai.provider import FallbackProvider, OpenAICompatProvider, ProviderError

    calls: list[str] = []

    def make(name, handler):
        return OpenAICompatProvider(
            name, f"https://{name}.test/v1", f"{name}-model", "k", transport=httpx.MockTransport(handler)
        )

    def quota(request):
        calls.append("gemini")
        return httpx.Response(429, headers={"retry-after": "9"}, json={"error": "quota"})

    def down(request):
        calls.append("groq")
        return httpx.Response(503, text="down")

    def ok(request):
        calls.append("openrouter")
        body = json.loads(request.content)
        assert "extra_content" not in json.dumps(body)  # another member's extras are stripped
        return httpx.Response(200, json=_chat_completion("answer from openrouter"))

    chain = FallbackProvider([make("gemini", quota), make("groq", down), make("openrouter", ok)])
    assert chain.chain == ["gemini", "groq", "openrouter"] and chain.name == "gemini"
    history = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "c", "name": "t", "input": {}, "extra": {"google": 1}}],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c", "content": "r"}]},
    ]
    resp = chain.complete("s", history, [])
    assert (
        resp.text == "answer from openrouter"
        and chain.name == "openrouter"
        and chain.model == "openrouter-model"
    )
    assert calls == ["gemini", "groq", "openrouter"]
    # the failed members are resting: the next call goes straight to openrouter
    calls.clear()
    chain.complete("s", [{"role": "user", "content": "q2"}], [])
    assert calls == ["openrouter"]
    text, usage = chain.draft("model_validation", {"x": 1}, "write")
    assert text == "answer from openrouter" and usage["input_tokens"] == 120
    # every member failing raises one error naming each failure
    all_bad = FallbackProvider([make("gemini", quota), make("groq", down)])
    with pytest.raises(ProviderError, match="every provider.*gemini.*groq"):
        all_bad.complete("s", [{"role": "user", "content": "q"}], [])
    # once every member is resting they are all tried again rather than refused outright
    with pytest.raises(ProviderError, match="every provider"):
        all_bad.complete("s", [{"role": "user", "content": "q"}], [])


def test_make_provider_builds_the_chain_from_settings(monkeypatch):
    from novera.ai import make_provider
    from novera.config import Settings

    for var in (
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "NOVERA_LLM_MODEL",
        "NOVERA_LLM_BASE_URL",
        "NOVERA_LLM_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NOVERA_LLM_PROVIDER", "gemini,groq,openrouter,ollama")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    p = make_provider(Settings(_env_file=None))
    assert p.chain == ["gemini", "openrouter", "ollama"] and p.name == "gemini"  # groq has no key
    # per-member models; the global NOVERA_LLM_MODEL is ignored in a chain (ids differ per provider)
    monkeypatch.setenv("NOVERA_LLM_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
    monkeypatch.setenv(
        "NOVERA_LLM_PROVIDER", "gemini:gemini-3.5-flash,openrouter:google/gemma-4-31b-it:free,ollama"
    )
    p = make_provider(Settings(_env_file=None))
    assert [m.model for m in p.providers] == ["gemini-3.5-flash", "google/gemma-4-31b-it:free", "qwen3:4b"]
    monkeypatch.setenv("NOVERA_LLM_PROVIDER", "gemini,openrouter")
    assert [m.model for m in make_provider(Settings(_env_file=None)).providers] == [
        "gemini-3.6-flash",
        "nvidia/nemotron-3-super-120b-a12b:free",
    ]
    monkeypatch.setenv("NOVERA_LLM_PROVIDER", "openrouter")
    assert make_provider(Settings(_env_file=None)).model == "nvidia/nemotron-3-super-120b-a12b:free"
    monkeypatch.delenv("NOVERA_LLM_MODEL")
    monkeypatch.setenv("NOVERA_LLM_PROVIDER", "gemini,groq")
    single = make_provider(Settings(_env_file=None))
    assert single.name == "gemini" and not hasattr(single, "chain")
    monkeypatch.setenv("NOVERA_LLM_PROVIDER", "groq,anthropic")
    with pytest.raises(ValueError, match="none of groq, anthropic"):
        make_provider(Settings(_env_file=None))
    monkeypatch.setenv("NOVERA_LLM_PROVIDER", "auto")
    monkeypatch.setenv("GROQ_API_KEY", "q")
    assert make_provider(Settings(_env_file=None)).chain == ["gemini", "groq", "openrouter"]
