"""Domain authorization and persistence, without Flask, MCP, or a database."""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from mcp_server.alfred_domain import collection_service, planning_service, progress_service
from mcp_server.alfred_domain.errors import CollectionNotFoundError, ValidationError
from mcp_server.alfred_domain.papers import require_paper


@pytest.fixture
def repo():
    repository = Mock()
    repository.get_collection.return_value = {"collection_id": "collection", "user_id": "owner"}
    repository.get_paper.return_value = {"paper_id": "paper", "title": "A paper"}
    repository.get_collection_papers.return_value = [
        {"paper_id": "recent", "title": "Recent", "publication_year": 2025},
        {"paper_id": "foundation", "title": "Foundation", "publication_year": 2019},
    ]
    return repository


@pytest.mark.parametrize("action", ["add", "remove", "plan", "reorder"])
@pytest.mark.parametrize("actor", [None, "foreign", "curated"])
def test_collection_writes_refuse_missing_foreign_and_curated_actors(repo, action, actor):
    if actor == "foreign":
        repo.get_collection.return_value = None
    elif actor == "curated":
        repo.get_collection.return_value = {"is_curated": True}
    calls = {
        "add": lambda: collection_service.add_paper_to_collection(repo, "collection", "paper", user_id=actor),
        "remove": lambda: collection_service.remove_paper_from_collection(repo, "collection", "paper", user_id=actor),
        "plan": lambda: planning_service.generate_reading_plan(repo, "collection", user_id=actor),
        "reorder": lambda: planning_service.reorder_reading_plan(
            repo, "collection", [{"paper_id": "foundation", "sequence_order": 1}], user_id=actor),
    }
    with pytest.raises((ValidationError, CollectionNotFoundError)):
        calls[action]()
    repo.add_paper_to_collection.assert_not_called()
    repo.remove_paper_from_collection.assert_not_called()
    repo.update_paper_orders.assert_not_called()
    repo.get_collection_papers.assert_not_called()


def test_plan_saves_one_complete_order_and_preserves_research_result(repo):
    result = planning_service.generate_reading_plan(repo, "collection", user_id="owner")
    repo.update_paper_orders.assert_called_once_with(
        "collection", [("foundation", 1), ("recent", 2)])
    assert result == {
        "collection_id": "collection", "total_papers": 2,
        "strategy": "Chronological Foundations with Citation Impact Weighting",
        "reading_plan": [
            {"sequence_order": 1, "stage": "Foundations", "paper_id": "foundation",
             "title": "Foundation", "publication_year": 2019, "venue": None, "tldr": None, "citation_count": 0},
            {"sequence_order": 2, "stage": "Advanced Applications", "paper_id": "recent",
             "title": "Recent", "publication_year": 2025, "venue": None, "tldr": None, "citation_count": 0},
        ],
    }


def test_plan_does_not_report_success_if_persistence_fails(repo):
    repo.update_paper_orders.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        planning_service.generate_reading_plan(repo, "collection", user_id="owner")


@pytest.mark.parametrize("orders", [
    [{"paper_id": "foundation", "sequence_order": 1}, {"paper_id": "foreign", "sequence_order": 2}],
    [{"paper_id": "foundation", "sequence_order": 1}, {"paper_id": "foundation", "sequence_order": 2}],
    [{"paper_id": "foundation", "sequence_order": -1}],
    [{"paper_id": "foundation", "sequence_order": True}],
])
def test_reorder_validates_all_members_before_any_write(repo, orders):
    with pytest.raises(ValidationError):
        planning_service.reorder_reading_plan(repo, "collection", orders, user_id="owner")
    repo.update_paper_orders.assert_not_called()


def test_reorder_keeps_explicit_positions_and_legacy_result(repo):
    result = planning_service.reorder_reading_plan(
        repo, "collection", [{"paper_id": "recent", "sequence_order": 7}], user_id="owner")
    repo.update_paper_orders.assert_called_once_with("collection", [("recent", 7)])
    assert result == {"status": "success", "message": "Reading plan reordered."}


def test_personal_progress_keeps_actor_and_normalizes_status(repo):
    repo.upsert_reading_progress.return_value = {
        "status": "reading", "updated_at": datetime(2026, 10, 1, tzinfo=timezone.utc)}
    result = progress_service.mark_paper_status(repo, "owner", "paper", " READING ")
    repo.upsert_reading_progress.assert_called_once_with(user_id="owner", paper_id="paper", status="reading")
    assert result["reading_status"] == "reading"
    assert result["updated_at"] == "2026-10-01T00:00:00+00:00"


def test_paper_note_retains_legacy_fields_and_scoped_write(repo):
    repo.save_note.return_value = {"note_id": "note", "note_text": "Summary", "created_at": "now"}
    result = progress_service.save_note(repo, "owner", "paper", "  Summary  ")
    repo.save_note.assert_called_once_with(user_id="owner", paper_id="paper", note_text="Summary")
    assert result == {"status": "success", "note_id": "note", "paper_id": "paper",
                      "paper_title": "A paper", "note_text": "Summary", "created_at": "now"}


@pytest.mark.parametrize("actor", [None, ""])
def test_personal_write_never_falls_back_to_demo(repo, actor):
    with pytest.raises(ValidationError):
        progress_service.save_note(repo, actor, "paper", "Summary")
    with pytest.raises(ValidationError):
        progress_service.mark_paper_status(repo, actor, "paper", "reading")
    repo.save_note.assert_not_called()
    repo.upsert_reading_progress.assert_not_called()


def test_local_paper_lookup_has_no_discovery_side_effect(repo):
    assert require_paper(repo, "paper")["title"] == "A paper"
    repo.get_paper.assert_called_once_with("paper")
    assert [call[0] for call in repo.mock_calls] == ["get_paper"]
