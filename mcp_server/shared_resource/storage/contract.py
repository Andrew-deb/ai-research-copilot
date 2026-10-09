"""Small storage boundary. Callers retain logical locations, never signed URLs."""

from typing import Protocol


class ObjectStorage(Protocol):
    def put(self, key: str, content: bytes, content_type: str) -> None: ...
    def read(self, key: str, max_bytes: int) -> bytes: ...
    def inspect(self, key: str) -> dict: ...
    def delete(self, key: str) -> None: ...


class StorageUnavailable(Exception):
    """Safe provider error; credentials and SDK messages are never client output."""
