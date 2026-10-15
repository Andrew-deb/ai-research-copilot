"""Build self-contained deployment roots from authoritative repository sources.

Run with Python's standard library from any working directory. Output uses an
explicit source allowlist: no environment files, caches, tests, or local state.
Generated trees are release artifacts, not another maintained implementation.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_ROOT = REPO_ROOT / "mcp_server"
TARGETS = ("research", "assistant", "render")


def copy_file(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Expected a regular source file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def copy_package(source: Path, destination: Path) -> None:
    for path in sorted(source.rglob("*.py")):
        if "__pycache__" not in path.parts:
            copy_file(path, destination / path.relative_to(source))


def tools_in(source: Path) -> list[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return sorted(node.name for node in tree.body
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                          and isinstance(d.func.value, ast.Name)
                          and d.func.value.id == "mcp" and d.func.attr == "tool"
                          for d in node.decorator_list))


def populate_mcp(target: str, destination: Path) -> None:
    copy_file(MCP_ROOT / "shared_resource" / "requirements.txt", destination / "requirements.txt")
    copy_package(MCP_ROOT / "shared_resource", destination / "shared_resource")
    copy_package(MCP_ROOT / target, destination / target)
    copy_file(MCP_ROOT / "deploy" / f"{target}.app.yaml", destination / "app.yaml")


def populate_render(destination: Path) -> None:
    # Render consumes pure shared contracts, not MCP configuration, middleware,
    # database pools or runtime bindings. Its own repository is injected.
    shared = MCP_ROOT / "shared_resource"
    for name in ("__init__.py", "exceptions.py"):
        copy_file(shared / name, destination / "shared_resource" / name)
    copy_package(shared / "services", destination / "shared_resource" / "services")
    copy_package(shared / "brokers", destination / "shared_resource" / "brokers")
    copy_package(shared / "storage", destination / "shared_resource" / "storage")
    for path in sorted((shared / "repositories").glob("*.py")):
        if path.name != "lakebase.py":
            copy_file(path, destination / "shared_resource" / "repositories" / path.name)
    dashboard = REPO_ROOT / "dashboard"
    for path in sorted(dashboard.glob("*.py")):
        copy_file(path, destination / path.name)
    copy_file(dashboard / "requirements.txt", destination / "requirements.txt")
    for name in ("middleware", "repositories", "routes", "services"):
        copy_package(dashboard / name, destination / name)
    # Only tracked product asset formats, never an arbitrary recursive copy.
    formats = {".html", ".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".ico",
               ".webp", ".woff", ".woff2", ".ttf", ".otf", ".map"}
    for name in ("templates", "static"):
        for path in sorted((dashboard / name).rglob("*")):
            if path.is_file() and path.suffix.lower() in formats:
                copy_file(path, destination / path.relative_to(dashboard))
    for name in ("system_prompt.md", "wick_system_prompt.md"):
        copy_file(REPO_ROOT / "agent" / name, destination / "prompts" / name)


def source_revision() -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def build(target: str, output: Path) -> Path:
    if target not in TARGETS:
        raise ValueError("Unknown deployment target")
    output = output.resolve()
    # Prevent writing into any authoritative source tree, including through a symlink.
    if output == REPO_ROOT or (REPO_ROOT in output.parents and
                              REPO_ROOT / "dist" not in output.parents and
                              output != REPO_ROOT / "dist"):
        raise ValueError("Repository outputs must be under dist/")
    destination = output / target
    if destination.exists():
        raise FileExistsError(f"Release already exists: {destination}; choose a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{target}-", dir=output) as temporary:
        stage = Path(temporary)
        if target == "render":
            populate_render(stage)
            tools = []
        else:
            populate_mcp(target, stage)
            source = MCP_ROOT / target / "server.py"
            tools = tools_in(source)
        files = {str(p.relative_to(stage)).replace("\\", "/"):
                 hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(stage.rglob("*")) if p.is_file()}
        manifest = {"target": target, "source_revision": source_revision(),
                    "tools": tools, "files": files}
        (stage / "deployment_manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        # Copy into a new release root only after all source inputs are verified.
        stage.rename(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=(*TARGETS, "all"), default="all")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "dist" / "deploy")
    args = parser.parse_args()
    targets = TARGETS if args.target == "all" else (args.target,)
    for target in targets:
        if (args.output / target).exists():
            parser.error(f"Release exists: {args.output / target}; use a fresh --output")
    for target in targets:
        print(build(target, args.output))


if __name__ == "__main__":
    main()
