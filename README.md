# Novera

**AI-native risk intelligence layer for global trading portfolios.**

Novera is a working prototype of how a modern market and counterparty risk function can
operate. It runs a deterministic, auditable risk engine over a simulated multi-asset
trading organisation, then adds the layer most institutions lack: automated monitoring,
explanation, scenario investigation, exception management and an AI Risk Copilot that
works only from validated numbers.

> We have enough risk numbers. The problem is turning them into decisions.

## What it does

| Capability          | What the platform answers                                   |
|---------------------|-------------------------------------------------------------|
| Aggregation         | What do we hold, what is it worth, at every hierarchy level |
| Monitoring          | What changed since yesterday                                |
| Investigation       | Why it changed: market move, new trades, model, data        |
| Simulation          | What happens under historical and hypothetical stress       |
| Control             | Which limits are breached, who owns them, what happens next |
| Intelligence        | Which risks matter most today                               |
| Copilot             | Ask the platform in plain language, get sourced answers     |

Individual measures such as VaR, expected shortfall, Greeks, DV01, CS01, PFE and CVA are
components that serve those capabilities.

## Status

Phase 1 (foundation) in progress. See `docs/03-roadmap.md`.

## Quick start

```bash
uv sync --all-extras
uv run pytest
uv run novera --help
```

## Documentation

- `docs/01-product-vision.md` — positioning, audiences, claims we make and avoid
- `docs/02-architecture.md` — layers, module map, boundaries
- `docs/03-roadmap.md` — phases and instrument scope
- `docs/04-governance.md` — methodology records, run reproducibility, AI rules
- `docs/05-demo-script.md` — the 10-minute executive demo
- `docs/adr/` — architecture decision records
- `docs/methodology/` — one record per metric

## Design principles

1. Deterministic engine, AI on top. No LLM produces an official number.
2. One workflow end to end before breadth. Products are added only when the full
   pipeline (capture, valuation, sensitivities, VaR, stress, limits, report) already works.
3. Reproducible runs. Same inputs, same run ID lineage, same numbers.
4. Vendor neutral. Designed to ingest results from other engines and reconcile them.

## Licence

Proprietary. All rights reserved.
