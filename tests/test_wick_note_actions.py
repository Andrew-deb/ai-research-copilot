"""Note tools reuse shared mutations and stay inside Wick's approval boundary."""
import subprocess
import sys
from pathlib import Path

import pytest

from exceptions import CapabilityDeniedError
from services import agent_service, action_approval_service as approvals


@pytest.mark.parametrize('tool', ['edit_note','set_note_pinned','delete_note'])
def test_note_mutations_are_wick_only_and_authenticated(tool, monkeypatch):
    agent_service.ensure_callable('authenticated', tool, 'wick')
    for tier,mode in [('anonymous','wick'),('authenticated','research')]:
        with pytest.raises(CapabilityDeniedError):
            agent_service.ensure_callable(tier,tool,mode)
    monkeypatch.setattr(agent_service,'WRITE_TOOLS_ENABLED',False)
    with pytest.raises(CapabilityDeniedError):
        agent_service.ensure_callable('authenticated',tool,'wick')


@pytest.mark.parametrize('tool,arguments', [
    ('edit_note', {'note_text':'Replacement','title':'Retained','tags':['one']}),
    ('set_note_pinned', {'pinned':False}),
    ('delete_note', {}),
])
def test_note_mutations_pause_on_exact_owned_target(tool, arguments, monkeypatch):
    note = 'a851a799-d1d7-41ab-a557-a8f5d8a8834a'
    resolved = []
    def bundle(owner,refs):
        resolved.append((owner,refs))
        return {'items':[{'available':True,'label':'My note'}]}
    monkeypatch.setattr(approvals.assistant_context,'bundle',bundle)
    monkeypatch.setattr(approvals.config,'mcp_endpoint',lambda mode:'wick-url')
    monkeypatch.setattr(approvals.action_approvals,'has_grant',lambda *args:False)
    args = {'note_id':note,**arguments}
    with pytest.raises(approvals.ApprovalRequired) as pause:
        approvals.guard('owner',tool,args,{'pending':['call']})
    assert pause.value.proposal['arguments'] == args
    assert pause.value.proposal['target_label'] == 'My note'
    assert resolved[0] == ('owner',[{'kind':'note','id':note}])
    permit = {**pause.value.proposal,'decision':'once'}
    approvals.guard('owner',tool,args,{},permit)
    assert permit == {}


def test_note_adapters_bind_actor_and_use_existing_shared_validation():
    script = '''
from unittest.mock import Mock
from assistant import server
from shared_resource.middleware.request_context import set_current_user_id, clear_current_user
from shared_resource.exceptions import NoteNotFoundError, ValidationError
set_current_user_id('owner')
server.lakebase.update_note = Mock(return_value={'note_id':'note','note_text':'Body','title':'Title','tags':['tag']})
assert server.edit_note('note',' Body ',' Title ',[' tag '])['note_text'] == 'Body'
server.lakebase.update_note.assert_called_once_with('owner','note','Body','Title',['tag'])
server.lakebase.set_note_pinned = Mock(return_value={'note_id':'note','is_pinned':False})
assert server.set_note_pinned('note',False)['is_pinned'] is False
server.lakebase.set_note_pinned.assert_called_once_with('owner','note',False)
try: server.set_note_pinned('note','false')
except ValidationError: pass
else: raise AssertionError('String pin state accepted')
assert server.lakebase.set_note_pinned.call_count == 1
server.lakebase.delete_note = Mock(return_value=True)
assert server.delete_note('note') == {'status':'success','note_id':'note','deleted':True}
server.lakebase.delete_note.assert_called_once_with('owner','note')
server.lakebase.delete_note.return_value = False
try: server.delete_note('foreign')
except NoteNotFoundError: pass
else: raise AssertionError('Missing/foreign target reported deleted')
clear_current_user()
for name,args in [('edit_note',('note','Body')),('set_note_pinned',('note',True)),('delete_note',('note',))]:
    try: getattr(server,name)(*args)
    except PermissionError: pass
    else: raise AssertionError('Anonymous note mutation executed')
'''
    subprocess.run([sys.executable,'-c',script], cwd=Path(__file__).parents[1]/'mcp_server',
                   check=True,capture_output=True,text=True)
