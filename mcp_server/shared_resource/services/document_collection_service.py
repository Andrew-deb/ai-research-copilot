"""Focused document membership contract, reusable by manual and future MCP adapters."""

from uuid import UUID


def change(repository, owner, collection_id, document_id, *, remove=False):
    # The repository atomically checks both owners; no model-supplied actor exists.
    collection_id = str(UUID(collection_id))
    document_id = str(UUID(document_id))
    return (repository.remove if remove else repository.add)(
        owner, collection_id, document_id
    )
