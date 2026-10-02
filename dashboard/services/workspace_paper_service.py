"""Dashboard adapter for shared corpus retrieval; local and artifact imports."""
try:
    from shared_resource.services import paper_search_service, collection_service as domain_collections
    from shared_resource import exceptions as domain_errors
    from shared_resource.exceptions import ValidationError as DomainValidationError
except ModuleNotFoundError:
    from mcp_server.shared_resource.services import paper_search_service, collection_service as domain_collections
    from mcp_server.shared_resource import exceptions as domain_errors
    from mcp_server.shared_resource.exceptions import ValidationError as DomainValidationError

from exceptions import ValidationError, CollectionNotFoundError, PaperNotFoundError
from repositories import lakebase


def search(user_id, query='', saved_only=False, limit=20, cursor=None):
    try:
        return paper_search_service.search_workspace_papers(lakebase, user_id, query, saved_only, limit, cursor)
    except DomainValidationError as exc:
        raise ValidationError(str(exc)) from exc


def append(user_id, collection_id, paper_id):
    # The dashboard performs its read-only/curated check before this adapter.
    # Both interfaces then execute this same shared membership service.
    try:
        result = domain_collections.append_paper_to_collection(lakebase, collection_id, paper_id, user_id)
    except domain_errors.ValidationError as exc:
        raise ValidationError(str(exc)) from exc
    except domain_errors.CollectionNotFoundError as exc:
        raise CollectionNotFoundError(str(exc)) from exc
    except domain_errors.PaperNotFoundError as exc:
        raise PaperNotFoundError(str(exc)) from exc
    return {**result, "status": "ok"}
