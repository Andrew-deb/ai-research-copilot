"""Start process-owned background work only after the server has forked."""

import atexit
import logging
import os
import threading

import embedding
import upload_config
from config import DB_KEEPWARM_SECONDS, EMBEDDING_PRELOAD
from repositories import lakebase

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_started_pid = None
_stop = None


def _after_fork():
    global _lock, _started_pid, _stop
    _lock = threading.Lock()
    _started_pid = None
    _stop = None


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def start():
    """Idempotent per process; never called by the application factory."""
    global _started_pid, _stop
    with _lock:
        if _started_pid == os.getpid():
            return
        if upload_config.ENABLED and upload_config.WORKER_ENABLED:
            from services import upload_worker
            _stop = upload_worker.start()
        if EMBEDDING_PRELOAD:
            threading.Thread(target=embedding.warmup, name="embedding-warmup", daemon=True).start()
        if DB_KEEPWARM_SECONDS > 0:
            threading.Thread(target=lakebase.keep_warm, args=(DB_KEEPWARM_SECONDS,),
                             name="db-keepwarm", daemon=True).start()
        _started_pid = os.getpid()
        atexit.register(lakebase.close_pool)
        atexit.register(stop)
        logger.info("Background tasks started in serving process pid=%s uploads=%s",
                    _started_pid, upload_config.ENABLED and upload_config.WORKER_ENABLED)


def stop():
    """Signal durable upload work to stop; unfinished leases remain recoverable."""
    if _started_pid == os.getpid() and _stop is not None:
        _stop.set()
