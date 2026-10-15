# Regulatory capital by desk  (ID: REG-008)

| Field            | Value |
|------------------|-------|
| Version          | 1.0.0 |
| Owner            | Regulatory Capital Methodology |
| Approval status  | Draft |
| Code             | `novera.regulatory.allocation`, `frtb_sa.frtb_sa`, `frtb_ima._ima_by_desk` |
| Last validated   | 2026-10-15 |

## Definition
Each desk's share of every regulatory charge: FRTB SA, FRTB IMA, SA-CCR, BA-CVA, CCP trade
exposure and the CCP default-fund charge. The official figure is the **Euler contribution**.
It is the charge's derivative along the desk's own positions, and the contributions add up to
the charge. Two decision measures sit beside it:
- **Incremental capital**: the charge less the charge without the desk, which is what closing
  the desk would free.
- **Standalone capital**: the desk's charge on its own.

The standardised total is FRTB SA + SA-CCR + BA-CVA + CCP capital. FRTB IMA is allocated on its
own as the alternative to FRTB SA. Decision round 41.

## Mathematical method
For a charge K(x) of the desks' positions x = (x_1, …, x_n), the Euler contribution of desk d is
`E_d = x_d · ∂K/∂x_d`. When K is homogeneous of degree one (K(λx) = λK(x)), Euler's theorem
gives `Σ_d E_d = K`. Incremental: `I_d = K(x) − K(x with x_d = 0)`. Standalone:
`S_d = K(0, …, x_d, …, 0)`. Diversification is `Σ_d S_d − K`.

| Charge | How E_d is computed |
|---|---|
| FRTB SA | Central difference: (K(x_d·(1+h)) − K(x_d·(1−h))) / 2h, h = 10⁻⁴, scaling the desk's sensitivities, crypto exposure and RRAO notional |
| FRTB IMA | Analytic. For each liquidity horizon h, the desk's contribution to ES_h is minus its mean P&L over the scenarios in the total's 2.5% tail (× √10), which is exact for ES. Through the cascade, `E_d = m · Σ_h ES_h · ((h − h_prev)/10) · c_d,h / IMES + c_d,SES` |
| SA-CCR | Central difference per netting set on EAD = 1.4 (RC + multiplier × add-on), scaling the desk's trade values and adjusted notionals. Set EADs are summed by desk with the counterparty risk weight × 8% |
| BA-CVA | Analytic: `∂K/∂SCVA_c = 2.33² (ρ² Σ SCVA + (1 − ρ²) SCVA_c) / K`, with SCVA_c = k_c × EAD_c. Each desk carries ∂K/∂SCVA_c × k_c × its Euler EAD at the counterparty |
| CCP trade exposure | SA-CCR Euler on the CCP netting sets, plus the central-difference Euler of the margin posted (SIMM-lite on the set's sign-flipped sensitivities), × 2% × 8% |
| CCP default fund | K_CM per CCP, split by the desk's Euler share of the trade exposure at that CCP |

**Rescaling.** Where K is not homogeneous, the contributions are scaled to add up to the
charge. The scale (charge / Σ raw contributions) is stored with them. SA-CCR is rescaled per
netting set, then every component is checked against its charge at firm level. If the raw
contributions add up to zero (a charge set entirely by a floor), the standalone shares are
used instead.

## Inputs
- The stored valuation and sensitivities of the run (desk of each trade from the valuation).
- The historical scenario P&L by desk for each liquidity-horizon factor subset (REG-002).
- The SA-CCR netting-set inputs: trade values, add-ons and CSA terms (REG-003).
- BA-CVA detail by counterparty (REG-005), and CCP capital per CCP (REG-007).

## Assumptions
- Trades whose book has no desk are allocated to `UNASSIGNED` rather than dropped.
- Scaled and reduced netting sets derive their collateral from the CSA terms, as the reported
  sets do (REG-003 1.1.0).
- Leaving a CCP's business frees none of the default-fund charge while the firm stays a
  clearing member. Its incremental is zero, and standalone it is the whole K_CM.

## Calibration
Bump h = 10⁻⁴ of the desk's positions (`EULER_STEP`); the charges are evaluated in double
precision on amounts of 10⁶ to 10⁹, well above the round-off of the difference.

## Limitations
- **SA-CCR is far from homogeneous on margined sets.** The replacement-cost floor
  TH + MTA − NICA does not depend on positions, so its derivative is zero. On the demo book the
  raw contributions cover about 58% of SA-CCR capital, and the floor is spread in proportion to
  the desks' add-on contributions (scale about 1.7). The PFE multiplier and the CSA thresholds
  add smaller gaps.
- The FRTB SA charge picks the worst of three correlation scenarios and floors bucket charges
  at zero. Both are kinks; at a kink the central difference averages the two sides.
- ES is piecewise linear in the desk weights. The IMA Euler contribution is exact for the
  current tail; a desk whose growth would change the tail scenarios has a contribution that
  holds only locally.
- Incremental capital removes the whole desk. It does not show the cost of a single trade,
  and it does not add up to the charge.
- A negative Euler or incremental figure marks a desk that hedges the rest of the firm. It is a
  real effect, not an error.

## Validation tests
`tests/test_capital_allocation.py`:
- FRTB SA: Euler adds up, a hedge desk carries negative capital, and incremental equals the
  re-run without the desk.
- IMA: the analytic Euler adds up exactly and matches the numerical derivative of the cascade.
- SA-CCR: homogeneous on an unmargined set; the floor is spread by rescaling.
- BA-CVA: Euler adds up, and incremental and standalone re-measure K.
- Assembly: rescaling and the standardised total.

`tests/test_regulatory.py::test_regulatory_run_end_to_end` checks that every component of
the demo run adds up to its charge, and that the allocation is stored.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-10-15 | Euler allocation of every charge, with incremental and standalone capital (decision 41); replaces the standalone-scaled and EAD-share attribution | Novera |
