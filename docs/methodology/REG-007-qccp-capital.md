# Qualifying-CCP capital  (ID: REG-007)

| Field            | Value |
|------------------|-------|
| Version          | 1.0.0 |
| Owner            | Regulatory Capital Methodology |
| Approval status  | Draft |
| Code             | `novera.counterparty_risk.ccp.qccp_capital`, `novera.regulatory.engine` |
| Last validated   | 2026-10-14 |

## Definition
Capital for exposures to qualifying central counterparties, simplified from Basel CRE54: a trade
exposure charge and a default-fund charge per CCP.

## Mathematical method
Trade exposure = SA-CCR EAD of the CCP netting sets (margined, REG-003, run with `cleared=True` on
the cleared trades) plus the initial margin posted there (not bankruptcy-remote); capital =
trade exposure × 2% × 8%. Default-fund charge
`K_CM = max(K_CCP × DF_i / (DF_CCP + DF_CM), 8% × 2% × DF_i)`, with our contribution `DF_i`, the CCP's
prefunded default fund and its hypothetical capital `K_CCP` from `CCP_DEFAULT_FUND`.

## Inputs
SA-CCR of the CCP sets; posted margin from SIMM-lite (REG-004); `CCP_DEFAULT_FUND` (assumed levels,
not taken from CCP disclosures).

## Assumptions
Both CCPs are qualifying. SA-CCR applies its ten-day margined maturity factor rather than the CCP's
five days, a conservative shortcut.

## Limitations
Default-fund inputs are assumed, not read from the CCPs' public quantitative disclosures; no
unfunded default-fund commitments; no non-qualifying CCP treatment.

## Validation tests
`tests/test_ccp.py::test_qccp_capital`: the 2% trade charge, the K_CM formula and its total.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-10-14 | Trade exposure at 2% and the default-fund charge (decision 39.4) | Novera |
