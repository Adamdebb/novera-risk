"""MCP server: the Analyst's tools over the Model Context Protocol (AI-004).

`novera mcp` runs a local stdio server (one process per client, no network port) that
exposes the same read-only tools the Analyst uses, plus the what-if engine. Any MCP client
(Claude Desktop, Claude Code, a firm's own agent) can then ask questions of the stored run
while the model runs wherever the firm decided: the server never calls a model itself.

Every tool call is written to the audit trail as an ``MCP_TOOL_CALL`` event with the tool
name and arguments (and to ``data/mcp_audit.jsonl`` if the database is locked by a run).
Tools that write (the agents, which attach notes to breaches) are off unless
``--allow-agents`` is given.
"""

from __future__ import annotations

import functools
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from novera import __version__
from novera.ai.tools import Tool, build_tools
from novera.config import Settings, get_settings
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import AuditEvent

WRITE_TOOLS = {"agent"}  # everything else reads the stored run or prices a what-if in memory

INSTRUCTIONS = """Tools over {platform}'s stored risk runs for {firm}. Every number comes from the
deterministic engine's stored results or from an engine what-if; nothing is estimated here.
Start with run_summary to learn the latest run id and business date; pass run_id to other
tools or omit it for the latest run. Money is in millions of the reporting currency
(fields ending in _m). Cite run ids and trade ids in answers."""


def _audit(
    db_path: str, settings: Settings, name: str, args: dict[str, Any], seconds: float, error: bool
) -> None:
    ev = AuditEvent.now(
        "mcp", "MCP_TOOL_CALL", name, arguments=args, seconds=round(seconds, 3), is_error=error
    )
    try:
        with DuckDBRepository(db_path) as repo:
            repo.save_audit_events([ev])
    except Exception:  # noqa: BLE001 - a running EOD holds the write lock; keep the trail anyway
        log = Path(settings.data_dir) / "mcp_audit.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as f:
            f.write(
                json.dumps(
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "tool": name,
                        "arguments": args,
                        "seconds": seconds,
                        "is_error": error,
                    },
                    default=str,
                )
                + "\n"
            )


def _wrap(tool: Tool, db_path: str, settings: Settings):
    @functools.wraps(tool.fn)
    def call(*a: Any, **kw: Any) -> dict[str, Any]:
        t0 = time.perf_counter()
        error = False
        try:
            out = tool.fn(*a, **kw)
            if isinstance(out, dict) and "error" in out:
                error = True
            return json.loads(json.dumps(out, default=str))
        except Exception as e:  # noqa: BLE001 - the client's model must see the failure
            error = True
            return {"error": f"{type(e).__name__}: {e}"}
        finally:
            _audit(db_path, settings, tool.name, kw or {"args": list(a)}, time.perf_counter() - t0, error)

    call.__name__ = tool.name
    return call


def build_server(db_path: str | None = None, allow_agents: bool = False, settings: Settings | None = None):
    from mcp.server.mcpserver import MCPServer

    settings = settings or get_settings()
    db_path = str(db_path or settings.db_path)
    with DuckDBRepository(db_path, read_only=True) as repo:
        firm = repo.list_firm_ids()
        firm_name = repo.load_organisation(firm[0]).firm.name if firm else "the firm"
    server = MCPServer(
        name=f"{settings.platform_name} risk",
        instructions=INSTRUCTIONS.format(platform=settings.platform_name, firm=firm_name),
        version=__version__,
    )
    for tool in build_tools(db_path):
        if tool.name in WRITE_TOOLS and not allow_agents:
            continue
        server.add_tool(_wrap(tool, db_path, settings), name=tool.name, description=tool.description)
        # Advertise the Analyst's own JSON schema (argument descriptions, enums) rather than the
        # one derived from the Python signature; validation still goes through the signature.
        registered = server._tool_manager.get_tool(tool.name)  # noqa: SLF001
        if registered is not None:
            registered.parameters = tool.input_schema
    return server


def main(argv: list[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(description="Run the risk MCP server over stdio.")
    p.add_argument("--db", default=None, help="Database path; default NOVERA_DB_PATH")
    p.add_argument("--allow-agents", action="store_true", help="Expose the agent tool (writes notes)")
    p.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])
    ns = p.parse_args(argv)
    build_server(ns.db, ns.allow_agents).run(transport=ns.transport)


if __name__ == "__main__":
    main()
