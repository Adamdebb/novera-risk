# MCP server  (ID: AI-004)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Risk Technology |
| Approval status | Draft |
| Code | `novera.mcp_server`, `novera ai.tools` (tool registry) |
| Last validated | 2026-09-15 |

## Definition
`novera mcp` runs a Model Context Protocol server over stdio: the client (Claude Desktop,
Claude Code, a firm's own agent) starts it as a child process and exchanges JSON-RPC
messages over the process pipes, so there is no network port and one server per client.
The server registers the Copilot's tool registry (`ai/tools.py`) unchanged: eighteen
read-only tools over the stored run plus `what_if`, which prices a hypothetical shock in
memory through the engine (MR-005). The `agent` tool, which writes notes to breaches, is
registered only with `--allow-agents`. Tool schemas advertised to the client are the
Copilot's own JSON schemas (argument descriptions and enums), so a model sees the same
contract either way.

## Governance
- The server never calls a model. Which model reads the tool outputs, and where it runs
  (public API, the firm's cloud account via Bedrock/Vertex/Foundry, or none), is the
  client's configuration, and therefore the firm's confidentiality decision.
- Every tool call is an `MCP_TOOL_CALL` audit event with tool name, arguments, duration and
  error flag. If the database is locked by a running EOD, the event is appended to
  `data/mcp_audit.jsonl` instead, so the trail is never dropped.
- Tool outputs carry internal ids and aggregates in millions; nothing is computed or
  rounded by the client's model (rule 1 of `CLAUDE.md`).

## Client configuration
```
claude mcp add novera -- uv run --directory /path/to/novera novera mcp
```
Claude Desktop: add the same command to `claude_desktop_config.json` under `mcpServers`.
`--fund` serves the fund database, `--db` any sandbox (for example a Portfolio Lab).

## Limitations
stdio only by default (`--transport streamable-http` exists for a network deployment but
carries no authentication of its own; put it behind the firm's gateway). Tool outputs are
capped to keep the client's context small; long tables are truncated the same way the
Copilot truncates them.

## Validation tests
`test_server_registers_read_only_tools_by_default`, `test_stdio_round_trip_and_audit`
(schemas, a read tool, an engine what-if, an error surfaced to the client, audit events).
