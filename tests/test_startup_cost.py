"""
tests/test_startup_cost.py — importing configuration must not touch the network.

This file exists because the alternative was found the expensive way.

`_get_secret` asked a Databricks secret scope before reading the environment.
Eight values, one authenticated round trip each, measured at 5.7 to 8.4 seconds
— about 48 seconds added to every process start, after which the environment
variable was used anyway, because on this deployment the lookups do not merely
miss, they fail: PermissionDenied, PermissionDenied, ResourceDoesNotExist.

What that cost, in order:

    import config                       ~48 s of failed lookups
    Render cold start                   34–53 s, nearly all of it the above
    cron-job.org request budget         30 s, hard on free accounts
    => the ping that keeps the service awake timed out before the service
       could finish waking, every time
    => nothing reached Lakebase for days; its compute was suspended, then
       disabled, and the branch had to be recreated

And a visitor opening a cold link waited the same 34–53 seconds for a page.

None of it was visible: the handler was `except Exception: pass`, so eight
failing lookups produced no log line at all.

These tests assert the shape that prevents it — the environment is consulted
first, the network only when the environment is silent, failures are reported,
and the wait is bounded — rather than asserting a duration, which would be a
flaky measurement of whatever machine happens to run it.
"""

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

CONFIG_MODULES = (
    "dashboard/config.py",
    "mcp_server/shared_resource/config.py",
)


def _get_secret_body(relative: str) -> ast.FunctionDef:
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_get_secret":
            return node
    raise AssertionError(f"{relative} has no _get_secret")


# ---------------------------------------------------------------------------
# The order, which is the whole fix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("relative", CONFIG_MODULES)
def test_the_environment_is_read_before_the_network(relative):
    """
    Asserted on ORDER, not on presence. Both calls existed before; the bug was
    that the slow one came first and ran even when its answer was unnecessary.
    """
    function = _get_secret_body(relative)

    getenv_line = next(
        (node.lineno for node in ast.walk(function)
         if isinstance(node, ast.Call)
         and getattr(node.func, "attr", None) == "getenv"), None)
    client_line = next(
        (node.lineno for node in ast.walk(function)
         if isinstance(node, ast.Call)
         and getattr(node.func, "id", None) == "WorkspaceClient"), None)

    assert getenv_line, f"{relative}: _get_secret never reads the environment"
    assert client_line, f"{relative}: _get_secret no longer reaches Databricks at all"
    assert getenv_line < client_line, (
        f"{relative}: Databricks is consulted before the environment — the "
        f"ordering that added ~48s to every process start")


@pytest.mark.parametrize("relative", CONFIG_MODULES)
def test_a_value_from_the_environment_short_circuits(relative):
    """
    Reading the environment first is not enough: the function has to RETURN on
    it. Reading and then asking anyway would keep every second of the cost.
    """
    function = _get_secret_body(relative)

    returns_before_client = [
        node.lineno for node in ast.walk(function)
        if isinstance(node, ast.Return)
        and node.lineno < next(
            n.lineno for n in ast.walk(function)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "WorkspaceClient")]

    assert returns_before_client, (
        f"{relative}: nothing returns before the Databricks call, so the "
        f"lookup runs even when the environment already answered")


# ---------------------------------------------------------------------------
# The failure that nobody could see
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("relative", CONFIG_MODULES)
def test_a_failed_lookup_is_reported(relative):
    """
    `except Exception: pass` is why this went unnoticed for weeks. Eight
    failures per start, no evidence anywhere.
    """
    function = _get_secret_body(relative)

    handlers = [node for node in ast.walk(function) if isinstance(node, ast.ExceptHandler)]
    assert handlers, f"{relative}: _get_secret no longer handles failure"

    for handler in handlers:
        body = [n for n in handler.body if not isinstance(n, ast.Pass)]
        assert body, f"{relative}: a bare `pass` swallows the failure silently"
        logged = any(
            isinstance(node, ast.Call) and getattr(node.func, "attr", "") in
            {"info", "warning", "error", "exception", "debug"}
            for n in handler.body for node in ast.walk(n))
        assert logged, f"{relative}: the failure is handled but never reported"


@pytest.mark.parametrize("relative", CONFIG_MODULES)
def test_no_secret_value_is_ever_logged(relative):
    """
    The scope and key name a secret; they are not the secret. Reporting the
    failure must not become a way of printing credentials into a log.
    """
    function = _get_secret_body(relative)

    for node in ast.walk(function):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") in
                {"info", "warning", "error", "exception", "debug"}):
            continue
        logged = " ".join(ast.unparse(arg) for arg in node.args)
        for forbidden in ("raw", "raw.value", "value", "b64decode", "decode"):
            assert forbidden not in logged.split(), (
                f"{relative}: a secret value reaches a log line: {logged}")


# ---------------------------------------------------------------------------
# The bound, for the path that is still allowed to use the network
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("relative", CONFIG_MODULES)
def test_the_lookup_cannot_hang(relative):
    """
    Databricks is still consulted when the environment is silent, which is how a
    Databricks App supplies these. Without a bound, the only limit is the SDK's
    retry budget, and a single hang would reproduce the original outage.
    """
    source = (ROOT / relative).read_text(encoding="utf-8")

    assert "_SECRET_TIMEOUT_SECONDS" in source, (
        f"{relative}: the Databricks lookup has no timeout")

    tree = ast.parse(source)
    timeout = next(
        (node.value.value for node in ast.walk(tree)
         if isinstance(node, ast.Assign)
         and any(getattr(t, "id", "") == "_SECRET_TIMEOUT_SECONDS" for t in node.targets)
         and isinstance(node.value, ast.Constant)), None)

    assert isinstance(timeout, (int, float)), f"{relative}: timeout is not a number"
    # Eight lookups must not be able to outlast the 30-second budget of the
    # schedule that keeps the service awake.
    assert 0 < timeout <= 10, (
        f"{relative}: a {timeout}s timeout per lookup is too long to be safe "
        f"at import")


# ---------------------------------------------------------------------------
# What it is all for
# ---------------------------------------------------------------------------

def test_importing_config_does_not_reach_for_databricks():
    """
    The property that matters, asserted on the clearest available signal.

    A first attempt here spied on `socket.create_connection` and passed against
    the ORIGINAL code, which demonstrably made eight network calls — the
    Databricks SDK builds its own transport, so the spy never saw them. A test
    that cannot fail is worse than no test, so it was replaced.

    `databricks.sdk` is imported INSIDE `_get_secret`, only on the path that
    talks to the network. So its absence from a fresh interpreter's modules
    after importing config proves no lookup was attempted — no timing, no
    sockets, no flakiness.

    Run in a subprocess because the import is a one-time event: anything that
    already pulled the SDK in would mask the result.
    """
    import subprocess
    import sys
    import textwrap

    probe = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(ROOT / "dashboard")!r})
        sys.path.insert(0, {str(ROOT)!r})
        import config
        print("LOADED" if "databricks.sdk" in sys.modules else "CLEAN")
        print("DATABASE_URL" if config.DATABASE_URL else "NO_DATABASE_URL")
    """)
    result = subprocess.run([sys.executable, "-I", "-c", probe],
                            capture_output=True, text=True, timeout=180)

    assert result.returncode == 0, result.stderr[-800:]
    lines = result.stdout.split()

    assert "CLEAN" in lines, (
        "importing config pulled in databricks.sdk, so it attempted a secret "
        "lookup despite the environment already supplying every value — the "
        "~48s that pushed cold start past the keep-awake budget")
    # Guards the guard: if config somehow read nothing, "CLEAN" would be true
    # for the wrong reason.
    assert "DATABASE_URL" in lines, "config resolved no DATABASE_URL at all"
