"""Ownership, bounded retrieval, and Research-compatible note contracts."""

from unittest.mock import Mock
from uuid import UUID

import pytest

from mcp_server.shared_resource.exceptions import WorkspaceResourceNotFoundError, ValidationError
from mcp_server.shared_resource.repositories import workspace_repository as queries
from mcp_server.shared_resource.services import note_service, workspace_service as workspace
from services import progress_service as dashboard_notes

ID = "11111111-1111-1111-1111-111111111111"
USER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def db():
    repo = Mock()
    repo.run_query.return_value = []
    repo.get_paper.return_value = {"paper_id": UUID(ID), "title": "Paper", "abstract": "A" * 5000}
    repo.save_note.return_value = {"note_id": ID, "note_text": "Body", "created_at": "now"}
    return repo


def test_standalone_note_normalizes_metadata_without_selecting_a_paper(db):
    result = note_service.create_note(db, USER, " Body ", title=" Ideas ", tags="Lit Review, lit  review, ML")
    db.save_note.assert_called_once_with(user_id=USER, paper_id=None, note_text="Body",
                                         title="Ideas", tags=["lit review", "ml"])
    db.get_paper.assert_not_called()
    assert result["paper_title"] is None


@pytest.mark.parametrize("tags", ["Lit Review, lit review, ML", [" ML ", "ml", "Methods"], None])
def test_valid_tag_normalization_matches_dashboard(tags):
    assert note_service.clean_tags(tags) == dashboard_notes.clean_tags(tags)


@pytest.mark.parametrize("kwargs", [
    {"user_id": None}, {"note_text": ""}, {"note_text": "x" * 10001},
    {"title": "x" * 201}, {"tags": ["x" * 41]},
    {"tags": [str(n) for n in range(13)]}, {"tags": {"action": "anything"}},
])
def test_invalid_note_input_reaches_no_persistence(db, kwargs):
    values = {"user_id": USER, "note_text": "Body", **kwargs}
    with pytest.raises(ValidationError):
        note_service.create_note(db, **values)
    db.save_note.assert_not_called()
    db.get_paper.assert_not_called()


def test_research_note_still_requires_a_paper(db):
    with pytest.raises(ValidationError):
        note_service.save_note(db, USER, None, "Body")
    db.save_note.assert_not_called()


@pytest.mark.parametrize("kind", ["note", "goal"])
def test_private_reads_require_identity_before_database_access(db, kind):
    with pytest.raises(ValidationError, match="Sign in"):
        workspace.get_workspace_resource(db, None, kind, ID)
    assert db.mock_calls == []


def test_note_lookup_is_owner_scoped_and_paginates_content(db):
    db.run_query.return_value = [{"note_id": ID, "note_text": "a" * 4000 + "tail"}]
    first = workspace.get_workspace_resource(db, USER, "note", ID)
    assert len(first["resource"]["note_text"]) == 4000
    assert first["next_cursor"] == "4000"
    sql, args = db.run_query.call_args.args
    assert "note_id = %s AND user_id = %s" in sql
    assert args == (ID, USER)
    second = workspace.get_workspace_resource(db, USER, "note", ID, cursor=first["next_cursor"])
    assert second["resource"]["note_text"] == "tail"
    assert second["next_cursor"] is None


@pytest.mark.parametrize("kind", ["note", "collection", "goal"])
def test_missing_or_foreign_resource_has_the_same_response(db, kind):
    db.get_learning_goal.return_value = None
    with pytest.raises(WorkspaceResourceNotFoundError, match="could not be found"):
        workspace.get_workspace_resource(db, USER, kind, ID)


def test_anonymous_collection_read_is_explicitly_curated_and_bounded(db):
    db.run_query.side_effect = [
        [{"collection_id": ID, "name": "Example", "is_curated": True}],
        [{"paper_id": ID}, {"paper_id": USER}],
    ]
    result = workspace.get_workspace_resource(db, None, "collection", ID, limit=1)
    assert result["items"] == [{"paper_id": ID}]
    assert result["next_cursor"] == "1"
    first, second = db.run_query.call_args_list
    assert "user_id = %s OR is_curated" in first.args[0]
    assert first.args[1] == (ID, None)
    assert second.args[1] == (ID, 2, 0)


def test_goal_read_does_not_run_semantic_matching(db):
    db.get_learning_goal.return_value = {"goal_id": ID, "title": "Read", "description": "Topic"}
    result = workspace.get_workspace_resource(db, USER, "goal", ID)
    assert result["resource"]["title"] == "Read"
    assert [call[0] for call in db.mock_calls] == ["get_learning_goal"]


def test_local_paper_result_is_bounded_and_has_no_external_discovery(db):
    result = workspace.get_workspace_paper(db, ID)
    assert len(result["resource"]["abstract"]) == 4000
    assert result["truncated"]
    assert result["resource"]["paper_id"] == ID
    assert [call[0] for call in db.mock_calls] == ["get_paper", "run_query"]


@pytest.mark.parametrize("limit,cursor", [(0, None), (51, None), (True, None), (20, "-1"), (20, "oops"), (20, "10001")])
def test_invalid_pagination_is_rejected_before_work(db, limit, cursor):
    with pytest.raises(ValidationError):
        workspace.find_workspace_resources(db, USER, limit=limit, cursor=cursor)
    assert db.mock_calls == []


def test_resource_search_keeps_identity_and_query_as_parameters(db):
    result = queries.find_resources(db, USER, ["note", "goal", "collection"], "x%_", 21, 3)
    assert result == []
    sql, args = db.run_query.call_args.args
    assert sql.count("user_id = %s") == 3
    assert args[:3] == (USER, USER, USER)
    assert args[-4:] == ("%x\\%\\_%", "%x\\%\\_%", 21, 3)


def test_anonymous_default_search_contains_no_private_query_branches(db):
    workspace.find_workspace_resources(db, None, query="zz")
    sql, args = db.run_query.call_args.args
    assert "FROM notes" not in sql and "FROM learning_goals" not in sql
    assert args[0] is None  # only explicitly curated collections match


def test_explicit_private_search_requires_signin(db):
    with pytest.raises(ValidationError, match="Sign in"):
        workspace.find_workspace_resources(db, None, kinds=["note"])
    assert db.mock_calls == []


def test_page_search_needs_no_database_and_has_finite_references(db):
    result = workspace.find_workspace_resources(db, None, kinds=["page"], limit=1)
    assert result["items"][0]["reference"] == {"kind": "page", "id": "dashboard"}
    assert result["next_cursor"] == "1"
    assert db.mock_calls == []


def test_page_to_database_pagination_does_not_skip_items(db):
    # Anonymous has three pages. At global offset 2, one page remains,
    # then corpus results start at database offset zero.
    db.run_query.return_value = [{"kind": "paper", "id": ID, "label": "Paper", "snippet": "Abstract"}]
    result = workspace.find_workspace_resources(db, None, limit=1, cursor="2")
    assert result["items"][0]["kind"] == "page"
    assert result["next_cursor"] == "3"
    assert db.run_query.call_args.args[1][-2:] == (1, 0)


def test_progress_is_owner_scoped_with_a_bound_limit(db):
    workspace.get_reading_progress(db, USER, status="reading", limit=5, cursor="10")
    assert db.run_query.call_args.args[1] == (USER, "reading", "reading", 6, 10)


@pytest.mark.parametrize("value", ["not-a-uuid", "';DELETE FROM notes", None])
def test_invalid_resource_id_never_reaches_database(db, value):
    with pytest.raises(ValidationError):
        workspace.get_workspace_resource(db, USER, "note", value)
    assert db.mock_calls == []
