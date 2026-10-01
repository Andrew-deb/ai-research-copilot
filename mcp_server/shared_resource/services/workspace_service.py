"""Finite, bounded workspace read contracts for Wick; no model-selected actor."""

from datetime import date, datetime
from uuid import UUID

from ..exceptions import WorkspaceResourceNotFoundError, ValidationError
from ..repositories import workspace_repository as queries
from .paper_service import require_paper
from .progress_service import VALID_STATUSES

MAX_LIMIT = 50
MAX_CONTENT = 4000
KINDS = frozenset({"paper", "collection", "note", "goal", "page"})
PAGES = {
    "dashboard": "Workspace overview", "search": "Find research papers",
    "collections": "Organize papers in collections", "notes": "Read and write personal notes",
    "progress": "Track personal reading progress", "goals": "Manage personal learning goals",
}
PRIVATE_PAGES = {"notes", "progress", "goals"}


def require_identity(user_id: str | None) -> str:
    if not user_id:
        raise ValidationError("Sign in to access this workspace resource.")
    return user_id


def page_bounds(limit: int, cursor: str | None) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValidationError(f"Limit must be between 1 and {MAX_LIMIT}.")
    if cursor is None:
        return limit, 0
    if not isinstance(cursor, str) or not cursor.isascii() or not cursor.isdecimal() or len(cursor) > 5:
        raise ValidationError("Invalid workspace cursor.")
    offset = int(cursor)
    if offset > 10000:
        raise ValidationError("Workspace cursor is too large.")
    return limit, offset


def resource_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("Invalid resource ID.") from None


def present(value):
    if isinstance(value, dict):
        return {key: present(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [present(item) for item in value]
    if isinstance(value, (UUID, date, datetime)):
        return str(value) if isinstance(value, UUID) else value.isoformat()
    return value


def page_result(rows: list[dict], limit: int, offset: int) -> dict:
    return {"items": present(rows[:limit]),
            "next_cursor": str(offset + limit) if len(rows) > limit else None}


def find_workspace_resources(db, user_id: str | None, query: str = "",
                             kinds: list[str] | None = None, limit: int = 20,
                             cursor: str | None = None) -> dict:
    limit, offset = page_bounds(limit, cursor)
    if not isinstance(query, str) or len(query) > 300:
        raise ValidationError("Workspace search must be at most 300 characters.")
    if kinds is not None and (not isinstance(kinds, list) or not kinds or
                              any(not isinstance(k, str) or k not in KINDS for k in kinds)):
        raise ValidationError("Unsupported workspace resource kind.")
    chosen = list(KINDS) if kinds is None else list(dict.fromkeys(kinds))
    if kinds is not None and not user_id and any(k in {"note", "goal"} for k in chosen):
        require_identity(user_id)
    query = query.strip()
    pages = [{"kind": "page", "id": key, "label": key.title(), "snippet": description}
             for key, description in PAGES.items()
             if "page" in chosen and (user_id or key not in PRIVATE_PAGES)
             and query.lower() in f"{key} {description}".lower()]
    rows = pages[offset:offset + limit + 1]
    remaining = limit + 1 - len(rows)
    if remaining:
        rows += queries.find_resources(db, user_id, chosen, query, remaining,
                                       max(0, offset - len(pages)))
    result = page_result(rows, limit, offset)
    for item in result["items"]:
        item["reference"] = {"kind": item["kind"], "id": item["id"]}
    return result


def get_workspace_resource(db, user_id: str | None, kind: str, item_id: str,
                           limit: int = 20, cursor: str | None = None) -> dict:
    limit, offset = page_bounds(limit, cursor)
    if not isinstance(kind, str):
        raise ValidationError("Unsupported workspace resource kind.")
    if kind == "page":
        if not isinstance(item_id, str):
            raise ValidationError("Invalid page reference.")
        if item_id not in PAGES or (not user_id and item_id in PRIVATE_PAGES):
            raise WorkspaceResourceNotFoundError("This workspace resource could not be found.")
        if cursor is not None:
            raise ValidationError("Page summaries do not support cursors.")
        return {"kind": kind, "id": item_id, "label": item_id.title(),
                "content": PAGES[item_id], "provenance": "platform_page"}
    if kind not in {"note", "collection", "goal"}:
        raise ValidationError("Use local paper lookup for papers; unsupported resource kind.")
    if kind in {"note", "goal"}:
        require_identity(user_id)
    if kind == "goal" and cursor is not None:
        raise ValidationError("Goal summaries do not support cursors.")
    item_id = resource_id(item_id)
    if kind == "note":
        row = queries.get_note(db, user_id, item_id)
    elif kind == "collection":
        row = queries.get_collection(db, user_id, item_id)
    else:
        row = db.get_learning_goal(item_id, user_id)
    if not row:
        # Same response for missing and foreign resources.
        raise WorkspaceResourceNotFoundError("This workspace resource could not be found.")
    result = {"kind": kind, "id": item_id, "provenance": "workspace", "resource": present(row)}
    if kind == "collection":
        result["resource"]["description"] = (row.get("description") or "")[:MAX_CONTENT]
        result["truncated"] = len(row.get("description") or "") > MAX_CONTENT
        result.update(page_result(queries.collection_papers(db, item_id, limit + 1, offset), limit, offset))
    elif kind == "note":
        original = row.get("note_text") or ""
        text = original[:10000]
        result["resource"]["note_text"] = text[offset:offset + MAX_CONTENT]
        result["next_cursor"] = str(offset + MAX_CONTENT) if len(text) > offset + MAX_CONTENT else None
        result["truncated"] = result["next_cursor"] is not None or len(original) > 10000
    else:
        result["resource"] = present({key: row.get(key) for key in
                                      ("goal_id", "title", "description", "status", "created_at")})
        text = row.get("description") or ""
        result["resource"]["description"] = text[:MAX_CONTENT]
        result["truncated"] = len(text) > MAX_CONTENT
    return result


def get_workspace_paper(db, paper_id: str) -> dict:
    row = require_paper(db, resource_id(paper_id))
    fields = {key: row.get(key) for key in
              ("paper_id", "title", "doi", "publication_year", "venue", "citation_count", "open_access_url")}
    abstract = row.get("abstract") or ""
    fields["abstract"] = abstract[:MAX_CONTENT]
    fields["title"] = (row.get("title") or "")[:500]
    authors = queries.paper_authors(db, paper_id)
    fields["authors"] = present(authors[:50])
    return {"kind": "paper", "resource": present(fields), "provenance": "global_corpus",
            "truncated": len(abstract) > MAX_CONTENT or len(authors) > 50 or len(row.get("title") or "") > 500}


def get_reading_progress(db, user_id: str, status: str | None = None,
                         limit: int = 20, cursor: str | None = None) -> dict:
    require_identity(user_id)
    limit, offset = page_bounds(limit, cursor)
    if status is not None and (not isinstance(status, str) or status not in VALID_STATUSES):
        raise ValidationError("Invalid reading status.")
    return page_result(queries.reading_progress(db, user_id, status, limit + 1, offset), limit, offset)
