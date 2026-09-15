# ISDA/CSA document ingestion  (ID: AI-003)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Counterparty Risk / Legal Operations |
| Approval status | Draft |
| Code | `novera.ai.agents.ingest`, `novera.simulation.documents` |
| Last validated | 2026-09-15 |

## Definition
A CSA term sheet (text or Markdown; PDF through `pypdf` when installed) is parsed into a
proposed `NettingSet` and `CSA`. Extraction is deterministic: labelled-field patterns for
Party A and B, governing agreement, base currency, thresholds per party, minimum transfer
amount, independent amount, rounding, haircut (valuation percentage), margin period of
risk and call frequency; amounts accept `USD 10 million`, `10m`, `100,000`. Each field
records its source line or that a platform default was used. Party B is matched to the
counterparty reference data by id or name, Party A to a legal entity. The proposal is
validated against the domain models and flagged when a netting set already exists for the
pair. Nothing is saved at this point: the provider drafts a review note, and an approver
accepts (`approve_csa`, audit event `CSA_INGESTED`) or rejects (`CSA_REJECTED`).

## Limitations
Patterns cover the Paragraph 11/13 election style; free-form legal prose or scanned PDFs
without a text layer are not parsed. Eligible collateral schedules are not modelled beyond
one haircut. A Claude-backed provider could extract fields from harder documents, but the
deterministic extractor remains the validator of record.

## Validation tests
`test_csa_ingestion_round_trip`: amounts, haircut and MPoR read from a generated term
sheet; proposal saved on approval with an audit event; an unknown counterparty is invalid and
cannot be approved.
