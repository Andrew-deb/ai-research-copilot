"""Copy a saved Volume snapshot into ephemeral App storage via the Files API."""

import hashlib
import json
from pathlib import Path

MAX_BYTES = 1024 * 1024 * 1024


def stage_model(source, destination, client):
    if not source.startswith("/Volumes/"):
        raise ValueError("Expected a Unity Catalog Volume path")
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Use a fresh destination for the model snapshot")
    destination.mkdir(parents=True)
    total = 0
    hashes = {}

    def copy(directory):
        nonlocal total
        for entry in client.files.list_directory_contents(directory):
            relative = Path(entry.path).relative_to(source.rstrip("/"))
            if ".." in relative.parts or relative.is_absolute():
                raise ValueError("Invalid model path")
            # Use the native PyTorch snapshot, not alternate ONNX/OpenVINO weights.
            if relative.parts[0] in ("onnx", "openvino", ".cache", ".git"):
                continue
            if entry.is_directory:
                copy(entry.path)
                continue
            if relative.suffix not in (".json", ".safetensors", ".txt", ".model"):
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = client.files.download(entry.path).contents
            digest = hashlib.sha256()
            try:
                with target.open("wb") as output:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise ValueError("Model snapshot exceeds staging limit")
                        digest.update(chunk)
                        output.write(chunk)
            finally:
                stream.close()
            hashes[relative.as_posix()] = digest.hexdigest()

    copy(source.rstrip("/"))
    required = {
        "config.json",
        "modules.json",
        "tokenizer.json",
        "model.safetensors",
        "1_Pooling/config.json",
        "embedding_contract.json",
    }
    if not required.issubset(hashes):
        raise ValueError("Incomplete ingestion model snapshot")
    contract = json.loads((destination / "embedding_contract.json").read_text())
    expected = {
        "base_model": "nomic-ai/modernbert-embed-base",
        "embedding_dim": 768,
        "query_prefix": "search_query: ",
        "document_prefix": "search_document: ",
        "normalize": True,
    }
    if any(contract.get(key) != value for key, value in expected.items()):
        raise ValueError("Incompatible saved model contract")
    fingerprint = hashlib.sha256(
        json.dumps(hashes, sort_keys=True).encode()
    ).hexdigest()
    return fingerprint


if __name__ == "__main__":
    import argparse
    from databricks.sdk import WorkspaceClient

    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    print(stage_model(args.source, args.destination, WorkspaceClient()))
