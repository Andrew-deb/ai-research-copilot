"""Runtime storage wiring. Migration adds a provider adapter without service edits."""

import os
from functools import lru_cache
from urllib.parse import urlsplit
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
    url = os.getenv("AZURE_STORAGE_ACCOUNT_URL", "")
    parsed = urlsplit(url)
    values = [
        os.getenv(k)
        for k in (
            "AZURE_STORAGE_CONTAINER",
            "AZURE_TENANT_ID",
            "AZURE_CLIENT_ID",
            "AZURE_CLIENT_SECRET",
        )
    ]
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".blob.core.windows.net")
        or parsed.query
        or parsed.fragment
        or parsed.username
        or not all(values)
    ):
        raise StorageUnavailable("Private uploads have not been configured.")
    return AzureBlobStorage(url, *values)
