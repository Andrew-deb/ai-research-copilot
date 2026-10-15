"""Runtime storage wiring. Migration adds a provider adapter without service edits."""

import os
from functools import lru_cache
import upload_config

try:
    from shared_resource.storage.azure_blob import AzureBlobStorage
    from shared_resource.storage.contract import StorageUnavailable
except ModuleNotFoundError:
    from mcp_server.shared_resource.storage.azure_blob import AzureBlobStorage
    from mcp_server.shared_resource.storage.contract import StorageUnavailable


@lru_cache(maxsize=4)
def for_location(location):
    # Resolve each row's location, not just a global active-provider switch.
    if location != upload_config.LOCATION_ID:
        raise StorageUnavailable("This file storage location is unavailable.")
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    container = os.getenv("AZURE_STORAGE_CONTAINER", "").strip()
    if not connection_string or not container:
        raise StorageUnavailable("Private uploads have not been configured.")
    return AzureBlobStorage(connection_string, container)
