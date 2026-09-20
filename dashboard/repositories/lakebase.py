"""
dashboard/repositories/lakebase.py — Database access layer for the dashboard.

SRP: All SQL and pgvector queries for the dashboard live here.
     No Flask request context, no HTTP calls, no business logic.

Mirrors mcp_server/repositories/lakebase.py in connection pattern and most
domain functions. Dashboard-specific additions: stat aggregations and
paginated listing queries optimised for UI rendering.
"""

import json
import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Generator

import psycopg2
import psycopg2.extras
import psycopg2.pool

from config import DATABASE_URL, EMBEDDING_DIMENSION

logger = logging.getLogger(__name__)


# =============================================================================
# Connection plumbing — pooled
# =============================================================================
# Every page issues several queries. Opening a fresh TLS connection to Lakebase
# per query (the previous behaviour) added a full TCP + TLS + auth handshake to
# each one — the main source of the dashboard's sluggishness. A per-process
# pool amortises that: connect once, reuse.

_POOL: psycopg2.pool.ThreadedConnectionPool | None = None
_POOL_LOCK = threading.Lock()

_POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
_POOL_MAX = int(os.getenv("DB_POOL_MAX", "8"))


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                if not DATABASE_URL:
                    raise RuntimeError("DATABASE_URL is not configured.")
                _POOL = psycopg2.pool.ThreadedConnectionPool(
                    _POOL_MIN, _POOL_MAX, dsn=DATABASE_URL,
                    # Keep pooled connections alive through Lakebase / proxy idle timeouts.
                    keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
                )
                logger.info("Lakebase connection pool ready (min=%d max=%d)", _POOL_MIN, _POOL_MAX)
    return _POOL


@contextmanager
def get_connection() -> Generator[psycopg2.extensions.connection, None, None]:
    """
    Yield a pooled psycopg2 connection; commit on success, roll back on error,
    and return it to the pool (discarding it if it broke).
    """
    pool = _get_pool()
    conn = pool.getconn()
    broken = False
    try:
        yield conn
        conn.commit()
    except Exception:
        broken = getattr(conn, "closed", 0) != 0
        if not broken:
            try:
                conn.rollback()
            except psycopg2.Error:
                broken = True
        raise
    finally:
        try:
            pool.putconn(conn, close=broken)
        except psycopg2.pool.PoolError:
            pass


def close_pool() -> None:
    """Close every pooled connection — call on worker shutdown."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.closeall()
            _POOL = None


def run_query(sql: str, params: tuple = ()) -> list[dict]:
    """Execute a SELECT query and return rows as a list of dicts."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def run_write(sql: str, params: tuple = (), returning: bool = False) -> Any:
    """Execute an INSERT / UPDATE / DELETE. Returns first RETURNING row or rowcount."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            if returning:
                row = cur.fetchone()
                return dict(row) if row else None
            return cur.rowcount


# =============================================================================
# Users
# =============================================================================

def get_user_by_id(user_id: str) -> dict | None:
    rows = run_query("SELECT * FROM users WHERE user_id = %s;", (user_id,))
    return rows[0] if rows else None


def get_user_by_provider(provider: str, subject: str) -> dict | None:
    """The account behind a provider identity. This is the real identity key."""
    rows = run_query(
        "SELECT * FROM users WHERE auth_provider = %s AND provider_subject = %s;",
        (provider, subject),
    )
    return rows[0] if rows else None


def create_oauth_user(provider: str, subject: str, email: str,
                      display_name: str | None = None,
                      avatar_url: str | None = None) -> dict:
    return run_write(
        """
        INSERT INTO users (email, display_name, auth_provider, provider_subject,
                           avatar_url, last_login_at)
        VALUES (%s, %s, %s, %s, %s, now())
        RETURNING *;
        """,
        (email, display_name, provider, subject, avatar_url),
        returning=True,
    )


def link_user_provider(user_id: str, provider: str, subject: str,
                       display_name: str | None = None,
                       avatar_url: str | None = None) -> dict:
    """
    Attach a provider identity to an account that predates OAuth.

    COALESCE on the display fields so signing in never blanks a name the user has
    already set - Google may supply nothing, and nothing should not overwrite
    something.
    """
    return run_write(
        """
        UPDATE users
           SET auth_provider    = %s,
               provider_subject = %s,
               display_name     = COALESCE(%s, display_name),
               avatar_url       = COALESCE(%s, avatar_url),
               last_login_at    = now()
         WHERE user_id = %s
        RETURNING *;
        """,
        (provider, subject, display_name, avatar_url, user_id),
        returning=True,
    )


def touch_user_login(user_id: str, display_name: str | None = None,
                     avatar_url: str | None = None) -> None:
    run_write(
        """
        UPDATE users
           SET last_login_at = now(),
               display_name  = COALESCE(%s, display_name),
               avatar_url    = COALESCE(%s, avatar_url)
         WHERE user_id = %s;
        """,
        (display_name, avatar_url, user_id),
    )


def get_or_create_user(email: str, display_name: str | None = None) -> dict:
    rows = run_query("SELECT * FROM users WHERE email = %s;", (email,))
    if rows:
        return rows[0]
    return run_write(
        "INSERT INTO users (email, display_name) VALUES (%s, %s) RETURNING *;",
        (email, display_name),
        returning=True,
    )


# =============================================================================
# Dashboard home — aggregated stats
# =============================================================================

def get_dashboard_stats(user_id: str) -> dict:
    """
    Every number the home page stat cards need, in ONE round trip.

    This was four separate queries, and `home_service` then issued a fifth that was
    byte-identical to the reading_progress one below. Lakebase is remote and every
    statement costs a TLS round trip, so the home page was spending over a second
    waiting on network before it rendered anything.

    Scalar subqueries let Postgres answer all four counts in a single pass. The
    reading breakdown comes back as a JSON object rather than a second result set,
    which is what removes the duplicate query entirely.
    """
    rows = run_query(
        """
        SELECT
            (SELECT COUNT(*) FROM learning_goals
              WHERE user_id = %(uid)s AND status = 'active')          AS active_goals,
            (SELECT COUNT(DISTINCT cp.paper_id)
               FROM collection_papers cp
               JOIN collections c ON c.collection_id = cp.collection_id
              WHERE c.user_id = %(uid)s)                              AS papers_in_collections,
            (SELECT COUNT(*) FROM notes WHERE user_id = %(uid)s)      AS notes_written,
            COALESCE(
                (SELECT jsonb_object_agg(status, n)
                   FROM (SELECT status, COUNT(*) AS n
                           FROM reading_progress
                          WHERE user_id = %(uid)s
                          GROUP BY status) AS by_status),
                '{}'::jsonb
            )                                                          AS reading_by_status;
        """,
        {"uid": user_id},
    )
    if not rows:
        return {"active_goals": 0, "papers_in_collections": 0, "notes_written": 0,
                "reading_by_status": {}}

    row = dict(rows[0])
    by_status = row.get("reading_by_status") or {}
    # psycopg2 normally adapts jsonb to a dict, but that depends on the adapter being
    # registered. Falling over on a string here would break the home page for a
    # reason that has nothing to do with the home page.
    if isinstance(by_status, str):
        by_status = json.loads(by_status)
    # Kept for callers that read the flat `papers_<status>` keys.
    for status, count in by_status.items():
        row[f"papers_{status}"] = count
    row["reading_by_status"] = by_status
    return row


# =============================================================================
# Papers
# =============================================================================

def get_paper(paper_id: str) -> dict | None:
    rows = run_query("SELECT * FROM papers WHERE paper_id = %s;", (paper_id,))
    return rows[0] if rows else None


def get_paper_by_doi(doi: str) -> dict | None:
    rows = run_query("SELECT * FROM papers WHERE doi = %s;", (doi,))
    return rows[0] if rows else None


def search_papers_by_text(query: str, limit: int = 20, offset: int = 0) -> list[dict]:
    """Keyword search with pagination for the search page."""
    pattern = f"%{query}%"
    return run_query(
        """
        SELECT * FROM papers
        WHERE title ILIKE %s OR abstract ILIKE %s
        ORDER BY citation_count DESC
        LIMIT %s OFFSET %s;
        """,
        (pattern, pattern, limit, offset),
    )


def get_recent_papers(limit: int = 10) -> list[dict]:
    return run_query(
        "SELECT * FROM papers ORDER BY synced_at DESC LIMIT %s;",
        (limit,),
    )


def upsert_paper(paper: dict) -> dict:
    """Upsert a paper — used when the dashboard saves a paper found via search."""
    return run_write(
        """
        INSERT INTO papers (
            openalex_id, semantic_scholar_id, doi, title, abstract,
            publication_year, venue, citation_count, tldr, influence_score,
            source_api, open_access_url, payload, synced_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (openalex_id) DO UPDATE SET
            semantic_scholar_id = COALESCE(EXCLUDED.semantic_scholar_id, papers.semantic_scholar_id),
            tldr                = COALESCE(EXCLUDED.tldr, papers.tldr),
            influence_score     = COALESCE(EXCLUDED.influence_score, papers.influence_score),
            citation_count      = EXCLUDED.citation_count,
            synced_at           = now()
        RETURNING *;
        """,
        (
            paper.get("openalex_id"), paper.get("semantic_scholar_id"),
            paper.get("doi"), paper.get("title", ""), paper.get("abstract"),
            paper.get("publication_year"), paper.get("venue"),
            paper.get("citation_count", 0), paper.get("tldr"),
            paper.get("influence_score"), paper.get("source_api", "openalex"),
            paper.get("open_access_url"),
            json.dumps(paper.get("payload")) if paper.get("payload") else None,
        ),
        returning=True,
    )


# =============================================================================
# Authors
# =============================================================================

def get_authors_for_paper(paper_id: str) -> list[dict]:
    return run_query(
        """
        SELECT a.*, pa.position
        FROM authors a
        JOIN paper_authors pa ON pa.author_id = a.author_id
        WHERE pa.paper_id = %s
        ORDER BY pa.position;
        """,
        (paper_id,),
    )


# =============================================================================
# Vector search
# =============================================================================

def semantic_search_papers(query_embedding: list[float], top_k: int = 10) -> list[dict]:
    """Cosine similarity search across paper_embeddings. Returns papers with similarity score."""
    embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"
    return run_query(
        f"""
        SELECT
            p.*,
            pe.chunk_text,
            pe.chunk_index,
            pe.section_name,
            1 - (pe.embedding <=> %s::vector({EMBEDDING_DIMENSION})) AS similarity
        FROM paper_embeddings pe
        JOIN papers p ON p.paper_id = pe.paper_id
        ORDER BY pe.embedding <=> %s::vector({EMBEDDING_DIMENSION})
        LIMIT %s;
        """,
        (embedding_str, embedding_str, top_k),
    )


# =============================================================================
# Usage counters (Phase 3.2)
# =============================================================================

def increment_usage(scope: str, scope_id: str, metric: str) -> int:
    """
    Consume one unit and return the new total, in ONE statement.

    Increment-and-report together rather than SELECT-then-UPDATE: two concurrent
    requests reading the same count would both see room and both proceed. Letting
    the database arbitrate is what makes the limit a limit.
    """
    row = run_write(
        """
        INSERT INTO usage_counters (scope, scope_id, metric, day, count)
        VALUES (%s, %s, %s, CURRENT_DATE, 1)
        ON CONFLICT (scope, scope_id, metric, day)
        DO UPDATE SET count = usage_counters.count + 1
        RETURNING count;
        """,
        (scope, scope_id, metric),
        returning=True,
    )
    return int(row["count"]) if row else 1


def get_usage_counts(scope: str, scope_id: str) -> dict[str, int]:
    """Today's consumption per metric — read-only, consumes nothing."""
    rows = run_query(
        """
        SELECT metric, count FROM usage_counters
         WHERE scope = %s AND scope_id = %s AND day = CURRENT_DATE;
        """,
        (scope, scope_id),
    )
    return {row["metric"]: row["count"] for row in rows}


# =============================================================================
# Collections
# =============================================================================

def get_curated_collections() -> list[dict]:
    """
    The read-only demo collections, shown to everyone including signed-out
    visitors. Owned by the system account, so collections.user_id stays NOT NULL.
    """
    return run_query(
        """
        SELECT c.*, COUNT(cp.paper_id) AS paper_count
        FROM collections c
        LEFT JOIN collection_papers cp ON cp.collection_id = c.collection_id
        WHERE c.is_curated
        GROUP BY c.collection_id
        ORDER BY c.created_at;
        """
    )


def get_curated_collection(collection_id: str) -> dict | None:
    rows = run_query(
        "SELECT * FROM collections WHERE collection_id = %s AND is_curated;",
        (collection_id,),
    )
    return rows[0] if rows else None


def create_collection(user_id: str, name: str, description: str | None = None) -> dict:
    return run_write(
        "INSERT INTO collections (user_id, name, description) VALUES (%s, %s, %s) RETURNING *;",
        (user_id, name, description),
        returning=True,
    )


def get_collections(user_id: str) -> list[dict]:
    return run_query(
        """
        SELECT c.*, COUNT(cp.paper_id) AS paper_count
        FROM collections c
        LEFT JOIN collection_papers cp ON cp.collection_id = c.collection_id
        WHERE c.user_id = %s
        GROUP BY c.collection_id
        ORDER BY c.created_at DESC;
        """,
        (user_id,),
    )


def get_collection(collection_id: str, user_id: str) -> dict | None:
    rows = run_query(
        "SELECT * FROM collections WHERE collection_id = %s AND user_id = %s;",
        (collection_id, user_id),
    )
    return rows[0] if rows else None


def get_collection_papers(collection_id: str) -> list[dict]:
    return run_query(
        """
        SELECT p.*, cp.sequence_order, cp.added_at,
               rp.status AS reading_status
        FROM papers p
        JOIN collection_papers cp ON cp.paper_id = p.paper_id
        LEFT JOIN reading_progress rp ON rp.paper_id = p.paper_id
            AND rp.user_id = (SELECT user_id FROM collections WHERE collection_id = %s)
        WHERE cp.collection_id = %s
        ORDER BY cp.sequence_order;
        """,
        (collection_id, collection_id),
    )


def add_paper_to_collection(collection_id: str, paper_id: str, sequence_order: int = 0) -> None:
    run_write(
        """
        INSERT INTO collection_papers (collection_id, paper_id, sequence_order)
        VALUES (%s, %s, %s)
        ON CONFLICT (collection_id, paper_id) DO NOTHING;
        """,
        (collection_id, paper_id, sequence_order),
    )


def append_paper_to_collection(collection_id: str, paper_id: str) -> int:
    """
    Add a paper at the end of the collection, computing the next position in SQL.

    The service used to fetch every paper in the collection - a join across papers
    and reading_progress - purely to take max(sequence_order) in Python. That is a
    whole result set crossing the wire to produce one integer, and it races with a
    concurrent add.

    ON CONFLICT DO UPDATE with a no-op assignment rather than DO NOTHING, so the
    statement always returns a row: re-adding a paper reports the position it
    already occupies instead of silently returning nothing.
    """
    row = run_write(
        """
        INSERT INTO collection_papers (collection_id, paper_id, sequence_order)
        SELECT %(cid)s, %(pid)s, COALESCE(MAX(sequence_order), 0) + 1
          FROM collection_papers
         WHERE collection_id = %(cid)s
        ON CONFLICT (collection_id, paper_id) DO UPDATE
            SET sequence_order = collection_papers.sequence_order
        RETURNING sequence_order;
        """,
        {"cid": collection_id, "pid": paper_id},
        returning=True,
    )
    return int(row["sequence_order"]) if row else 1


def remove_paper_from_collection(collection_id: str, paper_id: str) -> int:
    return run_write(
        "DELETE FROM collection_papers WHERE collection_id = %s AND paper_id = %s;",
        (collection_id, paper_id),
    )


def update_paper_sequence(collection_id: str, paper_id: str, sequence_order: int) -> None:
    run_write(
        "UPDATE collection_papers SET sequence_order = %s WHERE collection_id = %s AND paper_id = %s;",
        (sequence_order, collection_id, paper_id),
    )


def update_paper_sequences(collection_id: str, ordered_paper_ids: list[str]) -> int:
    """
    Renumber a whole collection in ONE statement. Returns rows updated.

    Reordering and reading-plan generation both used to issue one UPDATE per paper,
    so a 20-paper collection was 20 sequential round trips to a remote database -
    and a partial failure left the collection half-renumbered.

    `unnest` of two parallel arrays keeps this a single prepared statement with two
    parameters, rather than SQL built by string concatenation.
    """
    if not ordered_paper_ids:
        return 0
    return run_write(
        """
        UPDATE collection_papers cp
           SET sequence_order = v.seq
          FROM unnest(%(ids)s::uuid[], %(seqs)s::int[]) AS v(paper_id, seq)
         WHERE cp.collection_id = %(cid)s
           AND cp.paper_id = v.paper_id;
        """,
        {
            "cid": collection_id,
            "ids": [str(pid) for pid in ordered_paper_ids],
            "seqs": list(range(1, len(ordered_paper_ids) + 1)),
        },
    )


# =============================================================================
# Reading progress
# =============================================================================

def upsert_reading_progress(user_id: str, paper_id: str, status: str) -> dict:
    return run_write(
        """
        INSERT INTO reading_progress (user_id, paper_id, status, updated_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (user_id, paper_id) DO UPDATE SET
            status     = EXCLUDED.status,
            updated_at = now()
        RETURNING *;
        """,
        (user_id, paper_id, status),
        returning=True,
    )


def get_user_progress(user_id: str, limit: int | None = None) -> list[dict]:
    """
    Progress rows this user has actually set, newest first.

    This is *activity* - it answers "what has this user been doing" for the home
    page. It deliberately does NOT include untouched papers; see
    `get_reading_board` for the Kanban view.
    """
    sql = """
        SELECT rp.*, p.title, p.publication_year, p.venue,
               p.tldr, p.citation_count, p.open_access_url
        FROM reading_progress rp
        JOIN papers p ON p.paper_id = rp.paper_id
        WHERE rp.user_id = %s
        ORDER BY rp.updated_at DESC
    """
    params: list = [user_id]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    return run_query(sql + ";", tuple(params))


def get_reading_board(user_id: str) -> list[dict]:
    """
    Everything that belongs on the Kanban board: papers in this user's collections,
    plus any paper they have explicitly given a status.

    The board used to inner-join `reading_progress`, so a paper appeared only after
    its status had already been set somewhere else. That left "Not Started"
    permanently empty and the board with nothing to drag - the page told you to drag
    cards between columns while showing none.

    Library membership is `collection_papers`, matching the definition already agreed
    for the agent: `papers` is discovery history, a collection is a deliberate choice.
    A collected paper with no progress row reads as `not_started`, which is true.
    """
    return run_query(
        """
        WITH library AS (
            SELECT DISTINCT cp.paper_id
              FROM collection_papers cp
              JOIN collections c ON c.collection_id = cp.collection_id
             WHERE c.user_id = %(uid)s
        )
        SELECT p.paper_id,
               rp.progress_id,
               %(uid)s::uuid                          AS user_id,
               COALESCE(rp.status, 'not_started')     AS status,
               rp.updated_at,
               (rp.progress_id IS NOT NULL)           AS has_progress,
               p.title, p.publication_year, p.venue,
               p.tldr, p.citation_count, p.open_access_url
          FROM papers p
          LEFT JOIN reading_progress rp
                 ON rp.paper_id = p.paper_id AND rp.user_id = %(uid)s
         WHERE rp.progress_id IS NOT NULL
            OR p.paper_id IN (SELECT paper_id FROM library)
         -- Touched papers first, newest activity at the top; untouched collected
         -- papers follow in a stable order rather than an arbitrary one.
         ORDER BY rp.updated_at DESC NULLS LAST, p.title ASC;
        """,
        {"uid": user_id},
    )


def get_progress_stats(user_id: str) -> dict:
    rows = run_query(
        "SELECT status, COUNT(*) AS count FROM reading_progress WHERE user_id = %s GROUP BY status;",
        (user_id,),
    )
    return {row["status"]: row["count"] for row in rows}


def get_progress_for_paper(user_id: str, paper_id: str) -> dict | None:
    """Single progress row for the paper detail page (None → not started)."""
    rows = run_query(
        "SELECT * FROM reading_progress WHERE user_id = %s AND paper_id = %s;",
        (user_id, paper_id),
    )
    return rows[0] if rows else None


# =============================================================================
# Notes
# =============================================================================

def save_note(user_id: str, paper_id: str, note_text: str) -> dict:
    return run_write(
        "INSERT INTO notes (user_id, paper_id, note_text) VALUES (%s, %s, %s) RETURNING *;",
        (user_id, paper_id, note_text),
        returning=True,
    )


def get_notes_for_paper(user_id: str, paper_id: str) -> list[dict]:
    return run_query(
        "SELECT * FROM notes WHERE user_id = %s AND paper_id = %s ORDER BY created_at DESC;",
        (user_id, paper_id),
    )


# =============================================================================
# Learning goals
# =============================================================================

def create_learning_goal(user_id: str, title: str, description: str | None = None) -> dict:
    return run_write(
        "INSERT INTO learning_goals (user_id, title, description) VALUES (%s, %s, %s) RETURNING *;",
        (user_id, title, description),
        returning=True,
    )


def get_learning_goals(user_id: str, status: str | None = None,
                      limit: int | None = None) -> list[dict]:
    # `limit` exists so the home page can ask for 5 rather than fetching every goal
    # and slicing in Python - the rows still cross the wire either way otherwise.
    clauses = ["user_id = %s"]
    params: list = [user_id]
    if status:
        clauses.append("status = %s")
        params.append(status)

    sql = f"SELECT * FROM learning_goals WHERE {' AND '.join(clauses)} ORDER BY created_at DESC"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    return run_query(sql + ";", tuple(params))


def get_learning_goal(goal_id: str, user_id: str) -> dict | None:
    rows = run_query(
        "SELECT * FROM learning_goals WHERE goal_id = %s AND user_id = %s;",
        (goal_id, user_id),
    )
    return rows[0] if rows else None


def update_goal_status(goal_id: str, user_id: str, status: str) -> dict | None:
    return run_write(
        "UPDATE learning_goals SET status = %s, updated_at = now() WHERE goal_id = %s AND user_id = %s RETURNING *;",
        (status, goal_id, user_id),
        returning=True,
    )


# =============================================================================
# Topic context
# =============================================================================

def get_topic_context(topic_name: str) -> dict | None:
    rows = run_query(
        "SELECT * FROM topic_context WHERE topic_name ILIKE %s;",
        (topic_name,),
    )
    return rows[0] if rows else None
