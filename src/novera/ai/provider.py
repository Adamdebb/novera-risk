"""LLM providers behind one small interface.

``AnthropicProvider`` calls the Claude API. ``OpenAICompatProvider`` calls any endpoint that
speaks the OpenAI chat-completions shape, which covers the free tiers of Gemini, Groq and
OpenRouter and a local Ollama (``PRESETS``). ``ScriptedProvider`` is a deterministic stand-in
that plans tool calls from keywords and writes the answer from the tool results, so the
platform demos and tests without credentials. All three speak the Messages API content-block
shape so the Analyst loop is identical.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class TextBlock:
    text: str
    type: str = "text"


@dataclass(frozen=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"
    extra: dict[str, Any] | None = None
    """Provider-specific data that must travel back with the call (Gemini 3 returns a
    ``thought_signature`` under ``extra_content`` and rejects a replay without it)."""


@dataclass
class ProviderResponse:
    content: list[TextBlock | ToolUseBlock]
    stop_reason: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.content if isinstance(b, TextBlock)).strip()

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]

    def as_message_content(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for b in self.content:
            if isinstance(b, TextBlock):
                out.append({"type": "text", "text": b.text})
            else:
                block: dict[str, Any] = {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
                if b.extra:
                    block["extra"] = b.extra
                out.append(block)
        return out


def strip_provider_extras(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same history without the ``extra`` field on tool_use blocks, for providers that
    reject unknown keys (the Messages API)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(b, dict) and "extra" in b for b in c):
            c = [{k: v for k, v in b.items() if k != "extra"} if isinstance(b, dict) else b for b in c]
            m = {**m, "content": c}
        out.append(m)
    return out


class Provider(Protocol):
    name: str
    model: str

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ProviderResponse: ...

    def draft(self, kind: str, evidence: dict[str, Any], instructions: str) -> tuple[str, dict[str, int]]:
        """Write prose from evidence the engine gathered (agents, AI-002). No tools: every number
        in the text must come from ``evidence``."""
        ...


DRAFT_SYSTEM = """You draft risk-management text for {platform} from evidence a deterministic engine
gathered. Rules: use only numbers that appear in the evidence, cite run ids and trade ids as given,
never estimate or compute new figures, say plainly when evidence is missing, write for a head of
market risk in short paragraphs and bullet lists, no headers deeper than level 3."""


# --- Anthropic ---------------------------------------------------------------------------


class AnthropicProvider:
    """Claude via the official SDK. Adaptive thinking, high effort, server-side refusal
    fallbacks (the API re-runs a declined request on a fallback model inside the call)."""

    name = "anthropic"

    def __init__(
        self,
        model: str = "claude-opus-5",
        api_key: str | None = None,
        effort: str = "high",
        max_tokens: int = 16000,
        fallbacks: bool = True,
    ) -> None:
        import anthropic

        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.fallbacks = fallbacks
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ProviderResponse:
        import anthropic

        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=strip_provider_extras(messages),
            tools=tools,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
        )
        try:
            if self.fallbacks:
                resp = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
            else:
                resp = self.client.messages.create(**kwargs)
        except anthropic.BadRequestError:
            if not self.fallbacks:
                raise
            resp = self.client.messages.create(**kwargs)  # older API surface without fallbacks
        content: list[TextBlock | ToolUseBlock] = []
        for b in resp.content:
            if b.type == "text":
                content.append(TextBlock(b.text))
            elif b.type == "tool_use":
                content.append(ToolUseBlock(b.id, b.name, dict(b.input)))
        usage = {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "cache_read_input_tokens": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        }
        if resp.stop_reason == "refusal":
            detail = getattr(resp, "stop_details", None)
            why = getattr(detail, "explanation", None) if detail else None
            content = [TextBlock("The model declined to answer this request" + (f": {why}" if why else "."))]
        return ProviderResponse(content, resp.stop_reason or "end_turn", resp.model, usage)

    def draft(self, kind: str, evidence: dict[str, Any], instructions: str) -> tuple[str, dict[str, int]]:
        from novera.config import get_settings

        system = DRAFT_SYSTEM.format(platform=get_settings().platform_name)
        user = f"Task: {kind}\n\n{instructions}\n\nEvidence (JSON):\n{json.dumps(evidence, default=str, indent=1)}"
        resp = self.complete(system, [{"role": "user", "content": user}], [])
        return resp.text, resp.usage


# --- OpenAI-compatible (Gemini, Groq, OpenRouter, Ollama, ...) ---------------------------

PRESETS: dict[str, dict[str, str | None]] = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-3.6-flash",  # 2.5-flash is closed to new keys since 2026-09
        "key_setting": "gemini_api_key",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",  # llama-3.3-70b-versatile was retired by 2026-09-17
        "key_setting": "groq_api_key",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "nvidia/nemotron-3-super-120b-a12b:free",  # free with tool calling, 2026-09-17
        "key_setting": "openrouter_api_key",
    },
    "ollama": {"base_url": "http://localhost:11434/v1", "model": "qwen3:4b", "key_setting": None},
}
"""Free routes the platform knows. Model defaults are the ids current when written (2026-09);
``NOVERA_LLM_MODEL`` overrides them when a provider renames its models."""


class ProviderError(RuntimeError):
    """The provider refused or failed the request; the message is safe to show."""


def _strip_schema_keys(schema: Any, drop: frozenset[str] = frozenset({"additionalProperties"})) -> Any:
    """JSON-schema keywords some compatible endpoints reject in function parameters."""
    if isinstance(schema, dict):
        return {k: _strip_schema_keys(v, drop) for k, v in schema.items() if k not in drop}
    if isinstance(schema, list):
        return [_strip_schema_keys(v, drop) for v in schema]
    return schema


def to_chat_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Messages-API tool definition -> chat-completions function tool."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": _strip_schema_keys(tool["input_schema"]),
        },
    }


def to_chat_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages-API turns (text, tool_use, tool_result blocks) -> chat-completions messages
    (assistant ``tool_calls``, ``tool`` role results)."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        if m["role"] == "assistant":
            text = "\n".join(b["text"] for b in content if b.get("type") == "text").strip()
            calls = []
            for b in content:
                if b.get("type") != "tool_use":
                    continue
                call: dict[str, Any] = {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
                }
                if b.get("extra"):
                    call.update(b["extra"])
                calls.append(call)
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        texts: list[str] = []
        for b in content:
            if b.get("type") == "tool_result":
                body = b.get("content", "")
                if not isinstance(body, str):
                    body = json.dumps(body, default=str)
                if b.get("is_error"):
                    body = f"ERROR: {body}"
                out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": body})
            elif b.get("type") == "text":
                texts.append(b["text"])
        if texts:
            out.append({"role": "user", "content": "\n".join(texts)})
    return out


def from_chat_completion(data: dict[str, Any], default_model: str) -> ProviderResponse:
    """Chat-completions response -> the Messages-API shape the Analyst loop reads."""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content: list[TextBlock | ToolUseBlock] = []
    raw_text = msg.get("content")
    if isinstance(raw_text, list):  # some endpoints return content parts
        raw_text = "".join(p.get("text", "") for p in raw_text if isinstance(p, dict))
    if raw_text and str(raw_text).strip():
        content.append(TextBlock(str(raw_text).strip()))
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments") or "{}"
        try:
            parsed = json.loads(args) if isinstance(args, str) else dict(args)
        except json.JSONDecodeError:
            parsed = {"_raw": args}
        extra = {k: v for k, v in tc.items() if k not in ("id", "type", "function", "index")} or None
        content.append(
            ToolUseBlock(
                tc.get("id") or f"call_{uuid.uuid4().hex[:12]}", fn.get("name", ""), parsed, extra=extra
            )
        )
    finish = choice.get("finish_reason")
    if any(isinstance(b, ToolUseBlock) for b in content):
        stop = "tool_use"
    elif finish == "length":
        stop = "max_tokens"
    else:
        stop = "end_turn"
    u = data.get("usage") or {}
    usage = {
        "input_tokens": int(u.get("prompt_tokens") or 0),
        "output_tokens": int(u.get("completion_tokens") or 0),
        "cache_read_input_tokens": 0,
    }
    return ProviderResponse(content, stop, str(data.get("model") or default_model), usage)


class OpenAICompatProvider:
    """Any endpoint speaking the OpenAI chat-completions shape with function tools. Used for
    the free tiers (Gemini, Groq, OpenRouter) and local Ollama; see ``PRESETS``."""

    def __init__(
        self,
        name: str,
        base_url: str,
        model: str,
        api_key: str | None = None,
        max_tokens: int = 8000,
        timeout: float = 180.0,
        transport: Any = None,
    ) -> None:
        import httpx

        self.name = name
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = httpx.Client(
            base_url=self.base_url, headers=headers, timeout=timeout, transport=transport
        )

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ProviderResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": to_chat_messages(system, messages),
            "max_tokens": self.max_tokens,
        }
        if tools:
            body["tools"] = [to_chat_tool(t) for t in tools]
            body["tool_choice"] = "auto"
        try:
            r = self.client.post("/chat/completions", json=body)
        except Exception as e:  # noqa: BLE001 - network errors become one readable message
            raise ProviderError(f"{self.name}: cannot reach {self.base_url} ({type(e).__name__}: {e})") from e
        if r.status_code == 429:
            wait = r.headers.get("retry-after")
            raise ProviderError(
                f"{self.name}: rate limit reached on the free tier"
                + (f", retry after {wait}s" if wait else "")
                + ". Wait, or set NOVERA_LLM_MODEL to a model with more headroom."
            )
        if r.status_code >= 400:
            raise ProviderError(f"{self.name}: HTTP {r.status_code} from {self.base_url}: {r.text[:800]}")
        return from_chat_completion(r.json(), self.model)

    def draft(self, kind: str, evidence: dict[str, Any], instructions: str) -> tuple[str, dict[str, int]]:
        from novera.config import get_settings

        system = DRAFT_SYSTEM.format(platform=get_settings().platform_name)
        user = f"Task: {kind}\n\n{instructions}\n\nEvidence (JSON):\n{json.dumps(evidence, default=str, indent=1)}"
        resp = self.complete(system, [{"role": "user", "content": user}], [])
        return resp.text, resp.usage


# --- Fallback chain -----------------------------------------------------------------------

logger = logging.getLogger("novera.ai.provider")


class FallbackProvider:
    """Providers tried in order. A provider that fails (quota, outage, bad key, unreachable)
    is skipped and put in a cooldown, so a chain like Gemini, Groq, OpenRouter, Ollama keeps
    answering while any free tier has credit. ``name`` and ``model`` are those of the provider
    that answered last, so every stored answer names its real source."""

    def __init__(self, providers: list[Any], cooldown_seconds: float = 300.0) -> None:
        if not providers:
            raise ValueError("a fallback chain needs at least one provider")
        self.providers = providers
        self.cooldown_seconds = cooldown_seconds
        self.active = providers[0]
        self._blocked_until: dict[str, float] = {}

    @property
    def name(self) -> str:
        return self.active.name

    @property
    def model(self) -> str:
        return self.active.model

    @property
    def chain(self) -> list[str]:
        return [p.name for p in self.providers]

    def _order(self) -> list[Any]:
        import time

        now = time.monotonic()
        ready = [p for p in self.providers if self._blocked_until.get(p.name, 0.0) <= now]
        return ready or list(self.providers)  # everything is cooling down: try them all again

    def _block(self, provider: Any, err: Exception) -> None:
        import re
        import time

        m = re.search(r"retry after (\d+)s", str(err))
        wait = float(m.group(1)) if m else self.cooldown_seconds
        self._blocked_until[provider.name] = time.monotonic() + wait

    def _attempt(self, call: Any) -> Any:
        failures: list[str] = []
        for p in self._order():
            try:
                result = call(p)
            except ProviderError as e:
                failures.append(str(e))
                self._block(p, e)
                logger.warning("provider %s skipped, trying the next in the chain: %s", p.name, str(e)[:200])
                continue
            self.active = p
            return result
        raise ProviderError("every provider in the chain failed: " + " | ".join(failures))

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ProviderResponse:
        def call(p: Any) -> ProviderResponse:
            # tool-call extras (Gemini's thought signatures) only make sense to their author
            msgs = messages if p is self.active else strip_provider_extras(messages)
            return p.complete(system, msgs, tools)

        return self._attempt(call)

    def draft(self, kind: str, evidence: dict[str, Any], instructions: str) -> tuple[str, dict[str, int]]:
        return self._attempt(lambda p: p.draft(kind, evidence, instructions))


# --- Scripted -----------------------------------------------------------------------------

_PCT = r"(-?\d+(?:\.\d+)?)\s*(%|percent|pct|bp|bps|points?|pts|vol)"


def _plan_what_if(q: str) -> list[dict[str, Any]] | None:
    """Parse 'equities fall 20%, vol up 15 points, oil -30%, BTC -40%, rates +100bp' style text."""
    ql = q.lower()
    if not re.search(r"what if|what happens|scenario|if .*(fall|drop|rise|up|down|widen|\+|-)", ql):
        return None
    words = {
        "equities": ["equit", "stock", "spx", "index"],
        "vol": ["vol"],
        "oil": ["oil", "brent", "wti", "crude"],
        "btc": ["btc", "bitcoin"],
        "crypto": ["crypto"],
        "credit": ["credit", "spread"],
        "rates_usd": ["usd rate", "us rate", "treasur", "ust"],
        "rates_all": ["rates", "yield"],
        "gold": ["gold"],
        "usd": ["dollar", "usd +", "usd up", "usd strength"],
        "eurusd": ["eurusd", "eur/usd", "euro"],
        "em_fx": ["em fx", "emerging"],
    }
    shocks: list[dict[str, Any]] = []
    for clause in re.split(r",|;| and | then ", ql):
        m = re.search(_PCT, clause)
        if not m:
            continue
        size = float(m.group(1))
        unit_raw = m.group(2)
        if re.search(r"\b(fall|drop|down|decline|lower|crash|sell)", clause) and size > 0:
            size = -size
        target = next((t for t, keys in words.items() if any(k in clause for k in keys)), None)
        if target is None:
            continue
        if target == "rates_all" and "usd" in clause:
            target = "rates_usd"
        unit = (
            "bp"
            if unit_raw.startswith("bp")
            else (
                "vol_points"
                if target == "vol" or "vol" in unit_raw or unit_raw.startswith(("point", "pts"))
                else "pct"
            )
        )
        if target == "vol" and unit == "pct":
            unit = "vol_points"
        shocks.append({"target": target, "size": size, "unit": unit})
    return shocks or None


def _plan(q: str) -> list[tuple[str, dict[str, Any]]]:
    ql = q.lower()
    shocks = _plan_what_if(q)
    if shocks:
        return [("what_if", {"shocks": shocks, "by": "asset_class"})]
    if any(k in ql for k in ("commentary", "morning", "report", "summar", "overview", "brief")):
        return [
            ("run_summary", {}),
            ("compare_runs", {"by": "asset_class"}),
            ("limits", {"status": "BREACH"}),
            ("breaches", {}),
            ("data_quality", {}),
            ("stress", {"by": "asset_class"}),
        ]
    mb = re.search(r"\b(brc_[a-z0-9]+_[a-z0-9]+)\b", q)
    if mb and any(k in ql for k in ("investigat", "why", "explain", "note")):
        return [("agent", {"agent": "investigate_breach", "breach_id": mb.group(1)})]
    if any(k in ql for k in ("suggest", "propose", "which scenario", "what scenario", "should i run")):
        return [("agent", {"agent": "suggest_scenarios"})]
    m = re.search(r"\b([A-Z]{2,4}_\d{6})\b", q)
    if m:
        return [("trade", {"trade_id": m.group(1)})]
    if any(
        k in ql for k in ("why", "changed", "change", "increase", "decrease", "since yesterday", "day on day")
    ):
        return [("compare_runs", {"by": "asset_class"}), ("pnl", {"by": "asset_class"})]
    if any(k in ql for k in ("closest", "near", "breach", "limit", "utilis")):
        return [("limits", {"status": "BREACH"}), ("limits", {"status": "WARNING"}), ("breaches", {})]
    if any(
        k in ql
        for k in ("look-through", "lookthrough", "look through", "etf", "mutual fund", "constituent", "prox")
    ):
        return [("lookthrough", {})]
    if any(
        k in ql
        for k in ("nav", "leverag", "prime broker", "redemption", "investor", "crowd", "strateg", "fund")
    ):
        return [("fund_overview", {})]
    if any(
        k in ql for k in ("capital", "frtb", "rwa", "sa-ccr", "saccr", "simm", "initial margin", "regulat")
    ):
        return [("capital", {})]
    if any(k in ql for k in ("concentrat", "hhi", "largest position", "biggest position")):
        return [("concentration", {})]
    if any(k in ql for k in ("liquid", "days to", "unwind", "exit")):
        return [("liquidity", {})]
    if any(k in ql for k in ("backtest", "exception", "kupiec", "traffic light")):
        return [("backtest", {})]
    if any(k in ql for k in ("trust", "data quality", "stale", "missing")):
        return [("data_quality", {})]
    if "stress" in ql or "scenario" in ql:
        return [("stress", {"by": "asset_class"})]
    if any(k in ql for k in ("p&l", "pnl", "profit", "explain")):
        return [("pnl", {"by": "desk_id"})]
    if any(k in ql for k in ("dv01", "duration", "rates risk", "curve")):
        return [("sensitivities", {"measure": "DV01", "by": "desk_id"})]
    if any(
        k in ql for k in ("counterpart", "bank a", "exposure", "cva", "pfe", "collateral", "netting", "wrong")
    ):
        m2 = re.search(
            r"\b(BANK_[A-Z]|HF_[A-Z]+|CORP_[A-Z]+|SOV_[A-Z]+|DEALER_[A-Z]|AM_[A-Z]+|INS_[A-Z]+|PENSION_[A-Z]+)\b",
            q.upper(),
        )
        return [("counterparty_exposure", {"counterparty_id": m2.group(1)} if m2 else {})]
    if "var" in ql or "risk" in ql:
        return [("run_summary", {}), ("var_by", {"by": "desk_id"})]
    return [("run_summary", {})]


def _fmt_m(x: Any) -> str:
    return "n/a" if x is None else f"{x:,.2f}m"


def _compose(question: str, results: list[tuple[str, dict[str, Any], dict[str, Any]]]) -> str:
    lines: list[str] = []
    run_ids: set[str] = set()
    for name, args, res in results:
        if "run_id" in res:
            run_ids.add(res["run_id"])
        if "error" in res:
            lines.append(f"- {name}: {res['error']}")
            continue
        if name == "run_summary":
            lines += [
                f"**{res['business_date']}** — VaR 99% 1d {_fmt_m(res['var_99_1d_m'])}, ES 97.5% "
                f"{_fmt_m(res['es_975_1d_m'])}, challenger {_fmt_m(res['challenger_var_m'])}.",
                f"- Worst stress: {res['worst_stress']['name']} {_fmt_m(res['worst_stress']['loss_m'])}.",
                f"- Limits: {res['limits']['breaches']} breach, {res['limits']['warnings']} warning of "
                f"{res['limits']['monitored']}. Data quality {res['data_quality']['verdict']} "
                f"({res['data_quality']['findings']} findings).",
                f"- P&L {_fmt_m(res['pnl_total_m'])}: "
                + ", ".join(f"{k} {v:+.2f}" for k, v in res["pnl_steps_m"].items() if v and abs(v) >= 0.05)
                + ".",
                "- VaR by asset class: "
                + ", ".join(
                    f"{k} {v}"
                    for k, v in sorted(res["var_by_asset_class_m"].items(), key=lambda kv: -(kv[1] or 0))
                )
                + ".",
            ]
        elif name == "var_by":
            top = res["rows"][:6]
            lines.append(
                f"Component VaR by {res['by']}: "
                + ", ".join(
                    f"{r[res['by']]} {_fmt_m(r['component_var_m'])} "
                    f"(challenger {_fmt_m(r['challenger_var_m'])})"
                    for r in top
                )
                + "."
            )
        elif name == "limits":
            tag = args.get("status") or args.get("level") or "all"
            if not res["limits"]:
                lines.append(f"- No limits with status {tag}.")
            for r in res["limits"][:8]:
                lines.append(
                    f"- {r['status']} {r['limit_id']} at {r['utilisation_pct']}% "
                    f"({r['current']}{'' if r['unit'] == 'share' else 'm'} of "
                    f"{r['limit']}{'' if r['unit'] == 'share' else 'm'}), owner {r['owner']}."
                )
        elif name == "breaches":
            for b in res["breaches"][:8]:
                lines.append(
                    f"- Breach {b['limit_id']}: {b['status']}, day {b['consecutive_days']}, "
                    f"{b['latest_utilisation']:.0%}"
                    + (f", escalated to {b['escalated_to']}" if b["escalated_to"] else "")
                    + (", back within limit" if b["within_limit_on_latest_run"] else "")
                    + "."
                )
        elif name == "pnl":
            steps = ", ".join(f"{k} {v:+.2f}" for k, v in res["steps_m"].items() if v and abs(v) >= 0.05)
            lines.append(f"P&L {_fmt_m(res['total_m'])}: {steps}.")
            ch = res.get("sensitivity_challenger_m")
            if ch:
                lines.append(
                    f"- Sensitivity-based estimate {_fmt_m(ch['predicted'])}, unexplained "
                    f"{_fmt_m(ch['unexplained'])}; largest residuals: "
                    + ", ".join(
                        f"{w['trade_id']} {w['residual_m']:+.2f}m" for w in ch["largest_unexplained"][:3]
                    )
                    + "."
                )
            key = next((k for k in res if k.startswith("by_")), None)
            if key:
                by = key[3:-2]
                rows = sorted(res[key], key=lambda r: r.get("TOTAL") or 0)
                lines.append(
                    f"- By {by}: worst "
                    + ", ".join(f"{r[by]} {r['TOTAL']:+.2f}m" for r in rows[:3])
                    + "; best "
                    + ", ".join(f"{r[by]} {r['TOTAL']:+.2f}m" for r in rows[-3:][::-1])
                    + "."
                )
        elif name == "data_quality":
            lines.append(f"Run verdict {res['verdict']} with {len(res['findings'])} findings:")
            for f in res["findings"]:
                lines.append(
                    f"- {f['severity']} {f['code']}: {f['message']} ({f['affected_trades']} trades, "
                    f"owner {f['owner']})."
                )
        elif name == "stress":
            for s in res["scenarios"][:5]:
                key = next(k for k in s if k.startswith("by_"))
                worst = sorted(s[key].items(), key=lambda kv: kv[1] or 0)[:2]
                lines.append(
                    f"- {s['scenario']}: {_fmt_m(s['total_m'])}, mostly "
                    + ", ".join(f"{k} {_fmt_m(v)}" for k, v in worst)
                    + "."
                )
        elif name == "sensitivities":
            for r in res["rows"][:8]:
                bucket = f" {r['bucket']}" if r.get("bucket") else ""
                lines.append(
                    f"- {r.get('desk_id') or r.get(list(r)[0])} {r['underlying']}{bucket}: "
                    f"{r['value_k']:+,.1f}k ({res['unit']})."
                )
        elif name == "trade":
            lines.append(
                f"{res['trade_id']}: {res['product']} in {res['book']} ({res['desk']}), facing "
                f"{res['counterparty']}, quantity {res['quantity']:,.0f} {res['currency']}, PV "
                f"{_fmt_m(res['pv_m'])}."
            )
            if res["sensitivities_k"]:
                lines.append(
                    "- Sensitivities: "
                    + ", ".join(
                        f"{s['measure']} {s['factor']} {s['value_k']:+,.1f}k"
                        for s in res["sensitivities_k"][:5]
                    )
                    + "."
                )
        elif name == "compare_runs":
            a, b, h = res["run_a"], res["run_b"], res["headline_m"]
            lines.append(
                f"From {a['business_date']} to {b['business_date']}: VaR {_fmt_m(h['var']['a'])} → "
                f"{_fmt_m(h['var']['b'])} ({h['var']['change']:+.2f}m), breaches {h['breaches']['a']} → "
                f"{h['breaches']['b']}, P&L {_fmt_m(h['pnl_total']['b'])}."
            )
            key = next(k for k in res if k.startswith("var_by_"))
            moves = sorted(res[key], key=lambda r: -abs(r["change"] or 0))[:4]
            lines.append(
                "- VaR moves: " + ", ".join(f"{list(r.values())[0]} {r['change']:+.2f}m" for r in moves) + "."
            )
            if res["limits_changed"]:
                lines.append(
                    "- Limits that moved: "
                    + ", ".join(
                        f"{r['limit_id']} {r['status_a']}→{r['status_b']}" for r in res["limits_changed"][:5]
                    )
                    + "."
                )
            lines.append(
                f"- {res['trades_only_in_b']} new trades, {res['trades_only_in_a']} left the book; largest "
                "moves: "
                + ", ".join(
                    f"{r['trade_id']} {r['pv_change_m']:+.2f}m ({r['presence']})"
                    for r in res["largest_trade_moves_m"][:3]
                )
                + "."
            )
        elif name == "what_if":
            lines.append(
                f"Scenario '{res['scenario']}' ({res['factors_shocked']} factors shocked): "
                f"P&L {_fmt_m(res['total_pnl_m'])}."
            )
            lines.append(
                "- By asset class: "
                + ", ".join(
                    f"{k} {_fmt_m(v)}" for k, v in sorted(res["by_m"].items(), key=lambda kv: kv[1] or 0)
                )
                + "."
            )
            lines.append(
                "- Worst trades: "
                + ", ".join(
                    f"{w['trade_id']} ({w['desk_id']}) {w['pnl_m']:+.2f}m" for w in res["worst_trades"][:4]
                )
                + "."
            )
        elif name == "concentration":
            for f in res["flags"][:6]:
                lines.append(f"- {f}")
            lines.append(
                "- Largest contributors: "
                + ", ".join(
                    f"{x['trade_id']} ({x['desk_id']}) {x['component_var_m']}m, {x['share_of_var']:.0%}"
                    for x in res["top_positions_m"][:4]
                )
                + "."
            )
        elif name == "liquidity":
            lines.append(
                f"Liquidity-adjusted VaR {_fmt_m(res['liquidity_adjusted_var_m'])} versus VaR "
                f"{_fmt_m(res['var_m'])}; weighted horizon {res['weighted_horizon_days']} days."
            )
            lines.append(
                "- By horizon: "
                + ", ".join(f"{b['bucket']} {b['share']:.0%}" for b in res["by_bucket"])
                + "."
            )
            for f in res["flags"][:4]:
                lines.append(f"- {f}")
        elif name == "backtest":
            for x in res["summary"]:
                lines.append(
                    f"- {x['kind']}: {x['exceptions']} exceptions in {x['days']} days (expected "
                    f"{x['expected_exceptions']:.1f}), Kupiec p {x['kupiec_pvalue']:.2f}, Christoffersen p "
                    f"{x['christoffersen_pvalue']:.2f}, zone {x['zone']}."
                )
        elif name == "counterparty_exposure":
            if "counterparties" in res:
                for x in res["counterparties"][:8]:
                    lines.append(
                        f"- {x['counterparty_id']} ({x['rating']}{'' if x['collateralised'] else ', no CSA'}): "
                        f"EPE {_fmt_m(x['epe_m'])}, peak PFE95 {_fmt_m(x['peak_pfe95_m'])} (gross "
                        f"{_fmt_m(x['peak_pfe95_gross_m'])}), CVA {_fmt_m(x['cva_m'])}"
                        + (", wrong-way risk" if x["wrong_way"] else "")
                        + "."
                    )
            else:
                cp = res.get("counterparty") or {}
                lines.append(
                    f"{cp.get('name', '')} ({cp.get('counterparty_type')}, {cp.get('rating')}): "
                    + ", ".join(
                        f"{p['step']} EE {_fmt_m(p['ee'])} / PFE95 {_fmt_m(p['pfe95'])}"
                        for p in res["profile_m"][:6]
                    )
                    + "."
                )
                for n in res["netting_sets"]:
                    csa = n["csa"]
                    lines.append(
                        f"- {n['netting_set_id']}: "
                        + (
                            f"threshold they post {csa['threshold_they_post'] / 1e6:.1f}m, "
                            f"MTA {csa['minimum_transfer_amount'] / 1e6:.2f}m"
                            if csa
                            else "no CSA"
                        )
                        + "."
                    )
                for w in res["wrong_way"]:
                    if w.get("wrong_way"):
                        lines.append(
                            f"- Wrong-way risk: correlation {w['correlation']:.2f} with {w['proxy']} at {w['at_step']}."
                        )
                if res["stressed_exposure_m"]:
                    s0 = res["stressed_exposure_m"][0]
                    lines.append(
                        f"- Under {s0['scenario']}, current exposure goes from {_fmt_m(s0['current'])} to "
                        f"{_fmt_m(s0['stressed'])}."
                    )
        elif name == "agent":
            if "error" in res:
                lines.append(f"- {res['error']}")
            else:
                lines.append(res["text"].split("\n\n_Scripted provider")[0])
                lines.append(f"- Stored as agent note {res['note_id']} ({res['status']}).")
        elif name == "lookthrough":
            if not res["fund_trades"]:
                lines.append("No ETF or mutual-fund positions in this run.")
            else:
                lines.append(
                    f"{res['fund_trades']} fund positions looked through: "
                    + ", ".join(f"{x['fund']} {_fmt_m(x['exposure_m'])}" for x in res["by_fund_m"][:4])
                    + "."
                )
                for c in res["constituents_m"][:4]:
                    lines.append(
                        f"- {c['constituent']}: {_fmt_m(c['direct_m'])} direct plus {_fmt_m(c['via_funds_m'])} "
                        f"via funds ({c['via_funds_share']:.0%} of the total)."
                    )
                for fl in res["flags"][:3]:
                    lines.append(f"- {fl}")
            px = res["market_data_proxies"]
            if px["applied"]:
                lines.append(
                    f"- Market data: {px['applied']} factor values were proxied before pricing "
                    f"({', '.join(px['families'][:4])}); {px['kept_stale']} stale values kept as observed."
                )
        elif name == "fund_overview":
            if "error" in res:
                lines.append(f"- {res['error']}")
            else:
                lines.append(
                    f"NAV {_fmt_m(res['nav_m'])}, gross leverage {res['gross_leverage']}x (net "
                    f"{res['net_leverage']}x), VaR {res['var_pct_nav']}% of NAV, worst stress "
                    f"{res['worst_stress_pct_nav']}% of NAV, margin {res['margin_to_nav']:.1%} of NAV with "
                    f"the largest broker at {res['largest_pb_share']:.0%}."
                )
                for f in res["flags"][:4]:
                    lines.append(f"- {f}")
                lines.append(
                    "- Strategies by VaR share: "
                    + ", ".join(
                        f"{x['strategy']} {x['share_of_var']:.0%} (gross {x['gross_pct_nav']}% NAV)"
                        for x in res["strategies"][:5]
                    )
                    + "."
                )
                worst = (
                    min(res["redemptions"], key=lambda z: z["coverage"] or 1e9)
                    if res["redemptions"]
                    else None
                )
                if worst:
                    lines.append(
                        f"- Redemption stress: worst coverage {worst['coverage']}x in {worst['scenario']} at "
                        f"{worst['date']}, shortfall {_fmt_m(worst['shortfall_m'])}."
                    )
                lines.append(
                    "- Largest factor betas: "
                    + ", ".join(
                        f"{x['strategy']} to {x['factor']} {x['beta_pct_nav_per_sigma']:+.2f}% NAV per sigma"
                        for x in res["top_factor_betas"][:3]
                    )
                    + "."
                )
        elif name == "capital":
            if "error" in res:
                lines.append(f"- {res['error']}")
            else:
                c = res["capital_m"]
                lines.append(
                    f"FRTB SA {_fmt_m(c['frtb_sa'])} (SBM {_fmt_m(c['frtb_sa_sbm'])}, DRC "
                    f"{_fmt_m(c['frtb_sa_drc'])}) versus IMA {_fmt_m(c['frtb_ima'])} (IMES {_fmt_m(c['imes'])} "
                    f"x {res['ima_multiplier']}, NMRF {res['nmrf']}); SA-CCR EAD {_fmt_m(c['saccr_ead'])}, "
                    f"RWA {_fmt_m(c['saccr_rwa'])}, capital {_fmt_m(c['saccr_capital'])}; BA-CVA "
                    f"{_fmt_m(c['ba_cva_capital'])}; SIMM IM {_fmt_m(c['simm_im'])}."
                )
                lines.append(
                    "- FRTB SA by class: "
                    + ", ".join(f"{x['class']} {_fmt_m(x['delta'])}" for x in res["frtb_sa_by_class_m"])
                    + "."
                )
                lines.append(
                    "- Largest desks: "
                    + ", ".join(f"{x['desk_id']} SA {_fmt_m(x['frtb_sa'])}" for x in res["by_desk_m"][:4])
                    + "."
                )
        elif name == "positions":
            lines.append(
                "PV by "
                + res["by"]
                + ": "
                + ", ".join(f"{r[res['by']]} {_fmt_m(r['pv_m'])}" for r in res["rows"][:8])
                + "."
            )
    if run_ids:
        lines.append(
            f"\n_Source: run {', '.join(sorted(run_ids))}. Scripted provider: numbers are read from "
            "the stored run, the wording is templated._"
        )
    return "\n".join(lines)


class ScriptedProvider:
    """Deterministic provider: first turn plans tool calls from the question, second turn
    writes the answer from the tool results. No network, no credentials."""

    name = "scripted"
    model = "scripted-v1"

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ProviderResponse:
        question = next(
            (m["content"] for m in messages if m["role"] == "user" and isinstance(m["content"], str)), ""
        )
        # Turn 2+: the last user message carries tool results.
        last = messages[-1]
        if (
            last["role"] == "user"
            and isinstance(last["content"], list)
            and any(b.get("type") == "tool_result" for b in last["content"])
        ):
            calls = {
                b["id"]: (b["name"], b["input"])
                for m in messages
                if m["role"] == "assistant"
                for b in m["content"]
                if isinstance(b, dict) and b.get("type") == "tool_use"
            }
            results = []
            for b in last["content"]:
                if b.get("type") != "tool_result":
                    continue
                name, args = calls.get(b["tool_use_id"], ("?", {}))
                try:
                    payload = json.loads(b["content"]) if isinstance(b["content"], str) else b["content"]
                except json.JSONDecodeError:
                    payload = {"error": str(b["content"])}
                results.append((name, args, payload))
            return ProviderResponse([TextBlock(_compose(question, results))], "end_turn", self.model)
        available = {t["name"] for t in tools}
        if not available:  # drafting call without tools: answer with the templated draft
            return ProviderResponse([TextBlock(question)], "end_turn", self.model)
        plan = [(n, a) for n, a in _plan(question) if n in available] or [("run_summary", {})]
        return ProviderResponse(
            [ToolUseBlock(f"toolu_{uuid.uuid4().hex[:12]}", n, a) for n, a in plan], "tool_use", self.model
        )

    def draft(self, kind: str, evidence: dict[str, Any], instructions: str) -> tuple[str, dict[str, int]]:
        from novera.ai.drafts import templated_draft

        return templated_draft(kind, evidence), {}
