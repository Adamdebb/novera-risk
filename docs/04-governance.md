# Governance

## Methodology records

Every metric has a file in `docs/methodology/` from `TEMPLATE.md` with: definition,
mathematical method, inputs, assumptions, calibration, limitations, validation tests,
version, owner, approval status. Code references the record ID in the function docstring.

## Reproducible runs

A run is `(portfolio_snapshot_id, market_snapshot_id, scenario_set_id, model_versions,
config_hash)`. Results are written once, never updated. A re-run creates a new run ID and
a comparison can be produced between any two runs.

## Audit events

Immutable, append-only: run started/finished, limit created/changed/approved, breach
raised/acknowledged/escalated/closed, override applied, data-quality exception raised,
AI answer produced (with tool calls and run IDs cited).

## Data quality

Checks run before results are trusted: market-data staleness per factor, missing curve
nodes, unpriced trades, invalid trade fields, position reconciliation between sources,
unexplained P&L above tolerance, business-date and timezone consistency. A run gets a
trust verdict: GREEN, AMBER (usable with caveats) or RED (do not publish).

## The VaR setup
An administrator decides on the Admin page which VaR measures the firm produces every day,
which of them feed the limits and which are for information (OPS-004): a bank keeps a 99%
two-year historical VaR and a stressed VaR on limits, a hedge fund a 95% exponentially
weighted one-year VaR. Every change names an actor and is audited; every run records the
matrix it produced, so a stored run is read and re-run on its own matrix, never on today's.

## Sign-off and release
After a run persists, named people sign the metrics the firm's policy requires (OPS-003).
The value seen is frozen with the signature, every sign, rejection and policy change is an
audit event, and a run is released when every required metric is signed. A RED verdict can
only be signed with a comment, recorded as an override. Sign-off never edits a stored result.

## AI rules

Allowed: explain, attribute, draft, summarise, translate a question into governed tool
calls, propose scenarios, triage exceptions, generate tests and documentation.

Forbidden: compute or alter any official number, change a limit without approval, produce
an explanation that does not cite the run ID and inputs, act as model validation, hide
pricing assumptions behind prose.

Every AI answer is stored with the prompt, the tool calls made, the run IDs used and the
model version, so it can be reviewed like any other output.

## Model versioning

Each pricer and risk method exposes `MODEL_VERSION`. The run record stores all versions.
A change to a method that alters numbers bumps the version and the methodology record.

## Model inventory

`docs/methodology/MV-001` lists every product with the model used, the market-standard
model, the simplifications, a rating (market standard, acceptable simplification, known
weakness) and the validation test. The table is rendered from the pricer catalogue in code
and a test fails when it is stale, so the inventory cannot say one thing and the pricer
another. Where QuantLib offers the market-standard model, the error of a simplification is
measured by a test and quoted in the record rather than assumed. The rating is set by the
methodology owner; AI may quote it, never change it.
