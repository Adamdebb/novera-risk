# ADR 0003: The AI layer never computes an official number

Status: Accepted. Date: 2026-09-14.

## Decision
LLMs interact with the platform only through tool calls to the service layer and typed
database queries. They explain, attribute, draft and propose. They never produce, adjust
or round a risk figure, and never change a limit without a human approval step.

## Why
Model-risk governance in banks would reject any other design. It is also the
differentiator: AI connected to validated data, not AI guessing.

## Consequences
Every AI answer stores its prompt, tool calls, run IDs and model version. Tools are the
same functions exposed to the API and to the future MCP server.
