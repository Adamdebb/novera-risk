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
- `anthropic`: Claude Opus 5 via the official SDK, adaptive thinking, effort high, server-side
  refusal fallbacks. Used when `ANTHROPIC_API_KEY` is set.
- `scripted`: deterministic keyword planner and templated answers over the same tools.
  Used without credentials and in the test suite. Answers are marked as scripted.

## Limitations
No multi-turn memory beyond the session history passed by the caller. The scripted
provider only understands the demo question patterns. The model cannot see trade-level
data unless a tool returns it, by design.

## Validation tests
`tests/test_analyst.py`: loop feeds tool results back and stores the record; tool errors
return as results, never raise; what-if reprices through the engine; scripted planner
covers the demo questions.
