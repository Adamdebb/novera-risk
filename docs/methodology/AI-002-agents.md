# Agents: breach investigation, scenario suggestion, model-validation drafting  (ID: AI-002)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology / Risk Technology |
| Approval status | Draft |
| Code | `novera.ai.agents` (`breach.py`, `scenarios.py`, `validation.py`, `base.py`), `novera.ai.drafts` |
| Last validated | 2026-09-15 |

## Definition
An agent is two steps. **Gather** is deterministic Python that reads stored run frames and,
where needed, calls the engine (the what-if module): it produces an evidence dictionary in
which every number has a source (run id, frame, trade id). **Draft** hands the evidence to
the provider with fixed instructions and gets prose back: Claude when an API key is set,
templated text from `novera.ai.drafts` otherwise. The note is stored (`agent_note` table)
with its evidence, provider, model and status, and an `AGENT_NOTE` audit event is written.
Rule 1 of `CLAUDE.md` holds: no number in a note is computed by the model.

### Breach investigation
Evidence: the breach and its workflow history; utilisation on every run since it opened;
per-trade contributions in the limit's own measure (sensitivities for DV01/CS01/delta/vega
limits, component VaR for VaR limits, worst-scenario loss for stress limits, positive PV
for counterparty limits, the concentrated bucket for concentration limits) with desk,
book, trader and counterparty; trades added to or removed from the scope since the first
breaching run; a remediation sized by the engine (amount to cut to the limit and to the
warning threshold, and the contributors that would deliver it, greedy by size); pending
temporary increases. The note is attached to the breach as a `COMMENT` by
`breach-investigator`, so it lives in the workflow audit trail.

### Scenario suggestion
Evidence: net exposure per factor family and underlying from the stored sensitivities;
the largest moves of the last 20 business days in units of the 500-day daily standard
deviation; the worst library stress. Proposals: for the largest exposures, a shock in the
loss direction of `2.33 σ √10` (a 99% ten-day move); a repeat of the largest recent move
scaled three times; the two largest exposures together. Every proposal is run through the
what-if engine on the stored run (MR-005, full revaluation) and ranked by loss.

### Model-validation drafting
Evidence: every methodology record parsed for id, version, owner, status, code, declared
limitations and the validation tests it names; whether each test exists in the suite and,
on request, its result when executed; live evidence from the latest run (VaR against the
challenger and Monte Carlo, backtest zone and exceptions, reconciliation gap and
attribution, data-quality verdict). The draft recommends approve / approve with conditions
/ return per record from those facts and is written to `data/reports/model_validation_<date>.md`.

## Limitations
The scripted drafts are templates: complete and traceable, not insightful. Contribution
attribution for VaR uses component VaR, which is a linear allocation; a breach driven by
diversification effects shows spread-out contributors. Scenario sizing assumes normal
scaling of daily moves to ten days. Test execution inside the validation agent runs pytest
as a subprocess and can take minutes.

## Validation tests
`test_breach_investigation_attaches_note_with_evidence`, `test_scenario_suggestion_runs_engine`,
`test_validation_report_reads_records_and_live_evidence`, `test_parse_record_template_shape`,
`test_copilot_routes_to_agents`, `test_scripted_provider_draft_is_templated`.
