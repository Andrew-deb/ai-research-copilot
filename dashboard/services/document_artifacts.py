"""Bounded verified extraction bytes, shared by grounding and private indexing."""

import hashlib
import json
import upload_config
from exceptions import ExternalAPIError
from services import upload_storage


def load(row):
    try:
        data = upload_storage.for_location(row["storage_location_id"]).read(
            row["artifact_key"], upload_config.LIMITS.max_artifact_bytes
        )
    except upload_storage.StorageUnavailable:
        raise ExternalAPIError("The document is temporarily unavailable.") from None
    if hashlib.sha256(data).hexdigest() != row.get("artifact_hash"):
        raise ExternalAPIError("Extracted file integrity verification failed.")
    try:
        artifact = json.loads(data)
        if not isinstance(artifact, dict) or artifact.get("version") != row.get(
            "extraction_version"
        ):
            raise ValueError()
        return artifact
    except (ValueError, TypeError):
        raise ExternalAPIError("The extracted file could not be read.") from None
