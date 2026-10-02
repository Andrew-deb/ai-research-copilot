"""Shared note writes validate first, preserve fields and enforce ownership in SQL."""
from unittest.mock import Mock
import pytest
from mcp_server.shared_resource.services import note_service
from mcp_server.shared_resource.repositories import note_mutation_repository
from mcp_server.shared_resource.exceptions import ValidationError, NoteNotFoundError

@pytest.mark.parametrize('text,title,tags', [('',None,None), ('x'*10001,None,None), ('Body','x'*201,None), ('Body',1,None), ('Body',None,[object()]), ('Body',None,['x'*41]), ('Body',None,[str(i) for i in range(13)])])
def test_invalid_replacement_never_writes(text,title,tags):
    repo=Mock()
    with pytest.raises(ValidationError): note_service.update_note(repo,'owner','note',text,title,tags)
    repo.update_note.assert_not_called()


def test_full_replacement_normalizes_and_returns_persisted_record():
    repo=Mock(); saved={'note_id':'note','note_text':'Body','title':'Title','tags':['lit review'],'pinned':True,'paper_id':'paper'}
    repo.update_note.return_value=saved
    assert note_service.update_note(repo,'owner','note',' Body ',' Title ',[' Lit  Review ','lit review']) == saved
    repo.update_note.assert_called_once_with('owner','note','Body','Title',['lit review'])
    repo.reset_mock()
    note_service.update_note(repo,'owner','note','Body')
    repo.update_note.assert_called_once_with('owner','note','Body',None,[])


@pytest.mark.parametrize('operation',['edit','pin','delete'])
def test_missing_identity_prevents_every_mutation(operation):
    repo=Mock()
    with pytest.raises(ValidationError):
        if operation=='edit': note_service.update_note(repo,None,'note','Body')
        elif operation=='pin': note_service.set_pinned(repo,None,'note',True)
        else: note_service.delete_note(repo,None,'note')
    assert repo.mock_calls == []


@pytest.mark.parametrize('operation',['edit','pin','delete'])
def test_missing_or_foreign_note_never_reports_success(operation):
    repo=Mock();repo.update_note.return_value=None;repo.set_note_pinned.return_value=None;repo.delete_note.return_value=False
    with pytest.raises(NoteNotFoundError):
        if operation=='edit': note_service.update_note(repo,'foreign','note','Body')
        elif operation=='pin': note_service.set_pinned(repo,'foreign','note',True)
        else: note_service.delete_note(repo,'foreign','note')


def test_pin_is_explicit_and_delete_receipt_requires_persistence():
    repo=Mock();repo.set_note_pinned.return_value={'note_id':'note','pinned':False}
    assert note_service.set_pinned(repo,'owner','note',False)['pinned'] is False
    repo.set_note_pinned.assert_called_once_with('owner','note',False)
    with pytest.raises(ValidationError):note_service.set_pinned(repo,'owner','note','false')
    repo.delete_note.return_value=True
    assert note_service.delete_note(repo,'owner','note') == {'status':'success','note_id':'note','deleted':True}


@pytest.mark.parametrize('operation',['edit','pin','delete'])
def test_queries_bind_owner_and_target_in_the_same_statement(operation):
    write=Mock(return_value={'note_id':'note'})
    if operation=='edit':note_mutation_repository.update_note(write,'owner','note','Body','Title',['tag'])
    elif operation=='pin':note_mutation_repository.set_note_pinned(write,'owner','note',True)
    else:assert note_mutation_repository.delete_note(write,'owner','note') is True
    sql,params=write.call_args.args
    assert 'WHERE note_id = %s AND user_id = %s' in sql
    assert params[-2:]==('note','owner')
    assert write.call_args.kwargs['returning'] is True


def test_database_failure_propagates_without_a_success_receipt():
    repo=Mock();repo.delete_note.side_effect=RuntimeError('offline')
    with pytest.raises(RuntimeError,match='offline'):note_service.delete_note(repo,'owner','note')

@pytest.mark.parametrize('runtime',['dashboard','mcp'])
def test_runtime_adapters_use_shared_queries_with_their_own_executor(monkeypatch,runtime):
    if runtime=='dashboard':
        from repositories import lakebase
    else:
        from mcp_server.shared_resource.repositories import lakebase
    saved={'note_id':'note','user_id':'owner'}
    write=Mock(return_value=saved)
    monkeypatch.setattr(lakebase,'run_write',write)
    assert lakebase.update_note('owner','note','Body','Title',['tag']) == saved
    assert lakebase.set_note_pinned('owner','note',False) == saved
    assert lakebase.delete_note('owner','note') is True
    assert write.call_count == 3
    for call in write.call_args_list:
        assert 'WHERE note_id = %s AND user_id = %s' in call.args[0]
        assert call.args[1][-2:] == ('note','owner')
