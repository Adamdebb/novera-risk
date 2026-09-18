# Novera Analyst  (ID: AI-001)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Technology |
| Approval status | Draft |
| Code | `novera.ai` |
| Last validated | 2026-09-14 |

## Definition
A tool-calling assistant over stored run results. The model chooses which tools to call
and writes the explanation; the tools are the same read methods the API exposes plus a
what-if tool that reprices the run's portfolio through the deterministic engine.

## Governance (ADR 0003)
- The model never computes a risk figure. Every number in an answer traces to a tool
  result in the same conversation, and the system prompt requires the run id to be cited.
- No write tools. Requests to change state are answered with the manual route.
- Every answer is stored with the question, provider, model, every tool call with input
  and output, the run ids cited, token usage and timing, plus an audit event.
- Tool outputs are compact and rounded (millions) so the model sees what a risk pack shows.

## Tools
run_summary, var_by, sensitivities, stress, limits, breaches, pnl, data_quality, trade,
positions, compare_runs, what_if (targets: equities, rates_usd, rates_all, credit, oil,
gold, metals, btc, crypto, vol, equity_vol, fx_vol, eurusd, em_fx, usd, or any factor
prefix; units pct, bp, vol_points; optional tenors).

## Providers
`NOVERA_LLM_PROVIDER` names one provider or a comma-separated chain (`gemini,groq,
openrouter,ollama`). A chain is a `FallbackProvider`: providers are tried in order, a
provider without a key is left out, and one that fails at run time (quota exhausted, outage,
bad key, unreachable) is skipped and rested for the `retry-after` the endpoint gave or five
minutes, then tried again. When the chain switches provider mid-conversation, the previous
provider's tool-call extras are stripped from the history. `auto` (the default) is the chain
of every provider with a key in the order Anthropic, Gemini, Groq, OpenRouter, then a
configured base URL, else `scripted`. A chain member may name its model after a colon
(`openrouter:google/gemma-4-31b-it:free`); `NOVERA_LLM_MODEL` applies to a single provider
only, since model ids do not carry across providers. Every answer records the provider and
model that actually produced it.
- `anthropic`: Claude Opus 5 via the official SDK, adaptive thinking, effort high, server-side
  refusal fallbacks. `ANTHROPIC_API_KEY`. Paid per token.
- `gemini`, `groq`, `openrouter`, `ollama`: one adapter (`OpenAICompatProvider`) speaking the
  OpenAI chat-completions shape with function tools, which all four expose; the Messages-API
  turns are translated both ways (`to_chat_messages`, `from_chat_completion`), so the loop,
  the tool registry and the governance record are identical. Presets in `PRESETS` hold the
  base URL, a default model and the key variable (`GEMINI_API_KEY`, `GROQ_API_KEY`,
  `OPENROUTER_API_KEY`; Ollama needs none). Gemini, Groq and OpenRouter have free tiers with
  daily rate limits; a 429 comes back as a readable `ProviderError`. On Google's free tier
  prompts may be used to improve its products, acceptable here because every number is
  simulated. `openai_compat` with `NOVERA_LLM_BASE_URL` reaches any other such endpoint.
  Gemini 3 attaches a `thought_signature` to each tool call and rejects the next turn
  without it; the adapter keeps any provider extras on the `ToolUseBlock` and returns them
  with the call (`extra_content`), and the Anthropic adapter strips them before sending.
  Verified live on 2026-09-16 with `gemini-3.6-flash` (three turns, 14 s, sourced answer);
  `gemini-2.5-flash` is closed to new keys.
- `scripted`: deterministic keyword planner and templated answers over the same tools.
  Used without credentials and in the test suite. Answers are marked as scripted.

## Limitations
No multi-turn memory beyond the session history passed by the caller. The scripted
provider only understands the demo question patterns. The model cannot see trade-level
data unless a tool returns it, by design.

## Validation tests
`tests/test_analyst.py`: fallback chain (skip on failure, cooldown, extras stripped on a
switch, every-member-failed error, chain from settings); OpenAI-compatible adapter (turn translation both ways through the
Analyst loop with a mock transport, tool-call parsing, 429 handling) and provider selection
from settings; loop feeds tool results back and stores the record; tool errors
return as results, never raise; what-if reprices through the engine; scripted planner
covers the demo questions.
