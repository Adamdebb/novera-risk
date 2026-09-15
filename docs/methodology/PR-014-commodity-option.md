# Commodity option valuation  (ID: PR-014)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.breadth.price_commodity_option` |
| Last validated | 2026-09-14 |

## Definition
Black (1976) on the futures price at the option expiry, read from the commodity curve
`CMD:{CODE}:{TENOR}` (linear in tenor), discounted at the USD zero curve, with implied vol
from the commodity's own surface `VOL:{CODE}:{EXPIRY}:{MONEYNESS}` at moneyness `K/F`.
PV = ±contracts × contract size × unit value.

## Inputs
Commodity curve, commodity vol surface, USD zero curve; strike, expiry, contract size from
the instrument.

## Assumptions and limitations
Options on futures are treated as European on the curve price at expiry (American early
exercise on futures options is ignored). The surface is quoted on the same moneyness grid as
equities; energy surfaces skew upward (calls richer) in the simulated market.

## Validation tests
`test_commodity_option_black76_and_parity`: unit price equals `ql.blackFormula`; put-call
parity `C − P = DF (F − K)` per unit.
