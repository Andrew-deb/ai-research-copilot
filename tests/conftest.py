"""
tests/conftest.py — shared fixtures.

No live database or network. `FakeDB` is an in-memory stand-in for
dashboard/repositories/lakebase.py; the `db` fixture monkeypatches every
matching function on the real module, and stubs the embedding model and the
OpenRouter client so tests run fast and offline.
"""

import datetime
import os
import uuid

# Must be set before app.py is imported — its module body calls create_app(),
# which would otherwise spawn the real SentenceTransformer warmup thread.
# ---------------------------------------------------------------------------
# Pin the environment BEFORE config is imported.
#
# config.py calls load_dotenv(), so without this the suite reads the developer's
# .env and its results depend on an untracked local file. That is how a test
# passes on one machine, fails on another, and does something third in CI.
#
# load_dotenv() does not override variables that already exist, so setting them
# here wins. Fixtures still monkeypatch individual values per test; these are the
# baseline those fixtures assume.
# ---------------------------------------------------------------------------
os.environ["EMBEDDING_PRELOAD"] = "false"
os.environ["APP_ENV"] = "local"
# Every request resolves to the dev identity unless a test signs in or uses one
# of the anonymous fixtures.
os.environ["ALLOW_DEV_USER_BYPASS"] = "true"
os.environ["ALLOW_ANONYMOUS_DEMO"] = "false"
# Metering must be ON: the capability tests assert what happens when an allowance
# runs out, which is unobservable if a local .env has switched quotas off.
os.environ["QUOTAS_ENABLED"] = "true"

# The agent is DISCONNECTED by default, and this is not a preference.
#
# Once real Databricks credentials existed in .env, agent_service.is_connected()
# became true during the suite and three tests started making live calls to the
# deployed MCP server — one of which came back 503 and failed a test that has
# nothing to do with that server being awake. A test suite that reaches the
# internet is not testing the thing it claims to test.
#
# Tests that need a connected agent fake the transport (see the `wired` fixture
# in test_agent_loop.py); nothing in the suite should ever open a socket.
os.environ["DATABRICKS_HOST"] = ""
os.environ["DATABRICKS_CLIENT_ID"] = ""
os.environ["DATABRICKS_CLIENT_SECRET"] = ""
os.environ["MCP_SERVER_URL"] = ""

import pytest

import embedding as embedding_module
from config import EMBEDDING_DIMENSION
import llm_client as llm_module
from middleware import auth as auth_module
from repositories import lakebase as lakebase_module

# The development identity. Shared with setup_db.py's seed and the MCP server's
# default user, so the agent and the dashboard operate on the same library.
DEV_EMAIL = "demo@research-copilot.dev"
DEMO_EMAIL = DEV_EMAIL


def _now():
    return datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)


class FakeDB:
    """Minimal in-memory implementation of the repository surface the app uses."""

    def __init__(self):
        self.users_by_id: dict[str, dict] = {}
        self.users_by_email: dict[str, dict] = {}
        self.goals: dict[str, dict] = {}
        self.collections: dict[str, dict] = {}
        self.collection_papers: dict[tuple[str, str], dict] = {}
        self.papers: dict[str, dict] = {}
        self.progress: dict[tuple[str, str], dict] = {}
        self.notes: dict[str, dict] = {}
        # (scope, scope_id, metric) -> count. No day key: a test never spans one.
        self.usage: dict[tuple[str, str, str], int] = {}
        # One dict per ai_operations row, in the order they were written.
        self.ai_operations: list[dict] = []

    # ---------- test helpers (not part of the repo surface) ----------
    def seed_paper(self, **overrides) -> dict:
        pid = overrides.get("paper_id") or str(uuid.uuid4())
        row = {
            "paper_id": pid, "openalex_id": None, "semantic_scholar_id": None, "doi": None,
            "title": "Test Paper", "abstract": "a test abstract about transformers",
            "publication_year": 2020, "venue": "Test Venue", "citation_count": 10,
            "tldr": None, "influence_score": None, "source_api": "openalex",
            "open_access_url": None, "payload": None, "synced_at": _now(),
        }
        row.update(overrides)
        row["paper_id"] = pid
        self.papers[pid] = row
        return dict(row)

    # ---------- users ----------
    def get_or_create_user(self, email, display_name=None):
        existing = self.users_by_email.get(email)
        if existing:
            return dict(existing)
        uid = str(uuid.uuid4())
        row = {"user_id": uid, "email": email, "display_name": display_name,
               "auth_provider": "dev", "provider_subject": None, "avatar_url": None,
               "is_system": False, "last_login_at": None, "created_at": _now()}
        self.users_by_id[uid] = row
        self.users_by_email[email] = row
        return dict(row)

    def get_user_by_email(self, email):
        row = self.users_by_email.get(email)
        return dict(row) if row else None

    def get_user_by_id(self, user_id):
        row = self.users_by_id.get(str(user_id))
        return dict(row) if row else None

    def get_user_by_provider(self, provider, subject):
        for row in self.users_by_id.values():
            if row.get("auth_provider") == provider and row.get("provider_subject") == subject:
                return dict(row)
        return None

    def create_oauth_user(self, provider, subject, email, display_name=None, avatar_url=None):
        uid = str(uuid.uuid4())
        row = {"user_id": uid, "email": email, "display_name": display_name,
               "auth_provider": provider, "provider_subject": subject,
               "avatar_url": avatar_url, "is_system": False,
               "last_login_at": _now(), "created_at": _now()}
        self.users_by_id[uid] = row
        self.users_by_email[email] = row
        return dict(row)

    def link_user_provider(self, user_id, provider, subject, display_name=None, avatar_url=None):
        row = self.users_by_id[str(user_id)]
        row["auth_provider"] = provider
        row["provider_subject"] = subject
        # COALESCE semantics: never blank a name the user already has.
        row["display_name"] = display_name or row.get("display_name")
        row["avatar_url"] = avatar_url or row.get("avatar_url")
        row["last_login_at"] = _now()
        return dict(row)

    def touch_user_login(self, user_id, display_name=None, avatar_url=None):
        row = self.users_by_id.get(str(user_id))
        if row:
            row["last_login_at"] = _now()
            row["display_name"] = display_name or row.get("display_name")
            row["avatar_url"] = avatar_url or row.get("avatar_url")

    # ---------- home stats ----------
    def get_dashboard_stats(self, user_id):
        active = sum(1 for g in self.goals.values() if g["user_id"] == user_id and g["status"] == "active")
        notes = sum(1 for n in self.notes.values() if n["user_id"] == user_id)
        by_status: dict[str, int] = {}
        for (uid, _pid), row in self.progress.items():
            if uid == user_id:
                by_status[row["status"]] = by_status.get(row["status"], 0) + 1
        stats = {
            "active_goals": active,
            "papers_in_collections": len(self._library_paper_ids(user_id)),
            "notes_written": notes,
            # The real query returns the breakdown inline so the home page needs one
            # round trip rather than two identical ones.
            "reading_by_status": by_status,
        }
        stats.update({f"papers_{k}": v for k, v in by_status.items()})
        return stats

    def get_progress_stats(self, user_id):
        out: dict[str, int] = {}
        for (uid, _pid), row in self.progress.items():
            if uid == user_id:
                out[row["status"]] = out.get(row["status"], 0) + 1
        return out

    def get_recent_papers(self, limit=10):
        return [dict(p) for p in list(self.papers.values())[:limit]]

    # ---------- goals ----------
    def create_learning_goal(self, user_id, title, description=None):
        gid = str(uuid.uuid4())
        row = {"goal_id": gid, "user_id": user_id, "title": title, "description": description,
               "status": "active", "created_at": _now(), "updated_at": _now()}
        self.goals[gid] = row
        return dict(row)

    def get_learning_goals(self, user_id, status=None, limit=None):
        rows = [dict(g) for g in self.goals.values()
                if g["user_id"] == user_id and (status is None or g["status"] == status)]
        return rows[:limit] if limit is not None else rows

    def get_learning_goal(self, goal_id, user_id):
        g = self.goals.get(goal_id)
        return dict(g) if g and g["user_id"] == user_id else None

    def update_goal_status(self, goal_id, user_id, status):
        g = self.goals.get(goal_id)
        if not g or g["user_id"] != user_id:
            return None
        g["status"], g["updated_at"] = status, _now()
        return dict(g)

    # ---------- papers ----------
    def get_paper(self, paper_id):
        p = self.papers.get(paper_id)
        return dict(p) if p else None

    def get_paper_by_doi(self, doi):
        return next((dict(p) for p in self.papers.values() if p.get("doi") == doi), None)

    def search_papers_by_text(self, query, limit=20, offset=0):
        q = query.lower()
        rows = [dict(p) for p in self.papers.values()
                if q in p["title"].lower() or q in (p.get("abstract") or "").lower()]
        return rows[offset:offset + limit]

    def get_authors_for_paper(self, paper_id):
        return []

    def semantic_search_papers(self, query_embedding, top_k=10):
        return []  # overridden per-test when semantic behaviour matters

    def get_notes_for_paper(self, user_id, paper_id):
        return [dict(n) for n in self.notes.values()
                if n["user_id"] == user_id and n["paper_id"] == paper_id]

    def get_progress_for_paper(self, user_id, paper_id):
        r = self.progress.get((user_id, paper_id))
        return dict(r) if r else None

    def _library_paper_ids(self, user_id) -> set:
        """Papers in this user's collections - the definition of "library"."""
        mine = {c["collection_id"] for c in self.collections.values() if c["user_id"] == user_id}
        return {pid for (cid, pid) in self.collection_papers if cid in mine}

    def _paper_fields(self, pid) -> dict:
        p = self.papers.get(pid, {})
        return {"title": p.get("title"), "publication_year": p.get("publication_year"),
                "venue": p.get("venue"), "tldr": p.get("tldr"),
                "citation_count": p.get("citation_count"),
                "open_access_url": p.get("open_access_url")}

    def get_user_progress(self, user_id, limit=None):
        out = []
        for (uid, pid), row in self.progress.items():
            if uid != user_id:
                continue
            out.append({**row, **self._paper_fields(pid)})
        return out[:limit] if limit is not None else out

    def get_reading_board(self, user_id):
        """Library papers plus anything explicitly given a status (see the real query)."""
        rows = []
        seen = set()
        for (uid, pid), row in self.progress.items():
            if uid != user_id:
                continue
            seen.add(pid)
            rows.append({**row, **self._paper_fields(pid), "paper_id": pid, "has_progress": True})
        for pid in self._library_paper_ids(user_id):
            if pid in seen:
                continue
            rows.append({"paper_id": pid, "user_id": user_id, "progress_id": None,
                         "status": "not_started", "updated_at": None, "has_progress": False,
                         **self._paper_fields(pid)})
        return rows

    def upsert_reading_progress(self, user_id, paper_id, status):
        row = self.progress.get((user_id, paper_id)) or {
            "progress_id": str(uuid.uuid4()), "user_id": user_id, "paper_id": paper_id}
        row["status"], row["updated_at"] = status, _now()
        self.progress[(user_id, paper_id)] = row
        return dict(row)

    def save_note(self, user_id, paper_id, note_text):
        nid = str(uuid.uuid4())
        row = {"note_id": nid, "user_id": user_id, "paper_id": paper_id,
               "note_text": note_text, "created_at": _now()}
        self.notes[nid] = row
        return dict(row)

    # ---------- usage counters ----------
    def increment_usage(self, scope, scope_id, metric):
        key = (scope, scope_id, metric)
        self.usage[key] = self.usage.get(key, 0) + 1
        return self.usage[key]

    def record_ai_operation(self, **fields):
        self.ai_operations.append(dict(fields))

    def get_usage_counts(self, scope, scope_id):
        return {m: n for (s, sid, m), n in self.usage.items()
                if s == scope and sid == scope_id}

    # ---------- collections ----------
    def create_collection(self, user_id, name, description=None):
        cid = str(uuid.uuid4())
        row = {"collection_id": cid, "user_id": user_id, "name": name,
               "description": description, "is_curated": False, "created_at": _now()}
        self.collections[cid] = row
        return dict(row)

    def get_curated_collections(self):
        return [{**c, "paper_count": sum(1 for (cid, _p) in self.collection_papers
                                         if cid == c["collection_id"])}
                for c in self.collections.values() if c.get("is_curated")]

    def get_curated_collection(self, collection_id):
        c = self.collections.get(collection_id)
        return dict(c) if c and c.get("is_curated") else None

    def get_collections(self, user_id):
        out = []
        for c in self.collections.values():
            if c["user_id"] != user_id:
                continue
            count = sum(1 for (cid, _pid) in self.collection_papers if cid == c["collection_id"])
            out.append({**c, "paper_count": count})
        return out

    def get_collection(self, collection_id, user_id):
        c = self.collections.get(collection_id)
        return dict(c) if c and c["user_id"] == user_id else None

    def get_collection_papers(self, collection_id):
        rows = []
        for (cid, pid), link in self.collection_papers.items():
            if cid != collection_id:
                continue
            p = self.papers.get(pid, {"paper_id": pid, "title": "Unknown"})
            rows.append({**p, "sequence_order": link["sequence_order"],
                         "added_at": _now(), "reading_status": None})
        return sorted(rows, key=lambda r: r["sequence_order"])

    def add_paper_to_collection(self, collection_id, paper_id, sequence_order=0):
        self.collection_papers[(collection_id, paper_id)] = {"sequence_order": sequence_order}

    def append_paper_to_collection(self, collection_id, paper_id):
        """Mirrors the SQL: next position, or the existing one if already present."""
        existing = self.collection_papers.get((collection_id, paper_id))
        if existing:
            return existing["sequence_order"]
        used = [link["sequence_order"] for (cid, _pid), link in self.collection_papers.items()
                if cid == collection_id]
        nxt = max(used, default=0) + 1
        self.collection_papers[(collection_id, paper_id)] = {"sequence_order": nxt}
        return nxt

    def update_paper_sequences(self, collection_id, ordered_paper_ids):
        """Mirrors the single-statement renumber: position in the list wins."""
        updated = 0
        for order, paper_id in enumerate(ordered_paper_ids, start=1):
            link = self.collection_papers.get((collection_id, str(paper_id)))
            if link is None:
                link = self.collection_papers.get((collection_id, paper_id))
            if link is not None:
                link["sequence_order"] = order
                updated += 1
        return updated

    def remove_paper_from_collection(self, collection_id, paper_id):
        return 1 if self.collection_papers.pop((collection_id, paper_id), None) else 0

    def update_paper_sequence(self, collection_id, paper_id, sequence_order):
        link = self.collection_papers.get((collection_id, paper_id))
        if link:
            link["sequence_order"] = sequence_order


@pytest.fixture
def db(monkeypatch):
    """In-memory repository + stubbed embedding/LLM. Returns the FakeDB instance."""
    fake = FakeDB()
    for name in dir(FakeDB):
        if name.startswith("_") or name == "seed_paper":
            continue
        if hasattr(lakebase_module, name):
            monkeypatch.setattr(lakebase_module, name, getattr(fake, name))

    monkeypatch.setattr(embedding_module, "encode_query",
                        lambda text: [0.0] * EMBEDDING_DIMENSION)
    monkeypatch.setattr(llm_module, "chat", lambda *a, **k: "Synthesised answer [1].")
    monkeypatch.setattr(llm_module, "is_available", lambda: True)

    # The identity cache is process-global — clear it so a user row from a prior
    # test's FakeDB never leaks into this one.
    auth_module._USER_CACHE.clear()
    return fake


@pytest.fixture
def app(db):
    from app import create_app
    application = create_app()  # EMBEDDING_PRELOAD=false is set at conftest import
    # CSRF off for the app under test so every POST test does not have to fetch a
    # token first. tests/test_auth.py builds a separate app with it ON and proves
    # an unprotected POST is refused - otherwise disabling it here could quietly
    # become disabling it everywhere.
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    application.config["_middleware_auth"] = auth_module
    return application


@pytest.fixture
def anon_app(app, monkeypatch):
    """App with the dev bypass off and anonymous browsing on (the 3.2 posture)."""
    monkeypatch.setattr(auth_module, "ALLOW_DEV_USER_BYPASS", False)
    monkeypatch.setattr(auth_module, "ALLOW_ANONYMOUS_DEMO", True)
    return app


@pytest.fixture
def signed_out_app(app, monkeypatch):
    """App that requires a session: no bypass, no anonymous access."""
    monkeypatch.setattr(auth_module, "ALLOW_DEV_USER_BYPASS", False)
    monkeypatch.setattr(auth_module, "ALLOW_ANONYMOUS_DEMO", False)
    return app


@pytest.fixture
def client(app):
    """Dev-mode client — no forwarded header falls back to the demo user."""
    return app.test_client()


@pytest.fixture
def anon_client(anon_app):
    return anon_app.test_client()


@pytest.fixture
def signed_out_client(signed_out_app):
    return signed_out_app.test_client()


def sign_in(client, user_id: str) -> None:
    """
    Put a user_id in the session, the way the OAuth callback does.

    Tests authenticate through the session rather than a header, because the
    header path no longer exists in production and a test-only one would be a
    second way to become a user.
    """
    with client.session_transaction() as sess:
        sess.clear()
        sess[auth_module.SESSION_USER_KEY] = str(user_id)
    auth_module._USER_CACHE.clear()
