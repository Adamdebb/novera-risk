# ADR 0004: Streamlit thin client over a typed API

Status: Accepted. Date: 2026-09-14.

## Decision
Streamlit is the first UI. It holds no business logic and calls the FastAPI service or
the service layer behind it. React replaces it only if productisation justifies it.

## Why
Speed of iteration for a demo. The rule "no logic in the UI" is what makes the later
swap a re-skin instead of a rewrite.
