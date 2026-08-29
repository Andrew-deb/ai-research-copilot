"""
mcp_server/research_mcp_server.py — AI Research & Learning Copilot FastMCP Server.

Exposes 13 specialized tools to AI Agents (Claude, Gemini, OpenAI) for literature
discovery, curriculum sequencing, collection management, and research progress tracking.

Design Principle:
  Tools act purely as interface definitions. Every @mcp.tool function is a single-line
  delegation to its respective service module, wrapped with telemetry middleware.
"""

import logging
import os
import sys
from typing import List, Optional

# Allow both `python -m mcp_server.research_mcp_server` (repo root on path) and a
# bare `python research_mcp_server.py` (script dir on path) — a Databricks App may
# do either depending on how the source is synced.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from mcp_server.config import MCP_SERVER_NAME, MCP_SERVER_VERSION
from mcp_server.middleware.request_context import get_current_user_id
from mcp_server.middleware.trace_middleware import trace_tool
from mcp_server.services import collection_service, discovery_service, planning_service, progress_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("mcp_server")

# Initialize FastMCP Server
mcp = FastMCP(
    name=MCP_SERVER_NAME,
    instructions="AI Research & Learning Copilot MCP Server providing literature discovery, pedagogical reading plans, paper comparison, and research tracking tools."
)


# =============================================================================
# HTTP health routes (only active for the sse / streamable-http transports).
# Databricks Apps probe the app over HTTP before routing traffic; the MCP
# protocol path (/mcp or /sse) is not a plain GET, so we add explicit ones.
# =============================================================================

@mcp.custom_route("/", methods=["GET"])
async def _root(_request):
    return JSONResponse({"status": "ok", "server": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION})


@mcp.custom_route("/healthz", methods=["GET"])
async def _healthz(_request):
    return JSONResponse({"status": "ok"})


# =============================================================================
# 1. Literature Discovery Tools
# =============================================================================

@mcp.tool()
@trace_tool("search_papers")
def search_papers(query: str, limit: int = 10) -> List[dict]:
    """
    Search academic research papers across OpenAlex and Semantic Scholar.
    Returns standardized paper records with titles, abstracts, DOIs, venues, and AI TLDRs.
    """
    return discovery_service.search_papers(query=query, limit=limit)


@mcp.tool()
@trace_tool("get_paper_details")
def get_paper_details(paper_id_or_doi: str) -> dict:
    """
    Fetch comprehensive paper metadata, abstract, authors, and citation counts by UUID or DOI.
    """
    return discovery_service.get_paper_details(paper_id_or_doi=paper_id_or_doi)


@mcp.tool()
@trace_tool("get_similar_papers")
def get_similar_papers(paper_id: str, limit: int = 5) -> List[dict]:
    """
    Retrieve semantically similar papers and recommended next reads for a given paper.
    """
    return discovery_service.get_similar_papers(paper_id=paper_id, limit=limit)


@mcp.tool()
@trace_tool("compare_papers")
def compare_papers(paper_ids: List[str]) -> dict:
    """
    Compare 2 or more research papers side-by-side on methodologies, influence, and citations.
    """
    return discovery_service.compare_papers(paper_ids=paper_ids)


@mcp.tool()
@trace_tool("explain_topic")
def explain_topic(topic: str) -> dict:
    """
    Retrieve accessible prerequisite definitions and Wikipedia explanations for foundational concepts.
    """
    return discovery_service.explain_topic(topic=topic)


# =============================================================================
# 2. Collection Management Tools
# =============================================================================

@mcp.tool()
@trace_tool("create_collection")
def create_collection(name: str, description: Optional[str] = None) -> dict:
    """
    Create a new curated paper collection/syllabus for the researcher.
    """
    return collection_service.create_collection(
        user_id=get_current_user_id(),
        name=name,
        description=description
    )


@mcp.tool()
@trace_tool("list_collections")
def list_collections() -> List[dict]:
    """
    List all paper collections and reading lists belonging to the current user.
    """
    return collection_service.list_collections(user_id=get_current_user_id())


@mcp.tool()
@trace_tool("get_collection_details")
def get_collection_details(collection_id: str) -> dict:
    """
    Get full metadata and the ordered list of papers inside a specific collection.
    """
    return collection_service.get_collection_details(
        collection_id=collection_id,
        user_id=get_current_user_id()
    )


@mcp.tool()
@trace_tool("add_paper_to_collection")
def add_paper_to_collection(collection_id: str, paper_id: str, sequence_order: int = 0) -> dict:
    """
    Add a research paper into a user collection with an optional sequence order.
    """
    return collection_service.add_paper_to_collection(
        collection_id=collection_id,
        paper_id=paper_id,
        sequence_order=sequence_order,
        user_id=get_current_user_id()
    )


@mcp.tool()
@trace_tool("remove_paper_from_collection")
def remove_paper_from_collection(collection_id: str, paper_id: str) -> dict:
    """
    Remove a paper from a user collection.
    """
    return collection_service.remove_paper_from_collection(
        collection_id=collection_id,
        paper_id=paper_id,
        user_id=get_current_user_id()
    )


# =============================================================================
# 3. Curriculum Planning Tools
# =============================================================================

@mcp.tool()
@trace_tool("generate_reading_plan")
def generate_reading_plan(collection_id: str) -> dict:
    """
    Generate an optimal curriculum/reading plan for a collection, ordering papers by
    chronological prerequisites and citation impact scores.
    """
    return planning_service.generate_reading_plan(
        collection_id=collection_id,
        user_id=get_current_user_id()
    )


# =============================================================================
# 4. Progress & Annotation Tools
# =============================================================================

@mcp.tool()
@trace_tool("mark_paper_status")
def mark_paper_status(paper_id: str, status: str) -> dict:
    """
    Update reading status for a paper ('not_started', 'reading', 'completed', 'skipped').
    """
    return progress_service.mark_paper_status(
        user_id=get_current_user_id(),
        paper_id=paper_id,
        status=status
    )


@mcp.tool()
@trace_tool("save_note")
def save_note(paper_id: str, note_text: str) -> dict:
    """
    Save a researcher note or annotation on a paper for future synthesis and semantic search.
    """
    return progress_service.save_note(
        user_id=get_current_user_id(),
        paper_id=paper_id,
        note_text=note_text
    )


# =============================================================================
# Entrypoint
# =============================================================================
#
# Transport is chosen by MCP_TRANSPORT:
#   - "streamable-http" (default) → HTTP server, MCP endpoint at /mcp   ← Databricks Apps
#   - "sse"                       → HTTP server, MCP endpoint at /sse
#   - "stdio"                     → local MCP clients (Claude Desktop, MCP Inspector)
#
# For an HTTP transport the server binds MCP_HOST:PORT, where PORT is
# DATABRICKS_APP_PORT (injected by Databricks Apps) or 8080.

if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")

    if transport in ("streamable-http", "sse"):
        mcp.settings.host = os.getenv("MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.getenv("DATABRICKS_APP_PORT") or os.getenv("PORT") or "8080")
        # Stateless = no server-side session affinity, which the AI Gateway / a
        # Databricks Apps load balancer in front of the server needs.
        mcp.settings.stateless_http = os.getenv("MCP_STATELESS", "true").lower() == "true"
        mcp.settings.json_response = os.getenv("MCP_JSON_RESPONSE", "true").lower() == "true"
        logger.info(
            "Starting %s v%s — %s on %s:%s",
            MCP_SERVER_NAME, MCP_SERVER_VERSION, transport, mcp.settings.host, mcp.settings.port,
        )
    else:
        logger.info("Starting %s v%s — stdio", MCP_SERVER_NAME, MCP_SERVER_VERSION)

    mcp.run(transport=transport)
