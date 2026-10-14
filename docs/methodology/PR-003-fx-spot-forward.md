# FX spot and forward valuation  (ID: PR-003)

| Field | Value |
|---|---|
| Version | 2.0.0 |
| Owner | Market Risk Methodology |
| Approval status | Draft |
| Code | `novera.pricing.fx.price_fx_spot`, `price_fx_forward`, `fx_forward_rate` |
| Last validated | 2026-10-14 |

## Definition
Forward PV in quote currency: `N_base (F(T) − K) DF_quote^C(T)`. The forward is the market's,
which the interbank market sets under USD collateral (decision 35.3):
`F(T) = S · DF_base^USD(T) / DF_quote^USD(T)`, each currency's USD-collateral curve being its OIS
curve plus its cross-currency basis against USD (`XCCY:{CCY}:{TENOR}`; USD has none), so the
forward points carry the basis of both currencies. The settlement amount discounts on the
trade's own collateral curve `C` (`MarketSnapshot.collateral_curve`): its CSA's collateral
currency, or the quote currency's OIS curve when the trade has no CSA (decision 35.4). Spot
trades are marked as `N_base (S − K)`.

## Assumptions
The basis is a zero spread per currency and tenor, simulated, not stripped from basis-swap
quotes. A snapshot without a currency's basis falls back to covered interest parity on the OIS
curves and sets `details.xccy_applied` to 0. Settled forwards have zero PV.

## Validation tests
`test_fx_forward_cip_and_spot`: forward reproduces parity on a snapshot without basis; sign of
the forward points matches the rate differential; settled trade returns zero.
`tests/test_xccy.py::test_fx_forward_carries_the_basis_and_collateral_only_discounts`: the forward
equals `S DF_EUR^USD / DF_USD` and sits above parity for a negative EUR basis; the collateral
changes only the discounting; a pair without basis is flagged.

## Change history
| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-14 | Covered interest parity on the OIS curves |
| 2.0.0 | 2026-10-14 | Basis-consistent forward under USD collateral, discounting on the trade's CSA (decision 35) |
