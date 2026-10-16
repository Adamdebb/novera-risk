# Stress setup  (ID: OPS-005)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Control |
| Approval status | Draft |
| Code | `novera.risk.stress_definitions`, `novera.workflows.stress_setup`, `GET /admin/stress/setup`, `POST /admin/stress/proposals`, `/decisions`, `/reviews`, `/seed`, `novera stress-setup`; the "Stress scenarios" section of the Admin page |
| Last validated | 2026-10-16 |

## Definition
The firm's stress library as governed records. Each scenario is a sequence of versions; only the
approved version runs. Each scenario has a goal: LIMIT scenarios feed the stress-loss limits,
INFORMATION scenarios are produced and reported but feed no limit. This is the first part of the
stress framework (`docs/08-stress-framework.md`, round 46). It changes how scenarios are
governed, not how they are revalued: the engine is still MR-005.

## A definition
| Field | Values | Meaning |
|---|---|---|
| scenario_id | lower-case slug | Stable across versions |
| kind | HYPOTHETICAL, HISTORICAL | Shock rules, or the observed moves over a window of the stored history |
| goal | LIMIT, INFORMATION | Whether the stress-loss limits read it |
| rules | prefix, size, vol points, tenors | HYPOTHETICAL: MR-005 shock rules in engine units (0.01 = +100bp for rates and basis, bp for CDS, fractions for prices, vol points for `VOL:` surfaces) |
| episode_id, or window_start and window_end | episode id, ISO dates | HISTORICAL: a stylised episode stored with the history (SIM-001), or a fixed window inside the history |
| enabled | yes, no | A disabled approved version retires the scenario without deleting it |
| tags | SENSITIVITY, CROSS_ASSET, HISTORICAL, BASIS, CORRELATION, JUMP, LIQUIDITY, COUNTERPARTY, CROWDING, MACRO, GEOPOLITICAL, REVERSE and the asset classes | The families of the framework; a scenario may carry several |
| severity | MODERATE, SEVERE, EXTREME | The committee's reading, not a probability |
| narrative, catalyst | text | What happens and what would trigger it |
| area | desk or business ids | Set from a desk or business head's scope when they propose |
| source | BUILT_IN, USER | The platform's library or a firm addition |

A proposal is validated before it is stored. Every rule must match at least one risk factor.
Vol points are allowed only on `VOL:` surfaces. Tenors must be curve nodes. No shock may be
zero. A historical window must lie inside the stored history, and an episode must be stored.

## Workflow
1. **Defaults.** Until anything is stored, the built-ins run: the 14 hypothetical rule sets of
   MR-005 and one definition per stored stylised episode, in that order, all with goal LIMIT.
   A database built before this record therefore produces the same stress results as before.
2. **Seeding.** The first write stores the built-ins as version 1, APPROVED. The person who
   triggered it is recorded as having adopted them (`STRESS_LIBRARY_SEEDED`).
   `novera stress-setup seed` or the Admin page stores the library without proposing anything.
3. **Proposal.** A new scenario, or a new version of one, is stored as a DRAFT
   (`STRESS_SCENARIO_PROPOSED`). A scenario has at most one open draft.
   - Risk Control and the heads of risk may propose any goal.
   - A desk or business head may propose INFORMATION scenarios for their area. They may not
     change a scenario that feeds limits (decision 46.2).
4. **Decision.** The Head of Market Risk or the CRO approves or rejects a draft. The proposer
   may never decide their own (four eyes, decision 46.1). On approval the previous approved
   version becomes SUPERSEDED (`STRESS_SCENARIO_APPROVED` or `_REJECTED`).
5. **Review.** An approved version falls due for review three months after its approval or
   last review, counted in business dates of the stored history. The Head of Market Risk or the
   CRO marks it reviewed (`STRESS_SCENARIO_REVIEWED`), which moves the due date three months
   on (decision 46.4).

Every event is in the audit trail under subject `stress_setup`, with the scenario, version, goal
and note.

## In the run
- **What runs.** The EOD run reads the approved, enabled definitions (`definitions_for_run`) and
  resolves them to MR-005 scenarios. Every scenario is revalued in full. The stress-loss limits
  (`STRESS_LOSS`, MR-006) read the LIMIT set only.
- **Two headline figures (decision 46.3).** `summary.worst_stress*` is the worst loss of the
  LIMIT set, the figure the limits read. `summary.worst_stress_all*` is the worst of every
  scenario, information included. The Overview shows a second line when the two differ.
  `stress_scenarios` and `stress_limit_scenarios` count them.
- **Stored per scenario.** `stress_summary` carries each scenario's goal, version and source,
  and the Stress page shows the goal.
- **Recorded definitions.** The run's config records the definitions it ran
  (`stress_scenarios`), with the dates each historical one replayed and its review due date. A
  later change of the library never alters how the run is read. A re-run of the stress stage
  (OPS-002) replays the parent's definitions. A parent made before this record re-runs on the
  built-ins, which is what it ran.
- **Data-quality findings.** A scenario whose review is past due on the run's business date
  still runs, and adds a MINOR finding `STRESS_REVIEW_OVERDUE` (AMBER verdict). A historical
  definition whose episode is not stored does not run, and adds a MAJOR finding
  `STRESS_SCENARIO_UNRESOLVED`.

## Assumptions and limitations
- **One library per firm face** (per database).
- **Promoting a scenario to LIMIT does not recalibrate the limits.** Decision 45.4 keeps new
  scenarios INFORMATION until promoted, and the recalibration draft at promotion arrives with
  round 58. Until then a scenario promoted to LIMIT can move stress-loss utilisation at once.
- **Desk heads write for their area,** but the scenario still shocks the whole book. Running a
  scenario on a desk only is not modelled.
- **The review clock uses the latest business date of the stored history,** not the wall
  clock. A frozen demo database therefore never falls overdue on its own.
- **Severity and tags are labels** for the committee. Plausibility measures arrive in round 47.

## Validation tests
`tests/test_stress_setup.py`:
- `test_built_ins_are_the_library_the_engine_always_ran`: the built-ins resolve to exactly the
  previous code path, including the fallback for databases without stored episode windows.
- `test_store_reproduces_the_run_and_governs_the_library`:
  - stress frames, limits, the headline and the verdict are identical before and after seeding;
  - desk-head scope, four eyes and the single open draft are enforced;
  - INFORMATION scenarios are kept out of FIRM_STRESS while counting in the second headline;
  - a stage re-run replays the parent's definitions;
  - the library page shows version and status.
- `test_overdue_review_is_a_minor_finding`, `test_validation_names_what_is_wrong`.
- `test_a_run_made_before_the_store_reruns_on_the_built_ins`.

Also `tests/test_api.py::test_stress_setup_endpoints`,
`tests/test_security.py::test_firm_wide_actions_and_signers` and
`tests/test_ui.py::test_admin_stress_scenarios_configuration`.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-10-16 | Versioned stress library with four-eyes approval, LIMIT and INFORMATION goals, quarterly reviews (decision 46) | Novera |
