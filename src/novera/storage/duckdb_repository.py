"""DuckDB implementation of the repository protocol.

Design: reference data and trades are stored with their key columns for querying and a
JSON ``payload`` holding the full pydantic document. That keeps the schema stable while
the domain evolves, and the payload round-trips through pydantic validation on load.
SQL is ANSI except where marked ``# duckdb-only``.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from pydantic import TypeAdapter

from novera.domain.breaches import Breach, BreachAction, LimitIncrease
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
CREATE TABLE IF NOT EXISTS breach (
    breach_id VARCHAR PRIMARY KEY,
    limit_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    first_date DATE NOT NULL,
    latest_date DATE NOT NULL,
    payload JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS breach_action (
    action_id VARCHAR PRIMARY KEY,
    breach_id VARCHAR NOT NULL,
    occurred_at TIMESTAMP NOT NULL,
    payload JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS signoff_policy (
    metric_id VARCHAR PRIMARY KEY,
    required BOOLEAN NOT NULL,
    actor VARCHAR NOT NULL,
    updated_at VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS signoff (
    run_id VARCHAR NOT NULL,
    metric_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    actor VARCHAR NOT NULL,
    signed_at VARCHAR NOT NULL,
    note VARCHAR,
    frozen_value VARCHAR,
    is_override BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (run_id, metric_id)
);
CREATE TABLE IF NOT EXISTS signoff_release (
    run_id VARCHAR PRIMARY KEY,
    released_at VARCHAR NOT NULL,
    released_by VARCHAR NOT NULL,
    is_override BOOLEAN NOT NULL DEFAULT FALSE,
    metrics VARCHAR
);
CREATE TABLE IF NOT EXISTS limit_increase (
    increase_id VARCHAR PRIMARY KEY,
    limit_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    effective_from DATE NOT NULL,
    expires_on DATE NOT NULL,
    payload JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS copilot_answer (
    answer_id VARCHAR PRIMARY KEY,
    asked_at TIMESTAMP NOT NULL,
    run_id VARCHAR,
    session_id VARCHAR,
    provider VARCHAR NOT NULL,
    model VARCHAR NOT NULL,
    question VARCHAR NOT NULL,
    answer VARCHAR NOT NULL,
    payload JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS alert (
    alert_id VARCHAR PRIMARY KEY,
    raised_at TIMESTAMP NOT NULL,
    business_date DATE NOT NULL,
    severity VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    subject VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    dedupe_key VARCHAR NOT NULL,
    run_id VARCHAR,
    payload JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS scheduled_job (
    job_id VARCHAR PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    action VARCHAR NOT NULL,
    business_date DATE,
    run_id VARCHAR,
    status VARCHAR NOT NULL,
    attempts INTEGER NOT NULL,
    payload JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS market_provenance (
    factor_id VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    fetched_at TIMESTAMP NOT NULL,
    first_date DATE NOT NULL,
    last_date DATE NOT NULL,
    row_count INTEGER NOT NULL,
    PRIMARY KEY (factor_id, source)
);
CREATE TABLE IF NOT EXISTS fund (
    firm_id VARCHAR PRIMARY KEY,
    payload JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS lab (
    name VARCHAR PRIMARY KEY,
    created_at TIMESTAMP NOT NULL,
    spec JSON NOT NULL,
    injections JSON NOT NULL,
    planted_market JSON NOT NULL,
    run_id VARCHAR,
    detections JSON,
    summary JSON
);
CREATE TABLE IF NOT EXISTS agent_note (
    note_id VARCHAR PRIMARY KEY,
    created_at TIMESTAMP NOT NULL,
    kind VARCHAR NOT NULL,
    subject VARCHAR NOT NULL,
    run_id VARCHAR,
    provider VARCHAR NOT NULL,
    model VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    text VARCHAR NOT NULL,
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
    def __init__(self, path: Path | str = ":memory:", read_only: bool = False, retries: int = 20) -> None:
        ro = read_only and str(path) != ":memory:"
        last: Exception | None = None
        for _ in range(retries):
            try:
                self._conn = duckdb.connect(str(path), read_only=ro)
                break
            except duckdb.IOException as e:  # another process holds the lock; wait briefly
                last = e
                time.sleep(0.25)
        else:
            raise last  # type: ignore[misc]

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

    def list_firm_ids(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT firm_id FROM organisation_entity ORDER BY firm_id"
        ).fetchall()
        return [r[0] for r in rows]

    def firm_ids(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT firm_id FROM organisation_entity ORDER BY firm_id"
        ).fetchall()
        return [r[0] for r in rows]

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

    def load_run_market(self, run_id: str) -> MarketSnapshot:
        """The snapshot a run priced with: the raw snapshot plus any stored proxies (MD-002)."""
        from novera.market_data.proxies import apply_stored_proxies

        run = self.load_run(run_id)
        market = self.load_market_snapshot(run.market_snapshot_id)
        try:
            actions = self.load_run_frame(run_id, "md_proxies")
        except Exception:  # noqa: BLE001 - frame absent on runs made before proxies existed
            return market
        return apply_stored_proxies(market, actions)

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
            + " ORDER BY business_date DESC, started_at DESC LIMIT ?"
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
        df = frame.drop(columns=["run_id"], errors="ignore").copy()
        df.insert(0, "run_id", run_id)
        self._conn.register("_frame_df", df)
        self._conn.execute(f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM _frame_df WHERE 1 = 0")
        self._conn.execute(f"DELETE FROM {table} WHERE run_id = ?", [run_id])
        self._conn.execute(f"INSERT INTO {table} SELECT * FROM _frame_df")
        self._conn.unregister("_frame_df")
        return len(df)

    def copy_run_frames(self, src_run_id: str, dst_run_id: str) -> list[str]:
        """Copy every stored result table of one run under another run id, leaving the source
        untouched (partial re-runs, OPS-002). Returns the tables that had rows to copy."""
        tables = [
            r[0]
            for r in self._conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'run_%' ORDER BY 1"
            ).fetchall()
        ]
        copied: list[str] = []
        for t in tables:
            cols = [
                r[0]
                for r in self._conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = ? "
                    "ORDER BY ordinal_position",
                    [t],
                ).fetchall()
            ]
            if "run_id" not in cols:
                continue
            others = [c for c in cols if c != "run_id"]
            quoted = ", ".join(f'"{c}"' for c in others)
            n = self._conn.execute(
                f'INSERT INTO {t} ("run_id"{", " + quoted if others else ""}) '
                f"SELECT ?{', ' + quoted if others else ''} FROM {t} WHERE run_id = ?",
                [dst_run_id, src_run_id],
            ).fetchone()
            if n and n[0]:
                copied.append(t)
        return copied

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

    # --- breaches and increases ------------------------------------------------------
    def save_breach(self, breach: Breach) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO breach VALUES (?, ?, ?, ?, ?, ?)",
            [
                breach.breach_id,
                breach.limit_id,
                breach.status.value,
                breach.first_date,
                breach.latest_date,
                breach.model_dump_json(),
            ],
        )

    def load_breach(self, breach_id: str) -> Breach:
        row = self._conn.execute("SELECT payload FROM breach WHERE breach_id = ?", [breach_id]).fetchone()
        if row is None:
            raise KeyError(f"breach {breach_id!r} not found")
        return Breach.model_validate_json(row[0])

    def load_breaches(self, open_only: bool = False, limit_id: str | None = None) -> list[Breach]:
        clauses, params = [], []
        if open_only:
            clauses.append("status <> 'CLOSED'")
        if limit_id:
            clauses.append("limit_id = ?")
            params.append(limit_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT payload FROM breach {where} ORDER BY first_date DESC, breach_id", params
        ).fetchall()
        return [Breach.model_validate_json(r[0]) for r in rows]

    def save_breach_action(self, action: BreachAction) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO breach_action VALUES (?, ?, ?, ?)",
            [action.action_id, action.breach_id, action.at, action.model_dump_json()],
        )

    def load_breach_actions(self, breach_id: str) -> list[BreachAction]:
        rows = self._conn.execute(
            "SELECT payload FROM breach_action WHERE breach_id = ? ORDER BY occurred_at", [breach_id]
        ).fetchall()
        return [BreachAction.model_validate_json(r[0]) for r in rows]

    def save_increase(self, inc: LimitIncrease) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO limit_increase VALUES (?, ?, ?, ?, ?, ?)",
            [
                inc.increase_id,
                inc.limit_id,
                inc.status.value,
                inc.effective_from,
                inc.expires_on,
                inc.model_dump_json(),
            ],
        )

    def load_increase(self, increase_id: str) -> LimitIncrease:
        row = self._conn.execute(
            "SELECT payload FROM limit_increase WHERE increase_id = ?", [increase_id]
        ).fetchone()
        if row is None:
            raise KeyError(f"increase {increase_id!r} not found")
        return LimitIncrease.model_validate_json(row[0])

    def load_increases(self, status: str | None = None, limit_id: str | None = None) -> list[LimitIncrease]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if limit_id:
            clauses.append("limit_id = ?")
            params.append(limit_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT payload FROM limit_increase {where} ORDER BY effective_from DESC, increase_id", params
        ).fetchall()
        return [LimitIncrease.model_validate_json(r[0]) for r in rows]

    def load_portfolio_snapshot_before(self, on: date) -> PortfolioSnapshot | None:
        """Latest portfolio snapshot strictly before ``on`` (for day-on-day P&L)."""
        row = self._conn.execute(
            "SELECT snapshot_id FROM portfolio_snapshot WHERE business_date < ? "
            "ORDER BY business_date DESC, created_at DESC LIMIT 1",
            [on],
        ).fetchone()
        return self.load_portfolio_snapshot(row[0]) if row else None

    # --- copilot ---------------------------------------------------------------------
    def save_copilot_answer(self, d: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO copilot_answer VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                d["answer_id"],
                d["at"],
                d.get("run_id"),
                d.get("session_id"),
                d["provider"],
                d["model"],
                d["question"],
                d["answer"],
                json.dumps(d, default=str),
            ],
        )

    def load_copilot_answers(self, limit: int = 50, session_id: str | None = None) -> list[dict]:
        if not self._conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'copilot_answer'"
        ).fetchone():
            return []
        q = (
            "SELECT payload FROM copilot_answer"
            + (" WHERE session_id = ?" if session_id else "")
            + " ORDER BY asked_at DESC LIMIT ?"
        )
        params = ([session_id] if session_id else []) + [limit]
        return [json.loads(r[0]) for r in self._conn.execute(q, params).fetchall()]

    # --- portfolio lab ---------------------------------------------------------------
    def save_lab_spec(self, name: str, spec: dict, injections: list[dict], planted_market: list[str]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO lab VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL)",
            [
                name,
                datetime.now(UTC),
                json.dumps(spec, default=str),
                json.dumps(injections, default=str),
                json.dumps(planted_market),
            ],
        )

    def save_lab_result(self, name: str, run_id: str, detections: list[dict], summary: dict) -> None:
        self._conn.execute(
            "UPDATE lab SET run_id = ?, detections = ?, summary = ? WHERE name = ?",
            [run_id, json.dumps(detections, default=str), json.dumps(summary, default=str), name],
        )

    def load_lab(self, name: str) -> dict | None:
        if not self._conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'lab'"
        ).fetchone():
            return None
        row = self._conn.execute(
            "SELECT created_at, spec, injections, planted_market, run_id, detections, summary FROM lab "
            "WHERE name = ?",
            [name],
        ).fetchone()
        if row is None:
            return None
        return {
            "created_at": row[0].isoformat() if row[0] else None,
            "spec": json.loads(row[1]),
            "injections": json.loads(row[2]),
            "planted_market": json.loads(row[3]),
            "run_id": row[4],
            "detections": json.loads(row[5]) if row[5] else [],
            "summary": json.loads(row[6]) if row[6] else {},
        }

    # --- agent notes -----------------------------------------------------------------
    def save_agent_note(self, d: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO agent_note VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                d["note_id"],
                d["at"],
                d["kind"],
                d["subject"],
                d.get("run_id"),
                d["provider"],
                d["model"],
                d.get("status", "DRAFT"),
                d["text"],
                json.dumps(d, default=str),
            ],
        )

    def load_agent_notes(
        self, kind: str | None = None, subject: str | None = None, limit: int = 50
    ) -> list[dict]:
        if not self._conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'agent_note'"
        ).fetchone():
            return []
        where, params = [], []
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if subject:
            where.append("subject = ?")
            params.append(subject)
        q = "SELECT payload FROM agent_note" + (" WHERE " + " AND ".join(where) if where else "")
        q += " ORDER BY created_at DESC LIMIT ?"
        return [json.loads(r[0]) for r in self._conn.execute(q, [*params, limit]).fetchall()]

    def update_agent_note_status(self, note_id: str, status: str) -> None:
        row = self._conn.execute("SELECT payload FROM agent_note WHERE note_id = ?", [note_id]).fetchone()
        if row is None:
            raise KeyError(f"agent note {note_id} not found")
        payload = json.loads(row[0])
        payload["status"] = status
        self._conn.execute(
            "UPDATE agent_note SET status = ?, payload = ? WHERE note_id = ?",
            [status, json.dumps(payload, default=str), note_id],
        )

    # --- alerts ---------------------------------------------------------------------
    def save_alert(self, d: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO alert VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                d["alert_id"],
                d["at"],
                d["business_date"],
                d["severity"],
                d["kind"],
                d["subject"],
                d["status"],
                d["dedupe_key"],
                d.get("run_id"),
                json.dumps(d, default=str),
            ],
        )

    def load_alerts(
        self, limit: int = 200, status: str | None = None, severity: str | None = None
    ) -> list[dict]:
        if not self._conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'alert'"
        ).fetchone():
            return []
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT payload, dedupe_key FROM alert {where} ORDER BY raised_at DESC LIMIT ?", [*params, limit]
        ).fetchall()
        return [{**json.loads(r[0]), "dedupe_key": r[1]} for r in rows]

    # --- sign-off (OPS-003) -----------------------------------------------------------
    def _has_table(self, name: str) -> bool:
        return bool(
            self._conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [name]
            ).fetchone()
        )

    def save_signoff_policy(self, rows: list[dict]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO signoff_policy VALUES (?, ?, ?, ?)",
            [(r["metric_id"], bool(r["required"]), r["actor"], r["updated_at"]) for r in rows],
        )

    def load_signoff_policy(self) -> list[dict]:
        if not self._has_table("signoff_policy"):
            return []
        rows = self._conn.execute(
            "SELECT metric_id, required, actor, updated_at FROM signoff_policy"
        ).fetchall()
        return [{"metric_id": r[0], "required": r[1], "actor": r[2], "updated_at": r[3]} for r in rows]

    def save_signoff(self, d: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO signoff VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                d["run_id"],
                d["metric_id"],
                d["status"],
                d["actor"],
                d["at"],
                d.get("comment"),
                d.get("value"),
                bool(d.get("override", False)),
            ],
        )

    def load_signoffs(self, run_id: str) -> list[dict]:
        if not self._has_table("signoff"):
            return []
        cols = "run_id, metric_id, status, actor, signed_at, note, frozen_value, is_override"
        keys = ["run_id", "metric_id", "status", "actor", "at", "comment", "value", "override"]
        rows = self._conn.execute(f"SELECT {cols} FROM signoff WHERE run_id = ?", [run_id]).fetchall()
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def save_release(self, d: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO signoff_release VALUES (?, ?, ?, ?, ?)",
            [
                d["run_id"],
                d["released_at"],
                d["released_by"],
                bool(d.get("override", False)),
                d.get("metrics"),
            ],
        )

    def delete_release(self, run_id: str) -> None:
        if self._has_table("signoff_release"):
            self._conn.execute("DELETE FROM signoff_release WHERE run_id = ?", [run_id])

    def load_release(self, run_id: str) -> dict | None:
        if not self._has_table("signoff_release"):
            return None
        cols = "run_id, released_at, released_by, is_override, metrics"
        keys = ["run_id", "released_at", "released_by", "override", "metrics"]
        row = self._conn.execute(f"SELECT {cols} FROM signoff_release WHERE run_id = ?", [run_id]).fetchone()
        return dict(zip(keys, row, strict=True)) if row else None

    # --- scheduler jobs ---------------------------------------------------------------
    def save_job(self, d: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO scheduled_job VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                d["job_id"],
                d["started_at"],
                d["action"],
                d.get("business_date"),
                d.get("run_id"),
                d["status"],
                d["attempts"],
                json.dumps(d, default=str),
            ],
        )

    def load_jobs(self, limit: int = 100) -> list[dict]:
        if not self._conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'scheduled_job'"
        ).fetchone():
            return []
        rows = self._conn.execute(
            "SELECT payload FROM scheduled_job ORDER BY started_at DESC LIMIT ?", [limit]
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    # --- market data provenance ---------------------------------------------------------
    def save_market_provenance(self, rows: list[dict]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO market_provenance VALUES (?, ?, ?, ?, ?, ?)",
            [(r["factor_id"], r["source"], r["fetched_at"], r["first"], r["last"], r["rows"]) for r in rows],
        )

    def load_market_provenance(self) -> pd.DataFrame:
        if not self._conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'market_provenance'"
        ).fetchone():
            return pd.DataFrame(
                columns=["factor_id", "source", "fetched_at", "first_date", "last_date", "row_count"]
            )
        return self._conn.execute(
            "SELECT * FROM market_provenance ORDER BY source, factor_id"
        ).df()  # duckdb-only

    # --- fund metadata ----------------------------------------------------------------
    def save_fund(self, fund: Any) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO fund VALUES (?, ?)", [fund.firm_id, fund.model_dump_json()]
        )

    def load_fund(self, firm_id: str) -> Any | None:
        if not self._conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'fund'"
        ).fetchone():
            return None
        row = self._conn.execute("SELECT payload FROM fund WHERE firm_id = ?", [firm_id]).fetchone()
        if row is None:
            return None
        from novera.simulation.fund import Fund

        return Fund.model_validate_json(row[0])


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
