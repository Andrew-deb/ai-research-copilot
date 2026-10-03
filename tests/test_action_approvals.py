"""Approvals authorize exact writes, survive transport wrapping, and cannot bypass policy."""
import json
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from exceptions import CapabilityDeniedError, ValidationError
from services import action_approval_service as policy, agent_service, mcp_client
from repositories import action_approvals

TARGET = 'd72afca0-492d-4f02-91c1-431519202098'

@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setattr(policy.config, 'mcp_endpoint', lambda mode: 'https://wick.example/mcp')
    monkeypatch.setattr(policy.assistant_context, 'bundle', lambda owner, refs: {
        'items': [{'available': True, 'label': 'My collection'}]})
    monkeypatch.setattr(action_approvals, 'has_grant', lambda owner, key: False)


def test_once_is_exact_single_use_and_changed_arguments_pause(gate):
    args = {'title': 'First'}
    p = policy.proposal('owner', 'create_collection', args)
    permit = {**p, 'decision': 'once'}
    with pytest.raises(policy.ApprovalRequired):
        policy.guard('owner', 'create_collection', {'title': 'Second'}, {}, permit)
    policy.guard('owner', 'create_collection', args, {}, permit)
    assert permit == {}
    with pytest.raises(policy.ApprovalRequired):
        policy.guard('owner', 'create_collection', args, {}, permit)


def test_scope_binds_endpoint_operation_and_target(gate, monkeypatch):
    first = policy.proposal('owner', 'add_paper_to_collection', {'collection_id': TARGET})
    changed_operation = policy.proposal('owner', 'remove_paper_from_collection', {'collection_id': TARGET})
    changed_target = policy.proposal('owner', 'add_paper_to_collection', {
        'collection_id': 'fd4a8fd8-6972-4386-a199-bf9ae959e0d6'})
    monkeypatch.setattr(policy.config, 'mcp_endpoint', lambda mode: 'https://replacement.example/mcp')
    changed_endpoint = policy.proposal('owner', 'add_paper_to_collection', {'collection_id': TARGET})
    assert len({p['scope_key'] for p in [first, changed_operation, changed_target, changed_endpoint]}) == 4
    with pytest.raises(ValidationError):
        policy.guard('owner', 'add_paper_to_collection', {'collection_id': TARGET}, {}, {**first, 'decision': 'once'})


def test_grant_rechecks_ownership_and_revocation(gate, monkeypatch):
    allowed = [True]
    monkeypatch.setattr(action_approvals, 'has_grant', lambda owner, key: allowed[0])
    policy.guard('owner', 'create_note', {'content': 'Hello'}, {})
    allowed[0] = False
    with pytest.raises(policy.ApprovalRequired):
        policy.guard('owner', 'create_note', {'content': 'Hello'}, {})
    monkeypatch.setattr(policy.assistant_context, 'bundle', lambda *args: {'items': [{'available': False}]})
    allowed[0] = True
    with pytest.raises(ValidationError):
        policy.guard('owner', 'add_paper_to_collection', {'collection_id': TARGET}, {})


def test_decline_is_consumed_and_never_authorizes_mutation(gate):
    args = {'title': 'No'}
    permit = {**policy.proposal('owner', 'create_collection', args), 'decision': 'deny'}
    with pytest.raises(CapabilityDeniedError, match='declined'):
        policy.guard('owner', 'create_collection', args, {}, permit)
    assert not permit


@pytest.mark.parametrize('owner,name,args', [(None,'create_note',{}), ('owner','unknown',{})])
def test_unregistered_or_anonymous_action_has_no_policy(gate, owner, name, args):
    with pytest.raises(CapabilityDeniedError):
        policy.proposal(owner, name, args)


@pytest.mark.parametrize('args', [[], {'value': float('nan')}, {'value': object()}, {'title':'x'*20001}])
def test_invalid_payload_fails_before_persistence(gate, args):
    with pytest.raises(ValidationError):
        policy.proposal('owner','create_collection',args)


def test_transport_task_group_preserves_pause(monkeypatch):
    pause = policy.ApprovalRequired({'tool': 'create_note'}, {'pending': []})
    async def run(self, plan):
        raise ExceptionGroup('transport teardown', [pause])
    monkeypatch.setattr(mcp_client.Turn, '_run', run)
    monkeypatch.setattr(mcp_client.config, 'mcp_endpoint', lambda mode: 'url')
    with pytest.raises(policy.ApprovalRequired) as caught:
        mcp_client.Turn(mode='wick').run(lambda call: None)
    assert caught.value is pause


@pytest.fixture
def loop(monkeypatch, gate):
    state = {'calls': [], 'turns': [], 'messages': []}
    monkeypatch.setattr(agent_service, 'is_connected', lambda mode='research': True)
    names = list(agent_service.WICK_TOOLS)
    monkeypatch.setattr(mcp_client, 'list_tools', lambda **kwargs: [
        {'name': name, 'description': name, 'input_schema': {'type':'object'}} for name in names])
    class Turn:
        def __init__(self, user_id=None, **kwargs): pass
        def run(self, plan):
            def call(name,args):
                state['calls'].append((name,args))
                return {'ok':True}
            return plan(call)
    monkeypatch.setattr(mcp_client,'Turn',Turn)
    def chat(messages, tools, **kwargs):
        state['messages'] = json.loads(json.dumps(messages))
        return state['turns'].pop(0) if state['turns'] else {'content':'Done.'}
    monkeypatch.setattr(agent_service.llm_client,'chat_with_tools',chat)
    return state


def call(name, args, id):
    return {'id':id,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}


def test_batch_resume_does_not_repeat_completed_calls(loop):
    loop['turns'] = [{'tool_calls': [call('create_collection', {'name':'One'}, '1'),
                                    call('create_collection', {'name':'Two'}, '2')]}]
    with pytest.raises(policy.ApprovalRequired) as first:
        agent_service.ask('Create two collections', tier='authenticated', user_id='owner', mode='wick',
            before_tool=lambda n,a,c: policy.guard('owner',n,a,c))
    assert loop['calls'] == []
    first_permit = {**first.value.proposal,'decision':'once'}
    with pytest.raises(policy.ApprovalRequired) as second:
        agent_service.ask('Create two collections', tier='authenticated', user_id='owner', mode='wick',
            resume=first.value.checkpoint, before_tool=lambda n,a,c: policy.guard('owner',n,a,c,first_permit))
    assert loop['calls'] == [('create_collection', {'name':'One'})]
    second_permit = {**second.value.proposal,'decision':'once'}
    result = agent_service.ask('Create two collections', tier='authenticated',user_id='owner',mode='wick',
        resume=second.value.checkpoint,before_tool=lambda n,a,c: policy.guard('owner',n,a,c,second_permit))
    assert loop['calls'] == [('create_collection', {'name':'One'}), ('create_collection', {'name':'Two'})]
    assert result['usage']['tool_calls'] == 2
    assert [m['tool_call_id'] for m in loop['messages'] if m['role']=='tool'] == ['1','2']


def test_write_without_gate_is_refused_before_transport(loop):
    loop['turns'] = [{'tool_calls':[call('create_note',{},'1')]}]
    result = agent_service.ask('Write a note',tier='authenticated',user_id='owner',mode='wick')
    assert not loop['calls']
    assert not result['tool_calls'][0]['ok']


def test_read_does_not_need_approval(loop):
    loop['turns'] = [{'tool_calls':[call('find_workspace_resources',{},'1')]}]
    agent_service.ask('Show collections',tier='authenticated',user_id='owner',mode='wick',
        before_tool=lambda *args: pytest.fail('Reads must not prompt'))
    assert len(loop['calls']) == 1


def test_claim_validates_decision_before_database(monkeypatch):
    monkeypatch.setattr(action_approvals,'get_connection',lambda: pytest.fail('Database called'))
    with pytest.raises(ValueError): action_approvals.claim('id','owner','invented')


def test_claim_locks_run_and_claims_once_before_grant(monkeypatch):
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor); cursor.__exit__ = Mock(return_value=False)
    proposal = {'scope_key':'scoped','scope_label':'Create a note'}
    cursor.fetchone.side_effect = [('run',), (proposal, {'checkpoint':{}})]
    conn = Mock(); conn.cursor.return_value = cursor
    @contextmanager
    def connection(): yield conn
    monkeypatch.setattr(action_approvals,'get_connection',connection)
    assert action_approvals.claim('approval','owner','always')['run_id'] == 'run'
    statements = cursor.execute.call_args_list
    assert 'FOR UPDATE OF r' in statements[0].args[0]
    assert statements[0].args[1] == ('approval','owner','owner')
    assert "state='pending'" in statements[1].args[0]
    assert 'INSERT INTO agent_action_grants' in statements[-1].args[0]
    cursor.fetchone.side_effect = [None]
    cursor.execute.reset_mock()
    assert action_approvals.claim('approval','owner','always') is None
    assert cursor.execute.call_count == 1


def test_measured_turn_propagates_checkpoint_and_counts_only_new_work(monkeypatch):
    from routes import chat
    measured = Mock()
    @contextmanager
    def measure(*args, **kwargs): yield measured
    monkeypatch.setattr(chat.telemetry_service,'measure',measure)
    checkpoint = {'state':{'llm_turns':2},'tool_calls':[{'ok':True}]}
    def ask(*args, **kwargs):
        checkpoint['state']['llm_turns'] += 1
        checkpoint['tool_calls'].append({'ok':True})
        raise policy.ApprovalRequired({},checkpoint)
    monkeypatch.setattr(chat.agent_service,'ask',ask)
    with pytest.raises(policy.ApprovalRequired):
        chat._run_turn('question','authenticated','owner',None,prepared={'history':[]},
                       chat_mode='wick',resume=checkpoint)
    assert measured.llm_turns == 1
    assert measured.tool_calls == 1


def test_json_turn_returns_pending_without_saving_answer(client, db, monkeypatch):
    from routes import chat
    from tests.conftest import DEV_EMAIL
    owner = str(db.get_or_create_user(DEV_EMAIL)['user_id'])
    cid = str(db.create_conversation(owner,'Wick',origin='assistant')['conversation_id'])
    monkeypatch.setattr(chat.agent_service,'is_connected',lambda *args: True)
    monkeypatch.setattr(chat,'consume_quota',lambda metric: None)
    def run(*args, **kwargs): raise policy.ApprovalRequired({'tool':'create_note'},{'pending':[]})
    monkeypatch.setattr(chat,'_run_turn',run)
    saved = []
    monkeypatch.setattr(chat.action_approvals,'pause',lambda run, user, p, job:
        saved.append((user,job)) or {**p,'approval_id':TARGET,'run_id':run})
    response = client.post('/chat/ask',json={'question':'Write a note','chat_mode':'wick','conversation_id':cid})
    assert response.status_code == 200
    assert response.json['status'] == 'awaiting_approval'
    assert saved[0][0] == owner
    assert saved[0][1]['conversation_id'] == cid


@pytest.mark.parametrize('decision', ['bad',None,123])
def test_approval_route_rejects_invalid_decision(client, monkeypatch, decision):
    from routes import chat
    monkeypatch.setattr(chat.action_approvals,'claim',lambda *args: pytest.fail('Claimed'))
    assert client.post('/chat/approvals/'+TARGET,json={'decision':decision}).status_code == 400


def test_expired_or_replayed_approval_does_not_execute(client, monkeypatch):
    from routes import chat
    monkeypatch.setattr(chat.agent_service,'is_connected',lambda *args: True)
    monkeypatch.setattr(chat.action_approvals,'claim',lambda *args: None)
    monkeypatch.setattr(chat,'_run_turn',lambda *a,**k: pytest.fail('Resumed'))
    assert client.post('/chat/approvals/'+TARGET,json={'decision':'once'}).status_code == 409


def test_permission_revocation_uses_current_actor(client, db, monkeypatch):
    from routes import chat
    from tests.conftest import DEV_EMAIL
    owner = str(db.get_or_create_user(DEV_EMAIL)['user_id'])
    calls = []
    monkeypatch.setattr(chat.action_approvals,'revoke',lambda u,g: calls.append((u,g)) or True)
    response = client.post('/chat/permissions/'+TARGET+'/revoke')
    assert response.status_code == 302
    assert calls == [(owner,TARGET)]
    monkeypatch.setattr(chat.action_approvals,'revoke',lambda *args: False)
    assert client.post('/chat/permissions/'+TARGET+'/revoke').status_code == 404


def test_pending_lookup_never_exposes_checkpoint_job(monkeypatch):
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor); cursor.__exit__ = Mock(return_value=False)
    cursor.fetchone.return_value = (TARGET, {'label':'Create note'})
    conn = Mock(); conn.cursor.return_value = cursor
    @contextmanager
    def connection(): yield conn
    monkeypatch.setattr(action_approvals,'get_connection',connection)
    result = action_approvals.pending('run','owner')
    assert 'job' not in result
    sql, params = cursor.execute.call_args.args
    assert params == ('run','owner','owner')
    assert "expires_at>now()" in sql and "r.state='awaiting_approval'" in sql


def test_task_once_resumes_all_pending_writes_but_does_not_grant_future_tasks(loop):
    loop['turns'] = [{'tool_calls': [call('create_collection', {'name':'One'}, '1'),
                                    call('create_collection', {'name':'Two'}, '2')]}]
    with pytest.raises(policy.ApprovalRequired) as paused:
        agent_service.ask('Create two collections', tier='authenticated', user_id='owner', mode='wick',
            before_tool=lambda n,a,c: policy.guard('owner',n,a,c))
    permit = {**paused.value.proposal, 'decision':'once'}
    result = agent_service.ask('Create two collections', tier='authenticated', user_id='owner', mode='wick',
        resume=paused.value.checkpoint,
        before_tool=lambda n,a,c: policy.guard('owner',n,a,c,permit,mode='autonomous'))
    assert result['answer'] == 'Done.'
    assert loop['calls'] == [('create_collection', {'name':'One'}), ('create_collection', {'name':'Two'})]
    assert not permit
    with pytest.raises(policy.ApprovalRequired):
        policy.guard('owner', 'create_collection', {'name':'Three'}, {})


@pytest.mark.parametrize('decision', ['once', 'always'])
def test_approval_continuation_streams_progress_without_consuming_quota(client, db, monkeypatch, decision):
    from routes import chat
    from tests.conftest import DEV_EMAIL
    owner = str(db.get_or_create_user(DEV_EMAIL)['user_id'])
    cid = str(db.create_conversation(owner,'Wick',origin='assistant')['conversation_id'])
    prepared = chat.conversation_service.prepare_turn(owner,cid,'new',None)
    run_id = '40aa3e42-3083-4fa5-a924-c466e13db3b8'
    chat.agent_runs.create(run_id,('user',owner))
    job = dict(question='Create and fill a collection',conversation_id=cid,prepared=prepared,
               mode='new',source_id=None,context=None,surface='assistant',checkpoint={'pending':['write']})
    monkeypatch.setattr(chat.agent_service,'is_connected',lambda *args: True)
    monkeypatch.setattr(chat.action_approvals,'claim',lambda *args: {
        'job':job,'run_id':run_id,'proposal':{'tool':'create_collection'}})
    monkeypatch.setattr(chat,'consume_quota',lambda *args: pytest.fail('Approval consumed quota'))
    def run(question,tier,user,cid,**kwargs):
        assert kwargs['resume'] is job['checkpoint']
        assert kwargs['permit']['decision'] == decision
        assert kwargs['approval_mode'] == ('autonomous' if decision == 'once' else 'ask')
        kwargs['on_event']({'type':'tool_start','name':'create_collection'})
        kwargs['on_event']({'type':'tool_end','name':'create_collection','ok':True})
        return agent_service.envelope(question,answer='Completed.')
    monkeypatch.setattr(chat,'_run_turn',run)
    response = client.post('/chat/approvals/'+TARGET,json={'decision':decision},headers={'Accept':'text/event-stream'})
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert [e['type'] for e in events] == ['conversation','run','status','tool_start','tool_end','done']
    assert events[-1]['result']['answer'] == 'Completed.'
    assert events[-1]['result']['conversation_id'] == cid
    assert len(db.get_conversation_messages(cid)) == 2


def test_always_grant_covers_other_papers_in_same_collection_only(gate, monkeypatch):
    granted = policy.proposal('owner','add_paper_to_collection',{'collection_id':TARGET,'paper_id':'one'})
    monkeypatch.setattr(action_approvals,'has_grant',lambda owner,key: key == granted['scope_key'])
    policy.guard('owner','add_paper_to_collection',{'collection_id':TARGET,'paper_id':'two'}, {})
    with pytest.raises(policy.ApprovalRequired):
        policy.guard('owner','add_paper_to_collection',{
            'collection_id':'fd4a8fd8-6972-4386-a199-bf9ae959e0d6','paper_id':'two'}, {})
    with pytest.raises(policy.ApprovalRequired):
        policy.guard('owner','remove_paper_from_collection',{'collection_id':TARGET,'paper_id':'two'}, {})


def test_continuation_telemetry_is_marked_without_hiding_work(monkeypatch):
    from routes import chat
    captured = []
    monkeypatch.setattr(chat.telemetry_service,'record',lambda **fields: captured.append(fields))
    monkeypatch.setattr(chat.agent_service,'ask',lambda question,**kwargs:
        agent_service.envelope(question,answer='Done',llm_turns=3,tool_calls=[{'ok':True},{'ok':True}]))
    checkpoint = {'state':{'llm_turns':2},'tool_calls':[{'ok':True}]}
    chat._run_turn('question','authenticated','owner',None,prepared={'history':[]},
                   chat_mode='wick',resume=checkpoint)
    assert captured[0]['is_continuation'] is True
    assert captured[0]['llm_turns'] == 1
    assert captured[0]['tool_calls'] == 1
