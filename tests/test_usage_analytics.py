"""
tests/test_usage_analytics.py — reading the telemetry back.

`ai_operations` has been written since the telemetry work and read by nothing.
This is the surface that reads it, and the properties worth defending are mostly
about restraint.

**It describes, it does not charge.** `estimated_cost_usd` sits in the table and
is never selected. A research tool that shows somebody the price of their last
question is inviting an argument about model pricing instead of about papers,
and in a multi-user deployment a per-account cost figure reads as a bill.

**It says when it does not know.** A row of zeros looks identical whether
somebody has run nothing or nothing is being recorded, and those are very
different facts.

**It asks the database to aggregate.** The alternative — fetch the window and
reduce it in Python — turns a fixed-size answer into one that grows with how
much somebody used the product, over a connection with a round-trip floor of
several hundred milliseconds.
"""

import ast
import pathlib

import pytest

from services import usage_service
from tests.conftest import _now          # frozen; the fake clock the rows use

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_PATH = ROOT / "dashboard" / "repositories" / "usage_analytics.py"
REPO = REPO_PATH.read_text(encoding="utf-8")


def sql() -> str:
    """
    Every string literal in the repository EXCEPT the docstrings.

    Scanning the raw file is how a test comes to fail on its own explanation: the
    module docstring says `SELECT *` and `percentile_cont` precisely to record
    why neither is used, and a substring check cannot tell a prohibition from an
    occurrence. Parsing lets the assertion look at the queries alone.
    """
    tree = ast.parse(REPO)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))

    return "\n".join(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings)


@pytest.fixture
def me(client, db):
    client.get("/dashboard")
    return next(iter(db.users_by_id))


def _op(db, user, **overrides):
    row = {"metric": "agent_query", "tier": "authenticated", "user_id": str(user),
           "occurred_at": _now(), "input_tokens": 100, "output_tokens": 50,
           "latency_ms": 900, "llm_turns": 2, "tool_calls": 1, "ok": True}
    row.update(overrides)
    db.ai_operations.append(row)
    return row


# ---------------------------------------------------------------------------
# What it counts
# ---------------------------------------------------------------------------

def test_an_account_with_no_history_says_so(db, me):
    data = usage_service.analytics(me, 7)
    assert data["has_data"] is False
    assert data["features"] == []


def test_operations_are_grouped_by_feature(db, me):
    _op(db, me, metric="agent_query")
    _op(db, me, metric="agent_query")
    _op(db, me, metric="semantic_search")

    features = {f["metric"]: f for f in usage_service.analytics(me, 7)["features"]}
    assert features["agent_query"]["operations"] == 2
    assert features["semantic_search"]["operations"] == 1


def test_tokens_are_added_up_across_both_directions(db, me):
    _op(db, me, input_tokens=400, output_tokens=120)
    _op(db, me, input_tokens=300, output_tokens=80)

    feature = usage_service.analytics(me, 7)["features"][0]
    assert feature["input_tokens"] == 700
    assert feature["output_tokens"] == 200
    assert feature["tokens"] == 900


def test_failures_are_shown_as_a_rate_as_well_as_a_count(db, me):
    """
    Two failures means something different out of five requests than out of five
    hundred, and the count alone cannot say which.
    """
    for _ in range(8):
        _op(db, me, ok=True)
    for _ in range(2):
        _op(db, me, ok=False)

    feature = usage_service.analytics(me, 7)["features"][0]
    assert feature["failures"] == 2
    assert feature["failure_rate"] == 20


def test_latency_is_reported_as_a_value_that_actually_happened(db, me):
    """
    percentile_disc, not percentile_cont: "about this slow" is more useful as a
    request that occurred than as an interpolation between two that did.
    """
    for latency in (100, 200, 300, 400, 500):
        _op(db, me, latency_ms=latency)

    feature = usage_service.analytics(me, 7)["features"][0]
    assert feature["median_ms"] in (100, 200, 300, 400, 500)
    assert feature["p95_ms"] >= feature["median_ms"]

    assert "percentile_disc" in sql()
    assert "percentile_cont" not in sql()


def test_one_account_never_sees_another(db, me):
    other = db.get_or_create_user("other@example.test", "Other")["user_id"]
    _op(db, other, metric="agent_query")

    assert usage_service.analytics(me, 7)["has_data"] is False


def test_anonymised_rows_belong_to_nobody(db, me):
    """
    A deleted account leaves its operations behind with a NULL user, so the
    operational record survives without naming anyone. Those rows must not
    reappear under somebody else's usage.
    """
    _op(db, me, user_id=None)
    assert usage_service.analytics(me, 7)["has_data"] is False


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

def test_the_chart_includes_the_days_with_nothing_in_them(db, me):
    """
    The empty days are the point. A chart built only from days that have rows
    draws a continuous line over a fortnight's silence.
    """
    _op(db, me)
    daily = usage_service.analytics(me, 7)["daily"]

    assert len(daily) == 7
    assert sum(1 for d in daily if d["operations"] == 0) == 6


def test_a_window_nobody_offered_falls_back_rather_than_running(db, me):
    """
    `days` arrives from a query string. An arbitrary range would scan the table
    however good the index is.
    """
    assert usage_service.analytics(me, 9999)["days"] == usage_service.DEFAULT_WINDOW
    assert usage_service.analytics(me, -5)["days"] == usage_service.DEFAULT_WINDOW


def test_the_repository_clamps_the_window_too():
    """
    Guarded where the query is built, not only where it is called. A second
    caller is a second chance to forget.
    """
    from repositories import usage_analytics

    assert usage_analytics._window(9999) == usage_analytics.MAX_DAYS
    assert usage_analytics._window(-5) == 1
    # 0 and None both mean "unset" rather than "zero days", so both fall back to
    # the default window rather than to an empty one.
    assert usage_analytics._window(0) == 7
    assert usage_analytics._window(None) == 7


# ---------------------------------------------------------------------------
# Cost
#
# Reversed on 1 October 2026. It was withheld on the reasoning that a currency
# figure on a research tool invites an argument about model pricing — but the
# number tells somebody what the free allowance is actually worth, and it is the
# figure a self-hoster most needs when the deployment is their own.
#
# What withholding it protected against is real, so the protection moved into
# the wording: the page says nobody is charged, before it says anything else.
# ---------------------------------------------------------------------------

def test_cost_is_reported(db, me):
    _op(db, me, estimated_cost_usd=0.0032)
    _op(db, me, estimated_cost_usd=0.0018)

    data = usage_service.analytics(me, 7)
    assert round(data["totals"]["cost_usd"], 6) == 0.005
    assert data["features"][0]["cost_usd"] > 0


def test_requests_the_provider_could_not_price_are_counted_not_dropped(db, me):
    """
    A total that silently omits unpriced rows is a wrong number rather than an
    incomplete one, and somebody comparing deployments would act on it.
    """
    _op(db, me, estimated_cost_usd=0.004, llm_turns=2)
    _op(db, me, estimated_cost_usd=None, llm_turns=2)
    _op(db, me, estimated_cost_usd=None, llm_turns=1)

    data = usage_service.analytics(me, 7)
    assert data["totals"]["cost_usd"] == 0.004
    assert data["totals"]["cost_unknown"] == 2


def test_a_request_that_never_called_a_model_is_free_not_unpriced(db, me):
    """
    The distinction that made the warning wrong in production. A NULL cost means
    one of two things, and `telemetry_service` says which in a comment that is
    easy to skim past: "a free turn is not the same as an unpriced one."

    An agent turn that stopped before reaching a provider, and a semantic search
    — which embeds rather than prompts — have no price because they spent
    nothing. Counting them put a warning under the chart about money that was
    never at stake.
    """
    _op(db, me, metric="agent_query", estimated_cost_usd=None, llm_turns=0)
    _op(db, me, metric="semantic_search", estimated_cost_usd=None, llm_turns=0)

    data = usage_service.analytics(me, 7)
    assert data["totals"]["operations"] == 2
    assert data["totals"]["cost_unknown"] == 0


def test_the_warning_only_appears_when_something_is_really_missing(db, me):
    """
    It is a caveat on a number, so it has to be absent when the number is whole.
    """
    _op(db, me, estimated_cost_usd=0.001, llm_turns=1)
    _op(db, me, estimated_cost_usd=None, llm_turns=0)

    assert usage_service.analytics(me, 7)["totals"]["cost_unknown"] == 0

    js = (ROOT / "dashboard" / "static" / "js"
          / "settings.js").read_text(encoding="utf-8")
    assert "if (data.totals.cost_unknown)" in js


def test_the_page_says_nobody_is_being_charged(client, db, me):
    """
    The sentence that stops a dollar figure reading as an invoice. Somebody who
    sees one on an account page assumes the worst before they finish reading, so
    it comes first.
    """
    body = client.get("/settings").get_data(as_text=True)
    usage = body.split('data-settings-panel="usage"')[1].split("</section>")[0]

    assert "You are not charged" in usage
    assert "free allowance" in usage


def test_a_small_cost_is_never_rendered_as_zero():
    """
    "$0.00" is the one answer that is actively wrong: it says free when the true
    figure is small but real, which is the opposite of what this is here to show.
    """
    js = (ROOT / "dashboard" / "static" / "js"
          / "settings.js").read_text(encoding="utf-8")

    assert "amount < 0.01" in js
    assert "toFixed(4)" in js


def test_the_caveat_is_shown_when_anything_is_unpriced():
    js = (ROOT / "dashboard" / "static" / "js"
          / "settings.js").read_text(encoding="utf-8")
    assert "cost_unknown" in js
    assert "no price" in js


# ---------------------------------------------------------------------------
# How it is queried
# ---------------------------------------------------------------------------

def test_the_queries_name_their_columns():
    """
    `error` is free text that can run to a stack trace, and a month of rows is
    not small. A measured case on `papers` was 3574ms selecting everything
    against 319ms with the columns named.
    """
    assert "SELECT *" not in sql()


def test_the_aggregation_happens_in_the_database():
    """
    Otherwise a fixed-size answer becomes one that grows with how much somebody
    used the product.
    """
    assert "GROUP BY metric" in sql()
    assert "count(*)" in sql()
    assert "generate_series" in sql()


def test_the_index_the_queries_depend_on_exists():
    """
    Both filter on user_id and sort by time. Neither existing index leads with
    user_id, so without this the planner scans the table — on a connection with
    a several-hundred-millisecond floor before it does any work.
    """
    migration = (ROOT / "sql"
                 / "24_ai_operations_user_index.sql").read_text(encoding="utf-8")
    assert "ai_operations (user_id, occurred_at DESC)" in migration
    assert "WHERE user_id IS NOT NULL" in migration


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def test_usage_has_a_section_of_its_own(client, db, me):
    body = client.get("/settings").get_data(as_text=True)
    assert 'data-settings-tab="usage"' in body
    assert 'data-settings-panel="usage"' in body


def test_it_is_no_longer_tacked_onto_the_account_page(client, db, me):
    body = client.get("/settings").get_data(as_text=True)
    account = body.split('data-settings-panel="account"')[1].split("</section>")[0]
    assert "usage-list" not in account


def test_analytics_are_fetched_rather_than_rendered_with_the_page(client, db, me):
    """
    Two aggregate queries for a tab most visits never open. Rendering them with
    every /settings load would put that cost on somebody changing their name.
    """
    body = client.get("/settings").get_data(as_text=True)
    assert "data-usage-analytics" in body

    source = (ROOT / "dashboard" / "routes"
              / "settings.py").read_text(encoding="utf-8")
    assert '@bp.get("/settings/usage")' in source


def test_the_data_route_answers_json(client, db, me):
    _op(db, me, metric="rag_query")
    data = client.get("/settings/usage?days=7").get_json()

    assert data["has_data"] is True
    assert data["features"][0]["metric"] == "rag_query"
    assert len(data["daily"]) == 7


def test_the_data_route_is_not_open_to_anonymous_visitors(anon_client):
    assert anon_client.get("/settings/usage").status_code in (302, 401, 403)


def test_a_failing_query_costs_the_tab_and_not_the_page(db, me, monkeypatch):
    """
    Usage history is interesting, not load-bearing. A database hiccup should
    lose the chart rather than the settings page.
    """
    from repositories import usage_analytics

    def boom(*a, **k):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(usage_analytics, "by_metric", boom)
    data = usage_service.analytics(me, 7)

    assert data["unavailable"] is True
    assert data["has_data"] is False


# ---------------------------------------------------------------------------
# The feature filter
#
# `metric` answers "which allowance did this spend", and the research agent and
# Wick spend the same one. That is right for quota and useless for a cost
# breakdown, where they are the two things somebody most wants separated: one
# reads papers, the other edits collections, and they cost very different
# amounts.
# ---------------------------------------------------------------------------

def test_the_agent_splits_into_research_and_wick(db, me):
    _op(db, me, metric="agent_query", mode="research")
    _op(db, me, metric="agent_query", mode="research")
    _op(db, me, metric="agent_query", mode="wick")

    labels = {f["label"]: f["operations"]
              for f in usage_service.analytics(me, 7)["features"]}
    assert labels["Research agent"] == 2
    assert labels["Wick"] == 1


def test_rows_from_before_the_mode_was_recorded_count_as_research(db, me):
    """
    They genuinely do not know which they were — the information was never
    captured. Research is the default mode and the large majority, and a
    backfilled guess would be worse than an honest default.
    """
    _op(db, me, metric="agent_query", mode=None)

    features = usage_service.analytics(me, 7)["features"]
    assert features[0]["feature"] == "research"


def test_filtering_narrows_the_totals(db, me):
    _op(db, me, metric="agent_query", mode="research")
    _op(db, me, metric="agent_query", mode="wick")
    _op(db, me, metric="rag_query")

    everything = usage_service.analytics(me, 7)
    just_wick = usage_service.analytics(me, 7, "wick")

    assert everything["totals"]["operations"] == 3
    assert just_wick["totals"]["operations"] == 1
    assert just_wick["feature_label"] == "Wick"


def test_filtering_narrows_the_chart_as_well(db, me):
    """
    The chart has to agree with the totals above it. Filtering one and not the
    other is a graph that contradicts its own caption.
    """
    _op(db, me, metric="agent_query", mode="wick")
    _op(db, me, metric="semantic_search")
    _op(db, me, metric="semantic_search")

    daily = usage_service.analytics(me, 7, "search")["daily"]
    assert sum(d["operations"] for d in daily) == 2


def test_the_chart_keeps_its_empty_days_when_filtered(db, me):
    """
    The filter goes in the JOIN, not a WHERE: on a LEFT JOIN a condition on the
    right-hand table discards the empty rows, which are the entire reason the
    query generates a series.
    """
    _op(db, me, metric="rag_query")
    daily = usage_service.analytics(me, 7, "ask")["daily"]

    assert len(daily) == 7
    from tests.test_usage_analytics import sql
    assert "WHERE" not in sql().split("LEFT JOIN")[1].split("GROUP BY")[0]


def test_an_unknown_feature_is_ignored_rather_than_obeyed(db, me):
    """`feature` arrives from a query string."""
    _op(db, me, metric="agent_query", mode="research")

    data = usage_service.analytics(me, 7, "'; DROP TABLE ai_operations; --")
    assert data["feature"] is None
    assert data["totals"]["operations"] == 1


def test_an_empty_filtered_window_says_what_was_filtered(db, me):
    """
    "Nothing recorded" under a Wick filter reads as never having used the
    product at all.
    """
    _op(db, me, metric="semantic_search")
    data = usage_service.analytics(me, 7, "wick")

    assert data["has_data"] is False
    assert data["feature_label"] == "Wick"

    js = (ROOT / "dashboard" / "static" / "js"
          / "settings.js").read_text(encoding="utf-8")
    assert "data.feature_label" in js


def test_the_quota_key_is_not_split_by_the_breakdown():
    """
    The mode is a column, not a new metric. Splitting `metric` into agent_query
    and wick_query would silently give every account two separate agent
    allowances — a pricing change disguised as a reporting one.
    """
    migration = (ROOT / "sql"
                 / "25_ai_operations_mode.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS mode TEXT" in migration

    quota = (ROOT / "dashboard" / "services"
             / "quota_service.py").read_text(encoding="utf-8")
    assert "wick_query" not in quota


def test_the_mode_reaches_the_row(db, me):
    """The whole chain: route, Operation, repository column list."""
    chat = (ROOT / "dashboard" / "routes" / "chat.py").read_text(encoding="utf-8")
    telemetry = (ROOT / "dashboard" / "services"
                 / "telemetry_service.py").read_text(encoding="utf-8")
    repo = (ROOT / "dashboard" / "repositories"
            / "lakebase.py").read_text(encoding="utf-8")

    assert "mode=chat_mode" in chat
    assert "mode=op.mode" in telemetry
    assert '"mode"' in repo


# ---------------------------------------------------------------------------
# The filter control, and the query that broke it
# ---------------------------------------------------------------------------

def test_the_feature_filter_is_a_menu_on_the_right(client, db, me):
    """
    Five chips beside the range control read as a second set of tabs competing
    with the real ones above them. A closed menu states the current filter in
    one line and takes the space of one control.
    """
    body = client.get("/settings").get_data(as_text=True)
    usage = body.split('data-settings-panel="usage"')[1].split("</section>")[0]

    assert "usage-feature-picker" in usage
    assert 'role="listbox"' in usage
    assert "chip-filter-btn" not in usage

    css = (ROOT / "dashboard" / "static" / "css"
           / "base.css").read_text(encoding="utf-8")
    assert ".usage-feature-picker { margin-left: auto; }" in css


def test_the_menu_offers_every_feature(client, db, me):
    body = client.get("/settings").get_data(as_text=True)
    for value in ("", "research", "wick", "ask", "search"):
        assert f'data-usage-feature="{value}"' in body


def test_filter_is_never_built_with_a_filter_clause_on_a_scalar():
    """
    The bug that took the whole tab out. `FILTER` is only valid after an
    AGGREGATE, so `COALESCE(mode, 'research') FILTER (WHERE ...)` is rejected by
    the planner before the query runs — and the service's catch-all turned that
    into "Usage history could not be loaded just now" rather than a traceback.

    CASE is the construct that does this job on a scalar.
    """
    # Through sql(), not the raw file: the comment above `_mode_expr` names the
    # broken construct in order to record why it is not used, and a raw scan
    # cannot tell a prohibition from an occurrence.
    assert "FILTER" not in sql().split("count(*)")[0]
    assert "CASE WHEN" in sql()


def test_the_mode_expression_is_written_once():
    """
    It appears in the select list, the GROUP BY and the filter. Three spellings
    of one rule is three chances for them to stop agreeing.
    """
    assert REPO.count("def _mode_expr") == 1
    assert REPO.count("_mode_expr(") >= 3


def test_the_day_query_qualifies_its_columns_properly():
    """
    It used to be built by string-replacing "metric" and "mode" into "o.metric"
    and "o.mode" after the fact, which would also rewrite any later column whose
    name happened to contain one of those words.
    """
    assert '.replace("metric"' not in REPO
    assert 'prefix="o."' in REPO


# ---------------------------------------------------------------------------
# Today
#
# The tab showed "Usage is not being metered on this deployment" to somebody who
# had used the product that morning. The sentence was true and useless: it
# reported deployment configuration to a person asking what they had done, and
# the answer was sitting in `ai_operations` the whole time.
# ---------------------------------------------------------------------------

def test_with_metering_on_it_counts_against_the_allowance(db, me):
    data = usage_service.today(me, "authenticated")

    assert data["metered"] is True
    assert data["rows"]
    assert all(row["limit"] for row in data["rows"])


def test_the_allowance_comes_from_the_counter_that_enforces_it(db, me, monkeypatch):
    """
    Not from `ai_operations`. Anything else could tell somebody they had room
    left at the moment they were turned away.
    """
    from services import quota_service

    quota_service.check_and_consume("agent_query", "authenticated", "user", me)
    _op(db, me, metric="agent_query")      # telemetry, not the counter
    _op(db, me, metric="agent_query")

    rows = {r["metric"]: r for r in usage_service.today(me, "authenticated")["rows"]}
    assert rows["agent_query"]["used"] == 1


def test_with_metering_off_it_still_says_what_you_did(db, me, monkeypatch):
    """
    The fix. `ai_operations` is written whether or not limits are enforced, so
    there is always an answer to "what have I done today".
    """
    from services import quota_service
    monkeypatch.setattr(quota_service, "usage_summary", lambda *a, **k: {})

    _op(db, me, metric="agent_query", mode="research")
    _op(db, me, metric="agent_query", mode="research")
    _op(db, me, metric="semantic_search")

    data = usage_service.today(me, "authenticated")
    counts = {row["label"]: row["used"] for row in data["rows"]}

    assert data["metered"] is False
    assert counts["Research agent"] == 2
    assert counts["Searches"] == 1


def test_an_unmetered_row_carries_no_limit(db, me, monkeypatch):
    """
    There is no ceiling, so nothing says "of N".
    """
    from services import quota_service
    monkeypatch.setattr(quota_service, "usage_summary", lambda *a, **k: {})
    _op(db, me, metric="agent_query")

    row = usage_service.today(me, "authenticated")["rows"][0]
    assert row["limit"] is None
    assert row["left"] is None


def test_an_unmetered_bar_is_relative_to_the_busiest_feature(db, me, monkeypatch):
    """
    A bar against an invented ceiling would measure against nothing. Against the
    busiest feature of the day it compares, which is the only honest thing a bar
    can do without a limit.
    """
    from services import quota_service
    monkeypatch.setattr(quota_service, "usage_summary", lambda *a, **k: {})

    for _ in range(4):
        _op(db, me, metric="agent_query", mode="wick")
    _op(db, me, metric="semantic_search")

    rows = {r["feature"]: r for r in usage_service.today(me, "authenticated")["rows"]}
    assert rows["wick"]["percent"] == 100
    assert rows["search"]["percent"] == 25


def test_a_quiet_day_says_so_rather_than_naming_the_configuration(client, db, me, monkeypatch):
    """
    "Nothing yet today" is the answer to the question somebody asked. "Usage is
    not being metered on this deployment" is an answer to a question only an
    operator would ask.
    """
    from services import quota_service
    monkeypatch.setattr(quota_service, "usage_summary", lambda *a, **k: {})

    assert usage_service.today(me, "authenticated")["rows"] == []

    body = client.get("/settings").get_data(as_text=True)
    usage = body.split('data-settings-panel="usage"')[1].split("</section>")[0]
    assert "Nothing yet today" in usage
    assert "not being metered on this deployment" not in usage


def test_today_means_today_not_the_last_day(db, me):
    """
    CURRENT_DATE, matching `usage_counters`. The page says counts reset daily,
    and a rolling 24-hour window would disagree with that sentence every evening.
    """
    assert "CURRENT_DATE" in sql()
    assert "interval '1 day'" not in sql().split("occurred_at >= CURRENT_DATE")[0][-200:]


def test_a_failure_costs_the_tab_and_not_the_page(db, me, monkeypatch):
    from repositories import usage_analytics
    from services import quota_service

    monkeypatch.setattr(quota_service, "usage_summary", lambda *a, **k: {})
    monkeypatch.setattr(usage_analytics, "today",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reset")))

    data = usage_service.today(me, "authenticated")
    assert data["rows"] == []
    assert data.get("unavailable") is True


# ---------------------------------------------------------------------------
# Shared allowances
#
# `agent_query` is ONE allowance covering TWO features. The research agent and
# Wick draw on it together, deliberately — splitting the metric would have given
# every account two separate allowances, a pricing change disguised as a
# reporting one.
#
# The Today tab borrowed a feature label for that row and reported a day of pure
# Wick usage as "Research agent".
# ---------------------------------------------------------------------------

def test_the_shared_row_is_named_for_the_allowance_not_a_feature(db, me):
    rows = {r["metric"]: r for r in usage_service.today(me, "authenticated")["rows"]}

    assert rows["agent_query"]["label"] == "Agent questions"
    assert rows["agent_query"]["label"] != "Research agent"


def test_a_day_of_wick_is_not_reported_as_research(db, me):
    """
    The bug, exactly. The bar is right — one allowance, shared — so the fix is
    the label and a line saying what it went on.
    """
    from services import quota_service

    for _ in range(4):
        quota_service.check_and_consume("agent_query", "authenticated", "user", me)
        _op(db, me, metric="agent_query", mode="wick")

    row = next(r for r in usage_service.today(me, "authenticated")["rows"]
               if r["metric"] == "agent_query")

    assert row["used"] == 4
    assert [(p["label"], p["used"]) for p in row["breakdown"]] == [("Wick", 4)]


def test_a_mixed_day_shows_both_largest_first(db, me):
    for _ in range(2):
        _op(db, me, metric="agent_query", mode="wick")
    for _ in range(5):
        _op(db, me, metric="agent_query", mode="research")

    row = next(r for r in usage_service.today(me, "authenticated")["rows"]
               if r["metric"] == "agent_query")

    assert [(p["label"], p["used"]) for p in row["breakdown"]] == [
        ("Research agent", 5), ("Wick", 2)]


def test_an_all_research_day_needs_no_breakdown(db, me):
    """
    "5 Research agent" under a bar labelled Agent questions is the same sentence
    twice. A breakdown that never varies is noise.
    """
    for _ in range(5):
        _op(db, me, metric="agent_query", mode="research")

    row = next(r for r in usage_service.today(me, "authenticated")["rows"]
               if r["metric"] == "agent_query")
    assert row["breakdown"] == []


def test_allowances_that_cover_one_feature_get_no_breakdown(db, me):
    _op(db, me, metric="rag_query")
    _op(db, me, metric="semantic_search")

    for row in usage_service.today(me, "authenticated")["rows"]:
        if row["metric"] != "agent_query":
            assert row["breakdown"] == []


def test_the_bar_still_comes_from_the_counter(db, me):
    """
    The breakdown reads `ai_operations`; the bar must keep reading
    `usage_counters`. A request refused by the quota increments the counter and
    never reaches the telemetry, so the two can legitimately disagree — and the
    number that decides whether the next request is allowed has to be the one on
    the bar.
    """
    from services import quota_service

    quota_service.check_and_consume("agent_query", "authenticated", "user", me)
    quota_service.check_and_consume("agent_query", "authenticated", "user", me)
    _op(db, me, metric="agent_query", mode="wick")      # telemetry only

    row = next(r for r in usage_service.today(me, "authenticated")["rows"]
               if r["metric"] == "agent_query")
    assert row["used"] == 2
    assert row["breakdown"][0]["used"] == 1


def test_a_broken_breakdown_does_not_cost_the_allowance(db, me, monkeypatch):
    from repositories import usage_analytics

    monkeypatch.setattr(usage_analytics, "today",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reset")))

    data = usage_service.today(me, "authenticated")
    assert data["metered"] is True
    assert data["rows"]


def test_the_page_says_the_allowance_is_shared(client, db, me):
    body = client.get("/settings").get_data(as_text=True)
    usage = body.split('data-settings-panel="usage"')[1].split("</section>")[0]
    assert "draw on the same allowance" in usage


# ---------------------------------------------------------------------------
# One bar, divided
#
# Wick asked for a graph of its own. It cannot have a BAR of its own, because a
# bar is an allowance and there is only one — a second would draw a quota that
# does not exist. So the one bar is divided by what used it.
# ---------------------------------------------------------------------------

def test_the_shared_bar_is_split_into_segments(db, me):
    from services import quota_service

    for _ in range(6):
        quota_service.check_and_consume("agent_query", "authenticated", "user", me)
    for _ in range(4):
        _op(db, me, metric="agent_query", mode="wick")
    for _ in range(2):
        _op(db, me, metric="agent_query", mode="research")

    row = next(r for r in usage_service.today(me, "authenticated")["rows"]
               if r["metric"] == "agent_query")

    assert [p["feature"] for p in row["breakdown"]] == ["wick", "research"]
    assert all("percent" in p for p in row["breakdown"])


def test_the_segments_fill_exactly_the_bar(db, me):
    """
    The geometry has to agree with the number printed beside it. Segments drawn
    from raw counts would overflow or fall short whenever the two tables
    disagree — which they can, and today they did: 9 recorded against 8 counted.
    """
    from services import quota_service

    for _ in range(8):
        quota_service.check_and_consume("agent_query", "authenticated", "user", me)
    for _ in range(9):                      # more recorded than counted
        _op(db, me, metric="agent_query", mode="wick")

    row = next(r for r in usage_service.today(me, "authenticated")["rows"]
               if r["metric"] == "agent_query")

    assert row["used"] == 8
    assert row["percent"] == 80
    assert round(sum(p["percent"] for p in row["breakdown"]), 2) == 80


def test_a_mixed_bar_divides_in_proportion(db, me):
    from services import quota_service

    for _ in range(5):
        quota_service.check_and_consume("agent_query", "authenticated", "user", me)
    for _ in range(3):
        _op(db, me, metric="agent_query", mode="wick")
    for _ in range(1):
        _op(db, me, metric="agent_query", mode="research")

    row = next(r for r in usage_service.today(me, "authenticated")["rows"]
               if r["metric"] == "agent_query")
    segments = {p["feature"]: p["percent"] for p in row["breakdown"]}

    # 50% of the limit, split three to one.
    assert round(segments["wick"], 2) == 37.5
    assert round(segments["research"], 2) == 12.5


def test_the_legend_counts_stay_as_recorded(db, me):
    """
    Only the geometry is scaled. The counts are a fact, and rewriting them to
    make the arithmetic tidy would be inventing numbers.
    """
    from services import quota_service

    for _ in range(8):
        quota_service.check_and_consume("agent_query", "authenticated", "user", me)
    for _ in range(9):
        _op(db, me, metric="agent_query", mode="wick")

    row = next(r for r in usage_service.today(me, "authenticated")["rows"]
               if r["metric"] == "agent_query")
    assert row["breakdown"][0]["used"] == 9


def test_each_feature_has_its_own_colour():
    """
    The segment and the number naming it have to be visibly the same thing, or
    a divided bar is just a bar with a seam in it.
    """
    css = (ROOT / "dashboard" / "static" / "css"
           / "base.css").read_text(encoding="utf-8")
    for feature in ("research", "wick", "ask", "search"):
        assert f".usage-fill-{feature}" in css


def test_the_bar_can_hold_segments_side_by_side():
    css = (ROOT / "dashboard" / "static" / "css"
           / "base.css").read_text(encoding="utf-8")
    rule = css[css.index(".usage-bar {"):]
    rule = rule[:rule.index("}")]
    assert "display: flex" in rule


def test_the_split_is_announced_to_a_screen_reader(client, db, me):
    """
    Colour is the only thing separating the segments, so the label has to carry
    what the colour says.
    """
    template = (ROOT / "dashboard" / "templates" / "settings"
                / "_usage.html").read_text(encoding="utf-8")
    bar = template.split('class="usage-bar"')[1].split("</span>")[0]
    assert "aria-label" in bar
    assert "part.label" in bar


def test_approval_segments_keep_costs_but_count_as_one_question(db, me):
    _op(db,me,mode='wick',input_tokens=100,estimated_cost_usd=0.01)
    _op(db,me,mode='wick',is_continuation=True,input_tokens=200,estimated_cost_usd=0.02)
    _op(db,me,mode='wick',is_continuation=True,input_tokens=300,estimated_cost_usd=0.03,ok=False)
    data = usage_service.analytics(me,7)
    feature = data['features'][0]
    assert feature['operations'] == 1
    assert feature['input_tokens'] == 600
    assert feature['cost_usd'] == pytest.approx(0.06)
    assert feature['failure_rate'] == 33
    assert data['daily'][-1]['operations'] == 1
    from repositories import usage_analytics
    assert usage_analytics.today(me)[0]['operations'] == 1
