# ADR 0005: Brand name is configuration, package name is renameable

Status: Accepted. Date: 2026-09-14.

## Decision
User-facing text reads `settings.platform_name`. The package `novera` can be renamed with
`scripts/rename_platform.py`, which renames the directory, imports, env-var prefix,
project metadata and docs in one pass.

## Why
The owner has not completed trademark clearance. Renaming must stay a five-minute job.
