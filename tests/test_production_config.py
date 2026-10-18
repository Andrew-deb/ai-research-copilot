"""
tests/test_production_config.py — the deployed posture (Phase 3.5).

Three things that are only wrong once the app is on Render, where nobody is
watching a terminal:

  * a pooled connection that died while the free instance was asleep;
  * the two timeouts drifting until a good answer is killed mid-stream;
  * a deployment whose configuration quietly disagrees with the product — the
    pricing page promising a demo tier the service has switched off.
"""

import io
import pathlib

import pytest
import yaml

import config


def _render() -> dict:
    path = pathlib.Path(__file__).resolve().parents[1] / "render.yaml"
    service = yaml.safe_load(io.open(path, encoding="utf-8").read())["services"][0]
    return {
        "service": service,
        "env": {e["key"]: e.get("value") for e in service["envVars"]},
    }


# ---------------------------------------------------------------------------
# A connection that slept through the instance being idle
# ---------------------------------------------------------------------------

class _DeadConnection:
    """A socket the pool still believes in. Fails on first use, like the real
    thing after Lakebase drops an idle connection."""
    closed = 0

    def cursor(self):
        import psycopg2
        raise psycopg2.OperationalError("server closed the connection unexpectedly")


class _LiveConnection:
    def __init__(self):
        self.queries = []

    def cursor(self):
        conn = self

        class Cur:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, sql, params=()): conn.queries.append(sql)
        return Cur()

    closed = 0


class _Pool:
    """Hands out `dead` connections first, then live ones."""

    def __init__(self, dead=1):
        self.to_hand_out = [_DeadConnection() for _ in range(dead)]
        self.discarded = []
        self.live = _LiveConnection()

    def getconn(self):
        return self.to_hand_out.pop(0) if self.to_hand_out else self.live

    def putconn(self, conn, close=False):
        if close:
            self.discarded.append(conn)


# These four assert the probe RAN, so each one pins _last_activity into the
# past. Without that they depend on no earlier test having touched the database
# in the last _IDLE_PROBE_SECONDS - which held by luck until a quota check began
# reading the day's spend on every metered call, and then failed with the probe
# silently skipped.
#
# `quiet` is the fixture that makes the subject of these tests the probe itself
# rather than the order pytest happened to choose.


@pytest.fixture
def quiet(monkeypatch):
    """A database nobody has spoken to recently, so the probe is due."""
    from repositories import lakebase

    monkeypatch.setattr(lakebase, "_last_activity", 0.0)
    return lakebase


def test_a_stale_connection_is_stepped_over(quiet):
    """
    Render's free instance sleeps and Lakebase drops the idle connections. The
    pool does not know: it hands back a socket that looks fine and fails on
    first use. Without the probe, the FIRST request after every wake is an error
    page — on a free tier that is the normal case, not an edge one.
    """
    lakebase = quiet

    pool = _Pool(dead=1)
    conn = lakebase._checkout(pool)

    assert conn is pool.live
    assert len(pool.discarded) == 1


def test_the_dead_connection_is_discarded_not_returned(quiet):
    """A dead connection put back is one the next caller inherits."""
    lakebase = quiet

    pool = _Pool(dead=2)
    lakebase._checkout(pool)
    assert len(pool.discarded) == 2
    assert all(isinstance(c, _DeadConnection) for c in pool.discarded)


def test_the_probe_gives_up_rather_than_looping(quiet):
    """A database that is genuinely down should fail quickly, not spin."""
    lakebase = quiet

    pool = _Pool(dead=99)
    lakebase._checkout(pool)          # returns whatever the pool gives last
    assert len(pool.discarded) == lakebase._CHECKOUT_ATTEMPTS


def test_a_live_connection_is_handed_straight_back(quiet):
    """The probe costs one round trip; it must not cost more than that."""
    lakebase = quiet

    pool = _Pool(dead=0)
    conn = lakebase._checkout(pool)

    assert conn is pool.live
    assert pool.discarded == []
    assert conn.queries == ["SELECT 1;"]


# ---------------------------------------------------------------------------
# The health check must not depend on the database
# ---------------------------------------------------------------------------

def test_healthz_does_not_touch_the_database(client, monkeypatch):
    """
    Render decides whether to route traffic by this route. If it needed
    Lakebase, a database blip would take the whole service out of rotation
    rather than degrading one feature.
    """
    from repositories import lakebase

    def explode(*a, **k):
        raise RuntimeError("database is down")

    monkeypatch.setattr(lakebase, "run_query", explode)
    monkeypatch.setattr(lakebase, "get_connection", explode)

    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_healthz_is_exempt_from_identity_resolution():
    """Resolving a user means a database read, which is the same trap."""
    from middleware import auth
    assert "/healthz" in auth._EXEMPT_PATHS


# ---------------------------------------------------------------------------
# The two timeouts
# ---------------------------------------------------------------------------

def test_the_outer_timeout_clears_the_inner_one():
    """
    The deadline bounds the SEARCH phase only. After it expires a turn still
    runs a final synthesis call, assembles citations and streams the answer out
    — measured at 90 to 122 seconds against a 75-second deadline.
    """
    margin = config.GUNICORN_TIMEOUT_SECONDS - config.AGENT_DEADLINE_SECONDS
    assert margin >= config.AGENT_TIMEOUT_HEADROOM_SECONDS


def test_both_timeouts_come_from_the_environment():
    """They move with the hosting plan, the model and the corpus — none of
    which is a reason to change code."""
    source = (pathlib.Path(__file__).resolve().parents[1] / "dashboard"
              / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("AGENT_DEADLINE_SECONDS"' in source
    assert 'os.getenv("GUNICORN_TIMEOUT_SECONDS"' in source


def test_the_start_command_uses_the_configured_timeout():
    """Otherwise config.py would be reasoning about a number the server never
    saw, and the boot-time check would be comparing fiction."""
    start = _render()["service"]["startCommand"]
    assert "${GUNICORN_TIMEOUT_SECONDS:-180}" in start
    assert "--timeout 120" not in start


def test_the_relationship_is_documented_in_one_place():
    source = (pathlib.Path(__file__).resolve().parents[1] / "dashboard"
              / "config.py").read_text(encoding="utf-8")
    block = source.split("The two timeouts, and why they are two")[1][:1400]
    for idea in ("inner", "outer", "synthesis", "mid-stream"):
        assert idea in block, idea


# ---------------------------------------------------------------------------
# The deployment agrees with the product
# ---------------------------------------------------------------------------

def test_the_anonymous_demo_is_on_in_production():
    """
    The capability layer shipped in 3.2. Left off, the deployed product is
    quieter than its own pricing page — which describes browsing, searching and
    asking without an account.
    """
    assert _render()["env"]["ALLOW_ANONYMOUS_DEMO"] == "true"


def test_metering_is_on_in_production():
    """Without it there is no ceiling on the bill and nothing for 3.6 to
    calibrate from."""
    assert _render()["env"]["QUOTAS_ENABLED"] == "true"


def test_the_global_ceilings_are_set():
    """Per-visitor limits reset when somebody clears a cookie. These do not, so
    these are what actually protect the bill."""
    env = _render()["env"]
    assert int(env["GLOBAL_RAG_PER_DAY"]) > 0
    assert int(env["GLOBAL_AGENT_PER_DAY"]) > 0


def test_every_quota_3_6_will_change_lives_in_the_deployment():
    """3.6 should change configuration, not source."""
    env = _render()["env"]
    for key in ("ANON_SEARCH_PER_DAY", "ANON_RAG_PER_DAY", "ANON_AGENT_PER_DAY",
                "USER_SEARCH_PER_DAY", "USER_RAG_PER_DAY", "USER_AGENT_PER_DAY",
                "GLOBAL_RAG_PER_DAY", "GLOBAL_AGENT_PER_DAY"):
        assert key in env, key


def test_production_still_refuses_the_dev_bypass():
    """The guard from 3.1, restated here because it is a deployment property."""
    env = _render()["env"]
    assert env["APP_ENV"] == "production"
    assert "ALLOW_DEV_USER_BYPASS" not in env

    source = (pathlib.Path(__file__).resolve().parents[1] / "dashboard"
              / "config.py").read_text(encoding="utf-8")
    assert "IS_PRODUCTION and ALLOW_DEV_USER_BYPASS" in source


def test_no_secret_is_committed_to_the_blueprint():
    """Every credential is `sync: false` — set in the dashboard, never in git."""
    service = _render()["service"]
    secrets = {"DATABASE_URL", "OPENROUTER_API_KEY", "HF_API_TOKEN",
               "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
               "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"}
    for entry in service["envVars"]:
        if entry["key"] in secrets:
            assert entry.get("sync") is False, entry["key"]
            assert "value" not in entry, entry["key"]


# ---------------------------------------------------------------------------
# The probe has to be cheap enough to keep
# ---------------------------------------------------------------------------

def test_a_busy_app_is_not_probed(monkeypatch):
    """
    Measured: the probe is a full round trip to Lakebase — 748 ms from a
    developer machine, 78% of the cost of an entire query. Paying it on every
    checkout would make a four-query page three seconds slower to avoid a
    failure that happens once per wake.
    """
    import time as _time

    from repositories import lakebase

    pool = _Pool(dead=1)                      # would be caught IF probed
    monkeypatch.setattr(lakebase, "_last_activity", _time.monotonic())

    conn = lakebase._checkout(pool)

    assert isinstance(conn, _DeadConnection)  # handed straight over, unprobed
    assert pool.discarded == []


def test_a_woken_app_is_probed(monkeypatch):
    """The case the probe exists for: everything was dropped while asleep."""
    from repositories import lakebase

    pool = _Pool(dead=1)
    monkeypatch.setattr(lakebase, "_last_activity", 0.0)   # long ago

    assert lakebase._checkout(pool) is pool.live
    assert len(pool.discarded) == 1


def test_the_idle_threshold_is_configurable():
    """Different hosts drop idle connections at different ages."""
    source = (pathlib.Path(__file__).resolve().parents[1] / "dashboard"
              / "repositories" / "lakebase.py").read_text(encoding="utf-8")
    assert 'os.getenv("DB_IDLE_PROBE_SECONDS"' in source


def test_activity_is_recorded_only_on_success():
    """A failed call says nothing reassuring about the connection, and is
    exactly when the next checkout should check."""
    source = (pathlib.Path(__file__).resolve().parents[1] / "dashboard"
              / "repositories" / "lakebase.py").read_text(encoding="utf-8")
    block = source.split("yield conn")[1][:300]
    assert "conn.commit()" in block
    assert "_last_activity = time.monotonic()" in block



# ---------------------------------------------------------------------------
# The model, which production used to pick by accident
# ---------------------------------------------------------------------------
#
# render.yaml named no model at all, so the deployment silently ran whatever
# dashboard/config.py happened to default to. Twice that default was a `:free`
# slug OpenRouter had since retired, which returns 404 on every call: every RAG
# answer and every agent turn stopped, and nothing in this repository had
# changed to explain it.
#
# ONE default, in dashboard/config.py, and the environment overriding it.
#
# An earlier version of these tests also checked mcp_server/research/config.py
# and agent/agent_config.yaml. Both named a model; neither is ever read. Pinning
# dead config made two files look load-bearing and would have demanded a four-way
# edit forever to satisfy a test rather than the application.

_FREE_SUFFIX = ":" + "free"          # built, not written, so the scan below
                                     # cannot match this file's own prose


def _code_default() -> str:
    """
    The literal default in dashboard/config.py, not the resolved value.

    `config.OPENROUTER_MODEL` is whatever os.getenv found, so asserting on it
    tests the machine rather than the code.
    """
    import ast

    root = pathlib.Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "dashboard" / "config.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "getenv"
                and len(node.args) == 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "OPENROUTER_MODEL"
                and isinstance(node.args[1], ast.Constant)):
            return node.args[1].value
    raise AssertionError("dashboard/config.py has no OPENROUTER_MODEL default")


def test_there_is_exactly_one_model_default():
    """
    The guard against this file's own earlier mistake: a second default is
    something a reader can find and believe, whether or not anything reads it.
    """
    import ast

    root = pathlib.Path(__file__).resolve().parents[1]
    declaring = []
    for path in sorted(root.rglob("config.py")):
        if "venv" in path.parts or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "getenv"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "OPENROUTER_MODEL"
                    and len(node.args) == 2):
                declaring.append(path.relative_to(root).as_posix())

    assert declaring == ["dashboard/config.py"], \
        f"expected one model default, found {declaring}"


def test_the_default_is_not_a_free_slug():
    """
    Not a style rule — a failure that has happened twice.

    `openai/gpt-oss-120b:free` was retired, was replaced with
    `inclusionai/ling-3.0-flash-vl:free`, and that was retired too. Both times
    every AI feature returned 404 until a human noticed and renamed it. A paid
    slug is not withdrawn for being popular, and at roughly 14,000 questions
    per dollar the saving never covered the fragility.
    """
    assert not _code_default().endswith(_FREE_SUFFIX), (
        f"{_code_default()} is a free slug; two have been retired already")


def test_the_example_env_does_not_hand_out_a_free_slug():
    """
    Commented out or not, it is what a self-hoster pastes.
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    for line in (root / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip("#").strip()
        if stripped.startswith("OPENROUTER_MODEL="):
            value = stripped.split("=", 1)[1].split("#")[0].strip()
            assert not value.endswith(_FREE_SUFFIX), f".env.example offers {value}"


def test_production_names_its_own_model():
    """
    A model this service depends on belongs in the deployment it runs in.
    Inheriting it from a code default means changing it is a discovery rather
    than a diff — which is how production came to serve a retired slug.
    """
    env = _render()["env"]

    assert "OPENROUTER_MODEL" in env, "render.yaml does not pin the model"
    assert env["OPENROUTER_MODEL"], "OPENROUTER_MODEL is named but has no value"


def test_the_deployment_agrees_with_the_code_default():
    """
    Otherwise local runs measure one model and production serves another, which
    is exactly how the quota calibration came to be based on the wrong one.
    """
    assert _render()["env"]["OPENROUTER_MODEL"] == _code_default()


def test_the_model_stays_overridable():
    """
    The default is a convenience, never a constraint. A self-hoster with their
    own provider key, or anyone trying a different model, changes one variable
    and no code.
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    source = (root / "dashboard" / "config.py").read_text(encoding="utf-8")

    assert 'os.getenv("OPENROUTER_MODEL"' in source
