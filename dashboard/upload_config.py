"""Upload limits and operator configuration; disabled until explicitly enabled."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class UploadLimits:
    max_file_bytes: int = 10 * 1024 * 1024
    max_artifact_bytes: int = 2 * 1024 * 1024
    per_user_bytes: int = 100 * 1024 * 1024
    deployment_bytes: int = 1024 * 1024 * 1024
    per_conversation_files: int = 3
    retention_days: int = 7


ENABLED = os.getenv("UPLOADS_ENABLED", "false").lower() == "true"
WORKER_ENABLED = os.getenv("UPLOAD_WORKER_ENABLED", "true").lower() == "true"
LOCATION_ID = os.getenv("UPLOAD_STORAGE_LOCATION", "azure-primary")
LIMITS = UploadLimits(
    max_file_bytes=int(os.getenv("UPLOAD_MAX_FILE_BYTES", str(10 * 1024 * 1024))),
    per_user_bytes=int(os.getenv("UPLOAD_USER_BYTES", str(100 * 1024 * 1024))),
    deployment_bytes=int(os.getenv("UPLOAD_DEPLOYMENT_BYTES", str(1024 * 1024 * 1024))),
    retention_days=int(os.getenv("UPLOAD_RETENTION_DAYS", "7")),
)


def validate():
    if (
        not 1 <= LIMITS.max_file_bytes <= 10 * 1024 * 1024
        or not 1 <= LIMITS.retention_days <= 30
    ):
        raise ValueError(
            "Upload limits exceed the supported file size/retention bounds."
        )
    if (
        LIMITS.per_user_bytes < LIMITS.max_file_bytes + LIMITS.max_artifact_bytes
        or LIMITS.deployment_bytes < LIMITS.per_user_bytes
    ):
        raise ValueError("Upload capacity must cover files and derived artifacts.")
    if not LOCATION_ID or len(LOCATION_ID) > 100:
        raise ValueError("Configure a valid upload storage location.")
