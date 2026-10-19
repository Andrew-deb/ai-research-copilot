"""Durable private-index reservations, leases and atomic publication."""

import json
from uuid import uuid4
from psycopg2.extras import RealDictCursor
from repositories import lakebase, uploads
from exceptions import ValidationError

RESERVATION = 8 * 1024 * 1024


def queue(owner, document_id, contract, limits):
    with lakebase.get_connection() as conn, conn.cursor(
        cursor_factory=RealDictCursor
    ) as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('private-upload-capacity',0))"
        )
        uploads._lock_document(cur, document_id)
        cur.execute(
            "SELECT * FROM private_uploads WHERE user_id=%s AND document_id=%s AND retained_at IS NOT NULL AND status='ready' FOR UPDATE",
            (owner, document_id),
        )
        row = cur.fetchone()
        if not row:
            raise ValidationError("Only Ready saved documents can be indexed.")
        cur.execute(
            "SELECT *,updated_at>now()-interval '1 hour' AS cooling FROM document_index_jobs WHERE document_id=%s FOR UPDATE",
            (document_id,),
        )
        job = cur.fetchone()
        if (
            job
            and job["contract"] == contract
            and job["artifact_hash"] == row["artifact_hash"]
            and job["status"] in ("queued", "processing", "ready")
        ):
            return {"status": job["status"]}
        if job and job["status"] == "failed" and job.get("cooling"):
            raise ValidationError(
                "A failed index can be retried after one hour. Check the embedding service first."
            )
        if job and job["status"] == "processing" and job.get("lease_until"):
            raise ValidationError(
                "An index is already processing. Retry after it finishes."
            )
        cur.execute(
            "SELECT COALESCE(sum(reserved_bytes),0) AS total,COALESCE(sum(reserved_bytes) FILTER(WHERE user_id=%s),0) AS personal FROM private_uploads",
            (owner,),
        )
        used = cur.fetchone()
        delta = RESERVATION - row["index_bytes"]
        if (
            used["total"] + delta > limits.deployment_bytes
            or used["personal"] + delta > limits.per_user_bytes
        ):
            raise ValidationError("Not enough private storage capacity for this index.")
        cur.execute(
            "UPDATE private_uploads SET reserved_bytes=reserved_bytes+%s,index_bytes=%s WHERE document_id=%s",
            (delta, RESERVATION, document_id),
        )
        cur.execute(
            """INSERT INTO document_index_jobs(document_id,contract,artifact_hash,status)
        VALUES(%s,%s,%s,'queued') ON CONFLICT(document_id) DO UPDATE SET contract=excluded.contract,
        artifact_hash=excluded.artifact_hash,status='queued',attempts=0,error_code=NULL,lease_token=NULL,lease_until=NULL,updated_at=now()""",
            (document_id, contract, row["artifact_hash"]),
        )
        return {"status": "queued"}


def claim():
    with lakebase.get_connection() as conn, conn.cursor(
        cursor_factory=RealDictCursor
    ) as cur:
        cur.execute(
            """SELECT j.document_id FROM document_index_jobs j JOIN private_uploads d USING(document_id)
        WHERE (j.status='queued' OR (j.status='processing' AND j.lease_until<=now()))
        AND d.status='ready' AND d.retained_at IS NOT NULL AND d.user_id IS NOT NULL
        ORDER BY j.updated_at LIMIT 10"""
        )
        for candidate in cur.fetchall():
            doc = candidate["document_id"]
            if not uploads._lock_document(cur, doc, wait=False):
                continue
            cur.execute(
                """SELECT d.*,j.contract,j.attempts,j.artifact_hash AS indexed_hash FROM document_index_jobs j
            JOIN private_uploads d USING(document_id) WHERE j.document_id=%s
            AND (j.status='queued' OR (j.status='processing' AND j.lease_until<=now()))
            AND d.status='ready' AND d.retained_at IS NOT NULL AND d.user_id IS NOT NULL FOR UPDATE OF j,d""",
                (doc,),
            )
            row = cur.fetchone()
            if not row:
                continue
            token = str(uuid4())
            cur.execute(
                "UPDATE document_index_jobs SET status='processing',attempts=attempts+1,lease_token=%s,lease_until=now()+interval '5 minutes',updated_at=now() WHERE document_id=%s",
                (token, doc),
            )
            return dict(row, lease_token=token)


def renew(job):
    rows = lakebase.run_write(
        """UPDATE document_index_jobs j SET lease_until=now()+interval '5 minutes'
        FROM private_uploads d WHERE j.document_id=d.document_id AND j.document_id=%s AND j.lease_token=%s
        AND j.status='processing' AND d.status='ready' AND d.user_id=%s RETURNING j.document_id""",
        (job["document_id"], job["lease_token"], job["user_id"]),
    )
    return bool(rows)


def finish(job, chunks=None, error=None):
    with lakebase.get_connection() as conn, conn.cursor(
        cursor_factory=RealDictCursor
    ) as cur:
        doc = job["document_id"]
        uploads._lock_document(cur, doc)
        cur.execute(
            """SELECT d.* FROM private_uploads d JOIN document_index_jobs j USING(document_id)
        WHERE d.document_id=%s AND d.user_id=%s AND d.status='ready' AND d.retained_at IS NOT NULL
        AND j.lease_token=%s AND j.status='processing' AND j.contract=%s
        AND d.artifact_hash=j.artifact_hash AND d.artifact_hash=%s FOR UPDATE OF j,d""",
            (
                doc,
                job["user_id"],
                job["lease_token"],
                job["contract"],
                job["indexed_hash"],
            ),
        )
        row = cur.fetchone()
        if not row:
            return False
        cur.execute("DELETE FROM document_chunks WHERE document_id=%s", (doc,))
        size = 0
        if not error:
            for index, chunk in enumerate(chunks):
                vector = "[" + ",".join(str(value) for value in chunk["vector"]) + "]"
                size += (
                    len(chunk["text"].encode("utf-8")) + len(chunk["vector"]) * 4 + 1024
                )
                if size > RESERVATION:
                    raise ValidationError("Index exceeds its storage reservation.")
                cur.execute(
                    """INSERT INTO document_chunks(document_id,user_id,chunk_index,page,page_offset,chunk_text,embedding,contract,artifact_hash)
                VALUES(%s,%s,%s,%s,%s,%s,%s::vector,%s,%s)""",
                    (
                        doc,
                        row["user_id"],
                        index,
                        chunk["page"],
                        chunk["offset"],
                        chunk["text"],
                        vector,
                        job["contract"],
                        job["indexed_hash"],
                    ),
                )
        cur.execute(
            "UPDATE private_uploads SET reserved_bytes=reserved_bytes-index_bytes+%s,index_bytes=%s WHERE document_id=%s",
            (size, size, doc),
        )
        cur.execute(
            "UPDATE document_index_jobs SET status=%s,error_code=%s,lease_token=NULL,lease_until=NULL,updated_at=now() WHERE document_id=%s",
            ("failed" if error else "ready", error, doc),
        )
        return True


def state(owner, document_id):
    rows = lakebase.run_query(
        """SELECT j.status,j.error_code,j.contract,j.artifact_hash FROM document_index_jobs j
    JOIN private_uploads d USING(document_id) WHERE d.user_id=%s AND d.document_id=%s
    AND d.status='ready' AND d.retained_at IS NOT NULL""",
        (owner, document_id),
    )
    return rows[0] if rows else {"status": "not_indexed"}


def search(owner, vector, contract, limit=10, document_id=None):
    encoded = "[" + ",".join(str(v) for v in vector) + "]"
    return lakebase.run_query(
        """SELECT d.document_id,d.filename,c.page,c.page_offset AS offset,c.chunk_text AS text,
        1-(c.embedding <=> %s::vector) AS similarity FROM document_chunks c
        JOIN private_uploads d ON d.document_id=c.document_id
        JOIN document_index_jobs j ON j.document_id=d.document_id
        WHERE c.user_id=%s AND d.user_id=%s AND d.status='ready' AND d.retained_at IS NOT NULL
        AND j.status='ready' AND c.contract=%s AND j.contract=c.contract AND c.artifact_hash=d.artifact_hash
        AND j.artifact_hash=d.artifact_hash AND (%s::uuid IS NULL OR d.document_id=%s::uuid)
        ORDER BY c.embedding <=> %s::vector LIMIT %s""",
        (encoded, owner, owner, contract, document_id, document_id, encoded, limit),
    )
