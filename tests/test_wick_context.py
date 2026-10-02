"""Context persistence, ownership, stale assets and bounded grounding."""
import json
import pytest
from services import assistant_context
from exceptions import ValidationError
from tests.conftest import DEV_EMAIL


def owned_chat(db):
    owner = str(db.get_or_create_user(DEV_EMAIL)['user_id'])
    chat = db.create_conversation(owner, 'Wick', origin='assistant')
    return owner, str(chat['conversation_id'])


def test_context_persists_between_surfaces_and_ignores_page_override(client, db):
    owner, cid = owned_chat(db)
    paper = db.seed_paper(title='Durable selected paper')
    refs = [{'kind': 'paper', 'id': str(paper['paper_id'])}]
    response = client.post('/chat/' + cid + '/context', json={'references': refs})
    assert response.status_code == 200
    assert assistant_context.load(owner, cid) == refs
    for url in ['/chat/' + cid, '/chat/assistant?conversation_id=' + cid]:
        response = client.get(url + ('&' if '?' in url else '?') + 'context_kind=dashboard')
        assert response.status_code == 200
        assert b'Durable selected paper' in response.data
        assert b'id="wick-add-context"' in response.data
    client.post('/chat/' + cid + '/context', json={'references': []})
    assert assistant_context.load(owner, cid) == []
    assert b'Durable selected paper' not in client.get('/chat/' + cid).data


def test_foreign_conversation_cannot_be_changed_or_opened(client, db):
    foreign = str(db.get_or_create_user('elsewhere@example.com')['user_id'])
    cid = str(db.create_conversation(foreign, 'Private')['conversation_id'])
    assert client.post('/chat/' + cid + '/context', json={'references': []}).status_code == 400
    assert client.get('/chat/assistant?conversation_id=' + cid).status_code == 404


@pytest.mark.parametrize('refs', [None, {}, [{'kind': 'paper', 'id': 'invalid'}],
    [{'kind': 'page', 'id': 'dashboard', 'content': 'injected'}],
    [{'kind': 'page', 'id': 'unknown'}], [{'kind': 'page', 'id': 'dashboard'}] * 6])
def test_invalid_context_rejected(refs):
    with pytest.raises(ValidationError):
        assistant_context.normalize(refs)


def test_deleted_and_foreign_assets_have_no_content(db):
    owner, cid = owned_chat(db)
    other = str(db.get_or_create_user('other@example.com')['user_id'])
    collection = db.create_collection(other, 'Secret title')
    refs = [{'kind': 'collection', 'id': str(collection['collection_id'])}]
    result = assistant_context.bundle(owner, refs)
    assert result['items'][0]['available'] is False
    assert 'Secret title' not in json.dumps(result)
    paper = db.seed_paper(title='Soon deleted')
    refs = [{'kind': 'paper', 'id': str(paper['paper_id'])}]
    assistant_context.save(owner, cid, refs)
    del db.papers[str(paper['paper_id'])]
    assert assistant_context.bundle(owner, assistant_context.load(owner, cid))['items'][0]['available'] is False


def test_content_is_fresh_and_bounded(db):
    owner, _ = owned_chat(db)
    paper = db.seed_paper(title='Original', abstract='a' * 20000)
    refs = [{'kind': 'paper', 'id': str(paper['paper_id'])}]
    item = assistant_context.bundle(owner, refs)['items'][0]
    assert len(item['content']) <= assistant_context.MAX_ASSET_CHARS
    assert item['truncated']
    db.papers[str(paper['paper_id'])]['title'] = 'Changed'
    assert assistant_context.bundle(owner, refs)['items'][0]['label'] == 'Changed'


def test_asset_search_delegates_shared_owner_scoped_contract(client, db, monkeypatch):
    seen = []
    def find(repository, owner, query, kinds, limit, cursor):
        seen.append((owner, query, kinds, limit, cursor))
        return {'items': [], 'next_cursor': None}
    monkeypatch.setattr(assistant_context.shared_workspace, 'find_workspace_resources', find)
    result = client.get('/chat/assistant/assets?q=abc&kind=note&cursor=20')
    assert result.status_code == 200
    assert seen[0][0] == str(db.get_or_create_user(DEV_EMAIL)['user_id'])
    assert seen[0][1:] == ('abc', ['note'], 20, '20')


def test_turn_uses_persisted_context_when_client_omits_it(client, db, monkeypatch):
    from routes import chat
    owner, cid = owned_chat(db)
    refs = [{'kind': 'page', 'id': 'notes'}]
    assistant_context.save(owner, cid, refs)
    monkeypatch.setattr(chat.agent_service, 'is_connected', lambda mode='research': True)
    monkeypatch.setattr(chat, 'consume_quota', lambda metric: None)
    captured = []
    def run(*args, **kwargs):
        captured.append(kwargs['context'])
        return {'status': 'ok', 'answer': 'Done', 'citations': [], 'sources': [], 'tool_calls': [], 'usage': {}}
    monkeypatch.setattr(chat, '_run_turn', run)
    response = client.post('/chat/ask', json={'question': 'Show notes', 'chat_mode': 'wick', 'conversation_id': cid})
    assert response.status_code == 200
    assert captured[0]['references'] == refs
    assert captured[0]['items'][0]['available']


def test_context_repository_binds_owner_and_only_stores_references(monkeypatch):
    from repositories import conversation_context
    seen = []
    monkeypatch.setattr(conversation_context.lakebase, 'run_write', lambda sql, params: seen.append((sql, params)))
    refs = [{'kind': 'page', 'id': 'dashboard'}]
    conversation_context.save('owner', 'conversation', refs)
    assert 'user_id = %s' in seen[0][0]
    assert seen[0][1] == (json.dumps(refs), 'conversation', 'owner')


def test_new_foreign_selection_is_rejected_and_existing_deleted_item_can_be_removed(client, db):
    owner, cid = owned_chat(db)
    other = str(db.get_or_create_user('foreign-context@example.com')['user_id'])
    private = db.create_collection(other, 'Secret')
    refs = [{'kind': 'collection', 'id': str(private['collection_id'])}]
    assert client.post('/chat/' + cid + '/context', json={'references': refs}).status_code == 400
    assert assistant_context.load(owner, cid) == []
    paper = db.seed_paper(title='Deleted later')
    refs = [{'kind': 'paper', 'id': str(paper['paper_id'])}, {'kind': 'page', 'id': 'dashboard'}]
    assistant_context.save(owner, cid, refs)
    del db.papers[str(paper['paper_id'])]
    assert client.post('/chat/' + cid + '/context', json={'references': refs}).status_code == 200
    assert client.post('/chat/' + cid + '/context', json={'references': refs[1:]}).status_code == 200


def test_foreign_turn_context_rejected_before_quota_or_agent(client, db, monkeypatch):
    from routes import chat
    other = str(db.get_or_create_user('not-current@example.com')['user_id'])
    private = db.create_collection(other, 'Not yours')
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid context must be refused before execution')
    monkeypatch.setattr(chat, 'consume_quota', forbidden)
    monkeypatch.setattr(chat.agent_service, 'is_connected', forbidden)
    response = client.post('/chat/ask', json={'question': 'Read this', 'chat_mode': 'wick',
        'context_references': [{'kind': 'collection', 'id': str(private['collection_id'])}]})
    assert response.status_code == 400


def test_serialized_context_budget_preserves_every_reference(db):
    owner, _ = owned_chat(db)
    refs = []
    for index in range(5):
        paper = db.seed_paper(title='Paper ' + str(index), abstract='"\\' * 10000)
        refs.append({'kind': 'paper', 'id': str(paper['paper_id'])})
    result = assistant_context.bundle(owner, refs)
    assert len(json.dumps(result, ensure_ascii=False)) <= assistant_context.MAX_PAYLOAD_CHARS
    assert result['references'] == refs
    assert len(result['items']) == 5


def test_expanding_unsent_panel_preserves_all_references_without_get_mutation(client, db):
    from urllib.parse import urlencode
    owner, cid = owned_chat(db)
    paper = db.seed_paper(title='Expansion paper')
    refs = [{'kind': 'page', 'id': 'dashboard'}, {'kind': 'paper', 'id': str(paper['paper_id'])}]
    response = client.get('/chat/' + cid + '?' + urlencode({'mode': 'wick', 'context_references': json.dumps(refs)}))
    assert response.status_code == 200
    assert b'Expansion paper' in response.data
    assert assistant_context.load(owner, cid) == []
