"""Retention and discovery of a user's saved private documents; no byte copies."""
from exceptions import ValidationError
from repositories import uploads
from services import upload_service

PAGE_SIZE = 24


def save(owner, document_id):
    upload_service.require_enabled()
    row = upload_service.owned(owner, document_id)
    if row['status'] != 'ready' or (row.get('expired') and not row.get('retained_at')):
        raise ValidationError('Only Ready, unexpired files can be saved to workspace.')
    retained = uploads.retain(owner, str(row['document_id']))
    if not retained:
        raise ValidationError('This file is no longer available to save. Refresh Files.')
    return upload_service.receipt(retained)


def list_documents(owner, page=1):
    upload_service.require_enabled()
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 10000:
        raise ValidationError('Choose a valid documents page.')
    rows = uploads.list_retained(owner, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE)
    return {'items': [upload_service.receipt(row) for row in rows[:PAGE_SIZE]],
            'page': page, 'has_more': len(rows) > PAGE_SIZE}


def options(owner, query="", page=1):
    upload_service.require_enabled()
    if (
        not isinstance(query, str)
        or len(query) > 300
        or type(page) is not int
        or not 1 <= page <= 10000
    ):
        raise ValidationError("Invalid document search.")
    rows = uploads.find_retained(owner, query.strip(), 21, (page - 1) * 20)
    return {
        "items": [
            dict(
                upload_service.receipt(row),
                kind="attachment",
                id=str(row["document_id"]),
                label=row["filename"],
                snippet="Saved document",
            )
            for row in rows[:20]
        ],
        "next_cursor": str(page + 1) if len(rows) > 20 else None,
    }
