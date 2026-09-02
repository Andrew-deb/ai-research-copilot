"""
mcp_server/repositories/lakebase.py — Database access layer for the MCP server.

SRP: All SQL and pgvector queries for the MCP server live here.
     No Flask, no HTTP calls, no business logic.

Connection follows the Day 1/2/3 pattern:
  Secret scope → base64 decode → psycopg2 URL → context manager connection.

All write operations use INSERT ... ON CONFLICT ... DO UPDATE (upsert)
so the Spark pipeline and API syncs are safe to re-run without duplicates.
"""

import base64
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
# One TLS connect per query adds a full handshake to every tool call. A
# per-process pool connects once and reuses. Mirrors dashboard/repositories.

_POOL: psycopg2.pool.ThreadedConnectionPool | None = None
_POOL_LOCK = threading.Lock()
_POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
_POOL_MAX = int(os.getenv("DB_POOL_MAX", "6"))


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                if not DATABASE_URL:
                    raise RuntimeError("DATABASE_URL is not configured. Check your .env or secret scope.")
                _POOL = psycopg2.pool.ThreadedConnectionPool(
                    _POOL_MIN, _POOL_MAX, dsn=DATABASE_URL,
                    keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
                )
                logger.info("Lakebase connection pool ready (min=%d max=%d)", _POOL_MIN, _POOL_MAX)
    return _POOL


@contextmanager
def get_connection() -> Generator[psycopg2.extensions.connection, None, None]:
    """Yield a pooled connection; commit on success, roll back on error, return to pool."""
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
    """Close every pooled connection — call on process shutdown."""
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
    """
    Execute an INSERT / UPDATE / DELETE.
    If returning=True, returns the first row of RETURNING clause as a dict.
    Otherwise returns rowcount.
    """
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

def get_or_create_user(email: str, display_name: str | None = None) -> dict:
    """Return existing user by email, or create one if not found."""
    rows = run_query("SELECT * FROM users WHERE email = %s;", (email,))
    if rows:
        return rows[0]
    return run_write(
        "INSERT INTO users (email, display_name) VALUES (%s, %s) RETURNING *;",
        (email, display_name),
        returning=True,
    )


def get_user_by_email(email: str) -> dict | None:
    rows = run_query("SELECT * FROM users WHERE email = %s;", (email,))
    return rows[0] if rows else None


# =============================================================================
# Papers
# =============================================================================

def upsert_paper(paper: dict) -> dict:
    """
    Upsert a single paper by openalex_id. If it exists, update enrichment fields.
    Returns the full paper row after upsert.
    """
    return run_write(
        """
        INSERT INTO papers (
            openalex_id, semantic_scholar_id, doi, title, abstract,
            publication_year, venue, citation_count, tldr, influence_score,
            source_api, open_access_url, payload, synced_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (openalex_id) DO UPDATE SET
            semantic_scholar_id = COALESCE(EXCLUDED.semantic_scholar_id, papers.semantic_scholar_id),
            doi                 = COALESCE(EXCLUDED.doi, papers.doi),
            title               = EXCLUDED.title,
            abstract            = COALESCE(EXCLUDED.abstract, papers.abstract),
            citation_count      = EXCLUDED.citation_count,
            tldr                = COALESCE(EXCLUDED.tldr, papers.tldr),
            influence_score     = COALESCE(EXCLUDED.influence_score, papers.influence_score),
            open_access_url     = COALESCE(EXCLUDED.open_access_url, papers.open_access_url),
            payload             = EXCLUDED.payload,
            synced_at           = now()
        RETURNING *;
        """,
        (
            paper.get("openalex_id"),
            paper.get("semantic_scholar_id"),
            paper.get("doi"),
            paper.get("title", ""),
            paper.get("abstract"),
            paper.get("publication_year"),
            paper.get("venue"),
            paper.get("citation_count", 0),
            paper.get("tldr"),
            paper.get("influence_score"),
            paper.get("source_api", "openalex"),
            paper.get("open_access_url"),
            json.dumps(paper.get("payload")) if paper.get("payload") else None,
        ),
        returning=True,
    )


def upsert_papers(papers: list[dict]) -> list[dict]:
    """Batch upsert a list of papers. Returns upserted rows."""
    return [upsert_paper(p) for p in papers]


def enrich_paper_s2(paper_id: str, s2_data: dict) -> dict | None:
    """Update a paper row with Semantic Scholar enrichment fields."""
    return run_write(
        """
        UPDATE papers
        SET semantic_scholar_id = COALESCE(%s, semantic_scholar_id),
            tldr                = COALESCE(%s, tldr),
            influence_score     = COALESCE(%s, influence_score),
            synced_at           = now()
        WHERE paper_id = %s
        RETURNING *;
        """,
        (
            s2_data.get("semantic_scholar_id"),
            s2_data.get("tldr"),
            s2_data.get("influence_score"),
            paper_id,
        ),
        returning=True,
    )


def get_paper(paper_id: str) -> dict | None:
    rows = run_query("SELECT * FROM papers WHERE paper_id = %s;", (paper_id,))
    return rows[0] if rows else None


def get_paper_by_openalex_id(openalex_id: str) -> dict | None:
    rows = run_query("SELECT * FROM papers WHERE openalex_id = %s;", (openalex_id,))
    return rows[0] if rows else None


def get_paper_by_doi(doi: str) -> dict | None:
    rows = run_query("SELECT * FROM papers WHERE doi = %s;", (doi,))
    return rows[0] if rows else None


def get_papers_by_ids(paper_ids: list[str]) -> list[dict]:
    if not paper_ids:
        return []
    return run_query(
        "SELECT * FROM papers WHERE paper_id = ANY(%s::uuid[]);",
        (paper_ids,),
    )


def get_papers_without_embeddings(limit: int = 100) -> list[dict]:
    """Return papers that have no entry in paper_embeddings — for the Spark pipeline."""
    return run_query(
        """
        SELECT p.* FROM papers p
        LEFT JOIN paper_embeddings pe ON pe.paper_id = p.paper_id
        WHERE pe.id IS NULL AND p.abstract IS NOT NULL
        LIMIT %s;
        """,
        (limit,),
    )


def search_papers_by_text(query: str, limit: int = 10) -> list[dict]:
    """Full-text keyword search using Postgres ILIKE (fallback to vector search when available)."""
    pattern = f"%{query}%"
    return run_query(
        """
        SELECT * FROM papers
        WHERE title ILIKE %s OR abstract ILIKE %s
        ORDER BY citation_count DESC
        LIMIT %s;
        """,
        (pattern, pattern, limit),
    )


# =============================================================================
# Authors
# =============================================================================

def upsert_author(author: dict) -> dict:
    """Upsert an author by openalex_id. Returns the full author row."""
    return run_write(
        """
        INSERT INTO authors (openalex_id, s2_id, display_name, institution)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (openalex_id) DO UPDATE SET
            s2_id        = COALESCE(EXCLUDED.s2_id, authors.s2_id),
            display_name = EXCLUDED.display_name,
            institution  = COALESCE(EXCLUDED.institution, authors.institution),
            synced_at    = now()
        RETURNING *;
        """,
        (
            author.get("openalex_id"),
            author.get("s2_id"),
            author.get("display_name", ""),
            author.get("institution"),
        ),
        returning=True,
    )


def upsert_paper_authors(paper_id: str, authors: list[dict]) -> None:
    """Insert paper-author relationships, ignoring duplicates."""
    for author_data in authors:
        author_row = upsert_author(author_data)
        run_write(
            """
            INSERT INTO paper_authors (paper_id, author_id, position)
            VALUES (%s, %s, %s)
            ON CONFLICT (paper_id, author_id) DO NOTHING;
            """,
            (paper_id, author_row["author_id"], author_data.get("position", 0)),
        )


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
# Vector search (pgvector)
# =============================================================================

def semantic_search_papers(query_embedding: list[float], top_k: int = 10, user_id: str | None = None) -> list[dict]:
    """
    Find the top_k paper chunks most similar to query_embedding using cosine distance.
    Returns paper metadata joined with the matching chunk text and similarity score.

    The <=> operator is pgvector's cosine distance (0 = identical, 2 = opposite).
    We convert to similarity (1 - distance) so higher = better match.
    """
    embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"
    return run_query(
        f"""
        SELECT
            p.*,
            pe.chunk_text,
            pe.chunk_index,
            1 - (pe.embedding <=> %s::vector({EMBEDDING_DIMENSION})) AS similarity
        FROM paper_embeddings pe
        JOIN papers p ON p.paper_id = pe.paper_id
        ORDER BY pe.embedding <=> %s::vector({EMBEDDING_DIMENSION})
        LIMIT %s;
        """,
        (embedding_str, embedding_str, top_k),
    )


def semantic_search_notes(query_embedding: list[float], user_id: str, top_k: int = 5) -> list[dict]:
    """Find a user's notes most similar to query_embedding."""
    embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"
    return run_query(
        f"""
        SELECT
            n.*,
            ne.chunk_text,
            1 - (ne.embedding <=> %s::vector({EMBEDDING_DIMENSION})) AS similarity
        FROM note_embeddings ne
        JOIN notes n ON n.note_id = ne.note_id
        WHERE n.user_id = %s
        ORDER BY ne.embedding <=> %s::vector({EMBEDDING_DIMENSION})
        LIMIT %s;
        """,
        (embedding_str, user_id, embedding_str, top_k),
    )


# =============================================================================
# Collections
# =============================================================================

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


def add_paper_to_collection(collection_id: str, paper_id: str, sequence_order: int = 0) -> None:
    """Add a paper to a collection, ignoring if already present."""
    run_write(
        """
        INSERT INTO collection_papers (collection_id, paper_id, sequence_order)
        VALUES (%s, %s, %s)
        ON CONFLICT (collection_id, paper_id) DO NOTHING;
        """,
        (collection_id, paper_id, sequence_order),
    )


def remove_paper_from_collection(collection_id: str, paper_id: str) -> int:
    return run_write(
        "DELETE FROM collection_papers WHERE collection_id = %s AND paper_id = %s;",
        (collection_id, paper_id),
    )


def get_collection_papers(collection_id: str) -> list[dict]:
    """Return papers in a collection ordered by sequence_order."""
    return run_query(
        """
        SELECT p.*, cp.sequence_order, cp.added_at
        FROM papers p
        JOIN collection_papers cp ON cp.paper_id = p.paper_id
        WHERE cp.collection_id = %s
        ORDER BY cp.sequence_order;
        """,
        (collection_id,),
    )


def update_paper_sequence(collection_id: str, paper_id: str, sequence_order: int) -> None:
    run_write(
        "UPDATE collection_papers SET sequence_order = %s WHERE collection_id = %s AND paper_id = %s;",
        (sequence_order, collection_id, paper_id),
    )


# =============================================================================
# Reading progress
# =============================================================================

def upsert_reading_progress(user_id: str, paper_id: str, status: str) -> dict:
    """
    Set reading status for a paper. Uses ON CONFLICT upsert since there is
    exactly one progress record per user-paper pair (UNIQUE constraint).
    """
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


def get_user_progress(user_id: str) -> list[dict]:
    return run_query(
        """
        SELECT rp.*, p.title, p.publication_year, p.venue
        FROM reading_progress rp
        JOIN papers p ON p.paper_id = rp.paper_id
        WHERE rp.user_id = %s
        ORDER BY rp.updated_at DESC;
        """,
        (user_id,),
    )


def get_progress_for_paper(user_id: str, paper_id: str) -> dict | None:
    rows = run_query(
        "SELECT * FROM reading_progress WHERE user_id = %s AND paper_id = %s;",
        (user_id, paper_id),
    )
    return rows[0] if rows else None


def get_progress_stats(user_id: str) -> dict:
    """Return counts per status for the dashboard home page."""
    rows = run_query(
        """
        SELECT status, COUNT(*) AS count
        FROM reading_progress
        WHERE user_id = %s
        GROUP BY status;
        """,
        (user_id,),
    )
    return {row["status"]: row["count"] for row in rows}


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


def get_user_notes(user_id: str, limit: int = 50) -> list[dict]:
    return run_query(
        """
        SELECT n.*, p.title AS paper_title
        FROM notes n
        JOIN papers p ON p.paper_id = n.paper_id
        WHERE n.user_id = %s
        ORDER BY n.created_at DESC
        LIMIT %s;
        """,
        (user_id, limit),
    )


def get_notes_without_embeddings(limit: int = 100) -> list[dict]:
    """Return notes with no embedding entry — for the Spark pipeline."""
    return run_query(
        """
        SELECT n.* FROM notes n
        LEFT JOIN note_embeddings ne ON ne.note_id = n.note_id
        WHERE ne.id IS NULL
        LIMIT %s;
        """,
        (limit,),
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


def get_learning_goals(user_id: str, status: str | None = None) -> list[dict]:
    if status:
        return run_query(
            "SELECT * FROM learning_goals WHERE user_id = %s AND status = %s ORDER BY created_at DESC;",
            (user_id, status),
        )
    return run_query(
        "SELECT * FROM learning_goals WHERE user_id = %s ORDER BY created_at DESC;",
        (user_id,),
    )


def get_learning_goal(goal_id: str, user_id: str) -> dict | None:
    rows = run_query(
        "SELECT * FROM learning_goals WHERE goal_id = %s AND user_id = %s;",
        (goal_id, user_id),
    )
    return rows[0] if rows else None


def update_goal_status(goal_id: str, user_id: str, status: str) -> dict | None:
    return run_write(
        """
        UPDATE learning_goals
        SET status = %s, updated_at = now()
        WHERE goal_id = %s AND user_id = %s
        RETURNING *;
        """,
        (status, goal_id, user_id),
        returning=True,
    )


# =============================================================================
# Topic context
# =============================================================================

def upsert_topic_context(topic_name: str, summary: str | None, wiki_url: str | None) -> dict:
    return run_write(
        """
        INSERT INTO topic_context (topic_name, wikipedia_summary, wiki_url, synced_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (topic_name) DO UPDATE SET
            wikipedia_summary = COALESCE(EXCLUDED.wikipedia_summary, topic_context.wikipedia_summary),
            wiki_url          = COALESCE(EXCLUDED.wiki_url, topic_context.wiki_url),
            synced_at         = now()
        RETURNING *;
        """,
        (topic_name, summary, wiki_url),
        returning=True,
    )


def get_topic_context(topic_name: str) -> dict | None:
    rows = run_query(
        "SELECT * FROM topic_context WHERE topic_name ILIKE %s;",
        (topic_name,),
    )
    return rows[0] if rows else None


# =============================================================================
# MCP telemetry
# =============================================================================

def write_trace(trace: dict) -> None:
    """Persist a single MCP tool call trace. Called by TraceMiddleware only."""
    run_write(
        """
        INSERT INTO mcp_traces (
            request_id, session_id, started_at, finished_at, duration_ms,
            method, path, status_code, user_email, mcp_session_id,
            tool_name, session_result, error_message
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        """,
        (
            trace.get("request_id"),
            trace.get("session_id"),
            trace.get("started_at"),
            trace.get("finished_at"),
            trace.get("duration_ms"),
            trace.get("method"),
            trace.get("path"),
            trace.get("status_code", 200),
            trace.get("user_email"),
            trace.get("mcp_session_id"),
            trace.get("tool_name"),
            json.dumps(trace.get("session_result")) if trace.get("session_result") else None,
            trace.get("error_message"),
        ),
    )
