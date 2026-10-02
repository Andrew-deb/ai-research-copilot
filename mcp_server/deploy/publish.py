"""Publish verified deployment roots without changing the source checkout."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

BRANCHES = {"research": "deploy/research", "assistant": "deploy/assistant"}


def git(repo: Path, *args: str, env=None) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, env=env).strip()


def verify(root: Path, target: str, revision: str) -> None:
    paths = list(root.rglob("*"))
    if root.is_symlink() or any(p.is_symlink() for p in paths):
        raise ValueError("Deployment artifacts cannot contain symlinks")
    manifest = json.loads((root / "deployment_manifest.json").read_text())
    if manifest["target"] != target or manifest["source_revision"] != revision:
        raise ValueError("Artifact target or source revision does not match")
    actual = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in paths if p.is_file() and p != root / "deployment_manifest.json"}
    if actual != manifest["files"]:
        raise ValueError("Artifact files do not match their manifest")
    if not {"app.yaml", "requirements.txt"}.issubset(actual):
        raise ValueError("Incomplete deployment root")


def remote_sha(repo: Path, branch: str) -> str:
    result = git(repo, "ls-remote", "origin", f"refs/heads/{branch}")
    return result.split()[0] if result else ""


def publish(repo: Path, artifacts: Path, revision: str) -> bool:
    if git(repo, "rev-parse", "HEAD") != revision:
        raise ValueError("Checkout does not match the validated revision")
    for target in BRANCHES:
        verify(artifacts / target, target, revision)
    if remote_sha(repo, "main") != revision:
        print("Skipping publication: main has advanced")
        return False
    git_dir = git(repo, "rev-parse", "--absolute-git-dir")
    updates = []
    for target, branch in BRANCHES.items():
        parent = remote_sha(repo, branch)
        if parent:
            git(repo, "fetch", "--no-tags", "origin", parent)
            previous = json.loads(git(repo, "show", f"{parent}:deployment_manifest.json"))
            if previous.get("target") != target:
                raise ValueError(f"Refusing to overwrite an unrelated branch: {branch}")
        with tempfile.TemporaryDirectory() as temporary:
            env = os.environ.copy()
            env.update(GIT_INDEX_FILE=str(Path(temporary) / "index"),
                       GIT_AUTHOR_NAME="github-actions[bot]",
                       GIT_AUTHOR_EMAIL="41898282+github-actions[bot]@users.noreply.github.com",
                       GIT_COMMITTER_NAME="github-actions[bot]",
                       GIT_COMMITTER_EMAIL="41898282+github-actions[bot]@users.noreply.github.com")
            root = (artifacts / target).resolve()
            command = ["git", f"--git-dir={git_dir}", f"--work-tree={root}"]
            def staged(*args):
                return subprocess.check_output(command + list(args), cwd=root, env=env, text=True).strip()
            staged("read-tree", "--empty")
            staged("add", "--all", "--force", ".")
            tree = staged("write-tree")
            if parent and tree == git(repo, "rev-parse", f"{parent}^{{tree}}"):
                continue
            args = ["commit-tree", tree]
            if parent:
                args += ["-p", parent]
            commit = staged(*args, "-m", f"Deploy {target} from {revision}")
            updates.append(f"{commit}:refs/heads/{branch}")
    if remote_sha(repo, "main") != revision:
        print("Skipping publication: main advanced during preparation")
        return False
    if updates:
        # Fast-forward only; atomic push leaves both branches unchanged on failure.
        git(repo, "push", "--atomic", "origin", *updates)
    print("Deployment branches are current")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    publish(Path(__file__).resolve().parents[2], args.artifacts.resolve(), args.revision)


if __name__ == "__main__":
    main()
