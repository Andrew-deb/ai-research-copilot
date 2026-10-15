"""Owned attachment records, atomic capacity reservations and durable job leases."""

from uuid import uuid4
from psycopg2.extras import RealDictCursor
from exceptions import ValidationError
from repositories import lakebase


def _lock_document(cur, document_id, wait=True):
    function = "pg_advisory_xact_lock" if wait else "pg_try_advisory_xact_lock"
    cur.execute(
        f"SELECT {function}(hashtextextended(%s,0)) AS locked",
        ("private-upload-job:" + str(document_id),),
    )
    if wait:
        return True
    result = cur.fetchone()
    return result["locked"] if isinstance(result, dict) else result[0]


def reserve(owner, conversation_id, record, limits):
    with lakebase.get_connection() as conn, conn.cursor(
        cursor_factory=RealDictCursor
    ) as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('private-upload-capacity',0))"
        )
        cur.execute(
            "SELECT conversation_id FROM conversations WHERE conversation_id=%s AND user_id=%s FOR UPDATE",
            (conversation_id, owner),
        )
        if not cur.fetchone():
            raise ValidationError("That conversation is unavailable.")
        cur.execute(
            """SELECT COALESCE(sum(reserved_bytes),0) AS total,
            COALESCE(sum(reserved_bytes) FILTER (WHERE user_id=%s),0) AS personal,
            count(*) FILTER (WHERE user_id=%s AND conversation_id=%s AND status <> 'deleted') AS files
            FROM private_uploads""",
            (owner, owner, conversation_id),
        )
        used = cur.fetchone()
        needed = record["original_bytes"] + limits.max_artifact_bytes
        if (
            used["total"] + needed > limits.deployment_bytes
            or used["personal"] + needed > limits.per_user_bytes
        ):
            raise ValidationError(
                "Private upload storage is full. Delete unused files or try again later."
            )
        if used["files"] >= limits.per_conversation_files:
            raise ValidationError(
                "Keep at most three private files in this conversation. Delete an unused file first."
            )
        cur.execute(
            """INSERT INTO private_uploads (document_id,user_id,conversation_id,filename,media_type,
            storage_location_id,object_key,artifact_key,content_hash,original_bytes,reserved_bytes,status,expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'uploading',now()+(%s * interval '1 day')) RETURNING *""",
            (
                record["document_id"],
                owner,
                conversation_id,
                record["filename"],
                record["media_type"],
                record["storage_location_id"],
                record["object_key"],
                record["artifact_key"],
                record["content_hash"],
                record["original_bytes"],
                needed,
                limits.retention_days,
            ),
        )
        return dict(cur.fetchone())


def queue_extraction(owner, document_id):
    with lakebase.get_connection() as conn, conn.cursor() as cur:
        _lock_document(cur, document_id)
        cur.execute(
            "UPDATE private_uploads SET status='queued',upload_lease_until=NULL WHERE document_id=%s AND user_id=%s AND status='uploading' RETURNING document_id",
            (document_id, owner),
        )
        if not cur.fetchone():
            raise ValidationError("The upload is no longer available.")
        cur.execute(
            "INSERT INTO private_upload_jobs (document_id,action,status) VALUES (%s,'extract','queued')",
            (document_id,),
        )


def get_owned(owner, document_id):
    rows = lakebase.run_query(
        "SELECT *, expires_at <= now() AS expired FROM private_uploads WHERE document_id=%s AND user_id=%s",
        (document_id, owner),
    )
    return rows[0] if rows else None


def list_owned(owner, conversation_id):
    return lakebase.run_query(
        """SELECT *, expires_at <= now() AS expired FROM private_uploads
        WHERE user_id=%s AND conversation_id=%s ORDER BY created_at DESC LIMIT 50""",
        (owner, conversation_id),
    )


def _delete_job(cur, document_id):
    cur.execute(
        """INSERT INTO private_upload_jobs (document_id,action,status) VALUES (%s,'delete','queued')
        ON CONFLICT (document_id) DO UPDATE SET action='delete',status='queued',attempts=0,
        lease_until=CASE WHEN private_upload_jobs.lease_until > now() THEN private_upload_jobs.lease_until ELSE NULL END,
        updated_at=now()""",
        (document_id,),
    )


def request_delete(owner, document_id):
    with lakebase.get_connection() as conn, conn.cursor() as cur:
        _lock_document(cur, document_id)
        cur.execute(
            "UPDATE private_uploads SET status='deleting' WHERE document_id=%s AND user_id=%s AND status <> 'deleted' RETURNING document_id",
            (document_id, owner),
        )
        if cur.fetchone():
            _delete_job(cur, document_id)


def retry(owner, document_id):
    with lakebase.get_connection() as conn, conn.cursor() as cur:
        _lock_document(cur, document_id)
        cur.execute(
            "UPDATE private_uploads SET status='queued',error_code=NULL WHERE document_id=%s AND user_id=%s AND status='failed' AND expires_at>now() RETURNING document_id",
            (document_id, owner),
        )
        if not cur.fetchone():
            raise ValidationError("Only failed, unexpired files can be retried.")
        cur.execute(
            "UPDATE private_upload_jobs SET status='queued',action='extract',attempts=0,lease_token=NULL,lease_until=NULL WHERE document_id=%s",
            (document_id,),
        )


def release_upload(owner, document_id):
    """A completed/failed provider write releases its lease; abandoned writes expire."""
    with lakebase.get_connection() as conn, conn.cursor() as cur:
        _lock_document(cur, document_id)
        cur.execute(
            "UPDATE private_uploads SET upload_lease_until=NULL WHERE document_id=%s AND user_id=%s",
            (document_id, owner),
        )


def enqueue_cleanup():
    with lakebase.get_connection() as conn, conn.cursor() as cur:
        condition = """status NOT IN ('deleted','deleting') AND
            (expires_at<=now() OR user_id IS NULL OR conversation_id IS NULL
             OR (status='uploading' AND created_at<now()-interval '5 minutes'))"""
        cur.execute(
            f"SELECT document_id FROM private_uploads WHERE {condition} ORDER BY created_at LIMIT 50"
        )
        candidates = cur.fetchall()
        for (document_id,) in candidates:
            if not _lock_document(cur, document_id, wait=False):
                continue
            cur.execute(
                f"UPDATE private_uploads SET status='deleting' WHERE document_id=%s AND {condition} RETURNING document_id",
                (document_id,),
            )
            if cur.fetchone():
                _delete_job(cur, document_id)


_JOB_ELIGIBLE = """(j.status='queued' OR (j.status='processing' AND j.lease_until<=now()))
    AND (j.lease_until IS NULL OR j.lease_until<=now())
    AND (j.action <> 'delete' OR d.upload_lease_until IS NULL OR d.upload_lease_until<=now())"""


def claim():
    with lakebase.get_connection() as conn, conn.cursor(
        cursor_factory=RealDictCursor
    ) as cur:
        # Take the same document advisory lock before row locks in every mutator.
        # A bounded candidate list lets workers skip busy documents without waiting.
        cur.execute(
            f"""SELECT j.document_id FROM private_upload_jobs j JOIN private_uploads d USING(document_id)
            WHERE {_JOB_ELIGIBLE} ORDER BY CASE WHEN j.action='delete' THEN 0 ELSE 1 END,j.updated_at LIMIT 10"""
        )
        for candidate in cur.fetchall():
            document_id = candidate["document_id"]
            if not _lock_document(cur, document_id, wait=False):
                continue
            cur.execute(
                f"""SELECT d.*,j.action,j.attempts FROM private_upload_jobs j JOIN private_uploads d USING(document_id)
                WHERE j.document_id=%s AND {_JOB_ELIGIBLE} FOR UPDATE OF j,d SKIP LOCKED""",
                (document_id,),
            )
            row = cur.fetchone()
            if not row:
                continue
            token = str(uuid4())
            cur.execute(
                "UPDATE private_upload_jobs SET status='processing',attempts=attempts+1,lease_token=%s,lease_until=now()+interval '5 minutes',updated_at=now() WHERE document_id=%s",
                (token, document_id),
            )
            if row["action"] == "extract":
                cur.execute(
                    "UPDATE private_uploads SET status='processing' WHERE document_id=%s AND status IN ('queued','processing')",
                    (document_id,),
                )
            return {**dict(row), "lease_token": token, "attempts": row["attempts"] + 1}
        return None


def finish(job, result=None, error=None):
    with lakebase.get_connection() as conn, conn.cursor() as cur:
        _lock_document(cur, job["document_id"])
        cur.execute(
            "SELECT action FROM private_upload_jobs WHERE document_id=%s AND lease_token=%s FOR UPDATE",
            (job["document_id"], job["lease_token"]),
        )
        current = cur.fetchone()
        if not current:
            return False
        if current[0] != job["action"]:
            # A deletion requested while extracting is next, never overwritten by ready.
            cur.execute(
                "UPDATE private_upload_jobs SET status='queued',lease_until=NULL,lease_token=NULL WHERE document_id=%s",
                (job["document_id"],),
            )
            return False
        if job["action"] == "delete":
            if error:
                cur.execute(
                    "UPDATE private_upload_jobs SET status='queued',lease_until=now()+interval '1 minute',lease_token=NULL,updated_at=now() WHERE document_id=%s",
                    (job["document_id"],),
                )
                return False
            cur.execute(
                "UPDATE private_uploads SET status='deleted',deleted_at=now(),reserved_bytes=0,artifact_bytes=0,error_code=NULL WHERE document_id=%s",
                (job["document_id"],),
            )
        elif error:
            cur.execute(
                "UPDATE private_uploads SET status='failed',error_code=%s WHERE document_id=%s AND status='processing'",
                (error, job["document_id"]),
            )
        else:
            cur.execute(
                """UPDATE private_uploads SET status='ready',artifact_bytes=%s,reserved_bytes=original_bytes+%s,
                artifact_hash=%s,page_count=%s,extraction_version=%s,error_code=NULL WHERE document_id=%s AND status='processing' """,
                (
                    result["artifact_bytes"],
                    result["artifact_bytes"],
                    result["artifact_hash"],
                    result["page_count"],
                    result["version"],
                    job["document_id"],
                ),
            )
        cur.execute(
            "UPDATE private_upload_jobs SET status='done',lease_until=NULL,lease_token=NULL,updated_at=now() WHERE document_id=%s",
            (job["document_id"],),
        )
        return True
