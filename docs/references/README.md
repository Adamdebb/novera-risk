# External references for stress testing

Ideas only. Nothing here is a dependency, and nothing here produces a number.

## Rules
1. No library or paper listed here is imported, vendored or copied into `src/novera`, and none
   is added to `pyproject.toml` or `uv.lock`.
2. No stress scenario, shock size or result comes from these sources. The engine computes every
   figure (CLAUDE.md rule 1).
3. An idea is adopted only by re-implementing it in `novera.risk` with its own methodology
   record in `docs/methodology/` and tests. The record's "Prior art" line links the note here.
4. Licences of the libraries were not checked. That is a second reason not to copy code.

## Note template
Each note has: what the source does, the idea worth taking, where it would land in Novera,
what we deliberately do not take.

## Index
| Note | Source | Status |
|---|---|---|
| [wraquant](wraquant.md) | Python library, `wraquant.risk.stress` | Read (docs page only, not the repository) |
| [torchquantlib](torchquantlib.md) | Python library, `stress_testing` module | Read (docs page only) |
| [arXiv 2601.03983](arxiv-2601-03983.md) | Hurlin, Lajaunie, Pull (2026) | Abstract read, full paper not |
| [Erasmus thesis](erasmus-reverse-stress.md) | van Oers (2017) | Abstract and contents read, chapters not |

## Candidate improvements these notes feed
| Gap in MR-005 | Notes |
|---|---|
| Reverse stress test | arXiv, Erasmus, wraquant |
| Correlation breakdown | wraquant |
| Liquidity horizon / liquidation cost | wraquant |
| Loss attribution by position under a scenario | wraquant |
