"""
tests/conftest.py — shared fixtures.

No live database or network. `FakeDB` is an in-memory stand-in for
dashboard/repositories/lakebase.py; the `db` fixture monkeypatches every
matching function on the real module, and stubs the embedding model and the
OpenRouter client so tests run fast and offline.
"""

import datetime
import uuid

import pytest

from dashboard import embedding as embedding_module
from dashboard import llm_client as llm_module
from dashboard.middleware import auth as auth_module
from dashboard.repositories import lakebase as lakebase_module

DEMO_EMAIL = "demo@research-copilot.dev"


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
        row = {"user_id": uid, "email": email, "display_name": display_name, "created_at": _now()}
        self.users_by_id[uid] = row
        self.users_by_email[email] = row
        return dict(row)

    def get_user_by_email(self, email):
        row = self.users_by_email.get(email)
        return dict(row) if row else None

    # ---------- home stats ----------
    def get_dashboard_stats(self, user_id):
        active = sum(1 for g in self.goals.values() if g["user_id"] == user_id and g["status"] == "active")
        notes = sum(1 for n in self.notes.values() if n["user_id"] == user_id)
        return {"active_goals": active, "papers_in_collections": 0, "notes_written": notes}

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

    def get_learning_goals(self, user_id, status=None):
        return [dict(g) for g in self.goals.values()
                if g["user_id"] == user_id and (status is None or g["status"] == status)]

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

    def get_user_progress(self, user_id):
        out = []
        for (uid, pid), row in self.progress.items():
            if uid != user_id:
                continue
            p = self.papers.get(pid, {})
            out.append({**row, "title": p.get("title"), "publication_year": p.get("publication_year"),
                        "venue": p.get("venue"), "tldr": p.get("tldr"),
                        "citation_count": p.get("citation_count"), "open_access_url": p.get("open_access_url")})
        return out

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

    # ---------- collections ----------
    def create_collection(self, user_id, name, description=None):
        cid = str(uuid.uuid4())
        row = {"collection_id": cid, "user_id": user_id, "name": name,
               "description": description, "created_at": _now()}
        self.collections[cid] = row
        return dict(row)

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

    monkeypatch.setattr(embedding_module, "encode_query", lambda text: [0.0] * 384)
    monkeypatch.setattr(llm_module, "chat", lambda *a, **k: "Synthesised answer [1].")
    monkeypatch.setattr(llm_module, "is_available", lambda: True)
    return fake


@pytest.fixture
def app(db):
    from dashboard.app import create_app
    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(app):
    """Dev-mode client — no forwarded header falls back to the demo user."""
    return app.test_client()


@pytest.fixture
def strict_app(app, monkeypatch):
    """App with REQUIRE_FORWARDED_AUTH flipped on (Databricks App behaviour)."""
    monkeypatch.setattr(auth_module, "REQUIRE_FORWARDED_AUTH", True)
    return app


@pytest.fixture
def strict_client(strict_app):
    return strict_app.test_client()


def as_user(email: str) -> dict:
    """Header dict that makes a request act as `email` (mimics the Databricks proxy)."""
    return {"X-Forwarded-Email": email, "X-Forwarded-Preferred-Username": email.split("@")[0]}
