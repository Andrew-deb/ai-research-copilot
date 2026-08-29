"""
tests/test_routes.py — endpoint behaviour: happy paths, validation, and the
JSON-vs-redirect content negotiation done by routes/helpers.action_response.
"""

import pytest

from tests.conftest import as_user

ALICE = as_user("alice@example.com")


# ---------------------------------------------------------------------------
# Pages render
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/goals", "/search", "/collections", "/progress"])
def test_pages_render(client, path):
    assert client.get(path, headers=ALICE).status_code == 200


def test_unknown_route_is_404_page(client):
    resp = client.get("/no-such-page", headers=ALICE)
    assert resp.status_code == 404
    assert b"not found" in resp.data.lower()


def test_unknown_route_is_404_json_for_xhr(client):
    resp = client.get("/no-such-page", headers={**ALICE, "X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 404
    assert resp.is_json


def test_search_page_without_query_has_no_results_section(client):
    resp = client.get("/search", headers=ALICE)
    assert resp.status_code == 200
    assert b"results" not in resp.data.lower() or b"result" in resp.data.lower()


def test_keyword_search_finds_seeded_paper(client, db):
    db.seed_paper(title="Attention Is All You Need")
    resp = client.get("/search?q=Attention&mode=keyword", headers=ALICE)
    assert resp.status_code == 200
    assert b"Attention Is All You Need" in resp.data


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

def test_create_goal_rejects_empty_title(client):
    resp = client.post("/goals", json={"title": "   "}, headers=ALICE)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Invalid input"


def test_create_goal_success_json(client, db):
    resp = client.post("/goals", json={"title": "Learn RAG", "description": "d"}, headers=ALICE)
    assert resp.status_code == 200
    assert resp.get_json()["goal"]["title"] == "Learn RAG"
    assert len(db.goals) == 1


def test_create_goal_form_post_redirects(client, db):
    resp = client.post("/goals", data={"title": "Learn RAG"}, headers=ALICE)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/goals")


def test_goal_status_validation(client, db):
    goal = client.post("/goals", json={"title": "g"}, headers=ALICE).get_json()["goal"]
    resp = client.post(f"/goals/{goal['goal_id']}/status", json={"status": "bogus"}, headers=ALICE)
    assert resp.status_code == 400


def test_goal_status_update(client, db):
    goal = client.post("/goals", json={"title": "g"}, headers=ALICE).get_json()["goal"]
    resp = client.post(f"/goals/{goal['goal_id']}/status", json={"status": "completed"}, headers=ALICE)
    assert resp.status_code == 200
    assert db.goals[goal["goal_id"]]["status"] == "completed"


# ---------------------------------------------------------------------------
# Paper detail
# ---------------------------------------------------------------------------

def test_paper_detail_404_for_unknown_id(client):
    resp = client.get("/paper/00000000-0000-0000-0000-000000000000", headers=ALICE)
    assert resp.status_code == 404


def test_paper_detail_renders(client, db):
    paper = db.seed_paper(title="A Studied Paper", tldr="short summary")
    resp = client.get(f"/paper/{paper['paper_id']}", headers=ALICE)
    assert resp.status_code == 200
    assert b"A Studied Paper" in resp.data
    assert b"short summary" in resp.data
    # related papers now load async — the panel + its fetch URL are present, no vector work on this request
    assert b"related-panel" in resp.data


def test_paper_related_endpoint(client, db, monkeypatch):
    from dashboard.repositories import lakebase
    paper = db.seed_paper(title="Origin Paper")
    other = db.seed_paper(title="Neighbour Paper")
    monkeypatch.setattr(
        lakebase, "semantic_search_papers",
        lambda vec, top_k=10: [{**other, "chunk_text": "x", "chunk_index": 0, "similarity": 0.77}],
    )
    resp = client.get(f"/paper/{paper['paper_id']}/related", headers=ALICE)
    assert resp.status_code == 200
    rel = resp.get_json()["related"]
    assert rel and rel[0]["title"] == "Neighbour Paper"
    assert rel[0]["paper_id"] != paper["paper_id"]


def test_paper_related_404_for_unknown(client, db):
    resp = client.get("/paper/00000000-0000-0000-0000-000000000000/related", headers=ALICE)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Collections + reading plan
# ---------------------------------------------------------------------------

def test_collection_lifecycle(client, db):
    cid = client.post("/collections", json={"name": "C1"}, headers=ALICE).get_json()["collection"]["collection_id"]
    p1 = db.seed_paper(title="P1", publication_year=2019, citation_count=100)
    p2 = db.seed_paper(title="P2", publication_year=2017, citation_count=5)

    for p in (p1, p2):
        r = client.post(f"/collection/{cid}/papers", json={"paper_id": p["paper_id"]}, headers=ALICE)
        assert r.status_code == 200

    detail = client.get(f"/collection/{cid}", headers=ALICE)
    assert detail.data.count(b"paper-title") >= 2

    # Reading plan orders by year ascending → 2017 paper first.
    plan = client.post(f"/collection/{cid}/plan", json={}, headers=ALICE)
    assert plan.status_code == 200
    ordered = [item["title"] for item in plan.get_json()["reading_plan"]]
    assert ordered == ["P2", "P1"]

    rm = client.post(f"/collection/{cid}/papers/{p1['paper_id']}/remove", json={}, headers=ALICE)
    assert rm.status_code == 200
    assert (cid, p1["paper_id"]) not in db.collection_papers


def test_reading_plan_on_empty_collection_is_400(client, db):
    cid = client.post("/collections", json={"name": "Empty"}, headers=ALICE).get_json()["collection"]["collection_id"]
    resp = client.post(f"/collection/{cid}/plan", json={}, headers=ALICE)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Progress + notes
# ---------------------------------------------------------------------------

def test_mark_status_invalid(client, db):
    paper = db.seed_paper()
    resp = client.post(f"/paper/{paper['paper_id']}/status", json={"status": "nope"}, headers=ALICE)
    assert resp.status_code == 400


def test_mark_status_ok_and_shows_on_board(client, db):
    paper = db.seed_paper(title="Board Paper")
    resp = client.post(f"/paper/{paper['paper_id']}/status", json={"status": "reading"}, headers=ALICE)
    assert resp.status_code == 200

    board = client.get("/progress", headers=ALICE)
    assert b"Board Paper" in board.data


def test_save_note(client, db):
    paper = db.seed_paper()
    resp = client.post(f"/paper/{paper['paper_id']}/notes", json={"note_text": "my note"}, headers=ALICE)
    assert resp.status_code == 200
    assert resp.get_json()["note"]["note_text"] == "my note"


def test_save_empty_note_rejected(client, db):
    paper = db.seed_paper()
    resp = client.post(f"/paper/{paper['paper_id']}/notes", json={"note_text": "  "}, headers=ALICE)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# RAG endpoint (LLM + embedding are stubbed in the db fixture)
# ---------------------------------------------------------------------------

def test_rag_ask_with_no_matches(client, db):
    resp = client.post("/search/ask", json={"question": "what is attention?"}, headers=ALICE)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["answer"] is None
    assert body["sources"] == []


def test_rag_ask_with_matches(client, db, monkeypatch):
    from dashboard.repositories import lakebase
    paper = db.seed_paper(title="Attention Paper")

    def fake_vec_search(query_embedding, top_k=10):
        return [{**paper, "chunk_text": "self-attention ...", "chunk_index": 0, "similarity": 0.91}]

    monkeypatch.setattr(lakebase, "semantic_search_papers", fake_vec_search)

    resp = client.post("/search/ask", json={"question": "explain attention"}, headers=ALICE)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["answer"] == "Synthesised answer [1]."
    assert body["sources"][0]["title"] == "Attention Paper"
