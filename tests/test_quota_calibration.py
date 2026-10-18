"""
tests/test_quota_calibration.py — the ceilings after Phase 3.6.

Calibration found that the limits were not limits. One agent question is
several provider requests — 41 questions consumed 101 — so a ceiling of 50
agent questions asked for 123 OpenRouter requests against an allowance of 50.
The provider refused first, every time, and the quota layer never bound
anything it was supposed to.

Two properties follow, and they are what this file defends:

**Ceilings are stated in the unit the provider meters.** Requests, not
questions. The relationship between them is measured, not assumed.

**A tier can be capped as a tier.** A per-visitor limit cannot protect a shared
allowance, because it resets with a cleared cookie: ten anonymous visitors at
one question each are ten questions however low the per-visitor number is.
"""

import pathlib
import re

import pytest

import config
from exceptions import QuotaExceededError
from services import quota_service

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# The tier ceiling
# ---------------------------------------------------------------------------

def test_the_anonymous_tier_has_a_ceiling_of_its_own(db):
    assert quota_service.tier_global_limit_for("anonymous", "agent_query") is not None


def test_signed_in_users_are_not_capped_as_a_tier(db):
    """
    They are bounded by the global ceiling and their own allowance. A third
    limit on the tier would be a limit nobody could explain.
    """
    assert quota_service.tier_global_limit_for("authenticated", "agent_query") is None


def test_the_tier_ceiling_binds_across_different_visitors(db, monkeypatch):
    """
    The point of it. Each visitor is inside their own allowance; the tier is not.
    """
    monkeypatch.setattr(quota_service, "_TIER_GLOBAL_LIMITS",
                        {("anonymous", "agent_query"): 2})

    quota_service.check_and_consume("agent_query", "anonymous", "anon", "visitor-1")
    quota_service.check_and_consume("agent_query", "anonymous", "anon", "visitor-2")

    with pytest.raises(QuotaExceededError) as refused:
        quota_service.check_and_consume("agent_query", "anonymous", "anon", "visitor-3")
    assert refused.value.scope == "tier"


def test_a_cleared_cookie_does_not_reset_it(db, monkeypatch):
    """
    The failure the per-visitor limit cannot cover: a new anon id is a new
    visitor as far as that limit is concerned.
    """
    monkeypatch.setattr(quota_service, "_TIER_GLOBAL_LIMITS",
                        {("anonymous", "agent_query"): 1})

    quota_service.check_and_consume("agent_query", "anonymous", "anon", "before-clearing")
    with pytest.raises(QuotaExceededError):
        quota_service.check_and_consume("agent_query", "anonymous", "anon", "after-clearing")


def test_the_tier_ceiling_leaves_signed_in_users_alone(db, monkeypatch):
    """
    Which is the whole reason for capping the tier rather than lowering the
    global ceiling: what anonymous visitors do not spend stays available.
    """
    monkeypatch.setattr(quota_service, "_TIER_GLOBAL_LIMITS",
                        {("anonymous", "agent_query"): 1})

    quota_service.check_and_consume("agent_query", "anonymous", "anon", "visitor")
    with pytest.raises(QuotaExceededError):
        quota_service.check_and_consume("agent_query", "anonymous", "anon", "another")

    result = quota_service.check_and_consume("agent_query", "authenticated", "user", "u1")
    assert result["remaining"] >= 0


def test_the_refusal_tells_an_anonymous_visitor_what_would_help(db, monkeypatch):
    """
    "Resting for today" is the right thing to say when the GLOBAL ceiling is
    reached, and the wrong thing here: signing in genuinely would help.
    """
    monkeypatch.setattr(quota_service, "_TIER_GLOBAL_LIMITS",
                        {("anonymous", "agent_query"): 0})

    with pytest.raises(QuotaExceededError) as refused:
        quota_service.check_and_consume("agent_query", "anonymous", "anon", "visitor")
    assert "sign in" in str(refused.value).lower()


def test_the_global_ceiling_is_checked_before_the_tier(db, monkeypatch):
    """
    Order carries meaning. "Resting for everyone" and "visitors have had their
    share" are different facts, and only the second is worth a sign-in prompt.
    """
    monkeypatch.setattr(quota_service, "_GLOBAL_LIMITS", {"agent_query": 0})
    monkeypatch.setattr(quota_service, "_TIER_GLOBAL_LIMITS",
                        {("anonymous", "agent_query"): 10})

    with pytest.raises(QuotaExceededError) as refused:
        quota_service.check_and_consume("agent_query", "anonymous", "anon", "visitor")
    assert refused.value.scope == "global"


def test_the_tier_counter_is_separate_from_the_global_one(db, monkeypatch):
    """
    Sharing a counter would make anonymous use consume the global ceiling twice.
    """
    monkeypatch.setattr(quota_service, "_TIER_GLOBAL_LIMITS",
                        {("anonymous", "agent_query"): 5})
    quota_service.check_and_consume("agent_query", "anonymous", "anon", "visitor")

    scopes = {key[0] for key in db.usage}
    assert "global" in scopes
    assert "tier:anonymous" in scopes


def test_the_tier_name_matches_the_one_identity_assigns(db):
    """
    The table is keyed by a literal. A mismatch would not raise — the cap would
    simply never be found, and anonymous use would be unbounded.
    """
    from middleware.auth import TIER_ANONYMOUS

    assert any(key[0] == TIER_ANONYMOUS for key in quota_service._TIER_GLOBAL_LIMITS)


# ---------------------------------------------------------------------------
# The calibrated numbers
# ---------------------------------------------------------------------------

def _render_env() -> dict:
    import yaml
    data = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    return {entry["key"]: entry.get("value")
            for service in data["services"]
            for entry in service.get("envVars", []) if "key" in entry}


def test_the_deployment_sets_every_calibrated_limit():
    env = _render_env()
    for key in ("ANON_AGENT_PER_DAY", "USER_AGENT_PER_DAY", "GLOBAL_AGENT_PER_DAY",
                "GLOBAL_ANON_AGENT_PER_DAY", "GLOBAL_RAG_PER_DAY",
                "AGENT_MAX_TOOL_CALLS"):
        assert key in env, key


def test_no_limit_is_declared_twice():
    """
    Two entries with one key is a value that depends on which the parser reads
    last. This happened while writing the calibration in.
    """
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    keys = re.findall(r"^\s+- key: (\w+)$", text, re.M)
    assert len(keys) == len(set(keys)), [k for k in keys if keys.count(k) > 1]


# The dearest question this project has ever measured: the p95 cost of an
# agent_query on openai/gpt-oss-120b, which is ten times the price per token of
# the configured model. Used as the worst case on purpose - a ceiling that fits
# inside the backstop at this price fits at any price the project has run.
WORST_CASE_COST_PER_QUESTION = 0.002071


def test_the_ceilings_fit_inside_the_spend_backstop():
    """
    This test used to divide a 50-request-per-day free allowance, and that is
    the bug it now guards against.

    `free_model_daily_requests` meters only `:free` model variants. The
    configured model is paid, so the counter reads 0/50 while money is spent,
    and every ceiling derived from it was derived from a meter the application
    never touches. The arithmetic was right; the input was fiction.

    The surviving idea is that the ceilings must fit inside the budget. The
    correction is to say which budget: GLOBAL_SPEND_USD_PER_DAY, in money, which
    stays true when the model changes.
    """
    env = _render_env()
    ceilings = (int(env["GLOBAL_AGENT_PER_DAY"]) + int(env["GLOBAL_RAG_PER_DAY"]))
    worst_case = ceilings * WORST_CASE_COST_PER_QUESTION
    backstop = float(env["GLOBAL_SPEND_USD_PER_DAY"])

    assert worst_case <= backstop, (
        f"the ceilings allow ${worst_case:.2f}/day at the dearest measured "
        f"price, above the ${backstop:.2f} backstop - so the backstop would "
        f"fire before the ceilings do, and the ceilings would never bind")


def test_the_backstop_is_not_the_thing_that_normally_fires():
    """
    A backstop that trips in ordinary use is a limit, and a badly explained one:
    the visitor is told the feature is resting when it is merely busy. At the
    configured model's measured cost there should be ample room under it.
    """
    env = _render_env()
    ceilings = (int(env["GLOBAL_AGENT_PER_DAY"]) + int(env["GLOBAL_RAG_PER_DAY"]))
    # The configured model is roughly a tenth the price of the one above.
    typical = ceilings * (WORST_CASE_COST_PER_QUESTION / 10)

    assert typical * 3 <= float(env["GLOBAL_SPEND_USD_PER_DAY"]), (
        "a full day at every ceiling leaves under 3x headroom beneath the "
        "backstop; the backstop will fire before the request ceilings")


def test_the_anonymous_share_is_smaller_than_the_whole():
    env = _render_env()
    assert int(env["GLOBAL_ANON_AGENT_PER_DAY"]) < int(env["GLOBAL_AGENT_PER_DAY"])


def test_the_tool_budget_is_still_configurable():
    """
    Four is right for THIS allowance, not for the product. A self-hosted copy
    must be able to raise it without editing source.
    """
    source = (ROOT / "dashboard" / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("AGENT_MAX_TOOL_CALLS"' in source
    assert config.AGENT_MAX_TOOL_CALLS == 4


def test_the_calibration_script_changes_nothing():
    """
    It reports; the decision lands in a diff. A script that wrote the limits
    would move the reasoning somewhere nobody re-reads.
    """
    script = (ROOT / "scripts" / "calibrate_quotas.py").read_text(encoding="utf-8")
    assert "write_text" not in script
    assert "UPDATE " not in script and "INSERT " not in script


def test_a_per_user_allowance_never_exceeds_the_global_ceiling():
    """
    An incoherent pair rather than a loose one: the individual number promises
    what the shared ceiling will refuse, so somebody is told they have allowance
    left at the moment they are turned away.

    Checked against the CODE defaults, not render.yaml. A deployment that sets
    no environment variables gets these, and that deployment is the one least
    likely to notice.
    """
    assert config.USER_AGENT_PER_DAY <= config.GLOBAL_AGENT_PER_DAY
    assert config.USER_RAG_PER_DAY <= config.GLOBAL_RAG_PER_DAY
    assert config.ANON_AGENT_PER_DAY <= config.GLOBAL_ANON_AGENT_PER_DAY


def test_the_code_defaults_match_the_deployment():
    """
    Two sources for one number drift, and the drift is silent: render.yaml was
    calibrated while the code defaults stayed on their placeholders, so a
    self-hoster inherited limits this deployment had already rejected.
    """
    env = _render_env()
    for key in ("ANON_AGENT_PER_DAY", "USER_AGENT_PER_DAY", "USER_RAG_PER_DAY",
                "GLOBAL_AGENT_PER_DAY", "GLOBAL_RAG_PER_DAY",
                "GLOBAL_ANON_AGENT_PER_DAY", "AGENT_MAX_TOOL_CALLS"):
        assert int(env[key]) == getattr(config, key), (
            f"{key}: render.yaml says {env[key]}, config.py default "
            f"is {getattr(config, key)}")


# ---------------------------------------------------------------------------
# The backstop, in money
# ---------------------------------------------------------------------------
#
# Every other limit in this file counts requests, which is a proxy for spend
# that holds only while the per-request cost holds. Change the model and the
# same count costs a different amount — which is exactly how these numbers came
# to be built on a counter reading zero while money left the account.

def test_a_metered_call_is_refused_once_the_day_is_spent(db, monkeypatch):
    monkeypatch.setattr(quota_service, "GLOBAL_SPEND_USD_PER_DAY", 0.50)
    monkeypatch.setattr(quota_service.lakebase, "spend_today", lambda: 0.50)

    with pytest.raises(QuotaExceededError) as raised:
        quota_service.check_and_consume(
            quota_service.AGENT_QUERY, "authenticated", "user", "u1")

    assert raised.value.scope == "global"


def test_the_refusal_does_not_invite_signing_in(db, monkeypatch):
    """
    Signing in cannot buy more money. Offering it would be the same mistake the
    global ceiling avoids: promising that an action helps when it does not.
    """
    monkeypatch.setattr(quota_service, "GLOBAL_SPEND_USD_PER_DAY", 0.50)
    monkeypatch.setattr(quota_service.lakebase, "spend_today", lambda: 9.99)

    with pytest.raises(QuotaExceededError) as raised:
        quota_service.check_and_consume(
            quota_service.AGENT_QUERY, "anonymous", "visitor", "v1")

    assert "sign in" not in str(raised.value).lower()


def test_spending_under_the_backstop_changes_nothing(db, monkeypatch):
    monkeypatch.setattr(quota_service, "GLOBAL_SPEND_USD_PER_DAY", 1.00)
    monkeypatch.setattr(quota_service.lakebase, "spend_today", lambda: 0.01)

    result = quota_service.check_and_consume(
        quota_service.AGENT_QUERY, "authenticated", "user", "u1")

    assert result["used"] == 1


def test_search_is_not_charged_against_it(db, monkeypatch):
    """
    Semantic search embeds and queries a vector index — it reaches no paid
    completion provider, so it neither spends money nor should pay a database
    read to find out how much has been spent.
    """
    called = []
    monkeypatch.setattr(quota_service, "GLOBAL_SPEND_USD_PER_DAY", 0.01)
    monkeypatch.setattr(quota_service.lakebase, "spend_today",
                        lambda: called.append(1) or 99.0)

    result = quota_service.check_and_consume(
        quota_service.SEMANTIC_SEARCH, "authenticated", "user", "u1")

    assert result["used"] == 1
    assert not called, "search paid for a spend lookup it cannot affect"


def test_zero_switches_it_off(db, monkeypatch):
    """
    For a self-hoster who would rather bound spend at the provider, where it can
    be enforced rather than observed.
    """
    called = []
    monkeypatch.setattr(quota_service, "GLOBAL_SPEND_USD_PER_DAY", 0)
    monkeypatch.setattr(quota_service.lakebase, "spend_today",
                        lambda: called.append(1) or 99.0)

    assert quota_service.check_and_consume(
        quota_service.AGENT_QUERY, "authenticated", "user", "u1")["used"] == 1
    assert not called


def test_an_unreadable_spend_lets_the_request_through(db, monkeypatch, caplog):
    """
    Fails OPEN, deliberately.

    The request ceilings are the primary protection and they are still in force.
    Refusing every metered feature because a telemetry query failed would turn a
    reporting problem into an outage — the wrong direction for a backstop.
    """
    monkeypatch.setattr(quota_service, "GLOBAL_SPEND_USD_PER_DAY", 0.01)

    def unreachable():
        raise RuntimeError("connection pool exhausted")

    monkeypatch.setattr(quota_service.lakebase, "spend_today", unreachable)

    result = quota_service.check_and_consume(
        quota_service.AGENT_QUERY, "authenticated", "user", "u1")

    assert result["used"] == 1
    assert "could not read today's spend" in caplog.text.lower()


def test_it_is_checked_before_the_request_ceilings(db, monkeypatch):
    """
    Outermost, so a day that has spent its money does not also burn a visitor's
    personal allowance discovering that.
    """
    monkeypatch.setattr(quota_service, "GLOBAL_SPEND_USD_PER_DAY", 0.01)
    monkeypatch.setattr(quota_service.lakebase, "spend_today", lambda: 1.00)
    incremented = []
    monkeypatch.setattr(quota_service.lakebase, "increment_usage",
                        lambda *a: incremented.append(a) or 1)

    with pytest.raises(QuotaExceededError):
        quota_service.check_and_consume(
            quota_service.AGENT_QUERY, "authenticated", "user", "u1")

    assert not incremented, "a counter was consumed by a request that was refused"


def test_the_deployment_sets_the_backstop():
    assert "GLOBAL_SPEND_USD_PER_DAY" in _render_env()


def test_the_backstop_default_matches_the_deployment():
    assert float(_render_env()["GLOBAL_SPEND_USD_PER_DAY"]) == \
        config.GLOBAL_SPEND_USD_PER_DAY
