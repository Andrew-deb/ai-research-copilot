"""Discovery is read-only; verified imports and membership are separate writes."""
from contextlib import contextmanager
from unittest.mock import Mock
import pytest
import requests
from mcp_server.shared_resource.services import paper_discovery_service as domain
from mcp_server.shared_resource.repositories import paper_import_repository
from mcp_server.shared_resource.exceptions import ValidationError, ExternalAPIError
from services import workspace_paper_service, action_approval_service, agent_service
from tests.conftest import DEV_EMAIL


@pytest.mark.parametrize('query,limit,page', [('',10,1), ('x'*301,10,1), ('x',21,1), ('x',True,1), ('x',10,6)])
def test_invalid_discovery_does_not_call_provider(monkeypatch, query, limit, page):
    provider = Mock(); monkeypatch.setattr(domain.openalex_broker, 'search_works', provider)
    with pytest.raises(ValidationError): domain.discover_papers(query, limit, page)
    provider.assert_not_called()


def test_discovery_returns_bounded_candidates_without_import(monkeypatch):
    provider = Mock(return_value=[{'openalex_id':'W123','title':'Evidence','abstract':'x'*7000,'_authors':[]}] * 2)
    imports = Mock()
    monkeypatch.setattr(domain.openalex_broker, 'search_works', provider)
    monkeypatch.setattr(domain.paper_import_repository, 'import_metadata', imports)
    result = domain.discover_papers(' evidence ', 1, 2)
    assert len(result['items']) == 1 and len(result['items'][0]['abstract']) == 6000
    assert result['next_page'] == 3 and result['imports_performed'] == 0
    provider.assert_called_once_with('evidence', per_page=1, page=2)
    imports.assert_not_called()


def test_provider_failure_is_safe_and_no_import(monkeypatch):
    monkeypatch.setattr(domain.openalex_broker, 'search_works', Mock(side_effect=requests.HTTPError('secret')))
    with pytest.raises(ExternalAPIError, match='unavailable') as error: domain.discover_papers('evidence')
    assert 'secret' not in str(error.value)


@pytest.mark.parametrize('actor,identifier', [(None,'W123'), ('owner','https://evil.example/'), ('owner','W123?x=y')])
def test_invalid_import_has_no_io(monkeypatch, actor, identifier):
    provider = Mock(); monkeypatch.setattr(domain.openalex_broker, 'get_work', provider)
    with pytest.raises(ValidationError): domain.import_paper(Mock(), actor, identifier)
    provider.assert_not_called()


def test_import_refetches_selected_identity_and_returns_local_receipt(monkeypatch):
    paper = {'openalex_id':'W123', 'title':'Verified title'}
    provider = Mock(return_value=paper); importer = Mock(return_value={'paper_id':'local','already_imported':True})
    monkeypatch.setattr(domain.openalex_broker, 'get_work', provider)
    monkeypatch.setattr(domain.paper_import_repository, 'import_metadata', importer)
    repo = Mock(); receipt = domain.import_paper(repo, 'owner', 'W123')
    provider.assert_called_once_with('W123'); importer.assert_called_once_with(repo, paper)
    assert receipt['paper_id'] == 'local' and receipt['already_imported']
    assert not receipt['full_text_imported'] and not receipt['indexing_performed']


def test_provider_identity_mismatch_cannot_write(monkeypatch):
    monkeypatch.setattr(domain.openalex_broker, 'get_work', Mock(return_value={'openalex_id':'W999','title':'Other'}))
    importer = Mock(); monkeypatch.setattr(domain.paper_import_repository, 'import_metadata', importer)
    with pytest.raises(ValidationError): domain.import_paper(Mock(), 'owner', 'W123')
    importer.assert_not_called()


def repository_cursor(matches, returns):
    cursor = Mock(); cursor.fetchall.return_value = matches; cursor.fetchone.side_effect = returns
    connection = Mock(); connection.cursor.return_value.__enter__ = Mock(return_value=cursor)
    connection.cursor.return_value.__exit__ = Mock(return_value=False)
    @contextmanager
    def get_connection(): yield connection
    return Mock(get_connection=get_connection), cursor


def test_import_reuses_doi_match_and_does_not_overwrite():
    repo, cursor = repository_cursor([('existing','Original')], [])
    result = paper_import_repository.import_metadata(repo, {'openalex_id':'W123','title':'New','doi':'https://doi.org/10.A/ABC'})
    assert result == {'paper_id':'existing','title':'Original','already_imported':True}
    assert not any('INSERT' in call.args[0] for call in cursor.execute.call_args_list)
    assert cursor.execute.call_args.args[1] == ('W123','10.a/abc','10.a/abc')


def test_ambiguous_identity_refuses_merge():
    repo, cursor = repository_cursor([('one','A'),('two','B')], [])
    with pytest.raises(ValidationError, match='Multiple'): paper_import_repository.import_metadata(repo, {'openalex_id':'W123'})
    assert not any('INSERT' in call.args[0] for call in cursor.execute.call_args_list)


def test_paper_and_author_relationships_use_one_transaction():
    repo, cursor = repository_cursor([], [('local','Title'), ('author',)])
    result = paper_import_repository.import_metadata(repo, {'openalex_id':'W123','title':'Title','_authors':[{'openalex_id':'A123','display_name':'Author','position':1}]})
    assert result['paper_id'] == 'local' and not result['already_imported']
    statements = [c.args[0] for c in cursor.execute.call_args_list]
    assert any('INSERT INTO paper_authors' in s for s in statements)
    assert 'DO NOTHING' in next(s for s in statements if 'INSERT INTO papers' in s)


def test_concurrent_existing_openalex_receipt_is_accurate():
    repo, cursor = repository_cursor([], [None, ('existing','Title')])
    assert paper_import_repository.import_metadata(repo, {'openalex_id':'W123','title':'Title'})['already_imported']


def test_manual_discovery_and_import_refuse_foreign_before_io(client, db, monkeypatch):
    user = db.get_or_create_user('other@example.com'); collection = db.create_collection(user['user_id'],'Private')
    discover = Mock(); importer = Mock()
    monkeypatch.setattr(workspace_paper_service,'discover',discover)
    monkeypatch.setattr(workspace_paper_service,'import_external',importer)
    base = '/collection/' + collection['collection_id']
    assert client.get(base + '/external-paper-options?q=AI').status_code == 404
    assert client.post(base + '/external-paper-imports',json={'openalex_id':'W123'}).status_code == 404
    discover.assert_not_called(); importer.assert_not_called()


def test_manual_import_and_existing_membership_are_separate(client, db, monkeypatch):
    owner = db.get_or_create_user(DEV_EMAIL); collection = db.create_collection(owner['user_id'],'Mine')
    paper = db.seed_paper(title='Selected external paper')
    importer = Mock(return_value={'paper_id':paper['paper_id'],'already_imported':True})
    monkeypatch.setattr(workspace_paper_service,'import_external',importer)
    response = client.post('/collection/'+collection['collection_id']+'/external-paper-imports',json={'openalex_id':'W123'})
    assert response.status_code == 200 and response.json['paper_id'] == paper['paper_id']
    assert (collection['collection_id'],paper['paper_id']) not in db.collection_papers
    importer.assert_called_once_with(owner['user_id'],'W123')


def test_import_approval_names_shared_catalog_and_is_not_read_only():
    proposal = action_approval_service.proposal('owner','import_external_paper',{'openalex_id':'W123'})
    assert proposal['target_label'] == 'Alfred shared paper catalog'
    assert 'import_external_paper' in agent_service.WRITE_TOOLS
    assert 'discover_external_papers' not in agent_service.WRITE_TOOLS
    with pytest.raises(action_approval_service.ValidationError): action_approval_service.proposal('owner','import_external_paper',{'openalex_id':'invalid'})


@pytest.mark.parametrize('tool', ['discover_external_papers','import_external_paper'])
def test_external_tools_are_authenticated_wick_only(tool):
    agent_service.ensure_callable('authenticated', tool, 'wick')
    for tier, mode in [('anonymous','wick'),('authenticated','research')]:
        with pytest.raises(agent_service.CapabilityDeniedError): agent_service.ensure_callable(tier,tool,mode)


def test_invalid_manual_search_is_not_charged(client, db, monkeypatch):
    from routes import collections
    owner = db.get_or_create_user(DEV_EMAIL); collection = db.create_collection(owner['user_id'],'Mine')
    charge = Mock(); monkeypatch.setattr(collections, 'consume_quota', charge)
    url = '/collection/'+collection['collection_id']+'/external-paper-options'
    assert client.get(url+'?q=').status_code == 400
    assert client.get(url+'?q=AI&page=6').status_code == 400
    charge.assert_not_called()


def test_manual_discovery_uses_search_allowance_after_validation(client, db, monkeypatch):
    from routes import collections
    from services import quota_service
    owner = db.get_or_create_user(DEV_EMAIL); collection = db.create_collection(owner['user_id'],'Mine')
    charge = Mock(); discover = Mock(return_value={'items':[],'next_page':None,'imports_performed':0})
    monkeypatch.setattr(collections,'consume_quota',charge)
    monkeypatch.setattr(workspace_paper_service,'discover',discover)
    response = client.get('/collection/'+collection['collection_id']+'/external-paper-options?q=AI&page=2')
    assert response.status_code == 200
    charge.assert_called_once_with(quota_service.SEMANTIC_SEARCH)
    discover.assert_called_once_with('AI',2)


def test_import_asks_before_write_and_respects_saved_operation_grant(monkeypatch):
    monkeypatch.setattr(action_approval_service.action_approvals,'has_grant',Mock(return_value=False))
    with pytest.raises(action_approval_service.ApprovalRequired) as paused:
        action_approval_service.guard('owner','import_external_paper',{'openalex_id':'W123'},{'checkpoint':True})
    proposed = paused.value.proposal
    assert proposed['tool'] == 'import_external_paper'
    grants = Mock(return_value=True)
    monkeypatch.setattr(action_approval_service.action_approvals,'has_grant',grants)
    action_approval_service.guard('owner','import_external_paper',{'openalex_id':'W456'},{})
    grants.assert_called_once_with('owner',proposed['scope_key'])
    collection_scope = action_approval_service.proposal('owner','create_collection',{'name':'Mine'})['scope_key']
    assert collection_scope != proposed['scope_key']
