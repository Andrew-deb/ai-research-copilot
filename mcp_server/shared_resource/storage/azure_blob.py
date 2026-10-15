"""Azure implements byte operations only; private access is enforced by callers."""

from urllib.parse import urlsplit

from .contract import StorageUnavailable


class AzureBlobStorage:
    def __init__(self, connection_string, container):
        from azure.storage.blob import BlobServiceClient

        try:
            client = BlobServiceClient.from_connection_string(
                connection_string,
                connection_timeout=5,
                read_timeout=15,
                retry_total=0,
                max_single_put_size=4 * 1024 * 1024,
            )
            endpoint = urlsplit(client.url)
            if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username:
                raise ValueError("HTTPS storage endpoint required")
            self.container = client.get_container_client(container)
        except Exception:
            # SDK parse errors can contain credentials. Never expose them to callers.
            raise StorageUnavailable(
                "Private file storage could not be configured. Check its connection string."
            ) from None

    def _require_private(self):
        if self.container.get_container_properties().get("public_access") is not None:
            raise StorageUnavailable(
                "The upload container must have public access disabled."
            )

    def put(self, key, content, content_type):
        from azure.storage.blob import ContentSettings

        try:
            self._require_private()
            self.container.get_blob_client(key).upload_blob(
                content,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
                max_concurrency=1,
            )
        except Exception:
            raise StorageUnavailable(
                "Private file storage is temporarily unavailable."
            ) from None

    def read(self, key, max_bytes):
        try:
            self._require_private()
            blob = self.container.get_blob_client(key)
            if blob.get_blob_properties().size > max_bytes:
                raise StorageUnavailable("Stored file exceeds the supported size.")
            # Request at most max_bytes+1 even if the object changes after inspection.
            data = blob.download_blob(
                offset=0, length=max_bytes + 1, max_concurrency=1
            ).readall()
            if len(data) > max_bytes:
                raise StorageUnavailable("Stored file exceeds the supported size.")
            return data
        except StorageUnavailable:
            raise
        except Exception:
            raise StorageUnavailable(
                "Private file storage is temporarily unavailable."
            ) from None

    def inspect(self, key):
        try:
            properties = self.container.get_blob_client(key).get_blob_properties()
            return {"size": properties.size, "etag": properties.etag}
        except Exception:
            raise StorageUnavailable(
                "Private file storage is temporarily unavailable."
            ) from None

    def delete(self, key):
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self.container.delete_blob(key, delete_snapshots="include")
        except ResourceNotFoundError:
            pass
        except Exception:
            raise StorageUnavailable(
                "Private file storage is temporarily unavailable."
            ) from None
