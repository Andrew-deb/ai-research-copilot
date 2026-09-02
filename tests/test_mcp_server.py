"""
tests/test_mcp_server.py — MCP server deployment contract.

Two guards for the failure that took the Databricks App down:

1. `test_no_package_qualified_imports` — a fast static check that nothing under
   mcp_server/ imports `mcp_server.*`. A Databricks App deploy flattens the
   folder's *contents* to /app/python/source_code/, so there is no `mcp_server`
   package at runtime and such an import is an instant ModuleNotFoundError.

2. `test_serves_thirteen_tools_from_flattened_layout` — starts the server in a
   subprocess with the same layout Databricks produces and asserts /healthz
   answers and tools/list returns exactly the 13 documented tools.

The server runs out-of-process on purpose: both apps use flat imports, so their
top-level module names (config, services, repositories, middleware, exceptions)
collide and cannot be imported into one interpreter.
"""

import json
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
MCP_DIR = PROJECT_ROOT / "mcp_server"

EXPECTED_TOOLS = {
    "search_papers", "get_paper_details", "get_similar_papers", "compare_papers",
    "explain_topic", "create_collection", "list_collections", "get_collection_details",
    "add_paper_to_collection", "remove_paper_from_collection", "generate_reading_plan",
    "mark_paper_status", "save_note",
}


# ---------------------------------------------------------------------------
# 1. Static: deploy-safe import style
# ---------------------------------------------------------------------------

def test_no_package_qualified_imports():
    offenders = []
    for path in sorted(MCP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("from mcp_server.", "from mcp_server ", "import mcp_server")):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{lineno}: {stripped}")
    assert not offenders, (
        "mcp_server/ must use flat imports — a Databricks App deploy has no "
        "`mcp_server` package at runtime:\n  " + "\n  ".join(offenders)
    )


def _requirement_lines() -> list[str]:
    text = (MCP_DIR / "requirements.txt").read_text(encoding="utf-8")
    return [ln.strip().lower() for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def test_requirements_excludes_the_embedding_stack():
    reqs = _requirement_lines()
    # The MCP server never embeds; sentence-transformers drags in torch + CUDA (~3 GB).
    assert not any(r.startswith(("sentence-transformers", "torch")) for r in reqs), reqs
    assert any(r.replace(" ", "").startswith("mcp>=1.0.0,<2.0.0") for r in reqs), reqs


# ---------------------------------------------------------------------------
# 2. Integration: the flattened Databricks layout actually boots
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get_json(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def _rpc(url: str, method: str, timeout: float = 10.0) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


@pytest.fixture(scope="module")
def flattened_server(tmp_path_factory):
    """Copy mcp_server/'s *contents* to a temp root (what Databricks does) and run it."""
    pytest.importorskip("mcp.server.fastmcp", reason="needs mcp<2 (FastMCP 1.x API)")

    root = tmp_path_factory.mktemp("source_code")
    shutil.copytree(MCP_DIR, root, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    port = _free_port()
    env = {
        "PATH": "", "SYSTEMROOT": "", "PYTHONUNBUFFERED": "1",
        "MCP_TRANSPORT": "streamable-http", "DATABRICKS_APP_PORT": str(port),
    }
    import os
    env = {**os.environ, **env, "PATH": os.environ.get("PATH", ""),
           "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}

    proc = subprocess.Popen(
        [sys.executable, "-m", "research_mcp_server"],
        cwd=str(root), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 45
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail("MCP server exited during startup:\n" + (proc.stdout.read() or ""))
        try:
            if _get_json(f"{base}/healthz", timeout=2).get("status") == "ok":
                break
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail("MCP server did not become healthy within 45s")

    yield base

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_health_routes_answer(flattened_server):
    assert _get_json(f"{flattened_server}/healthz")["status"] == "ok"
    root = _get_json(f"{flattened_server}/")
    assert root["status"] == "ok"
    assert root["server"] == "ai-research-copilot"


def test_serves_thirteen_tools_from_flattened_layout(flattened_server):
    result = _rpc(f"{flattened_server}/mcp", "tools/list")
    names = {t["name"] for t in result["result"]["tools"]}
    assert names == EXPECTED_TOOLS
    assert len(names) == 13


def test_no_stray_health_tool(flattened_server):
    # A `health` *tool* would mean the Databricks sample server is deployed, not ours.
    names = {t["name"] for t in _rpc(f"{flattened_server}/mcp", "tools/list")["result"]["tools"]}
    assert names.isdisjoint({"health", "healthz", "ping", "status"})
