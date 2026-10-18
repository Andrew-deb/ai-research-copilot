"""Conversation ZIP exports keep the same ownership and size boundaries."""
from io import BytesIO
from zipfile import ZipFile
from unittest.mock import Mock
import pytest
from exceptions import ValidationError, UploadNotFoundError
from services import upload_archive as archive
from services import upload_service


@pytest.fixture
def files(monkeypatch):
    monkeypatch.setattr(upload_service, 'require_enabled', lambda: None)
    monkeypatch.setattr(upload_service, 'list_files', lambda *args: [
        {'document_id': 'a', 'status': 'ready'}, {'document_id': 'b', 'status': 'ready'},
        {'document_id': 'c', 'status': 'expired'}])
    download = Mock(side_effect=lambda owner, doc: ({'filename': '../same.txt', 'conversation_id': 'chat'}, doc.encode()))
    monkeypatch.setattr(upload_service, 'download', download)
    return download


def test_archive_deduplicates_safe_names_and_excludes_expired(files):
    with archive.build('owner', 'chat') as output:
        with ZipFile(output) as result:
            assert result.namelist() == ['same.txt', 'same (2).txt']
            assert result.read('same.txt') == b'a'
            assert result.read('same (2).txt') == b'b'
    assert [call.args for call in files.call_args_list] == [('owner', 'a'), ('owner', 'b')]


def test_archive_refuses_foreign_conversation(files):
    files.side_effect = None
    files.return_value = ({'filename': 'a.txt', 'conversation_id': 'other'}, b'a')
    with pytest.raises(ValidationError):
        archive.build('owner', 'chat')


def test_archive_preserves_owner_refusal(monkeypatch):
    monkeypatch.setattr(upload_service, 'require_enabled', lambda: None)
    monkeypatch.setattr(upload_service, 'list_files', Mock(side_effect=UploadNotFoundError('Unavailable')))
    with pytest.raises(UploadNotFoundError):
        archive.build('other', 'chat')


def test_archive_has_no_partial_success_on_download_error(files):
    files.side_effect = UploadNotFoundError('Expired during download')
    with pytest.raises(UploadNotFoundError):
        archive.build('owner', 'chat')


def test_archive_refuses_empty_and_over_limit(monkeypatch, files):
    for items in ([], [{'status':'ready', 'document_id':str(i)} for i in range(4)]):
        monkeypatch.setattr(upload_service, 'list_files', lambda *args: items)
        with pytest.raises(ValidationError):
            archive.build('owner', 'chat')
    files.assert_not_called()


def test_archive_route_returns_private_zip(client, monkeypatch):
    monkeypatch.setattr(archive, 'build', lambda *args: BytesIO(b'PK-test'))
    response = client.get('/uploads/conversations/00000000-0000-0000-0000-000000000001/download')
    assert response.status_code == 200
    assert response.mimetype == 'application/zip'
    assert response.data == b'PK-test'
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert response.headers['Content-Disposition'].startswith('attachment;')


def test_archive_route_is_unavailable_anonymously(anon_client, monkeypatch):
    build = Mock()
    monkeypatch.setattr(archive, 'build', build)
    response = anon_client.get('/uploads/conversations/00000000-0000-0000-0000-000000000001/download')
    assert response.status_code == 403
    build.assert_not_called()


def test_archive_rejects_oversized_original(files, monkeypatch):
    from dataclasses import replace
    monkeypatch.setattr(archive.upload_config, 'LIMITS', replace(archive.upload_config.LIMITS, max_file_bytes=1))
    files.side_effect = None
    files.return_value = ({'filename': 'a.txt', 'conversation_id': 'chat'}, b'oversized')
    with pytest.raises(ValidationError):
        archive.build('owner', 'chat')
