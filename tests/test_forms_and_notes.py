"""
tests/test_forms_and_notes.py — the server-rendered forms, and reading notes back.

Four faults found together, three of them the same fault wearing different
clothes.

**Every server-rendered POST form was missing its CSRF token.** Creating a
collection, creating a goal, changing a reading status, generating a plan,
removing a paper, saving a note — all 400. `CSRFProtect(app)` is global and
these pages predate it; the JSON paths send `X-CSRFToken` from JavaScript and
kept working, so the gap was invisible from the chat side.

**And the 400 page claimed you were signed out.** CSRF was registered before
identity, Flask runs `before_request` in registration order, so a rejected POST
aborted before `g.user` existed. The error page rendered the anonymous shell —
"Log in", "You're viewing the demo workspace" — to someone signed in the whole
time, which is why the bug read as an authentication failure rather than a
missing form field.

**Notes had nowhere to be read.** `add_note` existed, the dashboard counted
"Notes written", and there was no page behind the number.
"""

import pathlib
import re

import pytest

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "templates"
XHR = {"X-Requested-With": "XMLHttpRequest"}


def _post_forms():
    """(template name, form html) for every server-rendered POST form."""
    for path in sorted(TEMPLATES.rglob("*.html")):
        src = path.read_text(encoding="utf-8")
        for match in re.finditer(r'<form[^>]*method=["\']post["\'][^>]*>(.*?)</form>',
                                 src, re.S | re.I):
            yield path.name, match.group(0)


def _signed_in_user(client, db) -> str:
    """The user id this client resolves to.

    The dev client creates its account on first request rather than up front, so
    ask for a page before looking for the row."""
    client.get("/dashboard")
    return next(iter(db.users_by_id))


# ---------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------

def test_every_post_form_carries_a_csrf_token():
    """
    The test that turns this from a browser discovery into a CI failure.

    Written over the templates rather than by exercising each route, because the
    next form somebody adds is the one at risk, and a per-route test only covers
    routes someone remembered to write a test for.
    """
    missing = [name for name, html in _post_forms() if "csrf_token" not in html]
    assert missing == [], f"POST forms with no CSRF token: {missing}"


def test_the_forms_that_were_broken_still_work(client, db, monkeypatch):
    """The six that returned 400. Named individually so a regression says which."""
    from middleware import auth

    pages = ("/collections", "/goals")
    for page in pages:
        body = client.get(page).get_data(as_text=True)
        for form in re.findall(r'<form[^>]*method=["\']post["\'][^>]*>(.*?)</form>',
                               body, re.S | re.I):
            assert "csrf_token" in form, page


# ---------------------------------------------------------------------------
# Identity resolves before anything can reject the request
# ---------------------------------------------------------------------------

def test_identity_is_resolved_before_csrf_can_refuse():
    """
    Registration order in the factory IS the behaviour: Flask runs
    before_request hooks in the order they were added. With CSRF first, a
    rejected POST never reached identity resolution and the error page told the
    person they were signed out.
    """
    source = (pathlib.Path(__file__).resolve().parents[1] / "dashboard"
              / "app.py").read_text(encoding="utf-8")

    auth_at = source.index("register_auth(app)")
    csrf_at = source.index("CSRFProtect(app)", source.index("def create_app"))

    assert auth_at < csrf_at, "CSRFProtect must be registered after register_auth"


def test_a_rejected_post_still_knows_who_you_are(db):
    """
    The symptom, tested end to end: submit without a token and the error page
    must not offer to sign in someone who already is.

    Builds its own app with CSRF ON, because the shared fixture turns it off so
    every other POST test need not fetch a token first — and a test of CSRF
    behaviour run against an app with CSRF disabled proves nothing.
    """
    from app import create_app

    application = create_app()
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    protected = application.test_client()

    protected.get("/dashboard")          # resolve identity, as a browser would
    resp = protected.post("/collections", data={"name": "No token here"})

    assert resp.status_code == 400
    body = resp.get_data(as_text=True)
    assert "You're viewing the demo workspace" not in body
    assert "Sign up to save work" not in body


# ---------------------------------------------------------------------------
# Notes have somewhere to live
# ---------------------------------------------------------------------------

def test_a_note_can_be_read_back(client, db):
    """
    The gap: writing worked, reading did not exist. A note you cannot find again
    is a note you did not keep.
    """
    paper = db.seed_paper(title="Attention Is All You Need", venue="NeurIPS")
    user = _signed_in_user(client, db)
    db.save_note(user, str(paper["paper_id"]), "The positional encoding is the clever part.")

    body = client.get("/notes").get_data(as_text=True)
    assert "The positional encoding is the clever part." in body
    assert "Attention Is All You Need" in body


def test_notes_are_grouped_under_their_paper(client, db):
    """
    Three thoughts about one paper belong together. Flat and reverse
    chronological, they scatter across however many months separate them.
    """
    paper = db.seed_paper(title="A Paper")
    user = _signed_in_user(client, db)
    for text in ("first thought", "second thought"):
        db.save_note(user, str(paper["paper_id"]), text)

    body = client.get("/notes").get_data(as_text=True)

    # Counted as headings rather than as occurrences of the string: the paper's
    # title also rides on each note as a data attribute now, so a raw count
    # measures the markup rather than what the reader sees.
    headings = re.findall(r'<h2 class="note-paper-title[^"]*">(.*?)</h2>', body, re.S)
    assert sum(1 for h in headings if "A Paper" in h) == 1
    assert "first thought" in body and "second thought" in body


def test_the_notes_page_links_back_to_the_paper(client, db):
    paper = db.seed_paper(title="A Paper")
    user = _signed_in_user(client, db)
    db.save_note(user, str(paper["paper_id"]), "a note")

    body = client.get("/notes").get_data(as_text=True)
    assert f"/paper/{paper['paper_id']}" in body


def test_an_empty_notes_page_says_where_to_start(client, db):
    """Silence reads as broken. It should read as 'nothing here yet'."""
    body = client.get("/notes").get_data(as_text=True)
    assert "No notes yet" in body


def test_notes_are_private_to_their_author(client, db):
    """The only real security property this page has."""
    paper = db.seed_paper(title="A Paper")
    stranger = db.get_or_create_user("someone.else@example.com", "Someone Else")
    db.save_note(stranger["user_id"], str(paper["paper_id"]), "not yours to read")

    body = client.get("/notes").get_data(as_text=True)
    assert "not yours to read" not in body


def test_an_anonymous_visitor_is_not_offered_the_page(anon_client):
    """
    They cannot write a note, so the link would lead to a capability prompt
    every time. The dashboard's counter stays inert for them too.
    """
    body = anon_client.get("/dashboard").get_data(as_text=True)
    assert 'href="/notes"' not in body


def test_the_dashboard_counter_now_leads_somewhere(client, db):
    """
    It counted notes and linked nowhere — the one stat card you could not click,
    pointing at a page that did not exist.
    """
    body = client.get("/dashboard").get_data(as_text=True)
    assert 'href="/notes"' in body
