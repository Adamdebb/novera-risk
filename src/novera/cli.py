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
) -> None:
    """Build the simulated bank and portfolio, store them, and print a summary."""
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
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(bd, trades, seed, not no_inject))
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    with DuckDBRepository(s.db_path) as repo:
        repo.init_schema()
        repo.save_organisation(org)
        repo.save_counterparties(cp.counterparties)
        repo.save_netting_sets(cp.netting_sets, cp.csas)
        sid = repo.save_portfolio_snapshot(gen.snapshot)
    snap = gen.snapshot
    typer.echo(f"{org.firm.name}: {len(org.legal_entities)} legal entities, {len(org.desks)} desks, "
               f"{len(org.books)} books, {len(cp.counterparties)} counterparties")
    typer.echo(f"snapshot {sid} for {bd}: {len(snap)} trades")
    for ac, n in sorted(Counter(t.asset_class.value for t in snap.trades).items()):
        typer.echo(f"  {ac:<14} {n:>5}")
    if gen.injections:
        typer.echo("planted problems:")
        for inj in gen.injections:
            typer.echo(f"  - {inj.name}: {inj.description}")


if __name__ == "__main__":
    app()
