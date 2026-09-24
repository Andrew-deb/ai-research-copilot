"""
tests/test_identity_propagation.py — who a tool call is acting for.

The dashboard authenticates to the MCP server as a *service principal*. Those
credentials say which application is calling and nothing about which person the
call is on behalf of, so the acting user travels separately, in X-RC-User-Id,
and the server binds it for the life of the request.

It did not arrive. `mcp_traces` recorded `demo@research-copilot.dev` for every
call made on behalf of every signed-in person, and the cause was not the
transport, the middleware, or the proxy — it was this:

    get_current_user_email()      # a getter
      -> set_current_user(DEMO)   # that writes
      -> _current_user_id.set(demo_id)

`set_current_user_id` binds an id and leaves the email None, which is precisely
the branch that provisioned; and the trace decorator read the email one line
before running the tool. Asking who was acting was what changed the answer.

These tests load `mcp_server`'s module in isolation, because both that project
and the dashboard ship a `repositories.lakebase` and only one can win an import.
"""

import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MCP = ROOT / "mcp_server"

REAL_USER = "11111111-1111-1111-1111-111111111111"
DEMO_ID = "00000000-0000-0000-0000-00000000dead"
DEMO_EMAIL = "demo@research-copilot.dev"


@pytest.fixture
def rc(monkeypatch):
    """
    `mcp_server.middleware.request_context`, loaded fresh with a stub Lakebase.

    Fresh per test on purpose: the module's identity lives in contextvars, and a
    module shared between tests would share them too — which is the exact class
    of bug this file is about.
    """
    lookups: list[str] = []

    def get_or_create_user(email, display_name=None):
        lookups.append(email)
        return {"user_id": DEMO_ID, "email": email}

    fake = types.ModuleType("repositories.lakebase")
    fake.get_or_create_user = get_or_create_user
    package = types.ModuleType("repositories")
    package.lakebase = fake

    # setitem, so the dashboard's own `repositories` is restored afterwards.
    monkeypatch.setitem(sys.modules, "repositories", package)
    monkeypatch.setitem(sys.modules, "repositories.lakebase", fake)

    spec = importlib.util.spec_from_file_location(
        "mcp_request_context", MCP / "middleware" / "request_context.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.lookups = lookups
    return module


# ---------------------------------------------------------------------------
# The regression itself
# ---------------------------------------------------------------------------

def test_reading_the_email_does_not_replace_the_acting_user(rc):
    """
    The whole bug in four lines. Bind a person, ask for their email, and the
    person must still be the person.
    """
    rc.set_current_user_id(REAL_USER)
    rc.get_current_user_email()

    assert rc.get_current_user_id() == REAL_USER


def test_the_email_getter_touches_no_database(rc):
    """
    It provisioned the demo account on every call. That was one write and one
    round trip per traced tool call, to answer a question about a value already
    in memory — and the write was what did the damage.
    """
    rc.set_current_user_id(REAL_USER)
    rc.get_current_user_email()

    assert rc.lookups == []


def test_an_unknown_email_is_absent_rather_than_wrong(rc):
    """
    The dashboard sends an id and no email. Naming the demo account there would
    be a trace asserting that somebody else did a signed-in person's work.
    """
    rc.set_current_user_id(REAL_USER)
    assert rc.get_current_user_email() is None


def test_the_trace_reads_the_id_that_is_actually_bound(rc):
    """What telemetry should record: the identity, unchanged by being read."""
    rc.set_current_user_id(REAL_USER)

    assert rc.get_bound_user_id() == REAL_USER
    assert rc.get_current_user_id() == REAL_USER      # still, after the read


def test_nothing_bound_is_reported_as_nothing(rc):
    """
    The accessor telemetry uses must not invent a user. A call made by the
    service principal alone genuinely has no acting person, and a trace saying
    otherwise is worse than a null.
    """
    assert rc.get_bound_user_id() is None
    assert rc.get_current_user_email() is None
    assert rc.lookups == []


# ---------------------------------------------------------------------------
# What must not have changed
# ---------------------------------------------------------------------------

def test_anonymous_reads_still_get_the_demo_user(rc):
    """
    The demo fallback is deliberate and load-bearing: the anonymous tier browses
    the curated collections through it. Only the *email* getter provisioned by
    accident; this one means to.
    """
    assert rc.get_current_user_id() == DEMO_ID
    assert rc.lookups == [DEMO_EMAIL]


def test_a_write_still_refuses_to_guess(rc):
    """
    The guard that has kept this bug harmless. Without identity a write must
    stop, loudly — not land on the demo profile where nobody would look for it.
    """
    with pytest.raises(PermissionError):
        rc.require_current_user_id()


def test_a_bound_user_can_write(rc):
    rc.set_current_user_id(REAL_USER)
    assert rc.require_current_user_id() == REAL_USER


def test_identity_does_not_survive_the_request(rc):
    """A worker is reused; a contextvar left set is the next person's call."""
    rc.set_current_user_id(REAL_USER)
    rc.clear_current_user()

    assert rc.get_bound_user_id() is None


# ---------------------------------------------------------------------------
# The trace decorator, and the column it writes to
# ---------------------------------------------------------------------------

def _source(*parts) -> str:
    return (MCP.joinpath(*parts)).read_text(encoding="utf-8")


def test_the_decorator_uses_the_side_effect_free_accessors():
    source = _source("middleware", "trace_middleware.py")
    assert "get_bound_user_id" in source
    assert "user_id = get_bound_user_id()" in source


def test_the_trace_records_the_acting_user_id():
    """Otherwise identity propagation cannot be verified from the trace table,
    which is the only place it is visible after a deploy."""
    assert '"user_id": user_id,' in _source("middleware", "trace_middleware.py")

    write = _source("repositories", "lakebase.py").split("def write_trace")[1][:1400]
    assert "user_id" in write


def test_the_column_exists_in_a_migration():
    sql = (ROOT / "sql" / "14_mcp_traces_user_id.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE mcp_traces" in sql
    assert "ADD COLUMN IF NOT EXISTS user_id" in sql


def test_the_insert_names_as_many_columns_as_it_has_values():
    """
    A hand-written INSERT with a positional tuple: adding one and not the other
    is a silent column shift, not a syntax error, and it would write a user id
    into whatever column happened to be next.
    """
    write = _source("repositories", "lakebase.py").split("def write_trace")[1][:1600]
    columns = write.split("INSERT INTO mcp_traces (")[1].split(")")[0]
    placeholders = write.split("VALUES (")[1].split(")")[0]

    assert len(columns.split(",")) == len(placeholders.split(","))


# ---------------------------------------------------------------------------
# The caller's half, which was never the problem
# ---------------------------------------------------------------------------

def test_the_dashboard_sends_the_header_only_for_a_signed_in_person():
    """
    Anonymous visitors have no identity to send, and sending one would be an
    invention. This half was correct throughout — worth a test so the next
    investigation starts on the server side.
    """
    source = (ROOT / "dashboard" / "services" / "mcp_client.py").read_text(encoding="utf-8")
    assert 'USER_ID_HEADER = "X-RC-User-Id"' in source
    assert "if user_id:" in source
