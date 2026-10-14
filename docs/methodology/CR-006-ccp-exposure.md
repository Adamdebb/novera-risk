# Exposure to central counterparties  (ID: CR-006)

| Field            | Value |
|------------------|-------|
| Version          | 1.0.0 |
| Owner            | Counterparty Risk Methodology |
| Approval status  | Draft |
| Code             | `novera.counterparty_risk.ccp.build_ccp_sets`, `novera.counterparty_risk.exposure.collateralise` |
| Last validated   | 2026-10-14 |

## Definition
Expected and potential future exposure to the OTC clearing houses we clear through (LCH for
swaps, ICE for credit), per CCP and legal entity (decision 39.3): the value of the cleared
portfolio not yet covered by daily variation margin, plus the initial margin we post at the CCP,
which a house account does not make bankruptcy-remote.

## Mathematical method
The cleared trades are grouped into one netting set per CCP and legal entity (agreement type
`CCP`), built from the trades on each run rather than stored. They are valued on the same Monte
Carlo paths as the bilateral sets (CR-001). Variation margin follows `collateral_balance` with
zero threshold and transfer amount and the CCP's five-day margin period (`CCP_MPOR_DAYS`).
`EE(t) = E[max(V − C, 0)] + IM_posted(t)`, `ENE(t) = E[max(C − V − IM_posted, 0)]`, with the posted
margin (REG-004, the CCP leg: full model margin, no threshold) aged with the portfolio like
bilateral margin. `ee_funding` excludes the posted margin, which MVA funds instead (CR-005).

## Inputs
Cleared trades at `CCP_LCH` and `CCP_ICE`; the posted margin from the regulatory run; the CCP
reference data.

## Assumptions
The CCP's own margin model (PAIRS, SPAN and the like) is stood in for by SIMM-lite on the cleared
portfolio. Variation margin is in each trade's currency, so cleared trades discount on their own
OIS curve (decision 35.2), not the USD-collateral curve the synthetic CSA would suggest.

## Limitations
Listed derivatives at exchange clearing houses (CME, Eurex, ICE Futures) are not included
(decision 39.3). Client clearing, porting and the default waterfall beyond the default fund are not
modelled. CVA on a CCP is computed like any other counterparty, small at its AA rating.

## Validation tests
`tests/test_ccp.py`: grouping by CCP and legal entity, posted margin adding to CCP exposure and kept
out of `ee_funding`. `tests/test_regulatory.py::test_regulatory_run_end_to_end` (bilateral sets).

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-10-14 | CCP netting sets in the exposure engine, posted margin at risk (decision 39) | Novera |
