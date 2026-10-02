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
    text = (MCP_DIR / "shared_resource" / "requirements.txt").read_text(encoding="utf-8")
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


def _rpc(url: str, method: str, timeout: float = 10.0, params=None) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


@pytest.fixture(scope="module", params=["research.server", "assistant.server", "built-research", "built-assistant"])
def flattened_server(tmp_path_factory, request):
    """Copy mcp_server/'s *contents* to a temp root (what Databricks does) and run it."""
    pytest.importorskip("mcp.server.fastmcp", reason="needs mcp<2 (FastMCP 1.x API)")

    root = tmp_path_factory.mktemp("source_code")
    if request.param.startswith("built-"):
        from tests.test_deployment_packaging import load_builder
        target = request.param.removeprefix("built-")
        root = load_builder().build(target, root)
        entrypoint = "research.server" if target == "research" else "assistant.server"
    else:
        shutil.copytree(MCP_DIR, root, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env"))
        entrypoint = request.param

    port = _free_port()
    env = {
        "PATH": "", "SYSTEMROOT": "", "PYTHONUNBUFFERED": "1",
        "DATABASE_URL": "", "MCP_TRANSPORT": "streamable-http", "DATABRICKS_APP_PORT": str(port),
    }
    import os
    env = {**os.environ, **env, "PATH": os.environ.get("PATH", ""),
           "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}

    proc = subprocess.Popen(
        [sys.executable, "-m", entrypoint],
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
    assert root["server"] in ("ai-research-copilot", "wick-workspace")


def test_serves_thirteen_tools_from_flattened_layout(flattened_server):
    result = _rpc(f"{flattened_server}/mcp", "tools/list")
    names = {t["name"] for t in result["result"]["tools"]}
    expected = EXPECTED_TOOLS if _get_json(flattened_server + "/")["server"] == "ai-research-copilot" else {
        "find_workspace_resources", "get_workspace_resource", "get_workspace_paper", "search_workspace_papers",
        "get_reading_progress", "create_collection", "reorder_collection_papers", "add_paper_to_collection",
        "remove_paper_from_collection", "mark_paper_status", "create_note", "edit_note", "set_note_pinned", "delete_note", "create_learning_goal", "update_goal_status",
    }
    assert names == expected


def test_no_stray_health_tool(flattened_server):
    # A `health` *tool* would mean the Databricks sample server is deployed, not ours.
    names = {t["name"] for t in _rpc(f"{flattened_server}/mcp", "tools/list")["result"]["tools"]}
    assert names.isdisjoint({"health", "healthz", "ping", "status"})


def test_research_adapters_and_bulk_ordering_remain_deployable():
    """Exercise legacy service names in their own interpreter, with no database."""
    script = '''
from unittest.mock import Mock
from shared_resource.exceptions import ValidationError as SharedValidationError
from shared_resource.exceptions import ValidationError
from shared_resource.adapters import collection_service, progress_service, planning_service
from shared_resource.repositories import lakebase

assert ValidationError is SharedValidationError
lakebase.get_collection = Mock(return_value={"user_id": "owner"})
lakebase.get_paper = Mock(return_value={"paper_id": "paper", "title": "Paper"})
lakebase.get_collection_papers = Mock(return_value=[
    {"paper_id": "paper", "title": "Paper", "publication_year": 2020}])
lakebase.add_paper_to_collection = Mock()
lakebase.save_note = Mock(return_value={"note_id": "note", "note_text": "Body", "created_at": "now"})
lakebase.run_write = Mock()

membership = collection_service.add_paper_to_collection("collection", "paper", 4, "owner")
assert membership["sequence_order"] == 4
lakebase.add_paper_to_collection.assert_called_once_with("collection", "paper", 4)
assert progress_service.save_note("owner", "paper", " Body ")["note_text"] == "Body"
lakebase.save_note.assert_called_once_with(user_id="owner", paper_id="paper", note_text="Body")

plan = planning_service.generate_reading_plan("collection", "owner")
assert plan["reading_plan"][0]["paper_id"] == "paper"
assert lakebase.run_write.call_count == 1
sql, parameters = lakebase.run_write.call_args.args
assert "unnest" in sql
assert parameters == (["paper"], [1], "collection")
lakebase.run_write.reset_mock()
lakebase.update_paper_orders("collection", [])
lakebase.run_write.assert_not_called()

lakebase.create_learning_goal = Mock(return_value={"goal_id": "goal"})
assert planning_service.create_learning_goal("owner", " Goal ")["goal_id"] == "goal"
lakebase.create_learning_goal.assert_called_once_with(user_id="owner", title="Goal", description=None)
lakebase.get_notes_for_paper = Mock(return_value=[{"note_id": "note"}])
assert progress_service.get_notes_for_paper("owner", "paper") == [{"note_id": "note"}]
lakebase.get_notes_for_paper.assert_called_once_with(user_id="owner", paper_id="paper")
'''
    subprocess.run([sys.executable, "-c", script], cwd=MCP_DIR, check=True,
                   capture_output=True, text=True)


def test_wick_protocol_refuses_research_and_anonymous_personal_actions(flattened_server):
    if _get_json(flattened_server + "/")["server"] != "wick-workspace":
        return
    for name, arguments in (("search_papers", {"query": "transformers"}),
                            ("create_note", {"note_text": "Body"}),
                            ("get_reading_progress", {}),
                            ("create_learning_goal", {"title": "Goal"}),
                            ("update_goal_status", {"goal_id": "goal", "status": "completed"}),
                            ("edit_note", {"note_id": "note", "note_text": "Body"}),
                            ("set_note_pinned", {"note_id": "note", "pinned": True}),
                            ("delete_note", {"note_id": "note"})):
        result = _rpc(flattened_server + "/mcp", "tools/call",
                      params={"name": name, "arguments": arguments})
        assert result["result"]["isError"]
