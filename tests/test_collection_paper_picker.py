"""Bounded retrieval and collection authorization across the two interfaces."""
from unittest.mock import Mock
import pytest

from mcp_server.shared_resource.services import paper_search_service, collection_service
from mcp_server.shared_resource.exceptions import ValidationError, CollectionNotFoundError
from services import workspace_paper_service
from tests.conftest import DEV_EMAIL


@pytest.mark.parametrize('kwargs', [dict(query='x' * 301), dict(limit=0), dict(limit=51),
                                   dict(cursor='-1'), dict(saved_only='yes'), dict(saved_only=True)])
def test_invalid_search_never_queries_database(kwargs):
    db = Mock()
    with pytest.raises(ValidationError):
        paper_search_service.search_workspace_papers(db, **kwargs)
    db.run_query.assert_not_called()


def test_search_paginates_and_binds_private_scope():
    db = Mock()
    db.run_query.return_value = [{'paper_id': str(i), 'title': 'Benchmarks'} for i in range(3)]
    result = paper_search_service.search_workspace_papers(db, 'owner', ' AI evaluations ', True, 2, '4')
    assert len(result['items']) == 2
    assert result['next_cursor'] == '6'
    assert result['search_space'] == 'saved_papers'
    sql, params = db.run_query.call_args.args
    assert 'rp.user_id = %s' in sql
    assert params[:4] == ('AI evaluations', True, 'owner', 'AI evaluations')
    assert params[-2:] == (3, 4)


def test_picker_marks_membership_and_keeps_existing_add_endpoint(client, db, monkeypatch):
    owner = db.get_or_create_user(DEV_EMAIL)
    collection = db.create_collection(owner['user_id'], 'AI Evaluations')
    paper = db.seed_paper(title='Evaluation benchmarks')
    db.append_paper_to_collection(collection['collection_id'], paper['paper_id'])
    monkeypatch.setattr(workspace_paper_service, 'search', lambda *a, **k: {
        'items': [{'paper_id': paper['paper_id'], 'title': paper['title']}], 'next_cursor': None})
    response = client.get(f"/collection/{collection['collection_id']}/paper-options?q=evaluation")
    assert response.status_code == 200
    item = response.json['items'][0]
    assert item['already_added'] is True
    assert item['url'] == '/paper/' + paper['paper_id']
    page = client.get(f"/collection/{collection['collection_id']}").text
    assert 'id="paper-picker"' in page


def test_picker_refuses_foreign_and_curated_collections_before_search(client, db, monkeypatch):
    search = Mock()
    monkeypatch.setattr(workspace_paper_service, 'search', search)
    foreign = db.get_or_create_user('foreign@example.com')
    collection = db.create_collection(foreign['user_id'], 'Private')
    assert client.get(f"/collection/{collection['collection_id']}/paper-options").status_code == 404
    db.collections[collection['collection_id']]['is_curated'] = True
    assert client.get(f"/collection/{collection['collection_id']}/paper-options").status_code == 403
    search.assert_not_called()


def test_anonymous_cannot_open_picker(anon_client):
    assert anon_client.get('/collection/unknown/paper-options').status_code == 403


def test_wick_append_refuses_foreign_before_mutation():
    repo = Mock()
    repo.get_collection.return_value = None
    with pytest.raises(CollectionNotFoundError):
        collection_service.append_paper_to_collection(repo, 'collection', 'paper', 'owner')
    repo.append_paper_to_collection.assert_not_called()


def test_wick_append_reports_saved_position():
    repo = Mock()
    repo.get_collection.return_value = {'user_id': 'owner'}
    repo.get_paper.return_value = {'title': 'Benchmarks'}
    repo.append_paper_to_collection.return_value = 7
    assert collection_service.append_paper_to_collection(repo, 'collection', 'paper', 'owner')['sequence_order'] == 7


def test_append_transaction_locks_collection_and_preserves_duplicate_position():
    from contextlib import contextmanager
    from mcp_server.shared_resource.repositories.collection_membership_repository import append_paper
    cursor = Mock()
    cursor.fetchone.return_value = (3,)
    connection = Mock()
    connection.cursor.return_value.__enter__ = Mock(return_value=cursor)
    connection.cursor.return_value.__exit__ = Mock(return_value=False)
    @contextmanager
    def get_connection():
        yield connection
    repo = Mock(get_connection=get_connection)
    assert append_paper(repo, 'collection', 'paper') == 3
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert 'FOR UPDATE' in statements[0]
    assert 'sequence_order = collection_papers.sequence_order' in statements[1]


def test_picker_in_browser(app, db, monkeypatch):
    import os
    import subprocess
    import threading
    from pathlib import Path
    from werkzeug.serving import make_server
    if os.getenv('RUN_PICKER_BROWSER') != '1':
        pytest.skip('Set RUN_PICKER_BROWSER=1 with Playwright Chromium installed')
    owner = db.get_or_create_user(DEV_EMAIL)
    collection = db.create_collection(owner['user_id'], 'AI Evaluations')
    papers = [db.seed_paper(title=title) for title in ['Already saved', 'Evaluation benchmarks', '<script>alert(1)</script> Safety assessment']]
    db.append_paper_to_collection(collection['collection_id'], papers[0]['paper_id'])
    monkeypatch.setattr(workspace_paper_service, 'search', lambda *a, **k: {
        'items': [{**p, 'authors': 'A. Researcher'} for p in papers], 'next_cursor': None})
    server = make_server('127.0.0.1', 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = os.environ.copy()
    env['PICKER_TEST_URL'] = f"http://127.0.0.1:{server.server_port}/collection/{collection['collection_id']}"
    try:
        subprocess.run(['node', str(Path(__file__).with_name('paper_picker_browser.cjs'))], env=env, check=True, timeout=45)
    finally:
        server.shutdown()
        thread.join(timeout=5)
    assert len([key for key in db.collection_papers if key[0] == collection['collection_id']]) == 3
