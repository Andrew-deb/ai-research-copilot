"""Owned conversation trees and atomic branch updates.

All SQL for the version graph stays here. A conversation lock serializes seq
allocation and head selection; the agent runs outside that lock.
"""

import json

import psycopg2.extras

from repositories.lakebase import get_connection


def record_search(owner: str, query: str, mode: str) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO user_search_history (user_id, query, mode)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (user_id, query, mode)
                       DO UPDATE SET updated_at = now()""", (owner, query, mode))


def searches(owner: str, *, search: str = "", limit: int = 12,
             offset: int = 0) -> list[dict]:
    with get_connection() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT query, mode, updated_at FROM user_search_history
                        WHERE user_id = %s AND query ILIKE '%%' || %s || '%%'
                        ORDER BY updated_at DESC LIMIT %s OFFSET %s""",
                    (owner, search, limit, offset))
        return [dict(row) for row in cur.fetchall()]


def messages(owner: str, conversation_id: str) -> list[dict] | None:
    with get_connection() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT 1 FROM conversations WHERE conversation_id = %s AND user_id = %s",
                    (conversation_id, owner))
        if not cur.fetchone():
            return None
        cur.execute("""SELECT message_id, parent_message_id, role, content, seq,
                              citations, sources, tool_calls, usage
                         FROM conversation_messages WHERE conversation_id = %s
                        ORDER BY seq""", (conversation_id,))
        return [dict(row) for row in cur.fetchall()]


def append(owner: str, conversation_id: str, question: str, result: dict,
           *, mode: str = "new", source_id: str | None = None,
           expected_head: str | None = None) -> bool | None:
    """Append a branch. Return whether it became the selected continuation.

    If another tab selected a different version while this agent ran, preserve
    this response as a sibling without replacing that tab's choice.
    """
    with get_connection() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT selected_message_id FROM conversations
                        WHERE conversation_id = %s AND user_id = %s FOR UPDATE""",
                    (conversation_id, owner))
        conversation = cur.fetchone()
        if not conversation:
            return None
        head = conversation["selected_message_id"]
        parent = expected_head if mode == "new" else head
        if mode != "new":
            cur.execute("""SELECT message_id, parent_message_id, role
                             FROM conversation_messages
                            WHERE conversation_id = %s AND message_id = %s""",
                        (conversation_id, source_id))
            source = cur.fetchone()
            if not source or source["role"] != "user":
                return None
            parent = source["message_id"] if mode == "regenerate" else source["parent_message_id"]

        cur.execute("SELECT coalesce(max(seq), 0) AS last_seq FROM conversation_messages WHERE conversation_id = %s",
                    (conversation_id,))
        seq = cur.fetchone()["last_seq"]
        if mode != "regenerate":
            cur.execute("""INSERT INTO conversation_messages
                            (conversation_id, seq, parent_message_id, role, content)
                           VALUES (%s, %s, %s, 'user', %s) RETURNING message_id""",
                        (conversation_id, seq + 1, parent, question))
            parent = cur.fetchone()["message_id"]
            seq += 1
        cur.execute("""INSERT INTO conversation_messages
                        (conversation_id, seq, parent_message_id, role, content,
                         citations, sources, tool_calls, usage)
                       VALUES (%s, %s, %s, 'assistant', %s, %s::jsonb, %s::jsonb,
                               %s::jsonb, %s::jsonb) RETURNING message_id""",
                    (conversation_id, seq + 1, parent, result.get("answer"),
                     json.dumps(result.get("citations") or []),
                     json.dumps(result.get("sources") or []),
                     json.dumps(result.get("tool_calls") or []),
                     json.dumps(result.get("usage") or {})))
        answer_id = cur.fetchone()["message_id"]
        selected = str(head) == str(expected_head) if expected_head is not None else head is None
        if selected:
            cur.execute("""UPDATE conversations SET selected_message_id = %s,
                            updated_at = now() WHERE conversation_id = %s""",
                        (answer_id, conversation_id))
        return selected


def select(owner: str, conversation_id: str, message_id: str) -> bool:
    """Select an existing version and its latest descendant, under owner lock."""
    with get_connection() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT 1 FROM conversations WHERE conversation_id = %s
                        AND user_id = %s FOR UPDATE""", (conversation_id, owner))
        if not cur.fetchone():
            return False
        cur.execute("""WITH RECURSIVE descendants AS (
                         SELECT message_id, role, seq FROM conversation_messages
                          WHERE conversation_id = %s AND message_id = %s
                         UNION ALL
                         SELECT child.message_id, child.role, child.seq
                           FROM conversation_messages child
                           JOIN descendants d ON child.parent_message_id = d.message_id
                          WHERE child.conversation_id = %s
                       ) SELECT message_id FROM descendants
                          WHERE role = 'assistant' ORDER BY seq DESC LIMIT 1""",
                    (conversation_id, message_id, conversation_id))
        leaf = cur.fetchone()
        if not leaf:
            return False
        cur.execute("""UPDATE conversations SET selected_message_id = %s
                        WHERE conversation_id = %s""", (leaf["message_id"], conversation_id))
        return True
