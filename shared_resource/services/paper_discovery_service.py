"""Read-only provider candidates and explicit, verified metadata import."""
import re
import requests

from ..brokers import openalex_broker
from ..exceptions import ValidationError, ExternalAPIError, PaperNotFoundError
from ..repositories import paper_import_repository


def work_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'W[0-9]{1,20}', value):
        raise ValidationError('Select a valid OpenAlex work identifier from discovery.')
    return value


def validate_search(query, limit=10, page=1):
    if not isinstance(query, str) or not query.strip() or len(query) > 300:
        raise ValidationError('Enter a paper search between 1 and 300 characters.')
    if type(limit) is not int or not 1 <= limit <= 20 or type(page) is not int or not 1 <= page <= 5:
        raise ValidationError('Discovery supports 1–20 results per page and at most five pages.')
    return query.strip()


def discover_papers(query, limit=10, page=1):
    query = validate_search(query, limit, page)
    try:
        papers = openalex_broker.search_works(query.strip(), per_page=limit, page=page)
    except requests.RequestException as exc:
        raise ExternalAPIError('External discovery is unavailable or its allowance was reached. Try again later.') from exc
    items = []
    for paper in papers[:limit]:
        identifier = paper.get('openalex_id')
        if not isinstance(identifier, str) or not re.fullmatch(r'W[0-9]{1,20}', identifier):
            continue
        items.append({
            'openalex_id': identifier, 'title': paper.get('title') or 'Untitled work',
            'doi': paper.get('doi'), 'publication_year': paper.get('publication_year'),
            'venue': paper.get('venue'), 'abstract': (paper.get('abstract') or '')[:6000],
            'authors': ', '.join(a.get('display_name', '') for a in paper.get('_authors', []) if a.get('display_name')),
            'source': 'openalex', 'url': 'https://openalex.org/' + identifier,
        })
    return {'items': items, 'search_space': 'external_openalex', 'query': query.strip(),
            'next_page': page + 1 if len(papers) >= limit and page < 5 else None,
            'imports_performed': 0}


def import_paper(repository, user_id, openalex_id):
    if not user_id:
        raise ValidationError('Sign in to import papers.')
    identifier = work_id(openalex_id)
    try:
        paper = openalex_broker.get_work(identifier)
    except requests.RequestException as exc:
        raise ExternalAPIError('The selected paper could not be verified with OpenAlex. No import was attempted.') from exc
    if not paper:
        raise PaperNotFoundError('That OpenAlex paper is no longer available.')
    if paper.get('openalex_id') != identifier or not paper.get('title'):
        raise ValidationError('The provider returned an inconsistent paper identity.')
    # The model/client supplies only an ID; trusted provider data supplies metadata.
    result = paper_import_repository.import_metadata(repository, paper)
    return {**result, 'status': 'success', 'source': 'openalex',
            'full_text_imported': False, 'indexing_performed': False}
