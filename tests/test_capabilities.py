"""
tests/test_capabilities.py — what an anonymous visitor may do (Phase 3.2).

The demo tier has to be two things at once: generous enough that the product is
recognisable without signing in, and unable to accumulate state or spend without
limit. The tests below pin both halves, and the boundary between them.

The distinction worth protecting is between the two refusals:

    403  capability denied   "sign in"         — the answer is an account
    429  quota exceeded      "come back later" — the answer is time

Collapsing them tells a signed-in user who ran out of allowance to sign in, which
is advice they cannot act on.
"""

import pytest

from middleware import capabilities
from services import quota_service

# What dashboard/static/js/main.js actually sends. The error handler answers in
# JSON only for XHR callers, so a test that wants the JSON body must ask the way
# the real client asks.
XHR = {"X-Requested-With": "XMLHttpRequest"}


# ---------------------------------------------------------------------------
# The tier table itself
# ---------------------------------------------------------------------------

def test_anonymous_can_use_every_expensive_capability():
    """
    A demo that cannot search or ask questions is a screenshot. The expensive
    capabilities are the product; they are metered rather than withheld.
    """
    for cap in (capabilities.SEARCH_SEMANTIC, capabilities.RAG_ASK,
                capabilities.AGENT_QUERY):
        assert capabilities.tier_can("anonymous", cap), cap


def test_anonymous_holds_no_persistent_capability():
    """Nothing an anonymous visitor does may outlive their session."""
    for cap in (capabilities.LIBRARY_WRITE, capabilities.NOTES_WRITE,
                capabilities.GOALS_WRITE, capabilities.PROGRESS_WRITE):
        assert not capabilities.tier_can("anonymous", cap), cap


def test_authenticated_is_a_superset_of_anonymous():
    """Signing in must never take a capability away."""
    anon = capabilities.CAPABILITIES["anonymous"]
    auth = capabilities.CAPABILITIES["authenticated"]
    assert anon <= auth


def test_an_unknown_tier_gets_nothing():
    """Fail closed: a typo in a tier name must not grant access."""
    assert not capabilities.tier_can("superuser", capabilities.LIBRARY_WRITE)
    assert not capabilities.tier_can("", capabilities.SEARCH_SEMANTIC)


# ---------------------------------------------------------------------------
# Writes are refused anonymously, end to end
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,path,payload", [
    ("post", "/collections", {"name": "Mine"}),
    ("post", "/goals", {"title": "Learn RAG"}),
])
def test_anonymous_writes_are_refused(anon_client, method, path, payload):
    resp = getattr(anon_client, method)(path, json=payload, headers=XHR)
    assert resp.status_code == 403


def test_a_refused_write_changes_nothing(anon_client, db):
    anon_client.post("/goals", json={"title": "Should not exist"}, headers=XHR)
    assert not db.goals


def test_the_refusal_points_at_signing_in(anon_client):
    """
    The response has to carry the remedy: the page shows a sign-in prompt without
    parsing the message text.
    """
    resp = anon_client.post("/goals", json={"title": "x"},
                            headers=XHR)
    body = resp.get_json()
    assert body["requires_auth"] is True
    assert body["sign_in_url"].endswith("/login")


def test_the_same_write_succeeds_once_signed_in(client, db):
    """The control: the route works, it is the tier that was refused."""
    resp = client.post("/goals", json={"title": "Learn RAG"},
                       headers=XHR)
    assert resp.status_code == 200
    assert db.goals


# ---------------------------------------------------------------------------
# Reads stay open
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/search?q=transformer&mode=keyword", "/collections"])
def test_anonymous_can_browse(anon_client, path):
    assert anon_client.get(path).status_code == 200


# ---------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------

def test_a_metered_request_consumes_allowance(anon_client, db):
    anon_client.get("/search/semantic?q=attention")
    used = [n for (scope, _sid, metric), n in db.usage.items()
            if scope == "anon" and metric == "semantic_search"]
    assert used == [1]


def test_allowance_runs_out(anon_client, db, monkeypatch):
    monkeypatch.setattr(quota_service._policy, "_LIMITS",
                        {"anonymous": {"semantic_search": 2, "rag_query": 0, "agent_query": 0},
                         "authenticated": {"semantic_search": 99, "rag_query": 99, "agent_query": 99}})

    assert anon_client.get("/search/semantic?q=a").status_code == 200
    assert anon_client.get("/search/semantic?q=b").status_code == 200
    assert anon_client.get("/search/semantic?q=c").status_code == 429


def test_running_out_invites_signing_in(anon_client, monkeypatch):
    monkeypatch.setattr(quota_service._policy, "_LIMITS",
                        {"anonymous": {"semantic_search": 0, "rag_query": 0, "agent_query": 0},
                         "authenticated": {"semantic_search": 9, "rag_query": 9, "agent_query": 9}})

    body = anon_client.get("/search/semantic?q=a", headers=XHR).get_json()

    assert body["requires_auth"] is True
    assert body["metric"] == "semantic_search"


def test_an_authenticated_user_is_metered_too(client, db, monkeypatch):
    """
    The correction that matters: authentication raises the allowance, it does not
    remove it. A signed-in user with a runaway script costs the same as anyone.
    """
    monkeypatch.setattr(quota_service._policy, "_LIMITS",
                        {"anonymous": {"semantic_search": 0, "rag_query": 0, "agent_query": 0},
                         "authenticated": {"semantic_search": 1, "rag_query": 0, "agent_query": 0}})

    assert client.get("/search/semantic?q=a").status_code == 200
    assert client.get("/search/semantic?q=b").status_code == 429


def test_the_global_ceiling_does_not_tell_anyone_to_sign_in(anon_client, monkeypatch):
    """
    When the whole application is out of budget, signing in would not help. Saying
    otherwise promises something the product cannot deliver.
    """
    monkeypatch.setattr(quota_service, "_GLOBAL_LIMITS", {"rag_query": 0})

    resp = anon_client.post("/search/ask", json={"question": "why?"},
                            headers=XHR)
    body = resp.get_json()

    assert resp.status_code == 429
    assert body["requires_auth"] is False
    assert "sign_in_url" not in body


def test_the_global_ceiling_also_stops_authenticated_users(client, monkeypatch):
    """Many well-behaved users cost as much as one abusive one."""
    monkeypatch.setattr(quota_service, "_GLOBAL_LIMITS", {"rag_query": 0})

    resp = client.post("/search/ask", json={"question": "why?"},
                       headers=XHR)
    assert resp.status_code == 429


def test_quota_is_consumed_before_the_work_happens(anon_client, db, monkeypatch):
    """
    Metering after the fact records the cost without preventing it. This asserts
    the embedding call never runs once the allowance is gone.
    """
    import embedding

    calls: list[str] = []
    monkeypatch.setattr(embedding, "encode_query",
                        lambda text: calls.append(text) or [0.0] * 768)
    monkeypatch.setattr(quota_service._policy, "_LIMITS",
                        {"anonymous": {"semantic_search": 0, "rag_query": 0, "agent_query": 0},
                         "authenticated": {"semantic_search": 9, "rag_query": 9, "agent_query": 9}})

    anon_client.get("/search/semantic?q=expensive")

    assert calls == [], "the query was embedded despite having no allowance"


def test_search_is_not_billed_as_a_rag_query(anon_client, db):
    """Metrics are separate because their costs are: one is an LLM call, one is not."""
    anon_client.get("/search/semantic?q=a")
    metrics = {metric for (_s, _sid, metric) in db.usage}
    assert metrics == {"semantic_search"}


def test_related_papers_is_metered(anon_client, db):
    """
    Fetched asynchronously by every paper page. Unmetered, it would be the easiest
    way to run unlimited embedding calls — just keep opening papers.
    """
    paper = db.seed_paper(title="Something")
    anon_client.get(f"/paper/{paper['paper_id']}/related")
    assert any(metric == "semantic_search" for (_s, _sid, metric) in db.usage)


def test_keyword_search_is_free(anon_client, db):
    """It is a database LIKE. Metering it would spend the budget on nothing."""
    anon_client.get("/search?q=transformer&mode=keyword")
    assert not db.usage


def test_quotas_can_be_switched_off_for_local_work(anon_client, db, monkeypatch):
    monkeypatch.setattr(quota_service, "QUOTAS_ENABLED", False)
    for _ in range(5):
        assert anon_client.get("/search/semantic?q=a").status_code == 200
    assert not db.usage


# ---------------------------------------------------------------------------
# Curated demo collections
# ---------------------------------------------------------------------------

def _curated(db, name="Retrieval-Augmented Generation"):
    system = db.get_or_create_user("system@research-copilot.dev", "Research Copilot")
    coll = db.create_collection(system["user_id"], name)
    db.collections[coll["collection_id"]]["is_curated"] = True
    return db.collections[coll["collection_id"]]


def test_anonymous_visitors_see_curated_collections(anon_client, db):
    """
    The reason the collections page is worth opening signed-out at all. Without
    these it is an empty shell with a sign-in prompt.
    """
    _curated(db)
    body = anon_client.get("/collections").get_data(as_text=True)
    assert "Retrieval-Augmented Generation" in body


def test_a_curated_collection_can_be_opened_by_anyone(client, db):
    """
    The bug this covers: ownership-only lookup reported curated collections as
    missing, so they listed fine and 404ed when clicked — they belong to the
    system account, not the viewer.
    """
    coll = _curated(db)
    assert client.get(f"/collection/{coll['collection_id']}").status_code == 200


def test_a_signed_in_user_cannot_edit_a_curated_collection(client, db):
    """Read-only for every tier, not just anonymous ones."""
    coll = _curated(db)
    paper = db.seed_paper()

    resp = client.post(f"/collection/{coll['collection_id']}/papers",
                       json={"paper_id": paper["paper_id"]}, headers=XHR)

    assert resp.status_code == 403
    assert (coll["collection_id"], paper["paper_id"]) not in db.collection_papers


def test_editing_a_curated_collection_does_not_suggest_signing_in(client, db):
    """
    The user IS signed in. Offering an account as the remedy would be advice they
    have already taken, so the message has to name the real rule instead.
    """
    coll = _curated(db)
    paper = db.seed_paper()

    body = client.post(f"/collection/{coll['collection_id']}/papers",
                       json={"paper_id": paper["paper_id"]}, headers=XHR).get_json()

    assert body["requires_auth"] is False
    assert "shared example" in body["detail"]


def test_a_curated_collection_is_not_reported_as_missing(client, db):
    """
    "Not found" for something plainly on screen sends the reader hunting for a
    bug. The refusal must state the rule.
    """
    coll = _curated(db)
    body = client.post(f"/collection/{coll['collection_id']}/reorder",
                       json={"paper_ids": []}, headers=XHR).get_json()
    assert "not found" not in body["detail"].lower()


def test_a_users_own_collections_are_still_editable(client, db):
    """The control: curation is the restriction, not collections generally."""
    _curated(db)
    own = client.post("/collections", json={"name": "Mine"}, headers=XHR).get_json()["collection"]
    paper = db.seed_paper()

    resp = client.post(f"/collection/{own['collection_id']}/papers",
                       json={"paper_id": paper["paper_id"]}, headers=XHR)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# The interface matches what the server will allow
# ---------------------------------------------------------------------------
#
# A control that fails on click teaches the visitor the product is broken. These
# assert the page never offers an action the request would refuse.

def test_anonymous_sees_no_note_form(anon_client, db):
    paper = db.seed_paper(title="Some Paper")
    body = anon_client.get(f"/paper/{paper['paper_id']}").get_data(as_text=True)
    assert 'id="note-form"' not in body
    assert "Sign in" in body


def test_anonymous_sees_no_reading_status_buttons(anon_client, db):
    paper = db.seed_paper()
    body = anon_client.get(f"/paper/{paper['paper_id']}").get_data(as_text=True)
    assert "status-btn" not in body


def test_anonymous_sees_no_collection_creation_form(anon_client):
    body = anon_client.get("/collections").get_data(as_text=True)
    assert "collections.create_collection" not in body
    assert 'action="/collections"' not in body


def test_anonymous_sees_no_goal_creation_form(anon_client):
    body = anon_client.get("/goals").get_data(as_text=True)
    assert 'action="/goals"' not in body


def test_a_signed_in_user_does_see_those_controls(client, db):
    """The control: the gate is the tier, not a template that lost its form."""
    paper = db.seed_paper()
    detail = client.get(f"/paper/{paper['paper_id']}").get_data(as_text=True)
    assert 'id="note-form"' in detail
    assert "status-btn" in detail
    assert 'action="/goals"' in client.get("/goals").get_data(as_text=True)


def test_a_curated_collection_offers_no_edit_controls_even_when_signed_in(client, db):
    """Read-only for every tier, and the page has to say so rather than fail on click."""
    coll = _curated(db)
    paper = db.seed_paper()
    db.add_paper_to_collection(coll["collection_id"], paper["paper_id"])

    body = client.get(f"/collection/{coll['collection_id']}").get_data(as_text=True)

    assert "Generate reading plan" not in body
    assert "Remove from collection" not in body
    assert "js-sortable" not in body
    assert "shared example" in body.lower()


def test_an_editable_collection_keeps_its_controls(client, db):
    coll = client.post("/collections", json={"name": "Mine"}, headers=XHR).get_json()["collection"]
    paper = db.seed_paper()
    db.add_paper_to_collection(coll["collection_id"], paper["paper_id"])

    body = client.get(f"/collection/{coll['collection_id']}").get_data(as_text=True)

    assert "Generate reading plan" in body
    assert "js-sortable" in body


# ---------------------------------------------------------------------------
# The shell
# ---------------------------------------------------------------------------

def test_a_new_visitor_lands_on_the_chat_first_page(anon_client):
    """
    Not the dashboard. Someone who has never been here has no workspace, and
    showing them an empty one explains nothing about what this is.
    """
    body = anon_client.get("/").get_data(as_text=True)
    assert "What are you researching?" in body
    assert "Try demo" in body
    assert 'class="sidebar"' not in body


def test_a_signed_in_user_lands_on_their_workspace(client):
    body = client.get("/").get_data(as_text=True)
    assert "What are you researching?" not in body
    assert 'class="sidebar"' in body


def test_the_demo_workspace_is_reachable_without_an_account(anon_client):
    """'Try demo' has to go somewhere, and that somewhere is the real product."""
    assert anon_client.get("/dashboard").status_code == 200


def test_the_chat_shell_renders(client):
    body = client.get("/chat").get_data(as_text=True)
    assert "chat-composer" in body
    assert "No recent chats yet" in body


def test_a_conversation_url_resolves_before_persistence_exists(client):
    """
    Routed now so the sidebar, back button and shared links all work the moment
    storage lands, instead of needing a second pass over the navigation.
    """
    assert client.get("/chat/anything").status_code == 200
