"""Complete-order transactions and user-selected autonomous run boundaries."""
from contextlib import contextmanager
from unittest.mock import Mock
import pytest
from exceptions import ValidationError as AppValidationError
from services import action_approval_service as policy, collection_service
from mcp_server.shared_resource.repositories import collection_order_repository as ordering
from mcp_server.shared_resource.exceptions import ValidationError
from mcp_server.shared_resource.services.collection_service import reorder_collection

ID = 'de4bb1d9-9818-4d77-a9a9-31b8bc6d8882'
OTHER = 'de4bb1d9-9818-4d77-a9a9-31b8bc6d8883'

@pytest.mark.parametrize('ids', [[],[ID,ID],['bad'],{},[ID]*1001])
def test_invalid_order_never_reaches_write(ids, monkeypatch):
    db = Mock(); db.get_collection.return_value = {'name':'Owned'}
    monkeypatch.setattr(ordering,'reorder',lambda *args: pytest.fail('Write attempted'))
    with pytest.raises(ValidationError): reorder_collection(db,'owner','collection',ids)

@pytest.mark.parametrize('current,owned,valid', [([ID,OTHER],True,True),([ID],True,False),([ID,OTHER],False,False)])
def test_locked_order_requires_owner_and_complete_membership(current, owned, valid):
    cur = Mock(); cur.__enter__=Mock(return_value=cur);cur.__exit__=Mock(return_value=False)
    cur.fetchone.return_value = ('collection',) if owned else None
    cur.fetchall.return_value = [(id,) for id in current];cur.rowcount=2
    conn = Mock();conn.cursor.return_value=cur
    db = Mock()
    @contextmanager
    def connection(): yield conn
    db.get_connection = connection
    if valid: assert ordering.reorder(db,'owner','collection',[OTHER,ID]) == 2
    else:
        with pytest.raises(ValidationError): ordering.reorder(db,'owner','collection',[OTHER,ID])
    first = cur.execute.call_args_list[0].args
    assert 'FOR UPDATE' in first[0] and 'user_id=%s' in first[0] and 'NOT is_curated' in first[0]
    assert first[1] == ('collection','owner')
    assert any('UPDATE collection_papers' in call.args[0] for call in cur.execute.call_args_list) == valid


def test_autonomous_requires_valid_current_target_and_does_not_create_grant(monkeypatch):
    monkeypatch.setattr(policy.config,'mcp_endpoint',lambda mode:'wick')
    monkeypatch.setattr(policy.assistant_context,'bundle',lambda *args:{'items':[{'available':True,'label':'Owned'}]})
    monkeypatch.setattr(policy.action_approvals,'has_grant',lambda *args:pytest.fail('Autonomous should not consult or create grants'))
    policy.guard('owner','delete_note',{'note_id':ID},{},mode='autonomous')
    monkeypatch.setattr(policy.assistant_context,'bundle',lambda *args:{'items':[{'available':False}]})
    with pytest.raises(AppValidationError):policy.guard('owner','delete_note',{'note_id':ID},{},mode='autonomous')
    with pytest.raises(AppValidationError):policy.guard('owner','create_note',{}, {},mode='invalid')


def test_ask_after_autonomous_pauses_again_without_grant(monkeypatch):
    monkeypatch.setattr(policy.config,'mcp_endpoint',lambda mode:'wick')
    monkeypatch.setattr(policy.action_approvals,'has_grant',lambda *args:False)
    policy.guard('owner','create_note',{'note_text':'body'},{},mode='autonomous')
    with pytest.raises(policy.ApprovalRequired):policy.guard('owner','create_note',{'note_text':'body'},{},mode='ask')

@pytest.mark.parametrize('mode,chat_mode,status', [('invalid','wick',400),('autonomous','research',403)])
def test_invalid_or_research_autonomy_is_refused_before_execution(client,monkeypatch,mode,chat_mode,status):
    from routes import chat
    monkeypatch.setattr(chat,'_run_turn',lambda *a,**k:pytest.fail('Ran'))
    response = client.post('/chat/ask',json={'question':'Help','chat_mode':chat_mode,'approval_mode':mode})
    assert response.status_code==status


def test_authenticated_composer_choice_reaches_only_its_run(client, db, monkeypatch):
    from routes import chat
    from tests.conftest import DEV_EMAIL
    owner = str(db.get_or_create_user(DEV_EMAIL)['user_id'])
    cid = str(db.create_conversation(owner,'Wick',origin='assistant')['conversation_id'])
    monkeypatch.setattr(chat.agent_service,'is_connected',lambda *args:True)
    monkeypatch.setattr(chat,'consume_quota',lambda *args:None)
    captured=[]
    def run(*args,**kwargs):
        captured.append(kwargs.get('approval_mode','ask'))
        return chat.agent_service.envelope('Help',answer='Done')
    monkeypatch.setattr(chat,'_run_turn',run)
    for mode in ['autonomous','ask']:
        response=client.post('/chat/ask',json={'question':'Help','conversation_id':cid,'chat_mode':'wick','approval_mode':mode})
        assert response.status_code==200
    assert captured==['autonomous','ask']


def test_new_view_defaults_to_ask_and_offers_two_supported_options(client):
    text=client.get('/chat?mode=wick').get_data(as_text=True)
    assert 'id="wick-permission" value="ask"' in text
    assert 'data-permission="autonomous"' in text
    assert text.index('id="wick-permission-picker"') < text.index('id="chat-mode"')
