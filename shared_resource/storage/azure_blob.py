"""Azure implements byte operations only; private access is enforced by callers."""

from .contract import StorageUnavailable


class AzureBlobStorage:
    def __init__(self, account_url, container, tenant_id, client_id, client_secret):
        from azure.identity import ClientSecretCredential
        from azure.storage.blob import BlobServiceClient

        credential = ClientSecretCredential(
            tenant_id,
            client_id,
            client_secret,
            connection_timeout=5,
            read_timeout=15,
            retry_total=0,
        )
        self.container = BlobServiceClient(
            account_url,
            credential=credential,
            connection_timeout=5,
            read_timeout=15,
            retry_total=0,
            max_single_put_size=4 * 1024 * 1024,
        ).get_container_client(container)

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
