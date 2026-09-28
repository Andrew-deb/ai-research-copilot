"""
dashboard/services/command_service.py — one box that finds anything.

Two things with one field, because from the person's side they are one thing —
"get me to what I am thinking of":

  * **pages**, matched here in memory from a fixed list. Navigation is most of
    what a palette is used for and it must never wait on a database.
  * **assets** — papers, notes, collections, conversations — in ONE query. See
    repositories/palette.py for the measurements that forced that shape.

**Nothing here is semantic.** A palette runs while somebody types; an embedding
call per keystroke would be slow, metered, and would spend a search allowance on
navigating their own application.

**A failed lookup is reported, not swallowed.** The first version wrapped each
section in `except Exception` so one bad lookup could not take the palette down.
Under load every section failed at once and the palette returned an empty list —
which reads as "nothing matches" rather than "this is broken". Being quietly
wrong is worse than being loudly unavailable.
"""

from __future__ import annotations

import logging

from middleware.capabilities import CHAT_HISTORY, NOTES_WRITE, tier_can
from repositories import palette as palette_repo

logger = logging.getLogger(__name__)

# Below this a query matches most of the corpus, and the result is a slow round
# trip to say "here is everything". The palette shows destinations and recent
# work until there is enough to narrow on.
MIN_QUERY = 2

DESTINATIONS = (
    ("Dashboard", "home.dashboard", "dashboard", "home overview start"),
    ("Search", "search.search_page", "search", "find papers lookup"),
    ("Collections", "collections.list_collections", "folder", "library reading lists saved"),
    ("Progress", "progress.board", "progress", "reading status board"),
    ("Notes", "progress.notes", "note", "annotations writing"),
    ("Goals", "goals.list_goals", "target", "learning objectives"),
    ("Help & docs", "public.help_page", "book", "documentation guide support"),
    ("About", "public.about", "info", "what is this"),
    ("Plans & pricing", "public.pricing", "spark", "quotas limits billing"),
)

# Destinations only a signed-in person has anything behind.
_PRIVATE = {"progress.board", "progress.notes", "goals.list_goals"}

# What each asset kind is called and where it goes.
_KINDS = {
    "paper": ("Papers", "search.paper_detail", "paper_id"),
    "note": ("Notes", "progress.notes", None),
    "collection": ("Collections", "collections.collection_detail", "collection_id"),
    "conversation": ("Conversations", "chat.conversation", "conversation_id"),
}


def destinations(query: str, tier: str) -> list[dict]:
    """Pages, matched in memory. No query, no wait."""
    from flask import url_for

    found = []
    for label, endpoint, icon, aliases in DESTINATIONS:
        if endpoint in _PRIVATE and not tier_can(tier, NOTES_WRITE):
            continue
        if query and query not in f"{label} {aliases}".lower():
            continue
        found.append({"label": label, "url": url_for(endpoint), "icon": icon})
    return found


def _to_item(row: dict) -> dict | None:
    from flask import url_for

    kind = row["kind"]
    if kind not in _KINDS:
        return None
    _, endpoint, arg = _KINDS[kind]

    # A note has no page of its own, so it lands on the notes page with `note=`
    # — which opens that note in the notepad rather than dropping somebody into
    # a list to find again the thing they just picked out of a list.
    url = (url_for(endpoint, note=row["id"]) if kind == "note"
           else url_for(endpoint, **({arg: row["id"]} if arg else {})))
    return {
        "kind": kind,
        "label": row["label"] or "Untitled",
        "detail": row.get("detail") or "",
        "url": url,
    }


def _allowed(rows: list[dict], tier: str) -> list[dict]:
    """
    Capability, not the query, decides what a caller may see.

    The SQL already scopes private kinds to a user id, so this is the second of
    two independent checks rather than the only one.
    """
    keep = []
    for row in rows:
        if row["kind"] == "note" and not tier_can(tier, NOTES_WRITE):
            continue
        if row["kind"] == "conversation" and not tier_can(tier, CHAT_HISTORY):
            continue
        keep.append(row)
    return keep


def search(query: str, tier: str, user_id: str | None) -> dict:
    """
    What the palette shows. Returns sections plus whether the lookup worked.

    `ok: false` is the honest answer when the database could not be reached, and
    the palette says so rather than rendering an empty list that reads as "you
    have nothing".
    """
    query = (query or "").strip().lower()
    sections: list[dict] = []

    pages = destinations(query, tier)
    if pages:
        sections.append({"title": "Go to", "kind": "pages", "items": pages})

    if len(query) < MIN_QUERY:
        # Nothing useful to search on yet: offer what was last touched, which is
        # the question somebody opens a palette with before typing.
        try:
            recent = _allowed(palette_repo.recent(user_id), tier)
        except Exception:
            logger.warning("palette: recent lookup failed", exc_info=True)
            return {"sections": sections, "ok": False}

        items = [i for i in (_to_item(r) for r in recent) if i]
        if items:
            sections.append({"title": "Recent", "kind": "assets", "items": items})
        return {"sections": sections, "ok": True}

    try:
        rows = _allowed(palette_repo.search(query, user_id), tier)
    except Exception:
        # Logged at warning, not debug: this is the failure that presented as an
        # empty palette for a week, and it deserves to be findable in a log.
        logger.warning("palette: search failed for %r", query[:40], exc_info=True)
        return {"sections": sections, "ok": False}

    by_kind: dict[str, list[dict]] = {}
    for row in rows:
        item = _to_item(row)
        if item:
            by_kind.setdefault(item["kind"], []).append(item)

    # Fixed order, so the list does not reshuffle itself between keystrokes.
    for kind in ("note", "paper", "collection", "conversation"):
        if by_kind.get(kind):
            sections.append({"title": _KINDS[kind][0], "kind": "assets",
                             "items": by_kind[kind]})

    return {"sections": sections, "ok": True}
