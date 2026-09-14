# ADR 0002: DuckDB now, repository layer always

Status: Accepted. Date: 2026-09-14.

## Decision
All persistence goes through `novera.storage` repository classes. DuckDB over Parquet is
the first backend. Postgres is the planned transactional backend once multi-user matters.

## Why
DuckDB scans millions of result rows on a laptop with zero setup. Postgres is needed
only for concurrent writers and workflow state. Confining SQL to one layer keeps the
switch routine and keeps dialect drift visible.

## Rules
ANSI SQL by default. DuckDB-only syntax (Parquet functions, list/struct types) lives in
clearly named functions. A Postgres implementation of the same protocol is added in
Phase 3 and run in CI.
