# Limit calibration from a target utilisation  (ID: MR-017)

| Field            | Value |
|------------------|-------|
| Version          | 1.0.0 |
| Owner            | Market Risk Methodology |
| Approval status  | Draft |
| Code             | `novera.limits.calibration`, `novera.workflows.limit_calibration` |
| Last validated   | 2026-10-14 |

## Definition
Each limit's amount is set so that its measure on a reference run sits at a target utilisation:
`amount = |measure| / target`, the measure being what limit monitoring (MR-006) reports as
`current`, kept within a band of 100% to 200% of the limit's designed amount, its risk appetite
(`Limit.appetite`, the hand-set amount, kept when the limit is first calibrated). Appetite comes
top-down; usage moves each limit towards its target within that band, as a bank would use
utilisation history to check its appetite.

## When it runs
Never from the EOD or the scheduler: a limit that followed positions would never breach.
- **Seeding**, when a firm is created (`novera simulate`, on by default): a day-1 EOD runs on a
  throwaway copy of the database with alerts off, its limit table gives every measure, and the
  limits are set APPROVED. The real day-1 run reproduces the same measures, so each limit opens at
  its target.
- **Recalibration**, after a methodology change (`novera limits calibrate --run <id> --actor ...`,
  `POST /limits/recalibrate`, the Calibration tab): DRAFT versions effective the next business
  day, approved or rejected by the limit's approver (`APPROVERS_BY_LEVEL`), never the proposer. An
  approved draft ends the old version the day before; the old version is kept (`limit_version`) and
  runs on earlier dates still read it. A limit in breach on the reference run, or with an open
  breach at approval, is refused: that is an increase request (MR-008).

## Targets
VaR, ES and stressed VaR 75%; DV01, CS01, deltas, vega and gamma 70%; concentration 70%; stress loss
60%; counterparty and CCP exposure 60%; fund leverage, margin usage and prime-broker concentration
70%; anything else 70%. The limits carrying the demo's story get their designed day-1
utilisation instead (`STORY_TARGETS`: the planted breaches at 1.38 to 2.52, and the limits that
cross on later days at 0.85 to 0.98), so the story holds whatever the models compute.

## Mathematical method
Type targets are clamped to the appetite band, then rounded **up** to the next quarter of the
leading digit (1,230,417 → 1,250,000), so a limit opens at or below its target, never above. In
effect `limit = max(appetite, usage / target)`, capped at double the appetite: a quiet desk keeps
its designed limit, only a desk running hot earns headroom, and calibration never cuts a limit. Story targets are exact and outside the band. A limit with nothing in
scope, or not measured on the reference run, keeps its amount. A recalibration that would move an
amount by 1% or less proposes nothing.

## Limitations
One reference day, not a history of utilisation: positions are stored for three days, and
re-measuring today's book on a year of past markets would capture market moves rather than desk
activity (decision 40.1). Calibrated amounts depend on the models of the day; a later model change
moves utilisations until a recalibration is approved, which is the intended governance.

## Validation tests
`tests/test_limit_calibration.py`: rounding, type and story targets, nothing in scope, the breach
guard at seeding and at recalibration, drafts effective the next business day, no self-approval,
the approval matrix, the open-breach refusal, version history read by date, the appetite band
(`test_calibration_stays_within_the_appetite_band`). The demo rebuild checks
that every calibrated limit opens at its target on day 1.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-10-14 | Seeding on a day-1 dry run and governed recalibration drafts (decision 40) | Novera |
