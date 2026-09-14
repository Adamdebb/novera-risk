# FX spot and forward valuation  (ID: PR-003)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.fx.price_fx_spot`, `price_fx_forward` |
| Last validated | 2026-09-14 |

## Definition
Forward PV in quote currency: `N_base (F(T) − K) DF_quote(T)`, with covered interest parity
`F(T) = S · DF_base(T) / DF_quote(T)`. Spot trades are marked as `N_base (S − K)`.

## Assumptions
Zero curves used for both legs of CIP; no cross-currency basis. Settled forwards have zero PV.

## Validation tests
`test_fx_forward_cip_and_spot`: forward reproduces CIP; sign of forward points matches the
rate differential; settled trade returns zero.
