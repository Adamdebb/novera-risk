# Decision log

Every design question asked during the build, the options offered, the recommended option
(marked ★), the choice made, and where it landed. Use it to revisit choices: a decision is
easy to change while the module is young and expensive once other modules depend on it.
Dates are the day the choice was made.

Legend: **Choice** is what was picked. **Where** names the code or record that implements it.
**Revisit** notes what would change if the choice were reversed.

---

## Round 1 — Project setup (2026-09-14)

### 1.1 Platform name
- Options: Novera ★ · Veyra · Valyra · working name only
- Choice: **Novera, provisional, must stay renameable**
- Where: `settings.platform_name`, `scripts/rename_platform.py`, ADR 0005
- Revisit: trademark and domain clearance not done. Rename is a five-minute job as long as
  the brand stays in config and no one hard-codes it.

### 1.2 Market-risk MVP instrument set
- Options: one product per asset class ★ (10 products) · rates-heavy set · minimal five
- Choice: **One per asset class**
- Where: `domain/instruments.py`, `pricing/` (PR-001 to PR-009), roadmap Phase 2
- Revisit: Phase 6 adds repos, swaptions, IR futures, single-name CDS, ETFs, exotics.

### 1.3 Market data
- Options: synthetic first, real adapters later ★ · real free data from day one · fully synthetic only
- Choice: **Synthetic first**
- Where: `simulation/market_data.py` (SIM-001); adapters added in Phase 3 (MD-001)
- Revisit: `novera fetch` can now overlay real history; the demo stays synthetic by default.

### 1.4 Version control
- Options: local git, GitHub later ★ · create private GitHub repo now · no git
- Choice: **Private GitHub repo now**
- Where: https://github.com/Adamdebb/novera-risk, commits as Adamdebb with the GitHub
  no-reply address (the account blocks pushes exposing a private email)

---

## Round 2 — Simulated world (2026-09-14)

### 2.1 Git author name
- Options: Oussama · GitHub username ★
- Choice: **GitHub username (Adamdebb)**

### 2.2 GitHub repo name
- Options: novera-risk ★ · novera · skip
- Choice: **novera-risk**

### 2.3 Simulated organisation
- Options: Global Macro Bank, ~1,500 trades ★ · smaller bank, ~300 · multi-strategy hedge fund first
- Choice: **Global Macro Bank, ~1,500 trades**
- Where: `simulation/organisation.py`; 3 legal entities, 14 desks, 29 books, 30 counterparties

### 2.4 Synthetic history length
- Options: 3 years daily ★ · 5 years · 1 year
- Choice: **3 years daily** (783 business days)
- Revisit: 5 years would allow a longer backtest window than the current 250 + 250 days.

---

## Round 3 — Market data detail (2026-09-14)

### 3.1 Volatility surfaces
- Options: ATM term structure plus smile ★ · ATM term structure only · flat vol
- Choice: **ATM plus smile** (5 expiries × 5 moneyness points per underlying)
- Where: `market_data/vol_surface.py`, SIM-001

### 3.2 Crisis episodes in the history
- Options: two stylised episodes ★ · calm only · replay real crises later
- Choice: **Two stylised episodes** (risk-off crash, rates shock)
- Where: `DEFAULT_EPISODES` in `simulation/market_data.py`; named real crises arrive with `novera fetch`

### 3.3 What to build after market data
- Options: pricing engine for all ten products ★ · EOD workflow skeleton first · dashboard shell first
- Choice: **Pricing engine first**

---

## Round 4 — Risk engine (2026-09-14)

### 4.1 VaR convention
- Options: 99% 1-day historical simulation, 2-year window, plus 97.5% ES ★ · 99% 1-year window ·
  95% exponentially weighted
- Choice: **99% 1-day HS over 500 days, with 97.5% ES**; 10-day by square root of time
- Where: `risk/var.py`, MR-002, MR-003
- Revisit: the window is a `VaRConfig` field; the challenger and reconciliation already
  demonstrate window sensitivity.

### 4.2 VaR revaluation method
- Options: full revaluation plus sensitivity-based challenger ★ · full revaluation only ·
  sensitivity-based only
- Choice: **Full revaluation official, delta-gamma-vega as challenger**
- Where: MR-002, MR-004; the Compare view on the VaR page

### 4.3 Seed a limit hierarchy
- Options: seed the full hierarchy calibrated so planted problems breach ★ · firm and desk VaR
  and stress only · no limits yet
- Choice: **Full hierarchy** (79 bank limits)
- Where: `simulation/limits.py`, MR-006
- Revisit: amounts were hand-calibrated twice (after the option restrike); recalibrate after
  any generator change.

---

## Round 5 — Workflow (2026-09-14)

### 5.1 What next after the risk core
- Options: EOD run plus dashboard ★ · dashboard first, persistence later · counterparty engine first
- Choice: **EOD run plus dashboard**

### 5.2 P&L explain method
- Options: full-revaluation waterfall ★ · sensitivity-based · both, sensitivity as challenger
- Choice: **Both, sensitivity as challenger**
- Where: MR-007; unexplained residuals feed data quality (`PNL_UNEXPLAINED`)

### 5.3 Data-quality handling of planted problems
- Options: run anyway, verdict AMBER, flag affected trades ★ · block publication on RED ·
  auto-repair with proxies
- Choice: **Run anyway, AMBER, flag**
- Where: DQ-001; the run always completes, RED is advisory
- Revisit: a market-data proxy module (auto-repair) is still a candidate for Phase 6.

---

## Round 6 — Breach workflow (2026-09-14)

### 6.1 Breach lifecycle
- Options: OPEN → ACKNOWLEDGED → ESCALATED → CLOSED with actions logged ★ · simple OPEN → CLOSED ·
  full approval matrix
- Choice: **Full approval matrix** (lifecycle plus temporary limit increases with approvers and expiry)
- Where: `limits/workflow.py`, MR-008; desk limits to Head of Market Risk or CRO, firm and
  business limits and any increase above 25% to the CRO, requester cannot self-approve,
  45-day maximum

### 6.2 Where actions are taken
- Options: API write endpoints plus Streamlit buttons ★ · CLI only · Streamlit writes directly
- Choice: **API endpoints plus dashboard buttons** (and a `novera breach` CLI)

### 6.3 Actor identity
- Options: named actor field, no auth ★ · simple API key per role
- Choice: **Named actor, no authentication**
- Revisit: roles and authentication are still open; the audit trail is complete but unverified.

### 6.4 Second simulated business day
- Options: add a T+1 snapshot with new trades ★ · not yet
- Choice: **Yes**; later extended by the scheduler's day advance

---

## Round 7 — Risk Copilot (2026-09-14)

### 7.1 API key
- Options: user adds a key to .env ★ · build with the scripted provider only for now
- Choice: **Scripted provider for now**; Anthropic provider ready when `ANTHROPIC_API_KEY` is set
- Where: `ai/provider.py`, AI-001
- Revisit: the live Claude path has never been exercised; verify and tune prompts once a key exists.

### 7.2 Copilot powers
- Options: read, explain and run what-if scenarios ★ · read and explain only · also take breach
  actions with confirmation
- Choice: **Read, explain, what-if**; no writes (ADR 0003)

### 7.3 Model
- Options: Claude Opus 5 ★ · Claude Sonnet 5
- Choice: **Claude Opus 5** (`settings.llm_model = claude-opus-5`)

### 7.4 Morning commentary
- Options: automatic at the end of each EOD run ★ · on demand only
- Choice: **On demand only** (button on the Copilot page, `POST /copilot/commentary`)

---

## Round 8 — Phase 3 operations (2026-09-14)

### 8.1 Scheduler
- Options: in-process scheduler command ★ · cron-friendly one-shot only · Prefect
- Choice: **In-process** (`novera schedule`, retries, job log, day advance by bootstrap)
- Where: `workflows/scheduler.py`, `simulation/advance.py`, OPS-001

### 8.2 Alerts
- Options: alert log plus Slack webhook and email if configured ★ · database and dashboard only
- Choice: **Stored always, Slack and email when configured**
- Revisit: no channel has been exercised live; only fake channels in tests.

### 8.3 Independent challenger
- Options: simulated vendor feed with planted differences ★ · ingestion and gap only
- Choice: **Simulated feed with four planted differences** (scope, market-data timestamp,
  pricing model, methodology)
- Where: `simulation/vendor_feed.py`, `reconciliation/`, MR-009

### 8.4 Real market-data adapters
- Options: build adapters, keep synthetic as default ★ · defer
- Choice: **Build now, synthetic default** (FRED with key, Yahoo, Coinbase)
- Where: `market_data/adapters/`, MD-001; Yahoo and Coinbase checked live, FRED with recorded responses

---

## Round 9 — Phase 2 leftovers (2026-09-14)

### 9.1 Monte Carlo VaR
- Options: correlated shocks, full revaluation, 2,000 paths ★ · 10,000 paths on the delta-gamma-vega
  approximation · skip
- Choice: **10,000 paths on the delta-gamma-vega approximation**
- Where: `risk/monte_carlo.py`, MR-010
- Revisit: Gaussian tails put it below historical VaR; a full-revaluation variant would need
  the process pool and about four times the historical run time.

### 9.2 Backtesting
- Options: static-portfolio hypothetical backtest over 250 days ★ · live runs only
- Choice: **Static plus a live series that grows per run**
- Where: `risk/backtest.py`, MR-011

### 9.3 Concentration and liquidity
- Options: full set ★ · concentration only
- Choice: **Full set**
- Where: `risk/concentration.py`, `risk/liquidity.py`, MR-012, MR-013 (volume and spread assumptions are synthetic)

### 9.4 Risk pack format
- Options: HTML and PDF plus Excel ★ · Excel only · HTML only
- Choice: **HTML, PDF and Excel**
- Where: `reporting/pack.py`, RP-001; PDF through Playwright's Chromium

---

## Round 10 — Phase 4 counterparty risk (2026-09-14)

### 10.1 Exposure simulation
- Options: Monte Carlo, 1,000 paths, 12 dates, full revaluation ★ · 500 paths, 8 dates ·
  analytic per trade
- Choice: **1,000 paths, 12 dates, full revaluation** (about three minutes)
- Where: `counterparty_risk/simulation.py`, `exposure.py`, CR-001

### 10.2 Collateral model
- Options: threshold, MTA, independent amount, 10-day margin period ★ · perfect daily collateral
- Choice: **Full CSA terms with 10-day MPoR**; gross and collateralised profiles stored, CSA what-if on stored paths
- Where: CR-002

### 10.3 CVA
- Options: unilateral CVA from PD with DVA from own spread ★ · unilateral only
- Choice: **CVA and DVA**, bilateral CVA reported; wrong-way indicator separate
- Where: CR-003, CR-004; wrong-way flag threshold raised from 0.3 to 0.5 after review

### 10.4 Initial margin
- Options: simplified SIMM now ★ · defer to Phase 5
- Choice: **Deferred to Phase 5** (then delivered there as REG-004)

---

## Round 11 — Phase 5 segment modules (2026-09-14)

### 11.1 Order
- Options: bank first, then hedge fund ★ · hedge fund first · a thin slice of each
- Choice: **Bank first**

### 11.2 Bank scope
- Options: FRTB SA, SA-CCR, SIMM-lite, capital attribution ★ · add FRTB internal models ·
  capital only, no initial margin
- Choice: **Add FRTB internal models** (ES with liquidity horizons, P&L attribution test, NMRF)
- Where: `regulatory/`, REG-001 to REG-006
- Revisit: parameters are published-style approximations, not licensed calibrations.

### 11.3 Hedge-fund data
- Options: second simulated organisation with NAV and investors ★ · reuse the bank book as a fund
- Choice: **Second organisation** (Meridian Multi-Strategy Fund, own database, firm selector)
- Where: `simulation/fund.py`, `novera simulate --template hedge_fund`

### 11.4 Hedge-fund modules
- Options: margin, leverage, factor exposures, redemption stress, attribution ★ · margin, leverage,
  factors only · all of the above plus crowding
- Choice: **All plus crowding**
- Where: `fund/`, HF-001 to HF-006

---

## Round 12 — Phase 6 instrument breadth (2026-09-14)

### 12.1 Products
- Options: all eight (repo/reverse repo, IR future, swaption, single-name CDS, commodity option,
  ETF, mutual fund, equity barrier and digital) ★ · the four flow products first (repo, IR
  future, swaption, single-name CDS) · the four investor products first (ETF, mutual fund,
  commodity option, exotics)
- Choice: **All eight**
- Where: `domain/instruments.py`, `pricing/breadth.py`, `pricing/exotic_formulas.py`,
  PR-010 to PR-016; nine new bank books (USD_REPO, USD_STIR, EUR_STIR, SN_CDS_IG, SN_CDS_HY,
  ENERGY_OPTIONS, METALS_OPTIONS, FUNDS_ETF, EQ_EXOTICS); fund strategies hold the new
  products in their existing books.
- Revisit: the day-1 limit calibration moved with the larger book (see 4.3 and "second look" 5).

### 12.2 Funds
- Options: look-through to constituents ★ · price funds as a single equity-like factor per
  fund · look-through for ETFs only, single factor for mutual funds
- Choice: **Look-through to constituents** (synthetic baskets; pricing, sensitivities and VaR see
  through automatically; MR-014 reports direct versus via-fund exposure)
- Where: `pricing/breadth.py` (PR-015), `risk/lookthrough.py` (MR-014), dashboard
  "Concentration & liquidity", Copilot tool `lookthrough`

### 12.3 Exotics
- Options: closed-form barrier and digital under Black–Scholes, benchmarked to QuantLib ★ ·
  Monte Carlo under local vol · both
- Choice: **Closed form, QuantLib-benchmarked** (Reiner–Rubinstein barriers, cash-or-nothing
  digitals, all cases within 1e-8 of QuantLib; residual risk add-on in FRTB SA)
- Where: `pricing/exotic_formulas.py`, PR-016, `regulatory/frtb_sa.py`
- Revisit: barrier risk near the barrier is understated under flat vol; a Monte Carlo
  challenger would be the natural Phase 7 model-risk item.

### 12.4 Market-data proxies
- Options: proxy stale or missing factors with an audit trail ★ · keep pricing off the raw
  snapshot and only flag · proxy silently
- Choice: **Proxy with an audit trail** (interpolate missing nodes, roll missing singles,
  re-level stale families with a proxy family; INFO findings; raw snapshot kept as the run's
  market snapshot id; every downstream engine rebuilds the same proxied market from the stored
  actions)
- Where: `market_data/proxies.py`, MD-002, `NOVERA_PROXIES_ENABLED`, run frame `md_proxies`,
  dashboard "Data quality"

---

## Round 13 — Phase 7 AI and automation (2026-09-15)

### 13.1 MCP server
- Options: stdio MCP server over the same Copilot tools ★ · stdio plus HTTP transport · defer
- Choice: **Deferred at first, then built the same day** after discussing confidentiality and
  cost: stdio server over the Copilot tools, read-only by default, agents behind a flag, every
  call audited; the model runs wherever the client points it (AI-004).
- Where: `novera mcp`, `mcp_server.py`, AI-004

### 13.2 Agents
- Options (multi-select): breach investigation ★ · scenario suggestion ★ · model-validation
  report drafting · ISDA/CSA document ingestion
- Choice: **All four**. Every agent gathers its evidence deterministically from stored runs
  and the engine, then drafts text through the provider (Claude when a key exists, templated
  otherwise); numbers in the drafts come only from the evidence.
- Where: `ai/agents/`, AI-002 (agents), AI-003 (document ingestion)

### 13.3 Portfolio Lab
- Options: dashboard page plus CLI ★ · CLI only · defer
- Choice: **Dashboard page plus CLI** (`novera lab`): pick a template, choose the problems to
  plant and their size, run into a sandbox database, see what the platform detected.
- Where: `lab/`, LAB-001

---

## Standing instructions given outside the question rounds

- Do not read or use `../z-My_Tests` (private brainstorming).
- Ask questions at each step when something is unclear.
- The three brainstorming documents were merged into the vision, architecture and roadmap.
- Finish Phases 1 to 3 completely before Phase 4 (done); Phase 5 before Phase 6.

## Choices worth a second look

1. **No authentication on breach actions** (6.3): fine for a demo, first thing a bank
   security review will raise.
2. **Scripted Copilot** (7.1): the live Claude path is untested until a key is added.
3. **Alert channels** (8.2): untested against a real Slack webhook or SMTP server.
4. **Monte Carlo on the approximation** (9.1): a full-revaluation option would strengthen
   the model-risk story for option books.
5. **Limit calibration by hand** (4.3): any generator change moves the utilisations;
   consider calibrating limits from a target utilisation instead.
6. **Three-year history** (2.4): a five-year history would give a longer backtest.
7. **Synthetic parameters** (9.3, 11.2, HF-002, HF-006): volume, spread, margin schedules,
   crowding scores and capital risk weights are assumptions; each record says so.
8. **Swaption cube without a strike smile** (12.1): every strike prices off the at-the-money
   normal vol; a SABR-style smile is the obvious upgrade.
9. **Re-levelling stale data with a proxy family** (12.4): the basis between the stale family
   and its proxy is not measured; a bank would back-test the proxy choice.
10. **MCP over HTTP has no authentication of its own** (13.1): the stdio default needs none;
    the streamable-http transport must sit behind the firm's gateway.
11. **Templated agent drafts** (13.2): with the scripted provider the notes are traceable but
    flat; the Claude path for `draft` is untested until a key is added (same as item 2).
12. **Lab uses production limit calibration** (13.3): small sandbox books under-utilise
    limits, so detection scores depend on the background size chosen.
