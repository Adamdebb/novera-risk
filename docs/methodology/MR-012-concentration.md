# Concentration  (ID: MR-012)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.risk.concentration` |
| Last validated | 2026-09-14 |

## Definition
For each dimension (trade, desk, book, counterparty, currency, asset class, and the
underlying of each sensitivity family): Herfindahl–Hirschman index of |exposure| shares,
effective number of groups (1/HHI), top-1, top-5 and top-10 shares, and the largest
group. Exposure is component VaR (MR-002) where it exists, |PV| for counterparties. Curve
concentration is each node's share of the currency's |DV01|. Largest VaR contributors are
listed with their share of total VaR.

## Flags
A single trade above 25% of component VaR; a sensitivity family with HHI above 0.25 and
more than two underlyings; a curve node above 50% of a currency's |DV01| when that
currency carries at least 5% of the firm's |DV01|.

## Validation tests
`tests/test_measures.py::test_concentration_measures`.
