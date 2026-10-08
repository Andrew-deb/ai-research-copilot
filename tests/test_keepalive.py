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
    source = (ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert "lakebase.keep_warm" in source
    assert "daemon=True" in source.split("keep_warm")[1][:200]


def test_the_interval_is_configurable_and_can_be_switched_off():
    import config

    source = (ROOT / "dashboard" / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("DB_KEEPWARM_SECONDS"' in source

    app_source = (ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert "if DB_KEEPWARM_SECONDS > 0:" in app_source


# Lakebase's free tier suspends compute after about this long idle. The number
# is the database's, not Render's, and confusing the two is how the first
# version of this setting came to be useless.
LAKEBASE_SUSPEND_SECONDS = 300


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
    import config

    assert config.DB_KEEPWARM_SECONDS < LAKEBASE_SUSPEND_SECONDS, (
        f"{config.DB_KEEPWARM_SECONDS}s does not fit inside a "
        f"{LAKEBASE_SUSPEND_SECONDS}s idle timer")


def test_the_deployment_agrees_with_the_code_default():
    import config
    import yaml

    data = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    env = {e["key"]: e.get("value") for s in data["services"]
           for e in s.get("envVars", []) if "key" in e}
    assert int(env["DB_KEEPWARM_SECONDS"]) == config.DB_KEEPWARM_SECONDS


# ---------------------------------------------------------------------------
# The gap keep-warm cannot cover
# ---------------------------------------------------------------------------

def test_the_overnight_schedule_is_documented_against_readyz():
    """
    The keep-warm thread lives in the app process, so it stops when Render
    sleeps — 6.75 h of total silence every night against a platform that may
    disable an endpoint within hours. That gap cost this project its first
    Lakebase endpoint.

    It has to be `/readyz`: `/healthz` never touches the database, so pinging it
    would wake Render and leave Lakebase exactly as idle as it was.
    """
    guide = (ROOT.parent / "context" / "setup"
             / "pinger_setup.md").read_text(encoding="utf-8")
    overnight = guide.split("## 3. Lakebase, overnight")[1]

    assert "/readyz" in overnight
    assert "0 */4 * * *" in overnight
    assert "/healthz" in overnight      # and why it is NOT the target
