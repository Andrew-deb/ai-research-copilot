"""Atomic Lakebase transitions for agent runs. No Flask state in this layer."""

from repositories.lakebase import get_connection


def _expire(cur) -> None:
    cur.execute("""UPDATE agent_runs SET state='failed',updated_at=now()
        WHERE state='awaiting_approval' AND updated_at<now()-interval '15 minutes'""")
    # A process may die mid-turn. The provider deadline is far shorter than ten
    # minutes; do not leave a permanently active Stop button after a crash.
    cur.execute("""UPDATE agent_runs SET state = 'failed', updated_at = now()
                   WHERE state IN ('running', 'stop_requested')
                     AND updated_at < now() - interval '10 minutes'""")


def create(run_id: str, owner: tuple[str, str]) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO agent_runs (run_id, owner_kind, owner_id)
                       VALUES (%s, %s, %s)""", (run_id, *owner))


def status(run_id: str, owner: tuple[str, str]) -> str | None:
    with get_connection() as conn, conn.cursor() as cur:
        _expire(cur)
        cur.execute("""SELECT state FROM agent_runs
                       WHERE run_id = %s AND owner_kind = %s AND owner_id = %s""",
                    (run_id, *owner))
        row = cur.fetchone()
        return row[0] if row else None


def request_stop(run_id: str, owner: tuple[str, str]) -> str | None:
    with get_connection() as conn, conn.cursor() as cur:
        _expire(cur)
        cur.execute("""UPDATE agent_runs SET state='stopped',updated_at=now()
            WHERE run_id=%s AND owner_kind=%s AND owner_id=%s AND state='awaiting_approval' RETURNING state""",(run_id,*owner))
        if cur.fetchone():return 'stopped'
        cur.execute("""UPDATE agent_runs SET state = 'stop_requested', updated_at = now()
                       WHERE run_id = %s AND owner_kind = %s AND owner_id = %s
                         AND state = 'running' RETURNING state""", (run_id, *owner))
        if cur.fetchone():
            return "stop_requested"
        cur.execute("""SELECT state FROM agent_runs
                       WHERE run_id = %s AND owner_kind = %s AND owner_id = %s""",
                    (run_id, *owner))
        row = cur.fetchone()
        return row[0] if row else None


def finish(run_id: str, owner: tuple[str, str], *, failed: bool = False) -> str:
    """A requested stop wins the race with completion, in one SQL statement."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE agent_runs
                       SET state = CASE WHEN state = 'stop_requested' THEN 'stopped'
                                        WHEN %s THEN 'failed' ELSE 'completed' END,
                           updated_at = now()
                       WHERE run_id = %s AND owner_kind = %s AND owner_id = %s
                         AND state IN ('running', 'stop_requested')
                       RETURNING state""", (failed, run_id, *owner))
        row = cur.fetchone()
        return row[0] if row else "failed"
