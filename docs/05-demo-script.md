# Executive demo script (10 to 12 minutes)

Audience: head of market risk, CRO, or hedge-fund CIO. One coherent story.

1. **Morning dashboard** (1 min). Firm VaR, ES, stress loss, limit utilisation, P&L today
   and MTD, largest contributors, open breaches. Business date and run ID visible.
2. **What changed** (1 min). Day-on-day VaR change auto-attributed: market moves versus
   new trades versus model or data. Click USD Rates.
3. **Drill down** (1 min). Firm to asset class to desk to book to trader to trade. Show the
   USD 10Y DV01 concentration and the curve ladder.
4. **Can I trust it** (1 min). Data-quality verdict for today's run: one stale EUR/USD vol
   surface and a missing USD 7Y node (MAJOR, owners named), three invalid trades. Scroll to
   the proxies panel: the run re-levelled the stale surface with GBP/USD and interpolated
   the 7Y node before pricing, every touched factor listed with its source (MD-002); the raw
   snapshot is untouched.
4b. **Breach workflow** (1 min). Day 1 shows exactly the four planted breaches (USD 10Y
   DV01 and concentration, Brent concentration, digital-asset stress). Day 2 adds two: the
   single-name equity vega limit after new options were booked, and Northsea Energy's PFE
   limit. Day 3: the unacknowledged breaches are auto-escalated to Head of Market Risk; the
   Brent breach was acknowledged and escalates only after three breaching runs. Request a
   temporary increase, show the approval matrix reject the desk head and accept Head of
   Market Risk, close the breach against it.
4c. **Compare runs** (1 min). Day 1 versus day 2: VaR up 0.5m, one Bank A swap unwound, 45
   new trades; day 3 a EUR DV01 breach from new business.
4d. **Breadth** (1 min). Drill-down by product: repos, rates futures, swaptions,
   single-name CDS, commodity options, ETFs and mutual funds, barrier and digital options
   all priced by benchmarked closed forms. Concentration & liquidity page, fund look-through:
   how much NVDA or gold exposure arrives via funds rather than directly.
5. **Stress** (2 min). Run "Global risk-off". Losses by desk and asset class, top
   contributors, new breaches raised, escalation routed.
6. **Counterparty** (2 min). Counterparty page: Bank A largest PFE, the uncollateralised
   sovereign and the corporate flagged wrong-way. Pick Bank A, show gross versus
   collateralised profile and CVA, then raise the CSA threshold in the what-if and watch
   PFE95 move on the same paths. Current exposure under the stress library alongside.
7. **Independent challenger** (1 min). Challenger page: the official system's VaR versus
   Novera's, the gap attributed to scope (a book missing from the feed), market data (FX
   valued on the previous day), pricing model (options without a smile) and methodology
   (window), with the rerun that verifies the window effect.
7b. **Operations** (30 s). Alerts & jobs page: the scheduled job that advanced the world
   and ran EOD, the alerts it raised, and where real market data has replaced synthetic.
8. **Risk Copilot** (2 min). Five example buttons on the Copilot page; open the tool-call
   expander under an answer to show the run id and the exact numbers it read. "Why did VaR increase today?" then "Which books are closest
   to their limits?" then "What if equities fall 20 percent and vol rises 15 points?"
   Each answer cites run IDs and tool calls.
7c. **Capital** (1 min). Capital page: FRTB standardised versus internal models by desk,
   SA-CCR by counterparty, initial margin, BA-CVA, the funding ladder.
7d. **Hedge-fund face** (2 min, for fund audiences). Switch firm in the sidebar to Meridian
   Multi-Strategy: NAV, leverage, VaR as % of NAV, the crowded NVDA position, broker
   concentration in warning, redemption coverage by scenario, strategy attribution and
   factor betas.
8a. **Agents and the Lab** (2 min). Breaches page: "Investigate with the agent" on the USD
   10Y breach: contributors, what changed, the engine-sized unwind, note attached to the
   breach. Agents page: suggest scenarios (sized from the history, run through the
   engine), draft the validation report, ingest the demo CSA term sheet and approve it.
   Portfolio Lab page: plant two problems at twice the size in a sandbox, run, see which
   were detected and which limits stayed inside.
8b. **Risk pack** (30 s). Build the pack from the Risk pack page and open the PDF: the same
   numbers, stamped with the run id, ready for the risk committee.
9. **Close** (30 s). "This is a prototype of the layer above today's risk stack, not a
   replacement for it."
