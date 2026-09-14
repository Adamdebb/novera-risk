# ADR 0001: Modular monolith in Python

Status: Accepted. Date: 2026-09-14.

## Decision
One Python package with strict internal module boundaries. No microservices.

## Why
A single developer with AI assistance moves fastest in one process. The risk domain
needs shared models (risk factors, hierarchy) that microservices would duplicate.
Boundaries are enforced by convention and tests, not network calls.

## Consequences
Splitting later is possible along the documented boundaries (API, storage, AI).
