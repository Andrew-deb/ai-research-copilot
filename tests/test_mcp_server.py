"""
tests/test_mcp_server.py — the MCP server registers exactly the 13 documented tools.

This is the canary for the "agent only sees a `health` tool" failure: if the
Databricks App is running the wrong entrypoint (or the Databricks sample MCP
server), the tool set won't match.

Skips cleanly when `mcp` 2.x is installed (the Phase 5 code targets the 1.x
FastMCP API — see requirements.txt pin `mcp<2`).
"""

import asyncio

import pytest

pytest.importorskip("mcp.server.fastmcp", reason="needs mcp<2 (FastMCP 1.x API)")

from mcp_server.research_mcp_server import mcp  # noqa: E402

EXPECTED_TOOLS = {
    "search_papers",
    "get_paper_details",
    "get_similar_papers",
    "compare_papers",
    "explain_topic",
    "create_collection",
    "list_collections",
    "get_collection_details",
    "add_paper_to_collection",
    "remove_paper_from_collection",
    "generate_reading_plan",
    "mark_paper_status",
    "save_note",
}


def _tool_names() -> set[str]:
    return {t.name for t in asyncio.run(mcp.list_tools())}


def test_exactly_the_thirteen_documented_tools_register():
    assert _tool_names() == EXPECTED_TOOLS


def test_no_stray_health_or_diagnostic_tool():
    # A `health` *tool* means the deployed app is the Databricks sample, not this server.
    # (The real server exposes health only as an HTTP route, never as an MCP tool.)
    names = _tool_names()
    assert names.isdisjoint({"health", "healthz", "healthcheck", "ping", "status"})


def test_search_papers_schema_shape():
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    schema = tools["search_papers"].inputSchema
    assert schema["required"] == ["query"]
    assert schema["properties"]["limit"]["default"] == 10
