"""Exercise publication against a real local Git remote."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

spec = importlib.util.spec_from_file_location("publisher", Path(__file__).parents[1] / "mcp_server/deploy/publish.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def run(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def release(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    repo = tmp_path / "source"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    run(repo, "config", "user.name", "Test")
    run(repo, "config", "user.email", "test@example.com")
    (repo / "source.txt").write_text("authoritative source")
    run(repo, "add", ".")
    run(repo, "commit", "-m", "Source")
    run(repo, "remote", "add", "origin", str(remote))
    run(repo, "push", "origin", "main")
    revision = run(repo, "rev-parse", "HEAD")
    artifacts = tmp_path / "artifacts"
    for target in publisher.BRANCHES:
        root = artifacts / target
        root.mkdir(parents=True)
        files = {"app.yaml": "command: [python, server.py]\n", "requirements.txt": "", "server.py": target}
        for name, content in files.items():
            (root / name).write_text(content)
        (root / "deployment_manifest.json").write_text(json.dumps({
            "target": target, "source_revision": revision,
            "files": {name: hashlib.sha256(content.encode()).hexdigest() for name, content in files.items()}}))
    return repo, artifacts, revision


def test_publication_preserves_checkout_and_is_idempotent(release):
    repo, artifacts, revision = release
    index = run(repo, "write-tree")
    assert publisher.publish(repo, artifacts, revision)
    heads = {target: publisher.remote_sha(repo, branch) for target, branch in publisher.BRANCHES.items()}
    for target, sha in heads.items():
        assert set(run(repo, "ls-tree", "--name-only", sha).splitlines()) == {"app.yaml", "requirements.txt", "server.py", "deployment_manifest.json"}
        assert run(repo, "show", f"{sha}:server.py") == target
    assert run(repo, "write-tree") == index
    assert run(repo, "status", "--porcelain") == ""
    assert publisher.publish(repo, artifacts, revision)
    assert heads == {target: publisher.remote_sha(repo, branch) for target, branch in publisher.BRANCHES.items()}


def test_tampering_and_extra_files_are_rejected(release):
    repo, artifacts, revision = release
    (artifacts / "assistant" / "secret.env").write_text("unlisted")
    with pytest.raises(ValueError, match="manifest"):
        publisher.publish(repo, artifacts, revision)
    assert not publisher.remote_sha(repo, "deploy/research")


def test_stale_revision_cannot_publish(release):
    repo, artifacts, revision = release
    run(repo, "commit", "--allow-empty", "-m", "New source")
    run(repo, "push", "origin", "main")
    run(repo, "checkout", "--detach", revision)
    assert publisher.publish(repo, artifacts, revision) is False
    assert not publisher.remote_sha(repo, "deploy/assistant")


def test_atomic_push_keeps_both_branches_unchanged_on_rejection(release):
    repo, artifacts, revision = release
    remote = Path(run(repo, "remote", "get-url", "origin"))
    hook = remote / "hooks" / "update"
    hook.write_text('#!/bin/sh\n[ "$1" != "refs/heads/deploy/assistant" ]\n')
    hook.chmod(0o755)
    with pytest.raises(subprocess.CalledProcessError):
        publisher.publish(repo, artifacts, revision)
    assert not publisher.remote_sha(repo, "deploy/research")
    assert not publisher.remote_sha(repo, "deploy/assistant")
