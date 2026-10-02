"""
tests/test_write_boundary.py — a write that cannot be attributed is refused.

Writes were held behind WRITE_TOOLS_ENABLED until identity propagation was
proven in production. It is proven — `mcp_traces.user_id` now carries distinct
accounts for distinct people — so the gate is open.

Opening it needed a second thing to be true, and it was not. Every tool on the
MCP server, including all six writes, called `get_current_user_id()`, which
falls back to the demo account when nothing is bound. `require_current_user_id()`
was written for exactly this and was called nowhere. Its own docstring said why
that mattered:

    NOT safe for writes … this returns a real, writable account when nobody is
    bound, which is how a signed-in user's data ends up on the demo profile.

An unattributed write would not have failed. It would have succeeded, onto
somebody else's library, with nothing raised and nothing logged — the silent
failure the gate existed to prevent, simply relocated.

These are source-level checks because the server runs in a different process on
a different host, and the property has to hold at the moment somebody adds the
seventh write tool, not only when someone remembers to test it.
"""

import pathlib
import re

import pytest

from services.agent_service import WRITE_TOOLS

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp_server" / "research" / "server.py"


def _accessor_by_tool() -> dict[str, set[str]]:
    """Which identity accessor each tool function calls."""
    found: dict[str, set[str]] = {}
    current = None
    for server in (SERVER, SERVER.parent.parent / "assistant" / "server.py"):
        current = None
        for line in server.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*def (\w+)\(", line)
            if match:
                current = match.group(1)
            if current:
                for accessor in ("require_current_user_id", "get_current_user_id"):
                    if accessor + "()" in line:
                        found.setdefault(current, set()).add(accessor)
    return found




# ---------------------------------------------------------------------------
# The server refuses on its own authority
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool", WRITE_TOOLS)
def test_a_write_tool_demands_an_acting_user(tool):
    """
    `require_` raises when nothing is bound; `get_` returns the demo account and
    lets the write land on it. For a write there is only one correct choice.
    """
    accessors = _accessor_by_tool().get(tool)
    assert accessors, f"{tool} is a write tool but resolves no user at all"
    assert accessors == {"require_current_user_id"}, (
        f"{tool} uses {accessors} — a write must not fall back to the demo account")


def test_the_reads_keep_their_fallback():
    """
    Not a blanket rule. Anonymous browsing of the curated collections runs on
    exactly that fallback, so replacing it everywhere would break the demo tier
    to fix a problem the demo tier does not have.
    """
    accessors = _accessor_by_tool()
    for tool in ("list_collections", "get_collection_details"):
        assert accessors.get(tool) == {"get_current_user_id"}, tool


# ---------------------------------------------------------------------------
# The dashboard's own layers, with the gate open
# ---------------------------------------------------------------------------

def test_the_gate_is_open():
    from services import agent_service
    assert agent_service.WRITE_TOOLS_ENABLED is True


def test_an_anonymous_caller_is_still_refused_every_write():
    """
    The gate answers "is this switched on"; capability answers "may this tier".
    Opening the first must not have quietly answered the second — so this asserts
    the refusal survives with the gate open, which the old tests could not.
    """
    from exceptions import CapabilityDeniedError
    from services import agent_service

    for tool in WRITE_TOOLS:
        with pytest.raises(CapabilityDeniedError):
            agent_service.ensure_callable("anonymous", tool)


def test_a_signed_in_caller_may_now_use_every_write():
    from services import agent_service

    for tool in WRITE_TOOLS:
        mode = "research" if tool in agent_service.RESEARCH_TOOLS else "wick"
        agent_service.ensure_callable("authenticated", tool, mode)   # must not raise


def test_the_catalogs_agree_with_the_capability_table():
    """
    The menu the model is offered and the rule enforced on invocation are
    derived separately, and a model can always name a tool it was not offered.
    They have to agree, or one of them is decoration.
    """
    from services import agent_service

    anonymous = set(agent_service.callable_tools("anonymous"))
    signed_in = set(agent_service.callable_tools("authenticated")) | set(agent_service.callable_tools("authenticated", "wick"))

    assert not (anonymous & set(WRITE_TOOLS))
    assert set(WRITE_TOOLS) <= signed_in
