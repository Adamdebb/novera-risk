"""MCP server (AI-004): the Copilot tools over stdio, audited, read-only by default."""

import json
import os
import sys

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from novera.mcp_server import WRITE_TOOLS, build_server
from novera.storage.duckdb_repository import DuckDBRepository
from tests.test_agents_lab import db_path  # noqa: F401 - fixture with two stored runs


def test_server_registers_read_only_tools_by_default(db_path):  # noqa: F811
    srv = build_server(db_path)
    names = {t.name for t in anyio.run(srv.list_tools)}
    assert "run_summary" in names and "what_if" in names
    assert not (names & WRITE_TOOLS)
    with_agents = {t.name for t in anyio.run(build_server(db_path, allow_agents=True).list_tools)}
    assert "agent" in with_agents


def test_stdio_round_trip_and_audit(db_path):  # noqa: F811
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "novera.mcp_server", "--db", db_path],
        env={**os.environ, "PYTHONPATH": "src", "NOVERA_WORKERS": "1"},
    )

    async def run():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                tools = await s.list_tools()
                summary = await s.call_tool("run_summary", {})
                bad = await s.call_tool(
                    "what_if", {"shocks": [{"target": "nonsense", "size": -10, "unit": "pct"}]}
                )
                wi = await s.call_tool(
                    "what_if", {"shocks": [{"target": "equities", "size": -10, "unit": "pct"}]}
                )
                return init, tools, summary, bad, wi

    init, tools, summary, bad, wi = anyio.run(run)
    assert "risk" in init.server_info.name.lower()
    schema = next(t for t in tools.tools if t.name == "what_if").input_schema
    assert schema["properties"]["shocks"]["items"]["properties"]["unit"]["enum"] == [
        "pct",
        "bp",
        "vol_points",
    ]
    d = json.loads(summary.content[0].text)
    assert d["run_id"].startswith("run_") and d["business_date"] == "2026-09-14"
    assert "error" in json.loads(bad.content[0].text)
    out = json.loads(wi.content[0].text)
    assert "error" not in out and isinstance(out["total_pnl_m"], float) and out["factors_shocked"] > 0
    with DuckDBRepository(db_path, read_only=True) as repo:
        audit = repo.load_audit_events(limit=50)
    calls = audit[audit["event_type"] == "MCP_TOOL_CALL"]
    assert set(calls["subject"]) >= {"run_summary", "what_if"}
    payloads = [json.loads(p) for p in calls["payload"]]
    assert any(p.get("is_error") for p in payloads) and any(not p.get("is_error") for p in payloads)
