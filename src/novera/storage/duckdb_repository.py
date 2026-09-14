"""DuckDB implementation of the repository protocol.

Design: reference data and trades are stored with their key columns for querying and a
JSON ``payload`` holding the full pydantic document. That keeps the schema stable while
the domain evolves, and the payload round-trips through pydantic validation on load.
SQL is ANSI except where marked ``# duckdb-only``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from pydantic import TypeAdapter

from novera.domain.counterparties import CSA, Counterparty, NettingSet
from novera.domain.limits import Limit
from novera.domain.organisation import (
    Book,
    Business,
    Desk,
    Firm,
    LegalEntity,
    Organisation,
    Trader,
)
from novera.domain.snapshots import PortfolioSnapshot
from novera.domain.trades import Trade
from novera.market_data.risk_factors import RiskFactor
from novera.market_data.snapshot import MarketSnapshot
from novera.workflows.runs import AuditEvent, RunRecord

_TRADE = TypeAdapter(Trade)

SCHEMA = """
CREATE TABLE IF NOT EXISTS organisation_entity (
    firm_id VARCHAR NOT NULL,
    entity_kind VARCHAR NOT NULL,
    entity_id VARCHAR NOT NULL,
    ordinal INTEGER NOT NULL,
    payload JSON NOT NULL,
    PRIMARY KEY (firm_id, entity_kind, entity_id)
);
CREATE TABLE IF NOT EXISTS counterparty (
    counterparty_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    counterparty_type VARCHAR NOT NULL,
    country VARCHAR NOT NULL,
    rating VARCHAR NOT NULL,
    payload JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS csa (
    csa_id VARCHAR PRIMARY KEY,
    payload JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS netting_set (
    netting_set_id VARCHAR PRIMARY KEY,
    counterparty_id VARCHAR NOT NULL,
    legal_entity_id VARCHAR NOT NULL,
    csa_id VARCHAR,
    payload JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_limit (
    limit_id VARCHAR PRIMARY KEY,
    limit_type VARCHAR NOT NULL,
    scope_level VARCHAR NOT NULL,
    scope_entity_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    payload JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_snapshot (
    snapshot_id VARCHAR PRIMARY KEY,
    business_date DATE NOT NULL,
    source VARCHAR NOT NULL,
    trade_count INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS trade (
    snapshot_id VARCHAR NOT NULL,
    trade_id VARCHAR NOT NULL,
    version INTEGER NOT NULL,
    product_type VARCHAR NOT NULL,
    asset_class VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    book_id VARCHAR NOT NULL,
    trader_id VARCHAR NOT NULL,
    counterparty_id VARCHAR NOT NULL,
    netting_set_id VARCHAR,
    status VARCHAR NOT NULL,
    trade_date DATE NOT NULL,
    payload JSON NOT NULL,
    PRIMARY KEY (snapshot_id, trade_id, version)
);
CREATE TABLE IF NOT EXISTS risk_factor (
    factor_id VARCHAR PRIMARY KEY,
    factor_type VARCHAR NOT NULL,
    asset_class VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    underlying VARCHAR NOT NULL,
    payload JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS market_history (
    as_of DATE NOT NULL,
    factor_id VARCHAR NOT NULL,
    value DOUBLE NOT NULL,
    PRIMARY KEY (as_of, factor_id)
);
CREATE TABLE IF NOT EXISTS market_snapshot (
    snapshot_id VARCHAR PRIMARY KEY,
    as_of DATE NOT NULL,
    source VARCHAR NOT NULL,
    factor_count INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS risk_run (
    run_id VARCHAR PRIMARY KEY,
    run_type VARCHAR NOT NULL,
    business_date DATE NOT NULL,
    portfolio_snapshot_id VARCHAR NOT NULL,
    market_snapshot_id VARCHAR NOT NULL,
    previous_market_snapshot_id VARCHAR,
    reporting_currency VARCHAR NOT NULL,
    model_versions JSON NOT NULL,
    config JSON NOT NULL,
    config_hash VARCHAR NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status VARCHAR NOT NULL,
    verdict VARCHAR NOT NULL,
    summary JSON NOT NULL,
    timings JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_event (
    event_id VARCHAR PRIMARY KEY,
    occurred_at TIMESTAMP NOT NULL,
    actor VARCHAR NOT NULL,
    event_type VARCHAR NOT NULL,
    subject VARCHAR NOT NULL,
    payload JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS market_value (
    snapshot_id VARCHAR NOT NULL,
    factor_id VARCHAR NOT NULL,
    value DOUBLE NOT NULL,
    observed_at DATE NOT NULL,
    PRIMARY KEY (snapshot_id, factor_id)
);
"""


class DuckDBRepository:
    def __init__(self, path: Path | str = ":memory:", read_only: bool = False) -> None:
        self._conn = duckdb.connect(str(path), read_only=read_only and str(path) != ":memory:")

    # --- lifecycle -----------------------------------------------------------------
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> DuckDBRepository:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def init_schema(self) -> None:
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                self._conn.execute(stmt)

    # --- organisation --------------------------------------------------------------
    def save_organisation(self, org: Organisation) -> None:
        rows: list[tuple[str, str, str, int, str]] = [
            (org.firm.firm_id, "firm", org.firm.firm_id, 0, org.firm.model_dump_json())
        ]
        groups: list[tuple[str, tuple[Any, ...], str]] = [
            ("legal_entity", org.legal_entities, "legal_entity_id"),
            ("business", org.businesses, "business_id"),
            ("desk", org.desks, "desk_id"),
            ("book", org.books, "book_id"),
            ("trader", org.traders, "trader_id"),
        ]
        for kind, items, key in groups:
            rows.extend(
                (org.firm.firm_id, kind, getattr(i, key), n, i.model_dump_json()) for n, i in enumerate(items)
            )
        self._conn.execute("DELETE FROM organisation_entity WHERE firm_id = ?", [org.firm.firm_id])
        self._conn.executemany("INSERT INTO organisation_entity VALUES (?, ?, ?, ?, ?)", rows)

    def load_organisation(self, firm_id: str) -> Organisation:
        rows = self._conn.execute(
            "SELECT entity_kind, payload FROM organisation_entity WHERE firm_id = ? "
            "ORDER BY entity_kind, ordinal",
            [firm_id],
        ).fetchall()
        if not rows:
            raise KeyError(f"no organisation for firm_id={firm_id!r}")
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for kind, payload in rows:
            by_kind.setdefault(kind, []).append(json.loads(payload))
        return Organisation(
            firm=Firm(**by_kind["firm"][0]),
            legal_entities=tuple(LegalEntity(**d) for d in by_kind.get("legal_entity", [])),
            businesses=tuple(Business(**d) for d in by_kind.get("business", [])),
            desks=tuple(Desk(**d) for d in by_kind.get("desk", [])),
            books=tuple(Book(**d) for d in by_kind.get("book", [])),
            traders=tuple(Trader(**d) for d in by_kind.get("trader", [])),
        )

    # --- counterparties, netting, CSA ----------------------------------------------
    def save_counterparties(self, items: list[Counterparty]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO counterparty VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    c.counterparty_id,
                    c.name,
                    c.counterparty_type.value,
                    c.country,
                    c.rating,
                    c.model_dump_json(),
                )
                for c in items
            ],
        )

    def load_counterparties(self) -> list[Counterparty]:
        rows = self._conn.execute("SELECT payload FROM counterparty ORDER BY counterparty_id").fetchall()
        return [Counterparty(**json.loads(p)) for (p,) in rows]

    def save_netting_sets(self, items: list[NettingSet], csas: list[CSA]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO csa VALUES (?, ?)",
            [(c.csa_id, c.model_dump_json()) for c in csas],
        )
        self._conn.executemany(
            "INSERT OR REPLACE INTO netting_set VALUES (?, ?, ?, ?, ?)",
            [
                (n.netting_set_id, n.counterparty_id, n.legal_entity_id, n.csa_id, n.model_dump_json())
                for n in items
            ],
        )

    def load_netting_sets(self) -> tuple[list[NettingSet], list[CSA]]:
        ns = self._conn.execute("SELECT payload FROM netting_set ORDER BY netting_set_id").fetchall()
        cs = self._conn.execute("SELECT payload FROM csa ORDER BY csa_id").fetchall()
        return (
            [NettingSet(**json.loads(p)) for (p,) in ns],
            [CSA(**json.loads(p)) for (p,) in cs],
        )

    # --- limits --------------------------------------------------------------------
    def save_limits(self, items: list[Limit]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO risk_limit VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    lim.limit_id,
                    lim.limit_type.value,
                    lim.scope.level.value,
                    lim.scope.entity_id,
                    lim.status.value,
                    lim.effective_from,
                    lim.effective_to,
                    lim.model_dump_json(),
                )
                for lim in items
            ],
        )

    def load_limits(self, on: date | None = None) -> list[Limit]:
        if on is None:
            rows = self._conn.execute("SELECT payload FROM risk_limit ORDER BY limit_id").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT payload FROM risk_limit WHERE status = 'APPROVED' AND effective_from <= ? "
                "AND (effective_to IS NULL OR effective_to >= ?) ORDER BY limit_id",
                [on, on],
            ).fetchall()
        return [Limit(**json.loads(p)) for (p,) in rows]

    # --- portfolio snapshots -------------------------------------------------------
    def save_portfolio_snapshot(self, snapshot: PortfolioSnapshot) -> str:
        sid = snapshot.snapshot_id
        exists = self._conn.execute(
            "SELECT 1 FROM portfolio_snapshot WHERE snapshot_id = ?", [sid]
        ).fetchone()
        if exists:
            return sid  # immutable: same content, same id, nothing to do
        self._conn.execute(
            "INSERT INTO portfolio_snapshot (snapshot_id, business_date, source, trade_count) "
            "VALUES (?, ?, ?, ?)",
            [sid, snapshot.business_date, snapshot.source, len(snapshot)],
        )
        self._conn.executemany(
            "INSERT INTO trade VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    sid,
                    t.trade_id,
                    t.version,
                    t.product_type.value,
                    t.asset_class.value,
                    t.currency,
                    t.book_id,
                    t.trader_id,
                    t.counterparty_id,
                    t.netting_set_id,
                    t.status.value,
                    t.trade_date,
                    t.model_dump_json(),
                )
                for t in snapshot.trades
            ],
        )
        return sid

    def load_portfolio_snapshot(self, snapshot_id: str) -> PortfolioSnapshot:
        head = self._conn.execute(
            "SELECT business_date, source FROM portfolio_snapshot WHERE snapshot_id = ?",
            [snapshot_id],
        ).fetchone()
        if head is None:
            raise KeyError(f"snapshot {snapshot_id!r} not found")
        rows = self._conn.execute(
            "SELECT payload FROM trade WHERE snapshot_id = ? ORDER BY trade_id, version",
            [snapshot_id],
        ).fetchall()
        trades = tuple(_TRADE.validate_json(p) for (p,) in rows)
        snap = PortfolioSnapshot(business_date=head[0], trades=trades, source=head[1])
        if snap.snapshot_id != snapshot_id:
            raise RuntimeError(f"snapshot {snapshot_id} failed integrity check on load")
        return snap

    def list_portfolio_snapshots(self) -> list[tuple[str, date, int]]:
        rows = self._conn.execute(
            "SELECT snapshot_id, business_date, trade_count FROM portfolio_snapshot "
            "ORDER BY business_date, created_at"
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    # --- market data -----------------------------------------------------------------
    def save_risk_factors(self, items: list[RiskFactor]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO risk_factor VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    f.factor_id,
                    f.factor_type.value,
                    f.asset_class,
                    f.currency,
                    f.underlying,
                    f.model_dump_json(),
                )
                for f in items
            ],
        )

    def load_risk_factors(self) -> list[RiskFactor]:
        rows = self._conn.execute("SELECT payload FROM risk_factor ORDER BY factor_id").fetchall()
        return [RiskFactor(**json.loads(p)) for (p,) in rows]

    def save_market_history(self, history: pd.DataFrame) -> int:
        """Bulk insert; rows already present for (as_of, factor_id) are replaced."""
        df = history[["as_of", "factor_id", "value"]].copy()
        df["as_of"] = pd.to_datetime(df["as_of"]).dt.date
        self._conn.register("_history_df", df)  # duckdb-only: DataFrame scan
        self._conn.execute(
            "INSERT OR REPLACE INTO market_history SELECT as_of, factor_id, value FROM _history_df"
        )
        self._conn.unregister("_history_df")
        return len(df)

    def load_market_history(
        self, factor_ids: list[str] | None = None, start: date | None = None, end: date | None = None
    ) -> pd.DataFrame:
        clauses, params = [], []
        if factor_ids:
            clauses.append(f"factor_id IN ({', '.join('?' for _ in factor_ids)})")
            params += factor_ids
        if start is not None:
            clauses.append("as_of >= ?")
            params.append(start)
        if end is not None:
            clauses.append("as_of <= ?")
            params.append(end)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._conn.execute(
            f"SELECT as_of, factor_id, value FROM market_history {where} ORDER BY as_of, factor_id", params
        ).df()  # duckdb-only: .df()

    def save_market_snapshot(self, snapshot: MarketSnapshot) -> str:
        sid = snapshot.snapshot_id
        if self._conn.execute("SELECT 1 FROM market_snapshot WHERE snapshot_id = ?", [sid]).fetchone():
            return sid
        self._conn.execute(
            "INSERT INTO market_snapshot (snapshot_id, as_of, source, factor_count) VALUES (?, ?, ?, ?)",
            [sid, snapshot.as_of, snapshot.source, len(snapshot.values)],
        )
        self._conn.executemany(
            "INSERT INTO market_value VALUES (?, ?, ?, ?)",
            [(sid, k, v, snapshot.observation_date(k)) for k, v in snapshot.values.items()],
        )
        return sid

    def load_market_snapshot(self, snapshot_id: str) -> MarketSnapshot:
        head = self._conn.execute(
            "SELECT as_of, source FROM market_snapshot WHERE snapshot_id = ?", [snapshot_id]
        ).fetchone()
        if head is None:
            raise KeyError(f"market snapshot {snapshot_id!r} not found")
        rows = self._conn.execute(
            "SELECT factor_id, value, observed_at FROM market_value WHERE snapshot_id = ?", [snapshot_id]
        ).fetchall()
        values = {r[0]: r[1] for r in rows}
        observed = {r[0]: r[2] for r in rows if r[2] != head[0]}
        snap = MarketSnapshot(as_of=head[0], values=values, observed_at=observed, source=head[1])
        if snap.snapshot_id != snapshot_id:
            raise RuntimeError(f"market snapshot {snapshot_id} failed integrity check on load")
        return snap

    def list_market_snapshots(self) -> list[tuple[str, date, int]]:
        rows = self._conn.execute(
            "SELECT snapshot_id, as_of, factor_count FROM market_snapshot ORDER BY as_of, created_at"
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    # --- runs and results ------------------------------------------------------------
    def save_run(self, run: RunRecord) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO risk_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                run.run_id,
                run.run_type,
                run.business_date,
                run.portfolio_snapshot_id,
                run.market_snapshot_id,
                run.previous_market_snapshot_id,
                run.reporting_currency,
                json.dumps(run.model_versions),
                json.dumps(run.config, default=str),
                run.config_hash,
                run.started_at,
                run.finished_at,
                run.status,
                run.verdict,
                json.dumps(run.summary, default=str),
                json.dumps(run.timings),
            ],
        )

    def load_run(self, run_id: str) -> RunRecord:
        row = self._conn.execute("SELECT * FROM risk_run WHERE run_id = ?", [run_id]).fetchone()
        if row is None:
            raise KeyError(f"run {run_id!r} not found")
        return _run_from_row(row)

    def list_runs(self, run_type: str | None = None, limit: int = 50) -> list[RunRecord]:
        q = (
            "SELECT * FROM risk_run"
            + (" WHERE run_type = ?" if run_type else "")
            + " ORDER BY started_at DESC LIMIT ?"
        )
        params = ([run_type] if run_type else []) + [limit]
        return [_run_from_row(r) for r in self._conn.execute(q, params).fetchall()]

    def latest_run(self, run_type: str = "EOD", status: str = "COMPLETED") -> RunRecord | None:
        row = self._conn.execute(
            "SELECT * FROM risk_run WHERE run_type = ? AND status = ? "
            "ORDER BY business_date DESC, started_at DESC LIMIT 1",
            [run_type, status],
        ).fetchone()
        return _run_from_row(row) if row else None

    def save_run_frame(self, run_id: str, name: str, frame: pd.DataFrame) -> int:
        """Store a result table under the run id. Table ``run_<name>`` is created from the
        frame's schema on first use (duckdb-only: DataFrame scan)."""
        table = f"run_{name}"
        df = frame.copy()
        df.insert(0, "run_id", run_id)
        self._conn.register("_frame_df", df)
        self._conn.execute(f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM _frame_df WHERE 1 = 0")
        self._conn.execute(f"DELETE FROM {table} WHERE run_id = ?", [run_id])
        self._conn.execute(f"INSERT INTO {table} SELECT * FROM _frame_df")
        self._conn.unregister("_frame_df")
        return len(df)

    def load_run_frame(self, run_id: str, name: str) -> pd.DataFrame:
        table = f"run_{name}"
        exists = self._conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone()
        if not exists:
            return pd.DataFrame()
        df = self._conn.execute(f"SELECT * FROM {table} WHERE run_id = ?", [run_id]).df()  # duckdb-only
        return df.drop(columns=["run_id"])

    def save_audit_events(self, events: list[AuditEvent]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO audit_event VALUES (?, ?, ?, ?, ?, ?)",
            [
                (e.event_id, e.at, e.actor, e.event_type, e.subject, json.dumps(e.payload, default=str))
                for e in events
            ],
        )

    def load_audit_events(self, subject: str | None = None, limit: int = 200) -> pd.DataFrame:
        q = "SELECT event_id, occurred_at AS at, actor, event_type, subject, payload FROM audit_event"
        params: list = []
        if subject:
            q += " WHERE subject = ?"
            params.append(subject)
        q += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        return self._conn.execute(q, params).df()  # duckdb-only


def _run_from_row(row: tuple) -> RunRecord:
    (
        run_id,
        run_type,
        business_date,
        psid,
        msid,
        prev_msid,
        ccy,
        models,
        config,
        _hash,
        started,
        finished,
        status,
        verdict,
        summary,
        timings,
    ) = row
    return RunRecord(
        run_id=run_id,
        run_type=run_type,
        business_date=business_date,
        portfolio_snapshot_id=psid,
        market_snapshot_id=msid,
        previous_market_snapshot_id=prev_msid,
        reporting_currency=ccy,
        model_versions=json.loads(models),
        config=json.loads(config),
        started_at=started,
        finished_at=finished,
        status=status,
        verdict=verdict,
        summary=json.loads(summary),
        timings=json.loads(timings),
    )
