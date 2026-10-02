"""Durable, owner-scoped approval checkpoints and revocable operation grants."""
import json
import uuid
from repositories.lakebase import get_connection


def has_grant(user_id, scope_key):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT 1 FROM agent_action_grants WHERE user_id=%s AND scope_key=%s', (user_id, scope_key))
        return bool(cur.fetchone())


def pause(run_id, user_id, proposal, job):
    approval_id = str(uuid.uuid4())
    job_json=json.dumps(job,default=str)
    if len(job_json.encode())>500000:raise ValueError('Approval checkpoint is too large.')
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM agent_action_approvals WHERE expires_at<now()-interval '1 day'")
        cur.execute("""UPDATE agent_runs SET state='awaiting_approval' , updated_at=now()
            WHERE run_id=%s AND owner_kind='user' AND owner_id=%s AND state='running' RETURNING run_id""", (run_id,user_id))
        if not cur.fetchone():
            return None
        cur.execute('''INSERT INTO agent_action_approvals(approval_id,run_id,user_id,proposal,job)
            VALUES(%s,%s,%s,%s::jsonb,%s::jsonb)''',
            (approval_id,run_id,user_id,json.dumps(proposal),job_json))
    return {**proposal,'approval_id':approval_id,'run_id':run_id}


def pending(run_id, user_id):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT a.approval_id,a.proposal FROM agent_action_approvals a
            JOIN agent_runs r ON r.run_id=a.run_id
            WHERE a.run_id=%s AND a.user_id=%s AND a.state='pending' AND a.expires_at>now()
              AND r.owner_kind='user' AND r.owner_id=%s AND r.state='awaiting_approval'
            ORDER BY a.created_at DESC LIMIT 1""", (run_id,user_id,user_id))
        row=cur.fetchone()
        return {**row[1],'approval_id':str(row[0]),'run_id':run_id} if row else None


def claim(approval_id, user_id, decision):
    """Run lock precedes proposal lock; a stop or second click cannot resume a write."""
    if decision not in ('once', 'always', 'deny'):
        raise ValueError('Invalid approval decision.')
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT r.run_id FROM agent_runs r JOIN agent_action_approvals a ON a.run_id=r.run_id
            WHERE a.approval_id=%s AND a.user_id=%s AND a.state='pending' AND a.expires_at>now()
              AND r.owner_kind='user' AND r.owner_id=%s AND r.state='awaiting_approval'
            FOR UPDATE OF r""", (approval_id,user_id,user_id))
        run=cur.fetchone()
        if not run:return None
        cur.execute("""UPDATE agent_action_approvals SET state='claimed' WHERE approval_id=%s
            AND user_id=%s AND state='pending' AND expires_at>now() RETURNING proposal,job""", (approval_id,user_id))
        row=cur.fetchone()
        if not row:return None
        proposal,job=row
        cur.execute("UPDATE agent_runs SET state='running',updated_at=now() WHERE run_id=%s", (run[0],))
        if decision=='always':
            cur.execute('''INSERT INTO agent_action_grants(grant_id,user_id,scope_key,label)
                VALUES(%s,%s,%s,%s) ON CONFLICT(user_id,scope_key) DO NOTHING''',
                (str(uuid.uuid4()),user_id,proposal['scope_key'],proposal['scope_label']))
    return {'run_id':str(run[0]),'proposal':proposal,'job':job,'decision':decision}


def grants(user_id):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT grant_id,label,created_at FROM agent_action_grants WHERE user_id=%s ORDER BY created_at DESC',(user_id,))
        return [{'grant_id':str(r[0]),'label':r[1],'created_at':str(r[2])} for r in cur.fetchall()]


def revoke(user_id, grant_id):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('DELETE FROM agent_action_grants WHERE user_id=%s AND grant_id=%s RETURNING grant_id',(user_id,grant_id))
        return bool(cur.fetchone())
