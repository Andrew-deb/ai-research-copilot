"""
tests/test_dependency_pins.py — the deployed environment matching the local one.

Nothing was committed and nothing was wrong with the code, but the agent stopped
working in production:

    ImportError: cannot import name 'streamablehttp_client'
                 from 'mcp.client.streamable_http'

`dashboard/requirements.txt` asked for `mcp>=1.29.0` with no ceiling. A Render
rebuild resolved that to 2.2.0, where the function had been deleted — deprecated
in 1.30.0 in favour of `streamable_http_client`, removed in 2.0. The replacement
is not a rename: it takes a pre-built `httpx.AsyncClient` instead of `headers`
and `timeout`, so the call site has to change with it.

An unbounded requirement means every rebuild is an unreviewed upgrade, and the
first time anyone finds out is in production, because the local environment was
resolved months earlier and never moves.
"""

import inspect
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

REQUIREMENTS = (
    ROOT / "requirements.txt",
    ROOT / "dashboard" / "requirements.txt",     # what Render installs (rootDir)
    ROOT / "mcp_server" / "requirements.txt",    # what Databricks installs
)


def _requirement(path: pathlib.Path, package: str) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and re.match(rf"{package}\b", line):
            return line
    return None


# ---------------------------------------------------------------------------
# The ceiling that was missing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", REQUIREMENTS, ids=lambda p: p.parent.name)
def test_the_mcp_sdk_cannot_cross_a_major_version(path):
    """
    2.0 removed the function this app imports and moved onto httpx2, a different
    HTTP stack. Neither is a change to acquire by rebuilding.
    """
    line = _requirement(path, "mcp")
    assert line, f"{path} does not pin mcp at all"
    assert "<2.0.0" in line, f"{path}: unbounded — a rebuild can cross a major version ({line})"


def test_both_halves_of_the_protocol_move_together():
    """
    The dashboard is the client and mcp_server is the server. A ceiling on one
    and not the other lets them drift apart across a protocol change, which is a
    harder failure to read than this one was.
    """
    client = _requirement(ROOT / "dashboard" / "requirements.txt", "mcp")
    server = _requirement(ROOT / "mcp_server" / "requirements.txt", "mcp")

    assert "<2.0.0" in client and "<2.0.0" in server


# ---------------------------------------------------------------------------
# The call site and the installed library agreeing
# ---------------------------------------------------------------------------

def test_the_transport_helper_the_app_imports_exists():
    """
    The import that failed. Checked against whatever is installed rather than
    against a version number, so it fails wherever the two disagree.
    """
    from mcp.client.streamable_http import streamablehttp_client   # noqa: F401


def test_the_transport_helper_still_takes_the_arguments_we_pass():
    """
    The deeper check: the name surviving is not the same as the signature
    surviving. `streamable_http_client`, which replaces it, takes a pre-built
    httpx.AsyncClient instead — so a version where only the new shape exists
    would import fine and fail at the call.
    """
    from mcp.client.streamable_http import streamablehttp_client

    fn = getattr(streamablehttp_client, "__wrapped__", streamablehttp_client)
    parameters = inspect.signature(fn).parameters

    for passed in ("url", "headers", "timeout"):
        assert passed in parameters, f"mcp_client.py passes {passed}, the SDK no longer takes it"


def test_the_call_site_is_the_one_under_test():
    """
    Guards the test above from going stale: if the call site is rewritten for the
    newer API, this fails and sends someone here to update both.
    """
    source = (ROOT / "dashboard" / "services" / "mcp_client.py").read_text(encoding="utf-8")

    assert "from mcp.client.streamable_http import streamablehttp_client" in source
    assert "headers=headers" in source
