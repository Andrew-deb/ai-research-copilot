"""Runtime adapter for workspace contracts; no tools are registered here."""

from ..services import note_service, workspace_service as domain
from ..repositories import lakebase


def create_note(user_id: str, note_text: str, paper_id: str | None = None,
                title: str | None = None, tags: list[str] | str | None = None) -> dict:
    return note_service.create_note(lakebase, user_id, note_text, paper_id, title, tags)


def find_workspace_resources(user_id: str | None = None, query: str = "",
                             kinds: list[str] | None = None, limit: int = 20,
                             cursor: str | None = None) -> dict:
    return domain.find_workspace_resources(lakebase, user_id, query, kinds, limit, cursor)


def get_workspace_resource(user_id: str | None, kind: str, item_id: str,
                           limit: int = 20, cursor: str | None = None) -> dict:
    return domain.get_workspace_resource(lakebase, user_id, kind, item_id, limit, cursor)


def get_workspace_paper(paper_id: str) -> dict:
    return domain.get_workspace_paper(lakebase, paper_id)


def get_reading_progress(user_id: str, status: str | None = None,
                         limit: int = 20, cursor: str | None = None) -> dict:
    return domain.get_reading_progress(lakebase, user_id, status, limit, cursor)
