"""Command-line entry point: ``novera --help``."""
from __future__ import annotations

import typer

from novera import __version__
from novera.config import get_settings

app = typer.Typer(no_args_is_help=True, help="Market and counterparty risk intelligence platform.")


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
) -> None:
    """Build the simulated bank, portfolio and market data, store them, and print a summary."""
    from collections import Counter
    from datetime import date as _date

    from novera.simulation import (
        TradeGeneratorConfig,
        build_counterparty_universe,
        build_global_macro_bank,
        generate_portfolio,
    )
    from novera.storage.duckdb_repository import DuckDBRepository

    s = get_settings()
    bd = _date.fromisoformat(business_date)
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = None
    history = None
    if not no_market_data:
        from novera.market_data.history import MarketHistory
        from novera.simulation.market_data import MarketSimConfig, generate_market_data

        md = generate_market_data(MarketSimConfig(end_date=bd, years=years, seed=seed,
                                                  plant_data_quality_problems=not no_inject))
        history = MarketHistory.from_long(md.history)
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(bd, trades, seed, not no_inject, history))
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    with DuckDBRepository(s.db_path) as repo:
        repo.init_schema()
        repo.save_organisation(org)
        repo.save_counterparties(cp.counterparties)
        repo.save_netting_sets(cp.netting_sets, cp.csas)
        sid = repo.save_portfolio_snapshot(gen.snapshot)
        if md is not None:
            repo.save_risk_factors(md.universe)
            n_rows = repo.save_market_history(md.history)
            prev_id = repo.save_market_snapshot(md.previous_snapshot)
            md_id = repo.save_market_snapshot(md.snapshot)
    snap = gen.snapshot
    typer.echo(f"{org.firm.name}: {len(org.legal_entities)} legal entities, {len(org.desks)} desks, "
               f"{len(org.books)} books, {len(cp.counterparties)} counterparties")
    typer.echo(f"snapshot {sid} for {bd}: {len(snap)} trades")
    for ac, n in sorted(Counter(t.asset_class.value for t in snap.trades).items()):
        typer.echo(f"  {ac:<14} {n:>5}")
    if md is not None:
        typer.echo(f"market data: {len(md.universe)} risk factors, {n_rows:,} history rows over "
                   f"{md.history['as_of'].nunique()} days")
        typer.echo(f"market snapshots: {prev_id} ({md.previous_snapshot.as_of}), "
                   f"{md_id} ({md.snapshot.as_of})")
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
    typer.echo(f"portfolio {snap.snapshot_id} on market {market.snapshot_id} ({market.as_of}), "
               f"reporting {s.reporting_currency}")
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


if __name__ == "__main__":
    app()
