"""Resolve a page hint into a short, authorized context for the agent.

Browser-provided labels are never authority. An item is looked up under the
current user on each turn, so a stale panel cannot act on a deleted or foreign
collection or goal. The context is only a hint; tool permissions still apply.
"""

from repositories import lakebase
from exceptions import ValidationError
from uuid import UUID

PAGES = {
    "dashboard": "Dashboard", "search": "Paper search",
    "collections": "Collections", "progress": "Reading progress",
    "notes": "Notes", "goals": "Learning goals",
}


def resolve(user_id: str | None, kind: str, item_id: str = "") -> dict | None:
    kind = (kind or "").strip().lower()
    item_id = (item_id or "").strip()
    if not kind:
        return None
    if len(item_id) > 100:
        raise ValidationError("Invalid assistant context.")
    if kind in PAGES and not item_id:
        if kind in ("notes", "progress", "goals") and not user_id:
            raise ValidationError("Sign in to use this workspace context.")
        return {"kind": kind, "id": "", "label": PAGES[kind]}
    if not item_id:
        raise ValidationError("Invalid assistant context.")
    try:
        item_id = str(UUID(item_id))
    except (ValueError, AttributeError):
        raise ValidationError("Invalid assistant context.") from None
    if kind == "paper":
        row = lakebase.get_paper(item_id)
        label = row.get("title") if row else None
    elif kind == "collection":
        row = (lakebase.get_collection(item_id, user_id) if user_id else None) or \
              lakebase.get_curated_collection(item_id)
        label = row.get("name") if row else None
    elif kind == "goal" and user_id:
        row = lakebase.get_learning_goal(item_id, user_id)
        label = row.get("title") if row else None
    else:
        raise ValidationError("Invalid assistant context.")
    if not label:
        raise ValidationError("This assistant context is no longer available.")
    return {"kind": kind, "id": item_id, "label": str(label)[:120]}
