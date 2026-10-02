"""Wick MCP server. Run from the MCP source root: python -m assistant.server.

Thin tool adapters reuse shared services. The Databricks App proxy authenticates
service callers; only that trusted boundary may supply X-RC-User-Id. This server
must not be exposed directly on the public internet without equivalent auth.
"""

import os

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from middleware.identity_middleware import IdentityMiddleware
from middleware.request_context import get_bound_user_id, require_current_user_id
from middleware.trace_middleware import trace_tool
from services import collection_service, progress_service, workspace_service

mcp = FastMCP(
    name="wick-workspace",
    instructions="Wick reads authorized workspace assets and performs explicitly requested workspace changes. Research discovery belongs to the separate Research server.",
)


@mcp.custom_route("/", methods=["GET"])
async def root(_request):
    return JSONResponse({"status": "ok", "server": "wick-workspace"})


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request):
    return JSONResponse({"status": "ok"})


@mcp.tool()
@trace_tool("find_workspace_resources")
def find_workspace_resources(query: str = "", kinds: list[str] | None = None,
                             limit: int = 20, cursor: str | None = None) -> dict:
    """Find bounded authorized page/paper/note/collection/goal references. Never imports papers."""
    return workspace_service.find_workspace_resources(get_bound_user_id(), query, kinds, limit, cursor)


@mcp.tool()
@trace_tool("get_workspace_resource")
def get_workspace_resource(kind: str, item_id: str, limit: int = 20,
                           cursor: str | None = None) -> dict:
    """Read an authorized note, collection, goal or supported page by its exact reference; paginate bounded content."""
    return workspace_service.get_workspace_resource(get_bound_user_id(), kind, item_id, limit, cursor)


@mcp.tool()
@trace_tool("get_workspace_paper")
def get_workspace_paper(paper_id: str) -> dict:
    """Read bounded metadata/abstract of an existing corpus UUID. No external discovery or full PDF retrieval."""
    return workspace_service.get_workspace_paper(paper_id)


@mcp.tool()
@trace_tool("get_reading_progress")
def get_reading_progress(status: str | None = None, limit: int = 20,
                         cursor: str | None = None) -> dict:
    """Read the authenticated user's paginated reading progress, optionally filtered by status."""
    return workspace_service.get_reading_progress(require_current_user_id(), status, limit, cursor)


@mcp.tool()
@trace_tool("create_collection")
def create_collection(name: str, description: str | None = None) -> dict:
    """Create one private collection for the authenticated user; return its saved ID."""
    return collection_service.create_collection(require_current_user_id(), name, description)


@mcp.tool()
@trace_tool("add_paper_to_collection")
def add_paper_to_collection(collection_id: str, paper_id: str, sequence_order: int = 0) -> dict:
    """Add an existing paper to an owned, writable collection."""
    return collection_service.add_paper_to_collection(collection_id, paper_id, sequence_order, require_current_user_id())


@mcp.tool()
@trace_tool("remove_paper_from_collection")
def remove_paper_from_collection(collection_id: str, paper_id: str) -> dict:
    """Remove one paper from an owned collection; does not delete the corpus paper. Confirm the target first."""
    return collection_service.remove_paper_from_collection(collection_id, paper_id, require_current_user_id())


@mcp.tool()
@trace_tool("mark_paper_status")
def mark_paper_status(paper_id: str, status: str) -> dict:
    """Set personal reading status: not_started, reading, completed, or skipped."""
    return progress_service.mark_paper_status(require_current_user_id(), paper_id, status)


@mcp.tool()
@trace_tool("create_note")
def create_note(note_text: str, paper_id: str | None = None,
                title: str | None = None, tags: list[str] | None = None) -> dict:
    """Create a standalone or paper-linked personal note, optional title/tags; return actual saved fields."""
    return workspace_service.create_note(require_current_user_id(), note_text, paper_id, title, tags)


def create_app():
    """Identity binding is mandatory; startup fails if middleware cannot install."""
    app = mcp.streamable_http_app()
    app.add_middleware(IdentityMiddleware)
    return app


if __name__ == "__main__":
    import uvicorn

    mcp.settings.stateless_http = True
    mcp.settings.json_response = True
    uvicorn.run(create_app(), host=os.getenv("MCP_HOST", "0.0.0.0"),
                port=int(os.getenv("DATABRICKS_APP_PORT") or os.getenv("PORT") or "8080"))
