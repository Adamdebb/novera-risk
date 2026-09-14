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
    years: float = typer.Option(3.0, help="Years of daily market-data history"),
    no_market_data: bool = typer.Option(False, help="Skip market-data generation"),
    days: int = typer.Option(2, help="Business days to simulate; day 1 carries the planted problems"),
) -> None:
    """Build the simulated bank, portfolio and market data, store them, and print a summary."""
    from collections import Counter
    from datetime import date as _date

    from novera.simulation import (
        TradeGeneratorConfig,
        build_counterparty_universe,
        build_global_macro_bank,
        build_limits,
        evolve_portfolio,
        generate_portfolio,
    )
    from novera.storage.duckdb_repository import DuckDBRepository

    s = get_settings()
    bd = _date.fromisoformat(business_date)
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    from novera.simulation.market_data import business_days_after

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
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(bd, trades, seed, not no_inject, history))
    snapshots = [gen.snapshot]
    day_changes: dict[str, list[str]] = {}
    for d in dates[1:]:
        nxt, changes = evolve_portfolio(
            snapshots[-1], d, org, cp, gen.injections, history, seed=seed + len(snapshots)
        )
        snapshots.append(nxt)
        day_changes[str(d)] = changes
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    with DuckDBRepository(s.db_path) as repo:
        repo.init_schema()
        repo.save_organisation(org)
        repo.save_counterparties(cp.counterparties)
        repo.save_netting_sets(cp.netting_sets, cp.csas)
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
        f"{org.firm.name}: {len(org.legal_entities)} legal entities, {len(org.desks)} desks, "
        f"{len(org.books)} books, {len(cp.counterparties)} counterparties"
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


@run_app.command("eod")
def run_eod_cmd(
    business_date: str = typer.Option(None, help="YYYY-MM-DD; default latest market snapshot"),
    workers: int = typer.Option(0, help="Processes for full-revaluation VaR (0 = all cores but one)"),
) -> None:
    """Run the end-of-day pipeline and store an auditable run."""
    from datetime import date as _date

    from novera.storage.duckdb_repository import DuckDBRepository
    from novera.workflows.eod import EODConfig, run_eod

    s = get_settings()
    bd = _date.fromisoformat(business_date) if business_date else None
    with DuckDBRepository(s.db_path) as repo:
        res = run_eod(repo, EODConfig(workers=workers or None), bd)
    r, m = res.run, 1e6
    sm = r.summary
    typer.echo(f"run {r.run_id}  {r.business_date}  status {r.status}  verdict {r.verdict}")
    typer.echo(
        f"  portfolio {r.portfolio_snapshot_id}  market {r.market_snapshot_id}  "
        f"previous {r.previous_market_snapshot_id}  config {r.config_hash}"
    )
    typer.echo(
        f"  PV {sm['pv'] / m:,.1f}m   VaR {sm['var'] / m:,.2f}m   ES {sm['es'] / m:,.2f}m   "
        f"challenger {sm['challenger_var'] / m:,.2f}m"
    )
    typer.echo(f"  worst stress: {sm['worst_stress_name']} {sm['worst_stress'] / m:,.1f}m")
    typer.echo(f"  limits {sm['limits_monitored']}: {sm['breaches']} breach, {sm['warnings']} warning")
    typer.echo(f"  data quality: {sm['dq_findings']} findings -> {sm['dq_verdict']}")
    if sm.get("pnl_total") is not None:
        steps = ", ".join(f"{k} {v / m:+.2f}" for k, v in sm["pnl_steps"].items() if abs(v) > 1e3)
        typer.echo(f"  P&L {sm['pnl_total'] / m:+,.2f}m  [{steps}]")
    typer.echo("  timings: " + ", ".join(f"{k} {v:.1f}s" for k, v in r.timings.items()))


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
    question: str = typer.Argument(..., help="Question for the Risk Copilot"),
    run_id: str = typer.Option(None, help="Run to answer about (default latest)"),
    show_tools: bool = typer.Option(False, help="Print the tool calls made"),
) -> None:
    """Ask the Risk Copilot. Answers come only from stored run results (ADR 0003)."""
    from novera.ai import Copilot

    c = Copilot(get_settings().db_path)
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
) -> None:
    """Write the daily risk pack (HTML, PDF, Excel) for a stored run."""
    from novera.reporting import build_pack
    from novera.storage.duckdb_repository import DuckDBRepository

    with DuckDBRepository(get_settings().db_path, read_only=True) as repo:
        files = build_pack(repo, run_id, Path(out), pdf=not no_pdf)
    typer.echo(f"html {files.html}\nxlsx {files.xlsx}\npdf  {files.pdf or 'not generated'}")


if __name__ == "__main__":
    app()
