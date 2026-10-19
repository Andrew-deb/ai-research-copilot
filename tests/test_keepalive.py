"""
tests/test_keepalive.py — staying awake, and staying usable.

Two different sleeps, with two different cures, and conflating them is the
mistake this file guards against.

**Render sleeps.** The free tier stops the service after ~15 minutes idle and
the next request waits ~30 s. An external schedule hitting `/healthz` every five
minutes prevents that.

**Lakebase sleeps too.** Its free tier suspends compute after inactivity, and
that is invisible to the fix above, because `/healthz` deliberately never
touches the database. Measured on 6 October 2026: a cold connect took 9.8 s
against 0.33 s warm. The service can be wide awake and still make the next
visitor wait ten seconds.

TCP keepalives are already configured on the pool and do not help. The socket is
not what goes away — the server is.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------

def test_healthz_answers_without_a_session(anon_client):
    assert anon_client.get("/healthz").status_code == 200


def test_healthz_touches_no_database(client, db, monkeypatch):
    """
    Both reasons point the same way. As Render's health check, a database query
    here restarts the service during a blip it cannot fix by restarting; as the
    pinger's target, it fails exactly when the service most needs to stay up.
    """
    from repositories import lakebase

    def explode(*a, **k):
        raise AssertionError("/healthz queried the database")

    monkeypatch.setattr(lakebase, "run_query", explode)
    monkeypatch.setattr(lakebase, "ping", explode)

    assert client.get("/healthz").status_code == 200


def test_healthz_is_exempt_from_identity_resolution():
    """
    Resolving a user means a database read. The exemption is what makes the
    guarantee above hold for the middleware as well as the route.
    """
    source = (ROOT / "dashboard" / "middleware"
              / "auth.py").read_text(encoding="utf-8")
    assert '"/healthz"' in source.split("_EXEMPT_PATHS")[1][:200]


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

def test_readyz_reports_the_database(client, db):
    body = client.get("/readyz").get_json()
    assert body["status"] == "ok"
    assert "reachable" in body["database"]


def test_readyz_says_degraded_rather_than_raising(client, db, monkeypatch):
    """
    A probe that raises has told its caller nothing it can report.
    """
    from repositories import lakebase
    monkeypatch.setattr(lakebase, "ping", lambda: (False, "OperationalError: gone"))

    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.get_json()["status"] == "degraded"


def test_readyz_is_not_renders_health_check():
    """
    The whole point of keeping them separate. If this were the health check, a
    database outage would restart the service in a loop.
    """
    import yaml
    data = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    paths = [s.get("healthCheckPath") for s in data["services"]]
    assert "/readyz" not in paths
    assert "/healthz" in paths


# ---------------------------------------------------------------------------
# Keeping the database warm
# ---------------------------------------------------------------------------

def _body_of(module_path, function_name: str) -> str:
    """
    A function's CODE, without its docstring.

    Scanning the raw text is how a test fails on its own explanation: the
    docstring here says "Never raises" precisely to record the property, and a
    substring check cannot tell a description from a statement.
    """
    import ast

    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == function_name)

    body = function.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]                      # drop the docstring
    return "\n".join(ast.unparse(node) for node in body)


def test_the_keep_warm_loop_survives_a_failure():
    """
    A daemon thread that dies on the first failure is worse than none: the
    database being briefly unreachable is the condition it exists to outlast.
    """
    import ast

    path = ROOT / "dashboard" / "repositories" / "lakebase.py"
    loop = _body_of(path, "keep_warm")
    assert "while True" in loop
    assert "except Exception" in loop

    # A `raise` STATEMENT, found by parsing rather than by searching for the
    # word: it appears twice in this function already, once in the docstring
    # promising there is none and once in a log message about catching one.
    function = next(node for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                    if isinstance(node, ast.FunctionDef) and node.name == "keep_warm")
    assert not [node for node in ast.walk(function) if isinstance(node, ast.Raise)]


def test_it_is_in_process_rather_than_a_second_external_schedule():
    """
    It runs only while the service is up, which is exactly when a warm pool is
    worth having, and it needs nothing configured outside the deployment.
    """
    source = (ROOT / "dashboard" / "services" / "background_tasks.py").read_text(encoding="utf-8")
    assert "lakebase.keep_warm" in source
    assert "daemon=True" in source.split("keep_warm")[1][:200]


def test_the_interval_is_configurable_and_can_be_switched_off():
    import config

    source = (ROOT / "dashboard" / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("DB_KEEPWARM_SECONDS"' in source

    app_source = (ROOT / "dashboard" / "services" / "background_tasks.py").read_text(encoding="utf-8")
    assert "if DB_KEEPWARM_SECONDS > 0:" in app_source


# Lakebase's free tier suspends compute after about this long idle. The number
# is the database's, not Render's, and confusing the two is how the first
# version of this setting came to be useless.
LAKEBASE_SUSPEND_SECONDS = 300


def _code_default(name: str) -> int:
    """
    The literal default in config.py, not the value the environment resolved to.

    `config.DB_KEEPWARM_SECONDS` is whatever os.getenv found, so reading it here
    asserts against the machine rather than against the code — and the suite
    itself now sets this one to 0, because a keep-warm thread fighting the
    fixtures for the connection pool buries pytest's summary under several
    hundred "pool exhausted" lines.
    """
    import ast

    tree = ast.parse((ROOT / "dashboard" / "config.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) != 2:
            continue
        target = node.func
        if getattr(target, "attr", None) != "getenv":
            continue
        key, fallback = node.args
        if isinstance(key, ast.Constant) and key.value == name:
            return int(fallback.value)
    raise AssertionError(f"no os.getenv default found for {name}")


def test_the_interval_knocks_before_the_database_suspends():
    """
    The bug this replaced. The interval was 600 against a 300-second idle timer,
    chosen by analogy with Render's fifteen-minute spin-down before the
    database's own timer was known.

    At ten minutes against five, the compute suspends before the thread knocks:
    visitors pay the cold connect anyway, AND the deployment pays for a wake
    cycle every ten minutes to achieve nothing. An interval that merely EQUALS
    the window is no better — one slow query and the gap is already over.
    """
    interval = _code_default("DB_KEEPWARM_SECONDS")

    assert interval < LAKEBASE_SUSPEND_SECONDS, (
        f"{interval}s does not fit inside a "
        f"{LAKEBASE_SUSPEND_SECONDS}s idle timer")


def test_the_deployment_agrees_with_the_code_default():
    import yaml

    data = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    env = {e["key"]: e.get("value") for s in data["services"]
           for e in s.get("envVars", []) if "key" in e}
    assert int(env["DB_KEEPWARM_SECONDS"]) == _code_default("DB_KEEPWARM_SECONDS")


# ---------------------------------------------------------------------------
# The gap keep-warm cannot cover
# ---------------------------------------------------------------------------

def test_overnight_cover_does_not_depend_on_an_http_pinger():
    """
    This test used to require the guide to prescribe `0 */4 * * *` against
    `/readyz`, and that schedule never once succeeded.

    An HTTP pinger reaches the database only through the app, so it needs Render
    awake — and it cannot wake it. Measured 18-19 October 2026: a cold start
    takes ~42 s, cron-job.org abandons a request at 30 s and cannot be set
    higher on a free account, and Render discards a half-started container
    shortly after the requester disconnects. Six consecutive failures, five
    minutes apart, recovering only when a human waited in a browser. Lakebase's
    compute was disabled twice.

    So the assertion is inverted: the guide must NOT send somebody back to an
    HTTP schedule for the overnight gap, and must point at the workflow that
    talks to Postgres directly.
    """
    guide = (ROOT / "docs" / "setup"
             / "pinger_setup.md").read_text(encoding="utf-8")
    overnight = guide.split("## 3. Lakebase, overnight")[1]

    assert "keepalive.yml" in overnight, (
        "the guide does not point at the workflow that covers the overnight gap")
    assert "0 */4 * * *" not in overnight.split("earlier revision")[0], (
        "the guide still prescribes the HTTP schedule that never worked")


def test_the_overnight_workflow_exists_and_reaches_postgres_directly():
    """
    The property that makes it work at all: `SELECT 1` issued to the database,
    not to the application. Everything else here is scheduling.
    """
    workflow = (ROOT / ".github" / "workflows"
                / "keepalive.yml").read_text(encoding="utf-8")

    assert "psql" in workflow, "nothing connects to Postgres directly"
    assert "SELECT 1" in workflow
    assert "secrets.DATABASE_URL" in workflow


def test_the_overnight_runs_leave_render_asleep():
    """
    Waking Render overnight would spend instance hours to no benefit: the
    database suspends ~5 minutes after each touch regardless, and a service that
    never sleeps uses ~744 of the 750 monthly hours. Exceeding that suspends
    every free service until the next month.
    """
    workflow = (ROOT / ".github" / "workflows"
                / "keepalive.yml").read_text(encoding="utf-8")

    plan = workflow.split("Decide whether to wake the app")[1].split("- name:")[0]
    # The three overnight schedules must not appear among the waking cases.
    for overnight in ("0 22 * * *", "0 1 * * *", "0 4 * * *"):
        assert f"'{overnight}'" not in plan, (
            f"{overnight} would wake Render overnight")


def test_the_wake_outlasts_a_cold_start():
    """
    The whole reason this is a workflow and not another HTTP ping. A cold start
    was measured at ~42 s; anything that gives up sooner starts a wake it will
    not see finish, and Render throws it away.
    """
    workflow = (ROOT / ".github" / "workflows"
                / "keepalive.yml").read_text(encoding="utf-8")
    import re

    timeout = re.search(r"WAKE_TIMEOUT_SECONDS:\s*(\d+)", workflow)
    assert timeout, "the wake step has no timeout"
    assert int(timeout.group(1)) >= 60, (
        f"{timeout.group(1)}s does not reliably outlast a ~42s cold start")
