"""
tests/test_auth.py — identity resolution and endpoint authorization.

Covers the behaviour that caused deployment friction on Databricks Apps:
what happens when the `X-Forwarded-Email` proxy header is present, absent,
or blank, in both auth modes, and that users cannot reach each other's data.
"""

from tests.conftest import DEMO_EMAIL, as_user

PROTECTED_GET_PATHS = ["/", "/goals", "/search", "/collections", "/progress"]


# ---------------------------------------------------------------------------
# Health check — must always answer, no proxy header, no DB
# ---------------------------------------------------------------------------

def test_healthz_open_in_dev(client):
    assert client.get("/healthz").status_code == 200


def test_healthz_open_in_strict_mode(strict_client):
    # Deploy health checks hit this before identity/DB are reachable.
    resp = strict_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_static_assets_need_no_auth(strict_client):
    resp = strict_client.get("/static/css/base.css")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Dev mode — missing header falls back to the demo user
# ---------------------------------------------------------------------------

def test_dev_mode_falls_back_to_demo_user(client, db):
    assert client.get("/").status_code == 200
    assert DEMO_EMAIL in db.users_by_email


def test_dev_mode_honours_forwarded_email_when_present(client, db):
    resp = client.get("/", headers=as_user("alice@example.com"))
    assert resp.status_code == 200
    assert "alice@example.com" in db.users_by_email
    assert DEMO_EMAIL not in db.users_by_email  # demo not touched


def test_first_seen_user_is_provisioned(client, db):
    client.get("/goals", headers=as_user("newcomer@example.com"))
    row = db.get_user_by_email("newcomer@example.com")
    assert row is not None and row["display_name"] == "newcomer"


# ---------------------------------------------------------------------------
# Strict mode — missing / blank header is rejected
# ---------------------------------------------------------------------------

def test_strict_mode_rejects_missing_header(strict_client):
    for path in PROTECTED_GET_PATHS:
        assert strict_client.get(path).status_code == 401, path


def test_strict_mode_rejects_blank_header(strict_client):
    resp = strict_client.get("/", headers={"X-Forwarded-Email": "   "})
    assert resp.status_code == 401


def test_strict_mode_allows_with_header(strict_client, db):
    resp = strict_client.get("/", headers=as_user("bob@example.com"))
    assert resp.status_code == 200
    assert "bob@example.com" in db.users_by_email


def test_strict_mode_401_is_json_for_xhr(strict_client):
    resp = strict_client.get("/", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 401
    assert resp.is_json
    assert "detail" in resp.get_json()


def test_strict_mode_401_is_html_for_browser(strict_client):
    resp = strict_client.get("/", headers={"Accept": "text/html"})
    assert resp.status_code == 401
    assert b"not authorised" in resp.data.lower()


def test_write_endpoint_blocked_in_strict_mode(strict_client):
    resp = strict_client.post("/goals", json={"title": "x"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Cross-user isolation — every route is scoped to the resolved user
# ---------------------------------------------------------------------------

def test_users_cannot_read_each_others_goals(client, db):
    created = client.post("/goals", json={"title": "Alice's goal"}, headers=as_user("alice@example.com"))
    goal_id = created.get_json()["goal"]["goal_id"]

    # Bob asking for Alice's goal-match panel → not found
    resp = client.get(f"/goals/{goal_id}/matches", headers=as_user("bob@example.com"))
    assert resp.status_code == 404

    # Alice still can
    ok = client.get(f"/goals/{goal_id}/matches", headers=as_user("alice@example.com"))
    assert ok.status_code == 200


def test_users_cannot_open_each_others_collections(client, db):
    created = client.post("/collections", json={"name": "Alice's list"}, headers=as_user("alice@example.com"))
    collection_id = created.get_json()["collection"]["collection_id"]

    resp = client.get(f"/collection/{collection_id}", headers=as_user("mallory@example.com"))
    assert resp.status_code == 404

    assert client.get(f"/collection/{collection_id}", headers=as_user("alice@example.com")).status_code == 200


def test_users_cannot_mutate_each_others_collections(client, db):
    created = client.post("/collections", json={"name": "Alice's list"}, headers=as_user("alice@example.com"))
    collection_id = created.get_json()["collection"]["collection_id"]
    paper = db.seed_paper()

    resp = client.post(
        f"/collection/{collection_id}/papers",
        json={"paper_id": paper["paper_id"]},
        headers=as_user("bob@example.com"),
    )
    assert resp.status_code == 404
    assert (collection_id, paper["paper_id"]) not in db.collection_papers
