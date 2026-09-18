"""The Analyst: a tool-calling loop over the stored run, with every answer recorded.

Governance (docs/04-governance.md): the model only sees tool results; it never computes
a number; every answer is stored with the question, the tool calls and their results, the
run ids cited, the provider and model, and token usage.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from novera.ai.provider import AnthropicProvider, Provider, ScriptedProvider
from novera.ai.tools import Tool, build_tools, execute
from novera.config import Settings, get_settings
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import AuditEvent, new_run_id

MAX_TURNS = 8

SYSTEM_PROMPT = """You are {platform} Analyst, the question-answering assistant of {platform}, a market and
counterparty risk platform for a trading firm. You help risk managers, desk heads and the CRO understand
stored risk results.

Rules you must follow:
- Every number you state must come from a tool result in this conversation. Never estimate,
  extrapolate or compute a risk figure yourself. If a tool cannot provide it, say so.
- Cite the run id you used, e.g. "(run run_abc)". Numbers are in the run's reporting currency,
  in millions unless the tool says otherwise. Utilisations are percentages of the limit.
- Prefer one or two tool calls that answer the question over many. Start with run_summary
  for broad questions and compare_runs for "what changed" questions. For hypothetical
  scenarios use what_if, which reprices the actual portfolio through the pricing engine.
- Write for a risk professional: lead with the answer, then the drivers, then caveats.
  Short paragraphs or bullets, no headings. Mention data-quality caveats when the run
  verdict is AMBER or RED and they bear on the question.
- You cannot change limits, breaches or any state. If asked to, explain which action the
  user can take on the Breaches page and what the approval rules require.
- Today's business date and the latest run are in the context block below.

Context: {context}"""


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any]
    output: str
    is_error: bool
    seconds: float


@dataclass
class AnalystAnswer:
    answer_id: str
    question: str
    answer: str
    run_id: str | None
    provider: str
    model: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    run_ids_cited: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    turns: int = 0
    seconds: float = 0.0
    session_id: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer_id": self.answer_id,
            "question": self.question,
            "answer": self.answer,
            "run_id": self.run_id,
            "provider": self.provider,
            "model": self.model,
            "run_ids_cited": self.run_ids_cited,
            "usage": self.usage,
            "turns": self.turns,
            "seconds": round(self.seconds, 2),
            "session_id": self.session_id,
            "at": self.at.isoformat(),
            "tool_calls": [
                {
                    "name": c.name,
                    "input": c.input,
                    "is_error": c.is_error,
                    "seconds": round(c.seconds, 2),
                    "output": c.output[:4000],
                }
                for c in self.tool_calls
            ],
        }


KEYED = ("anthropic", "gemini", "groq", "openrouter")


def make_provider(settings: Settings | None = None) -> Provider:
    """The provider the settings ask for.

    ``NOVERA_LLM_PROVIDER`` names one provider, or a comma-separated chain tried in order
    (``gemini,groq,openrouter,ollama``): a provider that has no key is left out, and one
    that fails at run time (quota, outage, bad key) is skipped for the next in line. A member
    can carry its own model after a colon (``openrouter:google/gemma-4-31b-it:free``); the
    global ``NOVERA_LLM_MODEL`` applies only when a single provider is configured, because
    model ids do not carry across providers.
    ``auto`` is the chain of every provider with a key, in the order Anthropic, Gemini, Groq,
    OpenRouter; with no key it is a configured OpenAI-compatible base URL, else the scripted
    stand-in that needs nothing."""
    from novera.ai.provider import FallbackProvider

    s = settings or get_settings()
    raw = (s.llm_provider or "auto").strip()
    entries = [e.strip() for e in raw.split(",") if e.strip()]
    if [e.lower() for e in entries] == ["auto"]:
        entries = [p for p in KEYED if getattr(s, f"{p}_api_key")] or (
            ["openai_compat"] if s.llm_base_url else ["scripted"]
        )
    parsed: list[tuple[str, str | None]] = []
    for e in entries:
        name, _, model = e.partition(":")
        parsed.append((name.strip().lower(), model.strip() or None))
    if len(parsed) == 1:
        name, model = parsed[0]
        return _single_provider(name, s, model or s.llm_model)
    members = []
    for name, model in parsed:
        if name in KEYED and not getattr(s, f"{name}_api_key") and not s.llm_api_key:
            continue  # no key: not part of the chain
        members.append(_single_provider(name, s, model))  # the global model is not applied
    if not members:
        raise ValueError(f"none of {', '.join(n for n, _ in parsed)} has a key configured")
    return members[0] if len(members) == 1 else FallbackProvider(members)


def _single_provider(choice: str, s: Settings, model: str | None = None) -> Provider:
    from novera.ai.provider import PRESETS, OpenAICompatProvider

    if choice == "scripted":
        return ScriptedProvider()
    if choice == "anthropic":
        return AnthropicProvider(model=model or "claude-opus-5", api_key=s.anthropic_api_key)
    preset = PRESETS.get(choice, {})
    if choice != "openai_compat" and not preset:
        raise ValueError(
            f"unknown NOVERA_LLM_PROVIDER {choice!r}; one of auto, anthropic, scripted, "
            f"openai_compat, {', '.join(PRESETS)}"
        )
    base_url = s.llm_base_url or preset.get("base_url")
    if not base_url:
        raise ValueError("NOVERA_LLM_PROVIDER=openai_compat needs NOVERA_LLM_BASE_URL (the .../v1 base)")
    key_setting = preset.get("key_setting")
    api_key = s.llm_api_key or (getattr(s, key_setting) if key_setting else None)
    if choice != "ollama" and not api_key:
        var = key_setting.upper() if key_setting else "NOVERA_LLM_API_KEY"
        raise ValueError(f"provider {choice} needs a key: set {var}")
    model = model or preset.get("model")
    if not model:
        raise ValueError("NOVERA_LLM_PROVIDER=openai_compat needs NOVERA_LLM_MODEL")
    return OpenAICompatProvider(choice, base_url, model, api_key)


class Analyst:
    def __init__(
        self, db_path: str, provider: Provider | None = None, settings: Settings | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.db_path = str(db_path)
        self.provider = provider or make_provider(self.settings)
        self.tools: list[Tool] = build_tools(self.db_path)

    def _context(self, run_id: str | None) -> tuple[str, str | None]:
        from novera.api.service import RiskService

        with DuckDBRepository(self.db_path, read_only=True) as repo:
            svc = RiskService(repo)
            runs = svc.runs(5)
        if not runs:
            return "No completed run is stored yet.", None
        latest = runs[0]
        target = next((r for r in runs if r["run_id"] == run_id), latest) if run_id else latest
        ctx = {
            "latest_run": {
                "run_id": latest["run_id"],
                "business_date": latest["business_date"],
                "verdict": latest["verdict"],
            },
            "selected_run": {"run_id": target["run_id"], "business_date": target["business_date"]},
            "other_recent_runs": [
                {"run_id": r["run_id"], "business_date": r["business_date"]} for r in runs[1:4]
            ],
            "reporting_currency": latest["reporting_currency"],
        }
        return json.dumps(ctx), target["run_id"]

    def ask(
        self,
        question: str,
        run_id: str | None = None,
        history: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
        persist: bool = True,
    ) -> AnalystAnswer:
        t0 = time.perf_counter()
        context, resolved = self._context(run_id)
        system = SYSTEM_PROMPT.format(platform=self.settings.platform_name, context=context)
        tool_defs = [t.definition() for t in self.tools]
        messages: list[dict[str, Any]] = list(history or []) + [{"role": "user", "content": question}]
        calls: list[ToolCall] = []
        usage: dict[str, int] = {}
        text = ""
        turns = 0
        while turns < MAX_TURNS:
            turns += 1
            resp = self.provider.complete(system, messages, tool_defs)
            for k, v in resp.usage.items():
                usage[k] = usage.get(k, 0) + int(v)
            messages.append({"role": "assistant", "content": resp.as_message_content()})
            if resp.stop_reason != "tool_use" or not resp.tool_uses:
                text = resp.text
                break
            results = []
            for tu in resp.tool_uses:
                args = dict(tu.input)
                if "run_id" in args and args["run_id"] in (None, "", "latest") and resolved:
                    args["run_id"] = resolved
                elif "run_id" not in args and resolved and tu.name not in ("breaches", "compare_runs"):
                    args["run_id"] = resolved
                t1 = time.perf_counter()
                out, err = execute(self.tools, tu.name, args)
                calls.append(ToolCall(tu.name, args, out, err, time.perf_counter() - t1))
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out, "is_error": err})
            messages.append({"role": "user", "content": results})
        else:
            text = text or "I could not finish within the tool-call budget. Please narrow the question."
        cited = sorted(
            {
                json.loads(c.output).get("run_id")
                for c in calls
                if not c.is_error
                and isinstance(json.loads(c.output), dict)
                and json.loads(c.output).get("run_id")
            }
        )
        ans = AnalystAnswer(
            new_run_id("ans"),
            question,
            text,
            resolved,
            self.provider.name,
            self.provider.model,
            calls,
            cited,
            usage,
            turns,
            time.perf_counter() - t0,
            session_id,
        )
        if persist:
            self.store(ans)
        return ans

    def store(self, ans: AnalystAnswer) -> None:
        with DuckDBRepository(self.db_path) as repo:
            repo.init_schema()  # idempotent: databases created before the analyst table gain it here
            repo.save_analyst_answer(ans.to_dict())
            repo.save_audit_events(
                [
                    AuditEvent.now(
                        "analyst",
                        "ANALYST_ANSWER",
                        ans.answer_id,
                        question=ans.question[:500],
                        provider=ans.provider,
                        model=ans.model,
                        run_ids=ans.run_ids_cited,
                        tools=[c.name for c in ans.tool_calls],
                        usage=ans.usage,
                    )
                ]
            )

    def commentary(self, run_id: str | None = None, persist: bool = True) -> AnalystAnswer:
        """Draft the morning risk commentary for a run."""
        q = (
            "Draft the morning risk commentary for this run for the head of market risk: headline risk and "
            "how it changed since the previous run, the main drivers by asset class and desk, stress "
            "exposure, limit breaches and their workflow status, data-quality caveats, and the three "
            "actions you would recommend. Cite the run id."
        )
        return self.ask(q, run_id=run_id, persist=persist)

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        with DuckDBRepository(self.db_path, read_only=True) as repo:
            return repo.load_analyst_answers(limit)
