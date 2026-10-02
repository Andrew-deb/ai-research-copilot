"""
dashboard/repositories/usage_analytics.py — reading `ai_operations` back.

Two queries, both scoped to one account and one window.

**Narrow column lists, never `SELECT *`.** This table carries `error`, which is
a free-text message that can run to a stack trace, and the row count over a
month is not small. Selecting everything to compute five aggregates would pull
all of it across the public endpoint; a measured case on `papers` was 3574ms
that way against 319ms with the columns named.

**Aggregated in the database, not in Python.** The alternative is fetching every
row for the window and reducing it here, which turns a fixed-size answer into
one that grows with how much somebody used the product.

**Cost is read, and reported honestly.** `estimated_cost_usd` is summed, and so
is the number of rows that called a model and still came back without a price.

The `llm_turns > 0` half of that is the whole point. A NULL cost means one of
two very different things, and `telemetry_service` says which in a comment it
was easy to skim past: "a free turn is not the same as an unpriced one." A row
with no model call — an agent turn that stopped before reaching a provider, or a
semantic search, which embeds rather than prompts — has no price because it
spent nothing. Counting those as unpriced put a warning under the chart about
money that was never at stake.

No Flask state in this layer.
"""

from __future__ import annotations

from repositories.lakebase import run_query

# Guards the window at the query rather than trusting the caller: `days` reaches
# this from a query string, and an unbounded range would scan the table however
# good the index is.
MAX_DAYS = 90


def _window(days: int) -> int:
    return max(1, min(int(days or 7), MAX_DAYS))


# What the page calls a "feature" is a metric, except for the agent, which is
# two: the research agent and Wick spend the same allowance and do very
# different work. The mapping lives here so the filter and the grouping cannot
# disagree about what "Wick" means.
#
# A NULL mode is read as research: rows written before mode was recorded do not
# know which they were, and research is the default and the large majority.
FEATURES = {
    "research": ("agent_query", "research"),
    "wick": ("agent_query", "wick"),
    "ask": ("rag_query", None),
    "search": ("semantic_search", None),
}


# The expression that turns a row into a feature. Written once and used in the
# select list, the GROUP BY and the filter, because three spellings of the same
# rule is three chances for them to stop agreeing.
#
# CASE, not a FILTER clause: `FILTER` is only valid after an AGGREGATE, so
# `COALESCE(mode, 'research') FILTER (WHERE ...)` is rejected by the planner
# before it runs. That is what broke the Analytics tab outright.
def _mode_expr(prefix: str = "") -> str:
    return (f"CASE WHEN {prefix}metric = 'agent_query' "
            f"THEN COALESCE({prefix}mode, 'research') END")


def _feature_clause(feature: str | None, prefix: str = "") -> tuple[str, tuple]:
    """
    SQL and parameters for an optional feature filter.

    `prefix` qualifies the columns for the LEFT JOIN in `by_day`, where they
    belong to the right-hand table and a bare name is ambiguous.
    """
    if feature not in FEATURES:
        return "", ()
    metric, mode = FEATURES[feature]
    if mode:
        # COALESCE through _mode_expr, not `mode = 'research'`: pre-migration
        # rows are NULL and belong under research, and `NULL = 'research'`
        # excludes them silently.
        return (f" AND {prefix}metric = %s AND {_mode_expr(prefix)} = %s",
                (metric, mode))
    return f" AND {prefix}metric = %s", (metric,)


def by_metric(user_id: str, days: int = 7, feature: str | None = None) -> list[dict]:
    """
    One row per feature: how often, how much, how fast, how often it failed.

    `percentile_disc` rather than `percentile_cont`: it returns a latency that
    actually occurred rather than an interpolation between two that did, which
    is the honest answer when somebody asks how slow a request was.
    """
    clause, extra = _feature_clause(feature)
    return run_query(
        f"""
        SELECT metric,
               {_mode_expr()}                                              AS mode,
               count(*)                                                   AS operations,
               count(*) FILTER (WHERE NOT ok)                             AS failures,
               COALESCE(sum(input_tokens), 0)                             AS input_tokens,
               COALESCE(sum(output_tokens), 0)                            AS output_tokens,
               COALESCE(sum(llm_turns), 0)                                AS llm_turns,
               COALESCE(sum(tool_calls), 0)                               AS tool_calls,
               COALESCE(sum(estimated_cost_usd), 0)                       AS cost_usd,
               count(*) FILTER (WHERE estimated_cost_usd IS NULL
                                  AND COALESCE(llm_turns, 0) > 0)          AS cost_unknown,
               percentile_disc(0.5) WITHIN GROUP (ORDER BY latency_ms)    AS median_ms,
               percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms)   AS p95_ms
          FROM ai_operations
         WHERE user_id = %s
           AND occurred_at >= now() - make_interval(days => %s)
           {clause}
         GROUP BY metric, {_mode_expr()}
         ORDER BY operations DESC;
        """,
        (user_id, _window(days), *extra),
    )


def today(user_id: str) -> list[dict]:
    """
    What this account did since midnight, per feature.

    `CURRENT_DATE`, matching `usage_counters` rather than "the last 24 hours":
    the page says counts reset daily, and a rolling window would disagree with
    that sentence every evening.
    """
    return run_query(
        f"""
        SELECT metric,
               {_mode_expr()} AS mode,
               count(*)       AS operations
          FROM ai_operations
         WHERE user_id = %s
           AND occurred_at >= CURRENT_DATE
         GROUP BY metric, {_mode_expr()}
         ORDER BY operations DESC;
        """,
        (user_id,),
    )


def by_day(user_id: str, days: int = 7, feature: str | None = None) -> list[dict]:
    """
    One row per day in the window, including the days with nothing in them.

    The empty days are the point. A chart built only from days that have rows
    draws a continuous line over a fortnight's silence, which reads as steady
    use rather than as a gap.
    """
    window = _window(days)
    # Qualified at the source rather than by rewriting the string afterwards:
    # a blind replace of "mode" would also hit the word inside any column added
    # later that happens to contain it.
    #
    # The filter goes in the JOIN rather than a WHERE: on a LEFT JOIN a WHERE on
    # the right-hand table discards the empty days, which are the entire reason
    # this query generates a series in the first place.
    clause, extra = _feature_clause(feature, prefix="o.")
    return run_query(
        f"""
        SELECT d.day::date                       AS day,
               COALESCE(count(o.op_id), 0)       AS operations
          FROM generate_series(
                 current_date - (%s - 1),
                 current_date,
                 interval '1 day') AS d(day)
          LEFT JOIN ai_operations o
                 ON o.user_id = %s
                AND o.occurred_at >= d.day
                AND o.occurred_at <  d.day + interval '1 day'
                {clause}
         GROUP BY d.day
         ORDER BY d.day;
        """,
        (window, user_id, *extra),
    )
