"""Connection-string setup stays inside Azure wiring, never the upload lifecycle."""

from unittest.mock import Mock
import pytest
from services import upload_storage
import upload_config
from mcp_server.shared_resource.storage.azure_blob import AzureBlobStorage
from mcp_server.shared_resource.storage.contract import StorageUnavailable

# Deliberately fake account/key; constructing an SDK client makes no HTTP call.
CONNECTION_STRING = "DefaultEndpointsProtocol=https;AccountName=example;AccountKey=dGVzdA==;EndpointSuffix=core.windows.net"


@pytest.fixture(autouse=True)
def reset_storage_cache():
    upload_storage.for_location.cache_clear()
    yield
    upload_storage.for_location.cache_clear()


def test_azure_sdk_accepts_connection_string_without_entra():
    storage = AzureBlobStorage(CONNECTION_STRING, "private-uploads")
    assert (
        storage.container.url == "https://example.blob.core.windows.net/private-uploads"
    )
    storage.container.close()


def test_factory_only_requires_connection_string_and_container(monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", CONNECTION_STRING)
    monkeypatch.setenv("AZURE_STORAGE_CONTAINER", "private-uploads")
    for key in (
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_STORAGE_ACCOUNT_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    provider = Mock()
    monkeypatch.setattr(upload_storage, "AzureBlobStorage", provider)
    assert (
        upload_storage.for_location(upload_config.LOCATION_ID) is provider.return_value
    )
    provider.assert_called_once_with(CONNECTION_STRING, "private-uploads")


@pytest.mark.parametrize(
    "missing", ["AZURE_STORAGE_CONNECTION_STRING", "AZURE_STORAGE_CONTAINER"]
)
def test_missing_configuration_never_initializes_client(monkeypatch, missing):
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", CONNECTION_STRING)
    monkeypatch.setenv("AZURE_STORAGE_CONTAINER", "private-uploads")
    monkeypatch.delenv(missing)
    provider = Mock()
    monkeypatch.setattr(upload_storage, "AzureBlobStorage", provider)
    with pytest.raises(upload_storage.StorageUnavailable, match="not been configured"):
        upload_storage.for_location(upload_config.LOCATION_ID)
    provider.assert_not_called()


def test_location_identity_is_preserved(monkeypatch):
    provider = Mock()
    monkeypatch.setattr(upload_storage, "AzureBlobStorage", provider)
    with pytest.raises(upload_storage.StorageUnavailable, match="location"):
        upload_storage.for_location("unknown-location")
    provider.assert_not_called()


def test_malformed_connection_string_does_not_leak_sdk_error(monkeypatch):
    from azure.storage.blob import BlobServiceClient

    monkeypatch.setattr(
        BlobServiceClient,
        "from_connection_string",
        Mock(side_effect=ValueError("secret-value")),
    )
    with pytest.raises(StorageUnavailable) as error:
        AzureBlobStorage("secret-value", "private-uploads")
    assert "secret-value" not in str(error.value)
    assert error.value.__suppress_context__


def test_insecure_endpoint_is_refused():
    with pytest.raises(StorageUnavailable):
        AzureBlobStorage(
            CONNECTION_STRING.replace("Protocol=https", "Protocol=http"),
            "private-uploads",
        )
