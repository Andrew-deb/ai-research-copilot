"""Fork and lifecycle regressions; no live database, storage or model calls."""
import importlib.util
import os
from pathlib import Path
import select
import threading

import pytest
from repositories import lakebase
from services import background_tasks


def test_factory_does_not_start_background_threads(monkeypatch):
    import app
    monkeypatch.setattr(background_tasks, "start", lambda: pytest.fail("factory started threads"))
    app.create_app()


def test_background_start_is_once_per_serving_process(monkeypatch):
    from services import upload_worker
    stops = []
    def start_worker():
        event = threading.Event()
        stops.append(event)
        return event
    monkeypatch.setattr(background_tasks, "_started_pid", None)
    monkeypatch.setattr(background_tasks, "_stop", None)
    monkeypatch.setattr(background_tasks, "EMBEDDING_PRELOAD", False)
    monkeypatch.setattr(background_tasks, "DB_KEEPWARM_SECONDS", 0)
    monkeypatch.setattr(background_tasks.upload_config, "ENABLED", True)
    monkeypatch.setattr(background_tasks.upload_config, "WORKER_ENABLED", True)
    monkeypatch.setattr(upload_worker, "start", start_worker)
    background_tasks.start()
    background_tasks.start()
    assert len(stops) == 1
    background_tasks.stop()
    assert stops[0].is_set()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Gunicorn uses POSIX fork")
def test_child_cannot_inherit_locked_database_pool(monkeypatch):
    # Reproduces the old failure: another thread owns the mutex when fork occurs.
    # Without the at-fork reset the child can never acquire that inherited lock.
    monkeypatch.setattr(lakebase, "_POOL", object())
    lock = threading.Lock()
    lock.acquire()
    monkeypatch.setattr(lakebase, "_POOL_LOCK", lock)
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(read_fd)
            ok = lakebase._POOL is None and lakebase._POOL_LOCK.acquire(timeout=0.2)
            os.write(write_fd, b"ok" if ok else b"blocked")
        finally:
            os._exit(0)
    os.close(write_fd)
    try:
        assert select.select([read_fd], [], [], 2)[0], "child stalled on inherited lock"
        assert os.read(read_fd, 20) == b"ok"
        assert lakebase._POOL is not None
    finally:
        lock.release()
        os.close(read_fd)
        os.waitpid(pid, 0)


def test_gunicorn_hooks_start_and_stop_process_owned_tasks(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "dashboard" / "gunicorn.conf.py"
    spec = importlib.util.spec_from_file_location("gunicorn_hooks", path)
    hooks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hooks)
    calls = []
    monkeypatch.setattr(background_tasks, "start", lambda: calls.append("start"))
    monkeypatch.setattr(background_tasks, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(lakebase, "close_pool", lambda: calls.append("close"))
    hooks.post_worker_init(None)
    hooks.worker_exit(None, None)
    assert calls == ["start", "stop", "close"]


def test_gunicorn_preload_starts_upload_thread_only_in_child(tmp_path):
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    marker = tmp_path / "worker-pids"
    (tmp_path / "fixture_app.py").write_text('''
import os
import threading
from flask import Flask
from services import background_tasks, upload_worker
from app import app
background_tasks.upload_config.ENABLED = True
background_tasks.upload_config.WORKER_ENABLED = True
background_tasks.EMBEDDING_PRELOAD = False
background_tasks.DB_KEEPWARM_SECONDS = 0

def start():
    with open(os.environ["WORKER_PID_FILE"], "a") as f:
        f.write(str(os.getpid()) + "\\n")
    return threading.Event()
upload_worker.start = start
''')
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(tmp_path), str(root / "dashboard"), str(root))),
           "WORKER_PID_FILE": str(marker), "UPLOADS_ENABLED": "false", "DATABASE_URL": ""}
    process = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "fixture_app:app", "--preload", "--workers", "1",
         "--worker-class", "gthread", "--threads", "4", "--bind", "127.0.0.1:0",
         "--config", str(root / "dashboard" / "gunicorn.conf.py")],
        env=env, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        import time
        deadline = time.monotonic() + 8
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), "Gunicorn did not start upload lifecycle"
        pids = marker.read_text().splitlines()
        assert len(pids) == 1
        assert int(pids[0]) != process.pid, "upload worker started in preloading parent"
    finally:
        process.terminate()
        output, _ = process.communicate(timeout=8)
    assert process.returncode == 0, output


def test_pool_creation_has_bounded_database_waits(monkeypatch):
    from unittest.mock import Mock
    constructor = Mock()
    monkeypatch.setattr(lakebase, "_POOL", None)
    monkeypatch.setattr(lakebase, "DATABASE_URL", "postgresql://invalid/example")
    monkeypatch.setattr(lakebase.psycopg2.pool, "ThreadedConnectionPool", constructor)
    for name in ("DB_CONNECT_TIMEOUT_SECONDS", "DB_STATEMENT_TIMEOUT_MS", "DB_LOCK_TIMEOUT_MS"):
        monkeypatch.delenv(name, raising=False)
    lakebase._get_pool()
    assert constructor.call_args.kwargs["connect_timeout"] == 10
    assert constructor.call_args.kwargs["options"] == "-c statement_timeout=30000 -c lock_timeout=5000"
