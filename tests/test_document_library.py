"""Retained documents reuse bytes, preserve owner boundaries and coordinate expiry."""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import uuid4
import pytest
import upload_config
from exceptions import ValidationError, UploadNotFoundError
from repositories import uploads
from services import document_library, upload_service, attachment_context
from tests.test_private_uploads import cursor_repo


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(upload_config, 'ENABLED', True)


@pytest.fixture
def row():
    return {'document_id': str(uuid4()), 'conversation_id': str(uuid4()), 'filename': 'private.pdf',
            'original_bytes': 100, 'status': 'ready', 'expired': False,
            'expires_at': datetime.now(timezone.utc) + timedelta(days=7), 'retained_at': None}


def test_save_preserves_locator_and_reservation_and_returns_no_secrets(monkeypatch, enabled, row):
    monkeypatch.setattr(upload_service, 'owned', Mock(return_value=row))
    saved = dict(row, retained_at=datetime.now(timezone.utc), storage_location_id='private', object_key='secret')
    retain = Mock(return_value=saved)
    monkeypatch.setattr(uploads, 'retain', retain)
    result = document_library.save('owner', row['document_id'])
    retain.assert_called_once_with('owner', row['document_id'])
    assert result['retained'] and result['expires_at'] is None and result['agent_access']
    assert not {'storage_location_id', 'object_key'} & result.keys()


@pytest.mark.parametrize('state', ['queued','processing','failed','deleting','deleted','uploading'])
def test_only_ready_files_can_be_saved(monkeypatch, enabled, row, state):
    row['status'] = state
    monkeypatch.setattr(upload_service, 'owned', lambda *a: row)
    retain = Mock();monkeypatch.setattr(uploads, 'retain', retain)
    with pytest.raises(ValidationError):document_library.save('owner', row['document_id'])
    retain.assert_not_called()


def test_foreign_file_fails_before_mutation(monkeypatch, enabled):
    monkeypatch.setattr(uploads, 'get_owned', lambda *a: None)
    retain=Mock();monkeypatch.setattr(uploads, 'retain', retain)
    with pytest.raises(UploadNotFoundError):document_library.save('foreign', str(uuid4()))
    retain.assert_not_called()


def test_expired_file_cannot_be_resurrected(monkeypatch, enabled, row):
    row['expired']=True
    monkeypatch.setattr(upload_service, 'owned', lambda *a: row)
    with pytest.raises(ValidationError):document_library.save('owner',row['document_id'])


def test_deletion_or_expiry_winning_after_initial_read_is_reported(monkeypatch, enabled, row):
    monkeypatch.setattr(upload_service, 'owned', lambda *a: row)
    monkeypatch.setattr(uploads, 'retain', lambda *a: None)
    with pytest.raises(ValidationError):document_library.save('owner',row['document_id'])


def test_retention_uses_same_document_lock_and_atomic_ready_expiry_guard(monkeypatch, row):
    cur=cursor_repo(monkeypatch,[dict(row,retained_at=datetime.now(timezone.utc))])
    uploads.retain('owner',row['document_id'])
    sql=[call.args[0] for call in cur.execute.call_args_list]
    assert 'pg_advisory_xact_lock' in sql[0] and 'private-upload-job:' in cur.execute.call_args_list[0].args[1][0]
    assert "status='ready'" in sql[1] and 'user_id=%s' in sql[1] and 'expires_at>now()' in sql[1]
    assert 'COALESCE(retained_at,now())' in sql[1]
    assert 'reserved_bytes=' not in sql[1] and 'object_key=' not in sql[1] and 'INSERT' not in sql[1]


def test_cleanup_retains_saved_chat_orphans_but_not_owner_orphans(monkeypatch):
    cur=cursor_repo(monkeypatch,[]);cur.fetchall.return_value=[]
    uploads.enqueue_cleanup();sql=cur.execute.call_args.args[0]
    assert "user_id IS NULL OR (retained_at IS NULL AND (expires_at<=now() OR conversation_id IS NULL))" in sql


def test_retained_expiry_and_owner_scoping_live_in_repository(monkeypatch):
    query=Mock(return_value=[]);monkeypatch.setattr(uploads.lakebase,'run_query',query)
    uploads.get_owned('owner',str(uuid4()))
    assert '(retained_at IS NULL AND expires_at <= now()) AS expired' in query.call_args.args[0]
    uploads.list_retained('owner',25,24)
    sql,args=query.call_args.args
    assert args==('owner',25,24) and 'user_id=%s' in sql and "status NOT IN ('deleted','deleting')" in sql


def test_saved_file_is_idempotent_after_original_expiry(monkeypatch, enabled, row):
    row.update(retained_at=datetime.now(timezone.utc),expires_at=datetime.now(timezone.utc)-timedelta(days=1))
    monkeypatch.setattr(upload_service,'owned',lambda *a:row)
    monkeypatch.setattr(uploads,'retain',lambda *a:row)
    assert document_library.save('owner',row['document_id'])['retained_at']==row['retained_at'].isoformat()


def test_paginated_listing_is_bounded_and_owner_scoped(monkeypatch, enabled, row):
    query=Mock(return_value=[dict(row,retained_at=datetime.now(timezone.utc))]*25)
    monkeypatch.setattr(uploads,'list_retained',query)
    result=document_library.list_documents('owner',2)
    assert len(result['items'])==24 and result['has_more']
    query.assert_called_once_with('owner',25,24)


@pytest.mark.parametrize('page',[0,-1,10001,True,'1'])
def test_bad_page_is_refused_before_query(enabled, page):
    with pytest.raises(ValidationError):document_library.list_documents('owner',page)


def test_anonymous_cannot_list_save_or_open_documents(anon_client, enabled):
    doc=str(uuid4())
    assert anon_client.get('/documents').status_code==403
    assert anon_client.get('/documents/'+doc).status_code==403
    assert anon_client.post('/uploads/'+doc+'/retain',json={}).status_code==403


def test_saving_requires_csrf(app,client,enabled,monkeypatch):
    save=Mock();monkeypatch.setattr(document_library,'save',save)
    app.config['WTF_CSRF_ENABLED']=True
    assert client.post('/uploads/'+str(uuid4())+'/retain',json={}).status_code==400
    save.assert_not_called()


def test_document_library_escapes_filenames_and_exposes_no_storage_keys(client,monkeypatch,enabled,row):
    row.update(retained_at=datetime.now(timezone.utc),filename='<script>bad</script>.pdf')
    monkeypatch.setattr(uploads,'list_retained',lambda *a:[row])
    response=client.get('/documents')
    assert response.status_code==200 and '&lt;script&gt;' in response.text
    assert response.headers['Cache-Control']=='private, no-store'
    assert 'data-delete-document' in response.text and 'Saved to workspace' in response.text


def test_saved_view_remains_accessible_without_original_chat(client,monkeypatch,enabled,row):
    row.update(retained_at=datetime.now(timezone.utc),conversation_id=None)
    monkeypatch.setattr(upload_service,'owned',lambda *a:row)
    response=client.get('/documents/'+row['document_id'])
    assert response.status_code==200 and 'saved-document-preview' in response.text


def test_saved_document_requires_an_owned_target_conversation(
    monkeypatch, enabled, row
):
    row.update(retained_at=datetime.now(timezone.utc))
    monkeypatch.setattr(upload_service,'owned',lambda *a:row)
    monkeypatch.setattr(upload_service.lakebase, "get_conversation", lambda *a: None)
    with pytest.raises(ValidationError):attachment_context.references('owner','different-chat',[row['document_id']])
