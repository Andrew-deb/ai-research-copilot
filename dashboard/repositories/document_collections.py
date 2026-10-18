"""Owner-scoped associations; paper membership and reading order stay separate."""

from repositories import lakebase
from exceptions import ValidationError


def add(owner, collection_id, document_id):
    from repositories import uploads

    with lakebase.get_connection() as conn, conn.cursor() as cur:
        uploads._lock_document(cur, document_id)
        cur.execute(
            "SELECT collection_id FROM collections WHERE collection_id=%s AND user_id=%s AND NOT is_curated FOR UPDATE",
            (collection_id, owner),
        )
        if not cur.fetchone():
            raise ValidationError("This collection is unavailable.")
        cur.execute(
            """INSERT INTO collection_documents (collection_id,document_id,user_id)
        SELECT %s,d.document_id,d.user_id FROM private_uploads d
        WHERE d.user_id=%s AND d.document_id=%s AND d.retained_at IS NOT NULL AND d.status='ready'
        AND ((SELECT count(*) FROM collection_documents WHERE collection_id=%s)<100
          OR EXISTS(SELECT 1 FROM collection_documents WHERE collection_id=%s AND document_id=%s))
        ON CONFLICT (collection_id,document_id) DO UPDATE SET document_id=excluded.document_id
        RETURNING document_id""",
            (
                collection_id,
                owner,
                document_id,
                collection_id,
                collection_id,
                document_id,
            ),
        )
        if not cur.fetchone():
            raise ValidationError(
                "This document is unavailable or the collection has reached its 100-document limit."
            )
    return {
        "document_id": document_id,
        "collection_id": collection_id,
        "status": "added",
    }


def remove(owner, collection_id, document_id):
    lakebase.run_write(
        """DELETE FROM collection_documents cd USING collections c
        WHERE cd.collection_id=c.collection_id AND c.user_id=%s AND NOT c.is_curated
        AND cd.user_id=%s AND cd.collection_id=%s AND cd.document_id=%s RETURNING cd.document_id""",
        (owner, owner, collection_id, document_id),
    )
    return {"status": "removed", "document_id": document_id}


def list_owned(owner, collection_id):
    return lakebase.run_query(
        """SELECT d.document_id,d.filename,d.original_bytes FROM collection_documents cd
        JOIN collections c ON c.collection_id=cd.collection_id
        JOIN private_uploads d ON d.document_id=cd.document_id
        WHERE cd.collection_id=%s AND cd.user_id=%s AND c.user_id=%s AND NOT c.is_curated
          AND d.user_id=%s AND d.retained_at IS NOT NULL AND d.status='ready'
        ORDER BY cd.created_at,d.document_id LIMIT 100""",
        (collection_id, owner, owner, owner),
    )
