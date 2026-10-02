"""Release builds are self-contained, omit local state, and load full prompts."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_builder():
    spec = importlib.util.spec_from_file_location("deployment_builder", ROOT / "mcp_server/deploy/build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def release(tmp_path_factory):
    output = tmp_path_factory.mktemp("deployments")
    builder = load_builder()
    return {target: builder.build(target, output) for target in builder.TARGETS}


def test_mcp_packages_have_separate_entrypoints_and_catalogs(release):
    research, wick = release["research"], release["assistant"]
    assert (research / "research/server.py").is_file()
    assert (research / "research_mcp_server.py").is_file()
    assert not (research / "assistant").exists()
    assert (wick / "assistant/server.py").is_file()
    assert not (wick / "research_mcp_server.py").exists()
    assert not (wick / "brokers").exists()
    assert not (wick / "services/discovery_service.py").exists()
    assert not (wick / "services/planning_service.py").exists()
    for target, count in (("research", 13), ("assistant", 9)):
        assert len(json.loads((release[target] / "deployment_manifest.json").read_text())["tools"]) == count
        assert (release[target] / "shared_resource/services/note_service.py").read_bytes() == (
            ROOT / "mcp_server/shared_resource/services/note_service.py").read_bytes()


def test_manifest_matches_every_shipped_file_and_repeated_build(release, tmp_path):
    builder = load_builder()
    for target, root in release.items():
        manifest = json.loads((root / "deployment_manifest.json").read_text())
        actual = {str(path.relative_to(root)).replace("\\", "/"):
                  hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in root.rglob("*") if path.is_file() and path.name != "deployment_manifest.json"}
        assert manifest["files"] == actual
        rebuilt = builder.build(target, tmp_path)
        assert (rebuilt / "deployment_manifest.json").read_bytes() == (root / "deployment_manifest.json").read_bytes()


def test_outputs_omit_secrets_unregistered_services_and_local_state(tmp_path, monkeypatch):
    builder = load_builder()
    source = tmp_path / "source"
    shutil.copytree(ROOT / "mcp_server", source / "mcp_server",
                    ignore=shutil.ignore_patterns("__pycache__", ".env"))
    shutil.copytree(ROOT / "dashboard", source / "dashboard",
                    ignore=shutil.ignore_patterns("__pycache__", ".env"))
    shutil.copytree(ROOT / "agent", source / "agent")
    for relative in ("mcp_server/.env", "mcp_server/services/new_tool.py", "dashboard/.env",
                     "dashboard/static/private.json", "dashboard/static/debug.log"):
        (source / relative).write_text("SECRET_SENTINEL")
    monkeypatch.setattr(builder, "REPO_ROOT", source)
    monkeypatch.setattr(builder, "MCP_ROOT", source / "mcp_server")
    for target in builder.TARGETS:
        built = builder.build(target, tmp_path / "release")
        assert not list(built.rglob(".env"))
        assert not list(built.rglob("__pycache__"))
        assert not (built / "services/new_tool.py").exists()
        assert all(b"SECRET_SENTINEL" not in p.read_bytes() for p in built.rglob("*") if p.is_file())


def test_builder_refuses_source_overwrite_existing_release_and_symlinks(tmp_path, monkeypatch):
    builder = load_builder()
    with pytest.raises(ValueError, match="dist"):
        builder.build("research", ROOT / "mcp_server")
    builder.build("assistant", tmp_path)
    with pytest.raises(FileExistsError):
        builder.build("assistant", tmp_path)
    real = tmp_path / "secret"
    real.write_text("Do not ship")
    link = tmp_path / "link.py"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="regular"):
        builder.copy_file(link, tmp_path / "out.py")


def test_render_artifact_loads_authoritative_prompts_without_repo_siblings(release):
    root = release["render"]
    for name in ("system_prompt.md", "wick_system_prompt.md"):
        assert (root / "prompts" / name).read_bytes() == (ROOT / "agent" / name).read_bytes()
    script = '''
from pathlib import Path
from services import agent_service
assert agent_service._PROMPT_PATH == Path.cwd() / 'prompts/system_prompt.md'
assert agent_service._WICK_PROMPT_PATH == Path.cwd() / 'prompts/wick_system_prompt.md'
assert agent_service._base_prompt() == Path('prompts/system_prompt.md').read_text()
assert agent_service._wick_prompt() == Path('prompts/wick_system_prompt.md').read_text()
import app
client = app.app.test_client()
assert client.get('/healthz').status_code == 200
assert client.get('/static/js/chat.js').status_code == 200
assert client.get('/static/img/alfred/portrait.png').status_code == 200
'''
    # No developer .env, credentials, live provider, or preload in this smoke test.
    env = {**os.environ, "DATABASE_URL": "", "APP_ENV": "local",
           "ALLOW_DEV_USER_BYPASS": "false", "ALLOW_ANONYMOUS_DEMO": "true",
           "EMBEDDING_PRELOAD": "false", "DATABRICKS_HOST": "",
           "DATABRICKS_CLIENT_ID": "", "DATABRICKS_CLIENT_SECRET": "",
           "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "", "PYTHONPATH": str(root)}
    result = subprocess.run([sys.executable, "-c", script], cwd=root, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_render_blueprint_runs_from_built_dashboard():
    import yaml
    service = yaml.safe_load((ROOT / "render.yaml").read_text())["services"][0]
    assert service["rootDir"] == "."
    assert "deploy/build.py --target render" in service["buildCommand"]
    assert "dist/deploy/render/requirements.txt" in service["buildCommand"]
    assert service["startCommand"].startswith("cd dist/deploy/render && gunicorn app:app")
