"""Search existing corpus papers without discovery or imports."""
from ..exceptions import ValidationError
from ..repositories import paper_search_repository
from .workspace_service import page_bounds, page_result


def search_workspace_papers(db, user_id=None, query='', saved_only=False, limit=20, cursor=None):
    limit, offset = page_bounds(limit, cursor)
    if not isinstance(query, str) or len(query) > 300:
        raise ValidationError('Paper search must be at most 300 characters.')
    if not isinstance(saved_only, bool):
        raise ValidationError('saved_only must be a boolean.')
    if saved_only and not user_id:
        raise ValidationError('Sign in to search your saved papers.')
    query = query.strip()
    result = page_result(paper_search_repository.search(db, user_id, query, saved_only, limit + 1, offset), limit, offset)
    result.update(query=query, search_space='saved_papers' if saved_only else 'existing_corpus',
                  retrieval='ranked_keyword')
    return result
