"""Command-line entry point: ``novera --help``."""

from __future__ import annotations

from pathlib import Path

import typer

from novera import __version__
from novera.config import get_settings

app = typer.Typer(no_args_is_help=True, help="Market and counterparty risk intelligence platform.")
run_app = typer.Typer(no_args_is_help=True, help="Governed risk runs.")
app.add_typer(run_app, name="run")
breach_app = typer.Typer(no_args_is_help=True, help="Breach workflow: list, acknowledge, escalate, close.")
app.add_typer(breach_app, name="breach")
agent_app = typer.Typer(
    no_args_is_help=True, help="Agents: investigate, scenarios, validation, ingest (AI-002/003)."
)
app.add_typer(agent_app, name="agent")
alert_app = typer.Typer(no_args_is_help=True, help="Alert channels: test delivery, list stored alerts.")
app.add_typer(alert_app, name="alert")
lab_app = typer.Typer(
    no_args_is_help=True, help="Portfolio Lab: plant problems, run, see what was detected (LAB-001)."
)
app.add_typer(lab_app, name="lab")
signoff_app = typer.Typer(no_args_is_help=True, help="Sign-off and release of a run's metrics (OPS-003).")
app.add_typer(signoff_app, name="signoff")
var_setup_app = typer.Typer(
    no_args_is_help=True, help="The VaR measures produced daily, for limits or information (OPS-004)."
)
app.add_typer(var_setup_app, name="var-setup")


def _db(fund: bool):
    """Database for the selected firm face."""
    s = get_settings()
    return s.fund_db_path if fund else s.db_path


@app.command()
def info() -> None:
    """Show platform name, version and effective settings."""
    s = get_settings()
    typer.echo(f"{s.platform_name} v{__version__} — {s.platform_tagline}")
    typer.echo(f"reporting currency: {s.reporting_currency}")
    typer.echo(f"database: {s.db_path}")
    typer.echo(f"llm model: {s.llm_model} (key {'set' if s.anthropic_api_key else 'not set'})")


@app.command("init-db")
def init_db() -> None:
    """Create the database schema at the configured path."""
    from novera.storage.duckdb_repository import DuckDBRepository

    s = get_settings()
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    with DuckDBRepository(s.db_path) as repo:
        repo.init_schema()
    typer.echo(f"schema ready at {s.db_path}")


@app.command()
def simulate(
    business_date: str = typer.Option("2026-09-11", help="Business date YYYY-MM-DD"),
    trades: int = typer.Option(1500, help="Number of trades before injected problems"),
    seed: int = typer.Option(42, help="Random seed for reproducibility"),
    no_inject: bool = typer.Option(False, help="Do not plant the demo problems"),
    years: float = typer.Option(5.0, help="Years of daily market-data history"),
    no_market_data: bool = typer.Option(False, help="Skip market-data generation"),
    days: int = typer.Option(2, help="Business days to simulate; day 1 carries the planted problems"),
    template: str = typer.Option(
        "bank", help="bank (Global Macro Bank) or hedge_fund (Meridian Multi-Strategy)"
    ),
    db: str = typer.Option(
        None, help="Database path; default NOVERA_DB_PATH, or NOVERA_FUND_DB_PATH for hedge_fund"
    ),
) -> None:
    """Build the simulated bank, portfolio and market data, store them, and print a summary."""
    from collections import Counter
    from datetime import date as _date

    from novera.simulation import (
        BANK_TEMPLATE,
        FUND_TEMPLATE,
        TradeGeneratorConfig,
        build_counterparty_universe,
        build_fund,
        build_fund_counterparties,
        build_fund_limits,
        build_global_macro_bank,
        build_limits,
        build_multi_strategy_fund,
        evolve_portfolio,
        generate_portfolio,
    )
    from novera.simulation.market_data import business_days_after
    from novera.storage.duckdb_repository import DuckDBRepository

    s = get_settings()
    bd = _date.fromisoformat(business_date)
    fund = None
    if template == "hedge_fund":
        org = build_multi_strategy_fund()
        cp = build_fund_counterparties(org)
        fund = build_fund()
        tmpl = FUND_TEMPLATE
        db_path = Path(db) if db else s.fund_db_path
    else:
        org = build_global_macro_bank()
        cp = build_counterparty_universe(org)
        tmpl = BANK_TEMPLATE
        db_path = Path(db) if db else s.db_path

    dates = [bd] + business_days_after(bd, max(days - 1, 0))
    md = None
    history = None
    if not no_market_data:
        from novera.market_data.history import MarketHistory
        from novera.simulation.market_data import MarketSimConfig, generate_market_data

        md = generate_market_data(
            MarketSimConfig(
                end_date=dates[-1],
                years=years,
                seed=seed,
                plant_data_quality_problems=not no_inject,
                problem_date=bd,
                snapshot_days=len(dates) + 1,
            )
        )
        history = MarketHistory.from_long(md.history)
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(bd, trades, seed, not no_inject, history, tmpl))
    snapshots = [gen.snapshot]
    day_changes: dict[str, list[str]] = {}
    for d in dates[1:]:
        nxt, changes = evolve_portfolio(
            snapshots[-1], d, org, cp, gen.injections, history, seed=seed + len(snapshots), template=tmpl
        )
        snapshots.append(nxt)
        day_changes[str(d)] = changes
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with DuckDBRepository(db_path) as repo:
        repo.init_schema()
        repo.save_organisation(org)
        repo.save_counterparties(cp.counterparties)
        repo.save_netting_sets(cp.netting_sets, cp.csas)
        if fund is not None:
            repo.save_fund(fund)
            repo.save_limits(build_fund_limits(org, cp, fund))
        else:
            repo.save_limits(build_limits(org, cp, bd))
        sid = repo.save_portfolio_snapshot(gen.snapshot)
        for snap_ in snapshots[1:]:
            repo.save_portfolio_snapshot(snap_)
        if md is not None:
            repo.save_risk_factors(md.universe)
            n_rows = repo.save_market_history(md.history)
            md_ids = {d: repo.save_market_snapshot(m) for d, m in md.snapshots.items()}
    snap = gen.snapshot
    typer.echo(
        f"{org.firm.name} ({db_path}): {len(org.legal_entities)} legal entities, {len(org.desks)} desks, "
        f"{len(org.books)} books, {len(cp.counterparties)} counterparties"
        + (f", NAV {fund.nav / 1e6:,.0f}m, {len(fund.investors)} investors" if fund else "")
    )
    typer.echo(f"snapshot {sid} for {bd}: {len(snap)} trades")
    for ac, n in sorted(Counter(t.asset_class.value for t in snap.trades).items()):
        typer.echo(f"  {ac:<14} {n:>5}")
    if md is not None:
        typer.echo(
            f"market data: {len(md.universe)} risk factors, {n_rows:,} history rows over "
            f"{md.history['as_of'].nunique()} days"
        )
        typer.echo("market snapshots: " + ", ".join(f"{d} {i}" for d, i in md_ids.items()))
    for d, changes in day_changes.items():
        typer.echo(f"day {d}: " + "; ".join(changes))
    if gen.injections or (md is not None and md.planted):
        typer.echo("planted problems:")
        for inj in gen.injections:
            typer.echo(f"  - {inj.name}: {inj.description}")
        if md is not None:
            for p in md.planted:
                typer.echo(f"  - {p}")


@app.command()
def value(
    by: str = typer.Option("asset_class", help="Group by: asset_class, desk_id, book_id, product_type"),
) -> None:
    """Value the latest stored portfolio on the latest market snapshot and print PV by group."""
    from novera.pricing.valuation import value_portfolio
    from novera.storage.duckdb_repository import DuckDBRepository

    s = get_settings()
    with DuckDBRepository(s.db_path) as repo:
        psnaps = repo.list_portfolio_snapshots()
        msnaps = repo.list_market_snapshots()
        if not psnaps or not msnaps:
            raise typer.Exit("run `novera simulate` first")
        snap = repo.load_portfolio_snapshot(psnaps[-1][0])
        market = repo.load_market_snapshot(msnaps[-1][0])
        org = repo.load_organisation("GMB")
    out = value_portfolio(snap, market, org, s.reporting_currency)
    t = out.table
    typer.echo(
        f"portfolio {snap.snapshot_id} on market {market.snapshot_id} ({market.as_of}), "
        f"reporting {s.reporting_currency}"
    )
    grouped = t.groupby(by, dropna=False)["pv"].agg(["sum", "count"]).sort_values("sum")
    for name, row in grouped.iterrows():
        typer.echo(f"  {str(name):<22} {row['sum'] / 1e6:>12,.2f}m  ({int(row['count'])} trades)")
    typer.echo(f"  {'TOTAL':<22} {out.total_pv / 1e6:>12,.2f}m")
    if out.errors:
        typer.echo(f"{len(out.errors)} trades failed to price:")
        for tid, err in list(out.errors.items())[:10]:
            typer.echo(f"  - {tid}: {err}")
    notes = t[t["note"].fillna("") != ""]["note"].value_counts()
    for note, n in notes.items():
        typer.echo(f"  note '{note}': {n} trades")


@app.command()
def risk(
    workers: int = typer.Option(0, help="Processes for full-revaluation VaR (0 = all cores but one)"),
) -> None:
    """Run sensitivities, VaR, stress and limits on the latest snapshot and print the risk summary."""
    import os

    import pandas as pd

    from novera.limits import RiskInputs, monitor
    from novera.market_data.history import MarketHistory
    from novera.pricing.valuation import value_portfolio
    from novera.risk import (
        HYPOTHETICAL_LIBRARY,
        Portfolio,
        compare,
        compute_sensitivities,
        historical_var,
        run_stress,
        stress_table,
        taylor_var,
    )
    from novera.risk.stress import historical_episodes_from_simulation
    from novera.simulation.market_data import DEFAULT_EPISODES
    from novera.storage.duckdb_repository import DuckDBRepository

    if workers:
        os.environ["NOVERA_WORKERS"] = str(workers)
    s = get_settings()
    with DuckDBRepository(s.db_path) as repo:
        psnaps, msnaps = repo.list_portfolio_snapshots(), repo.list_market_snapshots()
        if not psnaps or not msnaps:
            raise typer.Exit("run `novera simulate` first")
        snap = repo.load_portfolio_snapshot(psnaps[-1][0])
        market = repo.load_market_snapshot(msnaps[-1][0])
        org = repo.load_organisation("GMB")
        universe = {f.factor_id: f for f in repo.load_risk_factors()}
        hist = MarketHistory.from_long(repo.load_market_history())
        limits = repo.load_limits(on=market.as_of)
    m = 1e6
    val = value_portfolio(snap, market, org, s.reporting_currency).table
    pf = Portfolio(snap.trades, market, s.reporting_currency, universe=universe)
    typer.echo(
        f"{s.platform_name} risk run — {market.as_of} — portfolio {snap.snapshot_id} "
        f"market {market.snapshot_id}"
    )
    typer.echo(f"  PV total {pf.total_pv / m:,.1f}m across {len(pf.priced_ids)} priced trades")
    sens = compute_sensitivities(pf)
    hs = historical_var(pf, hist)
    tv = taylor_var(pf, sens, hist)
    typer.echo(
        f"  VaR 99% 1d  {hs.var / m:,.2f}m   ES 97.5% {hs.es / m:,.2f}m   10d VaR {hs.var_scaled / m:,.1f}m"
        f"   (challenger DGV VaR {tv.var / m:,.2f}m)   worst scenario {hs.var_scenario_date}"
    )
    typer.echo("  VaR by asset class (component / standalone, m):")
    for _, r in hs.by(val, "asset_class").iterrows():
        typer.echo(
            f"    {str(r['asset_class']):<14} {r['component_var'] / m:>8.2f} / "
            f"{r['standalone_var'] / m:>8.2f}"
        )
    typer.echo("  Where the challenger disagrees most (m):")
    for ac, r in compare(hs, tv, val).head(3).iterrows():
        typer.echo(
            f"    {str(ac):<14} primary {r['primary'] / m:>8.2f}  challenger {r['challenger'] / m:>8.2f}"
        )
    keys = val.set_index("trade_id")[["desk_id"]]
    dv = (
        sens[sens.measure == "DV01"]
        .join(keys, on="trade_id")
        .groupby(["desk_id", "underlying"])["value"]
        .sum()
    )
    typer.echo("  Largest DV01 books (k per bp):")
    for (desk, ccy), _ in dv.abs().sort_values(ascending=False).head(4).items():
        typer.echo(f"    {desk:<16}{ccy}  {dv[(desk, ccy)] / 1e3:>9,.1f}")
    scen = list(HYPOTHETICAL_LIBRARY) + historical_episodes_from_simulation(
        hist, DEFAULT_EPISODES, market.as_of
    )
    st = run_stress(pf, scen, hist)
    typer.echo("  Stress (m):")
    for _, r in stress_table(st, val).head(6).iterrows():
        typer.echo(f"    {r['name']:<28} {r['total'] / m:>9.1f}")
    if limits:
        table = monitor(limits, RiskInputs(val, sens, hs, st), market.as_of)
        counts = table["status"].value_counts().to_dict()
        typer.echo(f"  Limits: {len(table)} monitored — " + ", ".join(f"{k} {v}" for k, v in counts.items()))
        for _, r in table[table.status.isin(["BREACH", "WARNING"])].iterrows():
            unit = "" if r["limit_type"] == "CONCENTRATION" else "m"
            scale = 1.0 if r["limit_type"] == "CONCENTRATION" else m
            typer.echo(
                f"    {r['status']:<8} {r['limit_id']:<34} {r['current'] / scale:>8.2f}{unit} / "
                f"{r['amount'] / scale:,.2f}{unit}  ({r['utilisation']:.0%})  owner: {r['owner']}"
            )
    else:
        typer.echo("  Limits: none stored (run `novera simulate` to seed)")
    pd.set_option("display.width", 200)


@run_app.command("counterparty")
def run_counterparty_cmd(
    run_id: str = typer.Option("latest"),
    paths: int = typer.Option(0, help="Monte Carlo paths (0 = settings)"),
    workers: int = typer.Option(0),
    fund: bool = typer.Option(False, help="Use the hedge-fund database"),
) -> None:
    """Run the counterparty exposure engine on a stored run: EE, PFE, collateral, CVA, DVA, wrong-way."""
    from novera.api.service import RiskService
    from novera.counterparty_risk import ExposureSimConfig, run_counterparty
    from novera.storage.duckdb_repository import DuckDBRepository

    s = get_settings()
    with DuckDBRepository(_db(fund)) as repo:
        rid = RiskService(repo).resolve(run_id).run_id
        cr = run_counterparty(
            repo, rid, ExposureSimConfig(paths=paths or s.exposure_paths), s, workers=workers or None
        )
    m = 1e6
    cps = cr.counterparty_summary
    typer.echo(
        f"run {rid}: {len(cps)} counterparties, {cr.notes['paths']} paths, grid {' '.join(cr.notes['grid'])}"
    )
    typer.echo(
        f"  total EPE {cps['epe'].sum() / m:,.1f}m  CVA {cps['cva'].sum() / m:,.2f}m  DVA "
        f"{cps['dva'].sum() / m:,.2f}m  wrong-way flags {int(cps['wrong_way'].fillna(False).sum())}"
    )
    for _, r in cps.head(8).iterrows():
        typer.echo(
            f"  {r['counterparty_id']:<12} {str(r['rating']):<4} CE {r['current_exposure'] / m:>7.1f}m  EPE "
            f"{r['epe'] / m:>6.1f}m  PFE95 {r['peak_pfe95'] / m:>7.1f}m ({r['peak_pfe95_step']}) gross "
            f"{r['peak_pfe95_gross'] / m:>7.1f}m  CVA {r['cva'] / m:>5.2f}m"
            + ("  WWR" if r.get("wrong_way") else "")
            + ("  no CSA" if not r["collateralised"] else "")
        )


@run_app.command("regulatory")
def run_regulatory_cmd(run_id: str = typer.Option("latest")) -> None:
    """FRTB SA and IMA, SA-CCR, SIMM-lite, BA-CVA and the cash ladder on a stored run."""
    from novera.api.service import RiskService
    from novera.regulatory import run_regulatory
    from novera.storage.duckdb_repository import DuckDBRepository

    with DuckDBRepository(get_settings().db_path) as repo:
        rid = RiskService(repo).resolve(run_id).run_id
        rr = run_regulatory(repo, rid)
    m = 1e6
    sm = rr.summary()
    typer.echo(f"run {rid}")
    typer.echo(
        f"  FRTB SA {sm['frtb_sa'] / m:,.1f}m (SBM {sm['frtb_sa_sbm'] / m:,.1f}m, "
        f"DRC {sm['frtb_sa_drc'] / m:,.1f}m)"
        f"   FRTB IMA {sm['frtb_ima'] / m:,.1f}m (IMES {sm['imes'] / m:,.1f}m x {sm['ima_multiplier']}, "
        f"SES {sm['ses'] / m:,.1f}m, NMRF {sm['nmrf']})"
    )
    typer.echo(
        f"  SA-CCR EAD {sm['saccr_ead'] / m:,.1f}m  RWA {sm['saccr_rwa'] / m:,.1f}m  capital "
        f"{sm['saccr_capital'] / m:,.1f}m   BA-CVA {sm['ba_cva_capital'] / m:,.1f}m   SIMM IM "
        f"{sm['simm_im'] / m:,.1f}m   PLA red desks {sm['pla_red_desks']}"
    )
    for _, r in rr.by_desk.head(6).iterrows():
        typer.echo(
            f"  {r['desk_id']:<16} FRTB SA {r['frtb_sa'] / m:>7.1f}m  IMA {r['frtb_ima'] / m:>6.1f}m  "
            f"SA-CCR {r['saccr'] / m:>5.1f}m  CVA {r['ba_cva'] / m:>5.1f}m"
        )


@run_app.command("eod")
def run_eod_cmd(
    business_date: str = typer.Option(None, help="YYYY-MM-DD; default latest market snapshot"),
    workers: int = typer.Option(0, help="Processes for full-revaluation VaR (0 = all cores but one)"),
    fund: bool = typer.Option(False, help="Run on the hedge-fund database (NOVERA_FUND_DB_PATH)"),
) -> None:
    """Run the end-of-day pipeline and store an auditable run."""
    from datetime import date as _date

    from novera.storage.duckdb_repository import DuckDBRepository
    from novera.workflows.eod import EODConfig, run_eod

    bd = _date.fromisoformat(business_date) if business_date else None
    with DuckDBRepository(_db(fund)) as repo:
        firm = repo.load_organisation("MSF").firm.firm_id if fund else "GMB"
        res = run_eod(repo, EODConfig(firm_id=firm, workers=workers or None), bd)
    r, m = res.run, 1e6
    sm = r.summary
    typer.echo(f"run {r.run_id}  {r.business_date}  status {r.status}  verdict {r.verdict}")
    typer.echo(
        f"  portfolio {r.portfolio_snapshot_id}  market {r.market_snapshot_id}  "
        f"previous {r.previous_market_snapshot_id}  config {r.config_hash}"
    )
    typer.echo(
        f"  PV {sm['pv'] / m:,.1f}m   VaR {sm['var'] / m:,.2f}m   ES {sm['es'] / m:,.2f}m   "
        + (f"challenger {sm['challenger_var'] / m:,.2f}m" if sm.get("challenger_var") is not None else "")
    )
    typer.echo(f"  worst stress: {sm['worst_stress_name']} {sm['worst_stress'] / m:,.1f}m")
    typer.echo(f"  limits {sm['limits_monitored']}: {sm['breaches']} breach, {sm['warnings']} warning")
    typer.echo(f"  data quality: {sm['dq_findings']} findings -> {sm['dq_verdict']}")
    if sm.get("pnl_total") is not None:
        steps = ", ".join(f"{k} {v / m:+.2f}" for k, v in sm["pnl_steps"].items() if abs(v) > 1e3)
        typer.echo(f"  P&L {sm['pnl_total'] / m:+,.2f}m  [{steps}]")
    typer.echo("  timings: " + ", ".join(f"{k} {v:.1f}s" for k, v in r.timings.items()))


@app.command("rerun")
def rerun_cmd(
    run_id: str = typer.Argument("latest", help="Parent run id, or latest"),
    stage: str = typer.Option(
        ...,
        help="valuation, sensitivities, var, backtest, stress, limits, concentration, pnl, regulatory, "
        "counterparty, fund",
    ),
    actor: str = typer.Option("risk-control", help="Who asked for the re-run (audit trail)"),
    reason: str = typer.Option("", help="Why (audit trail)"),
    fund: bool = typer.Option(False, help="Use the hedge-fund database"),
    workers: int = typer.Option(0, help="Processes for full-revaluation VaR (0 = all cores but one)"),
) -> None:
    """Re-run one EOD stage on a stored run into a new RERUN run; the parent run is untouched (OPS-002)."""
    from novera.api.service import RiskService
    from novera.storage.duckdb_repository import DuckDBRepository
    from novera.workflows.rerun import rerun_stage

    with DuckDBRepository(_db(fund)) as repo:
        rid = RiskService(repo).resolve(run_id).run_id
        res = rerun_stage(repo, rid, stage, actor, reason, workers=workers or None)
    r = res.run
    typer.echo(
        f"rerun {r.run_id}  stage {stage}  parent {res.parent.run_id}  {r.business_date}  {res.seconds:.1f}s"
    )
    typer.echo(
        f"  copied {len(res.copied_tables)} result tables; "
        f"not recomputed: {', '.join(res.stale_stages) or 'none'}"
    )
    if res.changed:
        for k, v in res.changed.items():
            typer.echo(f"  {k}: {v['before']} -> {v['after']}")
    else:
        typer.echo("  the stage reproduced the parent's numbers exactly")


@signoff_app.command("status")
def signoff_status_cmd(run_id: str = typer.Argument("latest"), fund: bool = typer.Option(False)) -> None:
    """Sign-off state of every metric of a run and its release status."""
    from novera.api.service import RiskService
    from novera.storage.duckdb_repository import DuckDBRepository

    with DuckDBRepository(_db(fund), read_only=True) as repo:
        st = RiskService(repo).signoff_status(run_id)
    typer.echo(
        f"run {st['run_id']}  {st['business_date']}  verdict {st['verdict']}  release {st['release_status']} "
        f"({st['signed_required']}/{st['required_total']} required metrics signed)"
    )
    for m in st["metrics"]:
        flag = "required" if m["required"] else "optional"
        who = f"{m['actor']} {m['at'][:19]}" if m["actor"] else ""
        typer.echo(f"  {m['metric_id']:<14} {flag:<9} {m['status']:<9} {who}  {m['comment'] or ''}")


@signoff_app.command("sign")
def signoff_sign_cmd(
    metric_id: str = typer.Argument(..., help="DATA_QUALITY, VAR, STRESS, LIMITS, PNL, ..."),
    run_id: str = typer.Option("latest"),
    actor: str = typer.Option(..., help="Who signs (audit trail)"),
    comment: str = typer.Option(""),
    fund: bool = typer.Option(False),
) -> None:
    """Sign one metric of a run; releases the run when every required metric is signed."""
    from novera.api.service import RiskWriteService
    from novera.storage.duckdb_repository import DuckDBRepository

    with DuckDBRepository(_db(fund)) as repo:
        st = RiskWriteService(repo).sign_metric(run_id, metric_id, actor, comment)
    typer.echo(f"{metric_id} signed on {st['run_id']} by {actor}; release {st['release_status']}")


@signoff_app.command("reject")
def signoff_reject_cmd(
    metric_id: str = typer.Argument(...),
    run_id: str = typer.Option("latest"),
    actor: str = typer.Option(...),
    comment: str = typer.Option(..., help="What is wrong (mandatory)"),
    fund: bool = typer.Option(False),
) -> None:
    """Reject one metric of a run, or withdraw its signature; a comment is mandatory."""
    from novera.api.service import RiskWriteService
    from novera.storage.duckdb_repository import DuckDBRepository

    with DuckDBRepository(_db(fund)) as repo:
        st = RiskWriteService(repo).reject_metric(run_id, metric_id, actor, comment)
    typer.echo(f"{metric_id} rejected on {st['run_id']} by {actor}; release {st['release_status']}")


@signoff_app.command("policy")
def signoff_policy_cmd(
    require: str = typer.Option(None, help="Comma-separated metric ids to require; omit to show the policy"),
    actor: str = typer.Option("risk-control"),
    comment: str = typer.Option(""),
    fund: bool = typer.Option(False),
) -> None:
    """Show or set which metrics must be signed before a run is released."""
    from novera.api.service import RiskService, RiskWriteService
    from novera.storage.duckdb_repository import DuckDBRepository

    if require is None:
        with DuckDBRepository(_db(fund), read_only=True) as repo:
            pol = RiskService(repo).signoff_policy()
    else:
        with DuckDBRepository(_db(fund)) as repo:
            ids = [x.strip() for x in require.split(",") if x.strip()]
            pol = RiskWriteService(repo).set_signoff_policy(actor, ids, comment)
    typer.echo(f"policy ({pol['source']}): {', '.join(pol['required']) or 'nothing required'}")
    for m in pol["metrics"]:
        typer.echo(f"  {m['metric_id']:<14} {'required' if m['required'] else 'optional':<9} {m['signer']}")


def _print_var_setup(st: dict) -> None:
    typer.echo(
        f"VaR setup ({st['source']}): headline {st['headline']}; "
        f"history {st['history_start']} to {st['history_end']}"
    )
    for m in st["measures"]:
        flag = "" if m["enabled"] else " (disabled)"
        typer.echo(f"  {m['measure_id']:<36} {m['goal']:<12} {m['label']}{flag}")


@var_setup_app.command("show")
def var_setup_show(fund: bool = typer.Option(False)) -> None:
    """Show the VaR measures the firm produces and which feed limits."""
    from novera.api.service import RiskService
    from novera.storage.duckdb_repository import DuckDBRepository

    with DuckDBRepository(_db(fund), read_only=True) as repo:
        _print_var_setup(RiskService(repo).var_setup())


@var_setup_app.command("template")
def var_setup_template(
    name: str = typer.Argument(..., help="bank or hedge_fund"),
    actor: str = typer.Option("risk-control"),
    comment: str = typer.Option(""),
    fund: bool = typer.Option(False),
) -> None:
    """Replace the VaR setup with a template; the next EOD run produces those measures."""
    from novera.api.service import RiskWriteService
    from novera.limits import WorkflowError
    from novera.storage.duckdb_repository import DuckDBRepository

    with DuckDBRepository(_db(fund)) as repo:
        try:
            st = RiskWriteService(repo).apply_var_template(name, actor, comment)
        except WorkflowError as e:
            raise typer.BadParameter(str(e)) from e
    _print_var_setup(st)


@breach_app.command("list")
def breach_list(all_: bool = typer.Option(False, "--all", help="Include closed breaches")) -> None:
    """List breaches with their status and latest utilisation."""
    from novera.storage.duckdb_repository import DuckDBRepository

    with DuckDBRepository(get_settings().db_path, read_only=True) as repo:
        for b in repo.load_breaches(open_only=not all_):
            flag = " (within limit, close pending)" if b.within_limit_on_latest_run else ""
            typer.echo(
                f"{b.breach_id}  {b.status:<12} {b.limit_id:<32} day {b.consecutive_days}  "
                f"{b.latest_utilisation:.0%}  owner {b.owner}"
                + (f"  -> {b.escalated_to}" if b.escalated_to else "")
                + flag
            )


def _breach_action(fn, *args, **kwargs) -> None:
    from novera.limits import WorkflowError
    from novera.storage.duckdb_repository import DuckDBRepository

    with DuckDBRepository(get_settings().db_path) as repo:
        try:
            obj, events = fn(repo, *args, **kwargs)
        except WorkflowError as e:
            raise typer.BadParameter(str(e)) from e
    typer.echo(
        f"{obj.status.value}: {getattr(obj, 'breach_id', getattr(obj, 'increase_id', ''))} "
        f"({len(events)} audit events)"
    )


@breach_app.command("ack")
def breach_ack(
    breach_id: str,
    actor: str = typer.Option(..., help="Who acknowledges"),
    comment: str = typer.Option("", help="Comment"),
) -> None:
    """Acknowledge an open breach."""
    from novera.limits import acknowledge

    _breach_action(acknowledge, breach_id, actor, comment)


@breach_app.command("escalate")
def breach_escalate(
    breach_id: str,
    actor: str = typer.Option(...),
    to: str = typer.Option(None),
    comment: str = typer.Option(""),
) -> None:
    """Escalate a breach to a named role."""
    from novera.limits import escalate

    _breach_action(escalate, breach_id, actor, to, comment)


@breach_app.command("close")
def breach_close(
    breach_id: str,
    actor: str = typer.Option(...),
    reason: str = typer.Option(
        ..., help="RISK_REDUCED, TEMPORARY_INCREASE_APPROVED, LIMIT_RETIRED, FALSE_POSITIVE"
    ),
    comment: str = typer.Option(""),
) -> None:
    """Close a breach with a reason. Rules in docs/methodology/MR-008-breach-workflow.md."""
    from novera.limits import close

    _breach_action(close, breach_id, actor, reason, comment)


@breach_app.command("request-increase")
def breach_request_increase(
    limit_id: str,
    amount: float = typer.Option(..., help="New limit amount"),
    expires: str = typer.Option(..., help="YYYY-MM-DD"),
    actor: str = typer.Option(...),
    rationale: str = typer.Option(...),
    breach_id: str = typer.Option(None),
) -> None:
    """Request a temporary limit increase."""
    from datetime import date as _date

    from novera.limits import request_increase

    _breach_action(
        request_increase,
        limit_id,
        amount,
        _date.fromisoformat(expires),
        actor,
        rationale,
        breach_id=breach_id,
    )


@breach_app.command("decide-increase")
def breach_decide_increase(
    increase_id: str,
    approver: str = typer.Option(...),
    reject: bool = typer.Option(False),
    comment: str = typer.Option(""),
) -> None:
    """Approve or reject a temporary increase (approval matrix applies)."""
    from novera.limits import decide_increase

    _breach_action(decide_increase, increase_id, approver, not reject, comment)


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question for the Analyst"),
    run_id: str = typer.Option(None, help="Run to answer about (default latest)"),
    show_tools: bool = typer.Option(False, help="Print the tool calls made"),
    fund: bool = typer.Option(False, help="Use the hedge-fund database"),
) -> None:
    """Ask the Analyst. Answers come only from stored run results (ADR 0003)."""
    from novera.ai import Analyst

    c = Analyst(_db(fund))
    a = c.ask(question, run_id)
    typer.echo(a.answer)
    typer.echo(
        f"\n[{a.provider}/{a.model} · {a.turns} turns · {a.seconds:.1f}s · runs {', '.join(a.run_ids_cited)}"
        f" · answer {a.answer_id}]"
    )
    if show_tools:
        for t in a.tool_calls:
            typer.echo(f"  {t.name}({t.input}) -> {'error ' if t.is_error else ''}{t.output[:200]}")


@app.command()
def schedule(
    at: str = typer.Option(None, help="HH:MM local time; default from settings (NOVERA_EOD_TIME)"),
    advance: bool = typer.Option(True, help="Advance the simulated world by a business day before each run"),
    once: bool = typer.Option(False, help="Run one job now and exit (no waiting)"),
    workers: int = typer.Option(0, help="Processes for VaR (0 = all cores but one)"),
) -> None:
    """Run the in-process scheduler: EOD at a fixed time each business day, with retries and alerts."""
    from novera.storage.duckdb_repository import DuckDBRepository
    from novera.workflows.eod import EODConfig
    from novera.workflows.scheduler import next_fire_time, run_once, serve

    s = get_settings()
    cfg = EODConfig(workers=workers or None)
    hhmm = at or s.eod_time
    if once:
        with DuckDBRepository(s.db_path) as repo:
            job = run_once(repo, advance, cfg)
        typer.echo(
            f"job {job.job_id} {job.status} business date {job.business_date} run {job.run_id} "
            f"attempts {job.attempts}"
        )
        for n in job.notes:
            typer.echo(f"  {n}")
        if job.error:
            typer.echo(f"  error: {job.error.splitlines()[0]}")
        raise typer.Exit(0 if job.status in ("COMPLETED", "SKIPPED") else 1)
    from datetime import datetime as _dt

    typer.echo(
        f"scheduler running: EOD at {hhmm} on business days, advance={advance}, next fire "
        f"{next_fire_time(_dt.now().astimezone(), hhmm):%Y-%m-%d %H:%M}. Ctrl-C to stop."
    )

    def log_sleep(seconds: float) -> None:
        import time as _t

        typer.echo(f"  sleeping {seconds / 3600:.1f}h until the next run")
        _t.sleep(seconds)

    for job in serve(str(s.db_path), hhmm, advance, cfg, sleep=log_sleep):
        typer.echo(f"  job {job.job_id} {job.status} {job.business_date} run {job.run_id}")


@alert_app.command("test")
def alert_test(
    actor: str = typer.Option("cli", help="Who is running the test; goes into the alert body"),
    note: str = typer.Option("", help="Free text appended to the alert body"),
) -> None:
    """Send one WARNING test alert through every configured channel (Slack webhook, SMTP email)
    and report delivery per channel. The alert is stored like any other, with its deliveries."""
    from novera.storage.duckdb_repository import DuckDBRepository
    from novera.workflows.alerts import channel_test_alert, channels_from_settings, dispatch

    s = get_settings()
    channels = channels_from_settings(s)
    if not channels:
        typer.echo(
            "no alert channel configured: set NOVERA_SLACK_WEBHOOK_URL, or NOVERA_SMTP_HOST with "
            "NOVERA_ALERT_EMAIL_FROM and NOVERA_ALERT_EMAIL_TO (see .env.example)"
        )
        raise typer.Exit(1)
    typer.echo("channels: " + ", ".join(ch.name for ch in channels))
    with DuckDBRepository(s.db_path) as repo:
        sent = dispatch(repo, [channel_test_alert(actor, note)], channels)
    a = sent[0]
    typer.echo(f"alert {a.alert_id} {a.status}")
    for name, outcome in a.deliveries.items():
        typer.echo(f"  {name}: {outcome}")
    raise typer.Exit(0 if a.status == "SENT" else 1)


@alert_app.command("list")
def alert_list(limit: int = typer.Option(20, help="Most recent alerts")) -> None:
    """Stored alerts, newest first, with delivery status."""
    from novera.storage.duckdb_repository import DuckDBRepository

    with DuckDBRepository(get_settings().db_path, read_only=True) as repo:
        rows = repo.load_alerts(limit=limit)
    for r in rows:
        typer.echo(f"{r['at'][:19]} {r['severity']:<8} {r['status']:<10} {r['kind']:<16} {r['title']}")


@app.command("vendor-feed")
def vendor_feed(
    run_id: str = typer.Option("latest", help="Run to derive the official feed from"),
    out: str = typer.Option("data/feeds", help="Output directory"),
    no_plant: bool = typer.Option(False, help="Do not plant the four differences"),
) -> None:
    """Write a simulated 'official risk system' feed (CSV + JSON) with planted differences."""
    from novera.api.service import RiskService
    from novera.simulation.vendor_feed import VendorFeedConfig, generate_vendor_feed
    from novera.storage.duckdb_repository import DuckDBRepository

    s = get_settings()
    with DuckDBRepository(s.db_path, read_only=True) as repo:
        rid = RiskService(repo).resolve(run_id).run_id
        cfg = VendorFeedConfig(plant=not no_plant)
        csv, meta = generate_vendor_feed(repo, rid, Path(out), cfg)
    typer.echo(f"feed written: {csv} and {meta}")
    for x in cfg.planted:
        typer.echo(f"  planted: {x}")


@app.command()
def reconcile(
    feed: str = typer.Argument(..., help="Path to the official feed CSV (the .json next to it is read too)"),
    run_id: str = typer.Option("latest"),
) -> None:
    """Reconcile an external risk feed against a run and attribute the VaR gap (MR-009)."""
    from novera.api.service import RiskService
    from novera.reconciliation import reconcile as _reconcile
    from novera.storage.duckdb_repository import DuckDBRepository

    s = get_settings()
    csv = Path(feed)
    meta = csv.with_suffix(".json")
    with DuckDBRepository(s.db_path) as repo:
        rid = RiskService(repo).resolve(run_id).run_id
        rec = _reconcile(repo, rid, csv, meta)
    m = 1e6
    typer.echo(f"reconciliation {rec.recon_id} · run {rid} · {rec.vendor} · {rec.business_date}")
    typer.echo(
        f"  Novera VaR {rec.novera_var / m:,.2f}m   official VaR {rec.official_var / m:,.2f}m   "
        f"gap {rec.gap / m:+,.2f}m ({rec.gap / rec.novera_var:+.1%})"
    )
    for k, v in rec.attribution.items():
        typer.echo(f"    {k:<14} {v / m:+8.2f}m")
    for f in rec.findings:
        typer.echo(f"  - {f}")
    typer.echo(
        "  by desk (gap, m): "
        + ", ".join(f"{r['desk_id']} {r['gap'] / m:+.2f}" for _, r in rec.by_desk.head(6).iterrows())
    )


@app.command()
def fetch(
    start: str = typer.Option("2007-01-01", help="First date YYYY-MM-DD"),
    end: str = typer.Option(None, help="Last date; default today"),
    sources: str = typer.Option("fred,yahoo,coinbase", help="Comma-separated adapters"),
) -> None:
    """Fetch real market history (FRED, Yahoo Finance, Coinbase) into the history table, replacing
    synthetic values on the fetched dates and recording provenance. Needs network access."""
    from datetime import date as _date

    from novera.market_data.adapters import (
        CoinbaseAdapter,
        FredAdapter,
        YahooAdapter,
        apply_real_history,
        fetch_all,
    )
    from novera.storage.duckdb_repository import DuckDBRepository

    s = get_settings()
    wanted = {x.strip() for x in sources.split(",")}
    adapters = []
    if "fred" in wanted:
        if not s.fred_api_key:
            typer.echo("fred skipped: set FRED_API_KEY in .env (free key)")
        else:
            adapters.append(FredAdapter(s.fred_api_key))
    if "yahoo" in wanted:
        adapters.append(YahooAdapter())
    if "coinbase" in wanted:
        adapters.append(CoinbaseAdapter())
    d0, d1 = _date.fromisoformat(start), _date.fromisoformat(end) if end else _date.today()
    results = fetch_all(adapters, d0, d1)
    with DuckDBRepository(s.db_path) as repo:
        repo.init_schema()
        summary = apply_real_history(repo, results)
    typer.echo(
        f"fetched {summary['rows']:,} rows for {summary['factors']} factors "
        f"from {', '.join(summary['sources'])}"
    )
    for r in results:
        typer.echo(
            f"  {r.source}: {sum(r.fetched.values()):,} rows, {len(r.fetched)} factors"
            + (f", {len(r.errors)} errors" if r.errors else "")
        )
        for k, v in list(r.errors.items())[:5]:
            typer.echo(f"    {k}: {v[:120]}")


@app.command()
def report(
    run_id: str = typer.Option("latest"),
    out: str = typer.Option("data/reports", help="Output directory"),
    no_pdf: bool = typer.Option(False, help="Skip the PDF (needs Playwright's Chromium)"),
    fund: bool = typer.Option(False, help="Use the hedge-fund database"),
) -> None:
    """Write the daily risk pack (HTML, PDF, Excel) for a stored run."""
    from novera.reporting import build_pack
    from novera.storage.duckdb_repository import DuckDBRepository

    with DuckDBRepository(_db(fund), read_only=True) as repo:
        files = build_pack(repo, run_id, Path(out), pdf=not no_pdf)
    typer.echo(f"html {files.html}\nxlsx {files.xlsx}\npdf  {files.pdf or 'not generated'}")


if __name__ == "__main__":
    app()


@app.command()
def mcp(
    fund: bool = typer.Option(False, help="Serve the fund database"),
    db: str = typer.Option(None, help="Database path (overrides --fund)"),
    allow_agents: bool = typer.Option(False, help="Expose the agent tool, which writes notes to breaches"),
    transport: str = typer.Option("stdio", help="stdio (default, one process per client) or streamable-http"),
) -> None:
    """MCP server exposing the Analyst's read-only tools and the what-if engine (AI-004).

    Register it with an MCP client, e.g. for Claude Code:
    claude mcp add novera -- uv run --directory <repo> novera mcp
    """
    from novera.mcp_server import build_server

    build_server(db or _db(fund), allow_agents).run(transport=transport)


# --- agents -----------------------------------------------------------------------------------
@agent_app.command("investigate")
def agent_investigate(
    breach_id: str = typer.Argument(..., help="Breach id (see `novera breach list`)"),
    fund: bool = typer.Option(False, help="Use the fund database"),
    no_attach: bool = typer.Option(False, help="Do not attach the note to the breach as a comment"),
) -> None:
    """Breach investigation agent: evidence from the stored runs, drafted note attached to the breach."""
    from novera.api.agents_api import AgentOps

    d = AgentOps(_db(fund)).investigate_breach(breach_id, attach=not no_attach)
    typer.echo(d["text"])
    typer.echo(f"\n[{d['provider']}/{d['model']} · note {d['note_id']} · {d['status']} · {d['seconds']}s]")


@agent_app.command("scenarios")
def agent_scenarios(
    run_id: str = typer.Option(None, help="Run id; default latest"),
    n: int = typer.Option(4, help="Scenarios to propose"),
    fund: bool = typer.Option(False, help="Use the fund database"),
) -> None:
    """Scenario suggestion agent: proposals sized by the history, run through the engine."""
    from novera.api.agents_api import AgentOps

    d = AgentOps(_db(fund)).suggest_scenarios(run_id, n)
    typer.echo(d["text"])
    typer.echo(f"\n[{d['provider']}/{d['model']} · note {d['note_id']} · {d['seconds']}s]")


@agent_app.command("validation")
def agent_validation(
    records: str = typer.Option(None, help="Comma-separated record ids or families, e.g. PR-012,MR"),
    run_tests: bool = typer.Option(False, help="Execute the named validation tests (slow)"),
    run_id: str = typer.Option(None, help="Run id for the live evidence; default latest"),
    fund: bool = typer.Option(False, help="Use the fund database"),
) -> None:
    """Model-validation report drafting agent; writes data/reports/model_validation_<date>.md."""
    from novera.api.agents_api import AgentOps

    recs = [r.strip() for r in records.split(",")] if records else None
    d = AgentOps(_db(fund)).draft_validation(run_id, recs, run_tests)
    typer.echo(d["text"][:3000] + ("\n..." if len(d["text"]) > 3000 else ""))
    typer.echo(f"\nreport written to {d['path']} · note {d['note_id']}")


@agent_app.command("ingest")
def agent_ingest(
    document: str = typer.Argument(
        None, help="CSA term sheet (.txt, .md or .pdf); omit to write a demo document"
    ),
    approve: str = typer.Option(None, help="Approve the proposal as this actor after review"),
    fund: bool = typer.Option(False, help="Use the fund database"),
) -> None:
    """ISDA/CSA ingestion agent: parse a term sheet into a proposed netting set and CSA, then approve."""
    from novera.api.agents_api import AgentOps
    from novera.storage.duckdb_repository import DuckDBRepository

    s = get_settings()
    if document is None:
        from novera.simulation.documents import write_demo_term_sheet

        with DuckDBRepository(_db(fund), read_only=True) as repo:
            org = repo.load_organisation(repo.list_firm_ids()[0])
            cps = {c.counterparty_id: c.name for c in repo.load_counterparties()}
        cid = "CORP_ENERGY" if "CORP_ENERGY" in cps else next(iter(cps))
        le = (
            org.legal_entities[1].legal_entity_id
            if len(org.legal_entities) > 1
            else org.legal_entities[0].legal_entity_id
        )
        document = str(write_demo_term_sheet(org, s.data_dir / "documents", cps[cid], le))
        typer.echo(f"demo term sheet written to {document}")
    ops = AgentOps(_db(fund))
    d = ops.propose_csa(document)
    typer.echo(d["text"])
    typer.echo(f"\n[note {d['note_id']} · {d['status']}]")
    if approve:
        out = ops.approve_csa(d["note_id"], approve)
        typer.echo(f"APPROVED by {approve}: netting set {out['netting_set_id']}, CSA {out['csa_id']}")


# --- portfolio lab --------------------------------------------------------------------------------
@lab_app.command("problems")
def lab_problems() -> None:
    """List the problems that can be planted."""
    from novera.api.agents_api import AgentOps

    cat = AgentOps(get_settings().db_path).problem_catalogue()
    for group, items in cat.items():
        typer.echo(group)
        for it in items:
            typer.echo(f"  {it['name']:<28} {it['title']}")


@lab_app.command("run")
def lab_run(
    name: str = typer.Argument(..., help="Lab name (sandbox database data/lab/<name>.duckdb)"),
    template: str = typer.Option("bank", help="bank or hedge_fund"),
    problems: str = typer.Option("", help="Comma-separated problem names; empty = none"),
    market_problems: str = typer.Option("", help="Comma-separated market-data problems"),
    scale: float = typer.Option(1.0, help="Size multiplier for every planted problem"),
    trades: int = typer.Option(600, help="Background trades"),
    seed: int = typer.Option(42),
    years: float = typer.Option(2.0, help="Years of history"),
    counterparty: bool = typer.Option(False, help="Run the counterparty engine too (slower)"),
    regulatory: bool = typer.Option(False, help="Run the regulatory engine too"),
) -> None:
    """Build a sandbox with the chosen problems, run EOD on it and print what was detected."""
    from novera.lab import LabSpec, run_lab

    spec = LabSpec(
        name=name,
        template=template,
        problems=tuple(p.strip() for p in problems.split(",") if p.strip()),
        market_problems=tuple(p.strip() for p in market_problems.split(",") if p.strip()),
        scale=scale,
        n_trades=trades,
        seed=seed,
        years=years,
        counterparty=counterparty,
        regulatory=regulatory,
    )
    res = run_lab(spec)
    sm = res.summary
    typer.echo(
        f"lab {name}: run {res.run_id} on {sm['business_date']} · {sm['trades']} trades · VaR "
        f"{(sm['var'] or 0) / 1e6:,.2f}m · {sm['breaches']} breaches, {sm['warnings']} warnings · verdict "
        f"{sm['verdict']} · {res.seconds:.0f}s"
    )
    typer.echo(f"detected {res.detected} of {len(res.detections)} planted problems")
    for d in res.detections:
        typer.echo(f"  [{'x' if d.detected else ' '}] {d.problem}: {d.description}")
        for e in d.evidence[:4]:
            typer.echo(f"        - {e}")
        if not d.detected:
            for c in d.context[:3]:
                typer.echo(f"        · {c}")
            if d.needs:
                typer.echo(f"        · needs {d.needs}")
    typer.echo(f"dashboard: NOVERA_DB_PATH={res.db_path} uv run streamlit run src/novera/ui/app.py")


@lab_app.command("list")
def lab_list() -> None:
    """List sandbox labs and their detection scores."""
    from novera.lab import list_labs

    for x in list_labs():
        det = x.get("detections") or []
        hits = sum(1 for d in det if d["detected"])
        typer.echo(f"{x['name']:<20} {x.get('run_id') or '-':<24} {hits}/{len(det)} detected")
