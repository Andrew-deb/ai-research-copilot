"""
dashboard/repositories/palette.py — everything the palette needs, in one query.

Measured, against the deployed Lakebase, before this existed:

    SELECT 1  (round-trip floor)            438 ms
    SELECT *  title OR abstract ILIKE      3574 ms
    narrow columns, same predicate          319 ms

Two findings, and neither was the one I expected.

**`SELECT *` was the cost, not the scan.** `papers.payload` holds the full API
response as JSONB and `abstract` holds a few thousand characters; shipping those
across the public internet for every keystroke is what took three and a half
seconds. The ILIKE itself, over 324 rows, is nothing. Asking for four columns
instead made it eleven times faster.

**The floor is 438 ms.** That is one round trip to Lakebase doing no work at
all, so four sequential lookups cost 1.7 seconds before anybody searches for
anything. The old palette issued one query per section and ran on every
keystroke; with a 120 ms debounce that is several overlapping requests per
word, each holding a pooled connection. It exhausted the pool, and because each
section was individually wrapped in `except Exception`, the failures arrived as
an empty result with nothing said — a 38-second query followed by a blank
palette that looked like "no matches".

So: ONE round trip, narrow columns, and no silent failures. A UNION ALL of
small, limit-bounded lookups costs one 438 ms hop instead of four.
"""

from __future__ import annotations

from repositories.lakebase import run_query

# Per kind. A palette is a shortlist; twenty papers under a two-letter query is
# a search results page that opened itself.
PER_KIND = 5


def _union(parts: list[str]) -> str:
    """
    Branches joined by UNION ALL, each PARENTHESISED.

    Not cosmetic. An unparenthesised branch cannot carry its own ORDER BY or
    LIMIT — those bind to the whole union — and Postgres answers with
    `syntax error at or near "UNION"`. Shipped without the brackets, every
    search raised, and the caller's error handling turned that into an empty
    palette: a query that never ran, presented as "nothing matches".
    """
    return "\nUNION ALL\n".join(f"({part.strip()})" for part in parts)


def search(query: str, user_id: str | None) -> list[dict]:
    """
    Matching assets across the platform, newest and best first, in one query.

    Every branch selects the same four columns so they can be unioned, and each
    carries its own LIMIT so one noisy kind cannot crowd out the rest.

    The private kinds are only added to the SQL when there is a user to scope
    them to — an anonymous visitor does not pay for a notes lookup that could
    only ever return nothing.
    """
    like = f"%{query}%"
    parts = [
        # Title only. The abstract was searched too, and it is the column that
        # made the row expensive to return; for a palette, matching what a paper
        # is CALLED is the useful half anyway.
        """
        SELECT 'paper' AS kind, p.paper_id::text AS id, p.title AS label,
               coalesce(p.venue, '') AS detail
          FROM papers p
         WHERE p.title ILIKE %(like)s
         ORDER BY p.citation_count DESC NULLS LAST
         LIMIT %(per)s
        """,
        """
        SELECT 'collection', c.collection_id::text, c.name, coalesce(c.description, '')
          FROM collections c
         WHERE c.name ILIKE %(like)s
           AND (c.user_id = %(user)s OR c.is_curated)
         LIMIT %(per)s
        """,
    ]

    if user_id:
        parts.append("""
        SELECT 'note', n.note_id::text,
               coalesce(nullif(n.title, ''), left(n.note_text, 60)),
               coalesce(p.title, 'Not about a paper')
          FROM notes n
          LEFT JOIN papers p ON p.paper_id = n.paper_id
         WHERE n.user_id = %(user)s
           AND n.search_tsv @@ websearch_to_tsquery('english', %(query)s)
         ORDER BY n.pinned DESC, n.created_at DESC
         LIMIT %(per)s
        """)
        parts.append("""
        SELECT 'conversation', v.conversation_id::text, v.title, ''
          FROM conversations v
         WHERE v.user_id = %(user)s AND v.title ILIKE %(like)s
         ORDER BY v.updated_at DESC
         LIMIT %(per)s
        """)

    return run_query(_union(parts),
                     {"like": like, "query": query, "user": user_id, "per": PER_KIND})


def recent(user_id: str | None) -> list[dict]:
    """
    The last few things touched, for a palette opened with nothing typed.

    Recency is the answer to "what was I doing", which is the question somebody
    opens a palette with when they have not typed anything yet — and it is one
    query rather than the four an empty search would have cost.
    """
    parts = [
        """
        SELECT 'paper' AS kind, p.paper_id::text AS id, p.title AS label,
               coalesce(p.venue, '') AS detail, p.synced_at AS at
          FROM papers p
         ORDER BY p.synced_at DESC
         LIMIT %(per)s
        """,
    ]

    if user_id:
        parts.append("""
        SELECT 'note', n.note_id::text,
               coalesce(nullif(n.title, ''), left(n.note_text, 60)),
               coalesce(p.title, 'Not about a paper'), n.updated_at
          FROM notes n
          LEFT JOIN papers p ON p.paper_id = n.paper_id
         WHERE n.user_id = %(user)s
         ORDER BY n.updated_at DESC
         LIMIT %(per)s
        """)
        parts.append("""
        SELECT 'conversation', v.conversation_id::text, v.title, '', v.updated_at
          FROM conversations v
         WHERE v.user_id = %(user)s
         ORDER BY v.updated_at DESC
         LIMIT %(per)s
        """)
        parts.append("""
        SELECT 'collection', c.collection_id::text, c.name,
               coalesce(c.description, ''), c.created_at
          FROM collections c
         WHERE c.user_id = %(user)s
         ORDER BY c.created_at DESC
         LIMIT %(per)s
        """)

    # Interleaved by time across every kind, so the list reads as one history
    # rather than four stacked ones.
    return run_query(
        f"SELECT kind, id, label, detail FROM (\n{_union(parts)}\n) AS recent "
        f"ORDER BY at DESC LIMIT %(total)s;",
        {"user": user_id, "per": PER_KIND, "total": 8},
    )
