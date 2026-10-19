"""Application adapter for private document collection membership."""

from exceptions import ValidationError
from repositories import document_collections as repository
from services import upload_service, collection_service

try:
    from shared_resource.services.document_collection_service import change
except ModuleNotFoundError:
    from mcp_server.shared_resource.services.document_collection_service import change


def modify(owner, collection_id, document_id, *, remove=False):
    upload_service.require_enabled()
    collection_id = upload_service.identifier(collection_id)
    document_id = upload_service.identifier(document_id)
    collection_service._require_writable_collection(collection_id, owner)
    try:
        return change(repository, owner, collection_id, document_id, remove=remove)
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("Invalid document membership reference.") from None


def list_documents(owner, collection_id):
    upload_service.require_enabled()
    collection_service._require_writable_collection(collection_id, owner)
    return repository.list_owned(owner, collection_id)
