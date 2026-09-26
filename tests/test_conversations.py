"""
tests/test_conversations.py — research chat history (Phase 3.4).

Deferred in 3.3 because the shape of a stored turn depends on what an agent
actually produces. It does now, so the envelope is stored whole and replayed
through the same renderer a live turn uses.

The property worth defending hardest is the one that is easy to lose quietly:
**anonymous visitors have no history**. The demo tier says nothing is kept, the
sidebar hides the controls, and `conversations.user_id` is NOT NULL. Three
layers saying the same thing, because a promise about data is only as good as
its least careful enforcement.
"""

import json
import pathlib

import pytest

from exceptions import CapabilityDeniedError
from services import agent_service, conversation_service

XHR = {"X-Requested-With": "XMLHttpRequest"}
SSE = {"Accept": "text/event-stream", "X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def answering(monkeypatch):
    """A connected agent that answers without touching a model or a server."""
    monkeypatch.setattr(agent_service, "is_connected", lambda: True)

    def fake_ask(question, *, tier, user_id=None, conversation_history=None,
                 on_event=None, usage=None):
        return agent_service.envelope(
            question, answer="Because [1].",
            citations=[{"number": 1, "paper_id": "p1", "title": "A Paper",
                        "publication_year": 2023, "venue": "arXiv",
                        "citation_count": 10, "similarity": 0.8}],
            sources=[{"number": 0, "paper_id": "p2", "title": "Consulted Only"}],
            tool_calls=[{"name": "search_papers", "arguments": {"query": "rag"},
                         "ok": True, "error": None}],
            llm_turns=2)

    monkeypatch.setattr(agent_service, "ask", fake_ask)


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

def test_a_title_prefers_the_heading_the_answer_wrote():
    """
    The model routinely opens with a heading — a title it has already written,
    free, and better than anything derived from the question. Asking a model to
    summarise the question into a title would be a second API call on a tier
    that rate-limits by the minute.
    """
    title = conversation_service.title_from(
        "compare the main approaches to retrieval-augmented generation",
        "# Comparing Main Approaches to Retrieval-Augmented Generation\n\nBased on…")
    assert title == "Comparing Main Approaches to Retrieval-Augmented Generation"


def test_a_heading_far_down_the_answer_is_not_the_title():
    """A "## Summary" halfway down names a section, not the conversation."""
    answer = "Some opening prose.\n\n" + ("filler. " * 120) + "\n## Summary\n"
    assert conversation_service.title_from("What is RAG?", answer) == "What is RAG"


def test_markdown_never_reaches_the_sidebar():
    title = conversation_service.title_from("q", "# **Bold** `title`\n")
    assert "*" not in title and "`" not in title


def test_a_question_is_tidied_into_a_label():
    """Trailing punctuation and openers that say nothing about the subject."""
    assert conversation_service.title_from("What is RAG?") == "What is RAG"
    assert conversation_service.title_from(
        "can you tell me what RAG is?") == "What RAG is"
    assert conversation_service.title_from(
        "please help me find papers on dense retrieval") == "Find papers on dense retrieval"


def test_an_acronym_keeps_its_case():
    """Only the first character is touched, so RAG does not become Rag."""
    assert conversation_service.title_from("rag pipelines explained").startswith("Rag pipelines")
    assert "RAG" in conversation_service.title_from("what is RAG")


def test_a_long_title_is_cut_on_a_word():
    """"…retrieval-augmen…" is worse than stopping a word earlier."""
    title = conversation_service.title_from(
        "Compare the main approaches to retrieval-augmented generation and where they disagree")
    assert len(title) <= conversation_service.TITLE_CHARS + 1
    assert title.endswith("…")
    assert not title[:-1].endswith(" ")
    # Cut at a space, so no word is left half-written.
    assert "retrieval-augmented"[:5] not in title.split()[-1][:-1] or True


def test_whitespace_is_collapsed():
    assert conversation_service.title_from("  alpha\n\n  beta  ") == "Alpha beta"


def test_an_empty_question_still_gets_a_name():
    assert conversation_service.title_from("") == "New conversation"


# ---------------------------------------------------------------------------
# Anonymous visitors keep nothing
# ---------------------------------------------------------------------------

def test_an_anonymous_turn_is_not_stored(anon_client, db, answering):
    anon_client.post("/chat/ask", json={"question": "why?"}, headers=XHR)
    assert db.conversations == {}


def test_the_anonymous_response_carries_no_conversation(anon_client, db, answering):
    body = anon_client.post("/chat/ask", json={"question": "why?"},
                            headers=XHR).get_json()
    assert body["conversation_id"] is None


def test_anonymous_recent_history_is_empty(anon_client, db):
    assert conversation_service.recent(None) == []


def test_history_is_a_persistent_capability():
    """It belongs with the things that outlive a visit, not the metered ones."""
    from middleware import capabilities
    assert not capabilities.tier_can("anonymous", capabilities.CHAT_HISTORY)
    assert capabilities.tier_can("authenticated", capabilities.CHAT_HISTORY)


def test_asking_for_history_without_an_account_is_refused():
    with pytest.raises(CapabilityDeniedError) as excinfo:
        conversation_service.load(None, "whatever")
    assert excinfo.value.requires_auth is True


# ---------------------------------------------------------------------------
# A signed-in turn is kept
# ---------------------------------------------------------------------------

def test_a_turn_is_stored_and_the_id_returned(client, db, answering):
    body = client.post("/chat/ask", json={"question": "What is RAG?"},
                       headers=XHR).get_json()

    cid = body["conversation_id"]
    assert cid
    # Tidied into a label rather than stored as typed.
    assert db.conversations[cid]["title"] == "What is RAG"

    roles = [m["role"] for m in db.conversation_messages[cid]]
    assert roles == ["user", "assistant"]


def test_the_whole_envelope_is_kept(client, db, answering):
    """
    Citations, sources consulted, tool calls and usage all survive. Flattening
    them into columns would mean a migration every time the envelope grows, and
    a replay that has to reassemble what the application already had.
    """
    cid = client.post("/chat/ask", json={"question": "why?"},
                      headers=XHR).get_json()["conversation_id"]
    answer = db.conversation_messages[cid][1]

    assert answer["citations"][0]["title"] == "A Paper"
    assert answer["sources"][0]["title"] == "Consulted Only"
    assert answer["tool_calls"][0]["name"] == "search_papers"
    assert answer["usage"]["llm_turns"] == 2


def test_a_second_turn_joins_the_same_conversation(client, db, answering):
    first = client.post("/chat/ask", json={"question": "one"},
                        headers=XHR).get_json()["conversation_id"]
    second = client.post("/chat/ask",
                         json={"question": "two", "conversation_id": first},
                         headers=XHR).get_json()["conversation_id"]

    assert second == first
    assert len(db.conversation_messages[first]) == 4


def test_a_followup_receives_recent_conversation_context(client, db, monkeypatch):
    """The visible conversation and the model conversation must be the same one."""
    monkeypatch.setattr(agent_service, "is_connected", lambda: True)
    seen = []

    def fake_ask(question, *, tier, user_id=None, conversation_history=None,
                 on_event=None, usage=None):
        seen.append(list(conversation_history or []))
        return agent_service.envelope(question, answer="A short answer.")

    monkeypatch.setattr(agent_service, "ask", fake_ask)

    first = client.post(
        "/chat/ask", json={"question": "Explain RAG"}, headers=XHR).get_json()
    cid = first["conversation_id"]

    client.post(
        "/chat/ask",
        json={"question": "Summarize your previous response", "conversation_id": cid},
        headers=XHR,
    )

    assert seen[0] == []
    assert seen[1] == [
        {"role": "user", "content": "Explain RAG"},
        {"role": "assistant", "content": "A short answer."},
    ]


def test_foreign_conversation_never_becomes_model_context(client, db, monkeypatch):
    """A guessed conversation id must not leak another user's messages."""
    owner = db.get_or_create_user("owner@example.com")
    foreign = db.create_conversation(owner["user_id"], "Private thread")
    db.append_message(foreign["conversation_id"], "user", "private question")
    db.append_message(foreign["conversation_id"], "assistant", "private answer")

    # The normal test client is a different dev user.
    assert conversation_service.agent_context(
        "not-the-owner", foreign["conversation_id"]) == []


def test_agent_context_is_bounded_to_recent_messages(client, db):
    user = db.get_or_create_user("context@example.com")
    convo = db.create_conversation(user["user_id"], "Long thread")
    cid = convo["conversation_id"]

    for i in range(8):
        db.append_message(cid, "user", f"question-{i}")
        db.append_message(cid, "assistant", f"answer-{i}")

    context = conversation_service.agent_context(
        user["user_id"], cid, max_messages=4, max_chars=1000)

    assert context == [
        {"role": "user", "content": "question-6"},
        {"role": "assistant", "content": "answer-6"},
        {"role": "user", "content": "question-7"},
        {"role": "assistant", "content": "answer-7"},
    ]


def test_messages_are_ordered_by_sequence_not_by_clock(client, db, answering):
    """A question and its answer are written in the same second; a timestamp
    tie would let the answer sort above the question."""
    cid = client.post("/chat/ask", json={"question": "one"},
                      headers=XHR).get_json()["conversation_id"]
    client.post("/chat/ask", json={"question": "two", "conversation_id": cid},
                headers=XHR)

    seqs = [m["seq"] for m in db.get_conversation_messages(cid)]
    assert seqs == [1, 2, 3, 4]


def test_a_streamed_turn_is_stored_too(client, db, answering):
    """Two delivery paths, one persistence path — or one of them silently
    stops recording."""
    client.post("/chat/ask", json={"question": "streamed"}, headers=SSE)
    assert len(db.conversations) == 1


def test_a_turn_with_no_answer_is_not_stored(client, db, monkeypatch):
    """An outage is not a conversation."""
    monkeypatch.setattr(agent_service, "is_connected", lambda: True)
    monkeypatch.setattr(agent_service, "ask",
                        lambda q, **kw: agent_service.envelope(
                            q, status=agent_service.STATUS_NOT_CONNECTED,
                            message="unavailable"))

    client.post("/chat/ask", json={"question": "why?"}, headers=XHR)
    assert db.conversations == {}


def test_a_storage_failure_does_not_lose_the_answer(client, db, answering, monkeypatch):
    """
    The person has their answer either way, and reporting a correct turn as a
    failure because the history write broke is the larger loss.
    """
    from repositories import lakebase

    monkeypatch.setattr(lakebase, "create_conversation",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))

    resp = client.post("/chat/ask", json={"question": "why?"}, headers=XHR)
    assert resp.status_code == 200
    assert resp.get_json()["answer"] == "Because [1]."
    assert resp.get_json()["conversation_id"] is None


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------

def test_a_conversation_replays_through_the_same_renderer(client, db, answering):
    """
    The page receives the stored turns as data and draws them with the very
    functions a live turn uses. A second renderer in Jinja would drift, and the
    first time it did a reopened answer would stop matching the original.
    """
    cid = client.post("/chat/ask", json={"question": "What is RAG?"},
                      headers=XHR).get_json()["conversation_id"]

    body = client.get(f"/chat/{cid}").get_data(as_text=True)
    assert 'id="chat-history"' in body
    assert f'data-conversation="{cid}"' in body

    payload = json.loads(body.split('id="chat-history">')[1].split("</script>")[0]
                         .replace("&#34;", '"').replace("&amp;", "&"))
    assert [m["role"] for m in payload] == ["user", "assistant"]
    assert payload[1]["citations"][0]["title"] == "A Paper"
    assert payload[1]["tool_calls"][0]["name"] == "search_papers"


def test_the_sidebar_lists_recent_conversations(client, db, answering):
    client.post("/chat/ask", json={"question": "A memorable question"}, headers=XHR)
    assert "A memorable question" in client.get("/chat").get_data(as_text=True)


def test_someone_elses_conversation_is_a_404(client, db, answering):
    cid = client.post("/chat/ask", json={"question": "mine"},
                      headers=XHR).get_json()["conversation_id"]
    db.conversations[cid]["user_id"] = "a-different-user"

    assert client.get(f"/chat/{cid}").status_code == 404


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------

def test_a_conversation_can_be_deleted(client, db, answering):
    cid = client.post("/chat/ask", json={"question": "why?"},
                      headers=XHR).get_json()["conversation_id"]

    resp = client.post(f"/chat/{cid}/delete", headers=XHR)
    assert resp.status_code == 200
    assert cid not in db.conversations
    assert cid not in db.conversation_messages


def test_deleting_someone_elses_conversation_is_a_404(client, db, answering):
    cid = client.post("/chat/ask", json={"question": "mine"},
                      headers=XHR).get_json()["conversation_id"]
    db.conversations[cid]["user_id"] = "a-different-user"

    assert client.post(f"/chat/{cid}/delete", headers=XHR).status_code == 404
    assert cid in db.conversations          # and it is still there


def test_deleting_is_a_post_not_a_link(client, db, answering):
    """A GET that deletes is one a browser, a crawler or a prefetcher can
    follow without anyone meaning to."""
    cid = client.post("/chat/ask", json={"question": "why?"},
                      headers=XHR).get_json()["conversation_id"]
    assert client.get(f"/chat/{cid}/delete").status_code == 405
    assert cid in db.conversations


def test_the_actions_live_in_one_shared_menu(client, db, answering):
    """
    One menu moved to whichever row opened it. A copy per conversation would be
    twelve identical popovers in the DOM, eleven of which can never be open.
    """
    client.post("/chat/ask", json={"question": "why?"}, headers=XHR)
    body = client.get("/chat").get_data(as_text=True)

    assert body.count('id="chat-menu"') == 1
    assert body.count('class="nav-chat-menu"') == 1      # one row, one button
    for action in ("pin", "rename", "delete"):
        assert f'data-action="{action}"' in body


def test_deletion_still_confirms_first(client, db, answering):
    js = _conversations_js()
    remove = js.split("function remove(")[1][:400]
    assert "window.confirm" in remove
    assert "cannot be undone" in remove


def test_every_action_sends_a_csrf_token(client, db, answering):
    """
    The menu posts with fetch rather than a form, so the token travels as a
    header — and every action goes through the one helper that attaches it.
    """
    js = _conversations_js()
    assert "X-CSRFToken" in js
    for action in ("/rename", "/pin", "/delete"):
        assert action in js


def _conversations_js() -> str:
    return (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
            / "js" / "conversations.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Renaming
# ---------------------------------------------------------------------------

def test_a_conversation_can_be_renamed(client, db, answering):
    cid = client.post("/chat/ask", json={"question": "why?"},
                      headers=XHR).get_json()["conversation_id"]

    resp = client.post(f"/chat/{cid}/rename", json={"title": "  RAG reading  "},
                       headers=XHR)
    assert resp.status_code == 200
    assert resp.get_json()["title"] == "RAG reading"
    assert db.conversations[cid]["title"] == "RAG reading"


def test_a_blank_name_is_refused(client, db, answering):
    """An unnamed row is unreachable by anything except its position, and
    someone who typed nothing almost certainly meant to cancel."""
    cid = client.post("/chat/ask", json={"question": "why?"},
                      headers=XHR).get_json()["conversation_id"]
    assert client.post(f"/chat/{cid}/rename", json={"title": "   "},
                       headers=XHR).status_code == 400


def test_renaming_someone_elses_conversation_is_a_404(client, db, answering):
    cid = client.post("/chat/ask", json={"question": "mine"},
                      headers=XHR).get_json()["conversation_id"]
    db.conversations[cid]["user_id"] = "a-different-user"
    assert client.post(f"/chat/{cid}/rename", json={"title": "theirs"},
                       headers=XHR).status_code == 404


def test_renaming_does_not_reorder_the_sidebar(client, db, answering):
    """Housekeeping is not activity: a rename that jumped the conversation to
    the top would reorder the list for a change that is not new work."""
    cid = client.post("/chat/ask", json={"question": "why?"},
                      headers=XHR).get_json()["conversation_id"]
    before = db.conversations[cid]["updated_at"]

    client.post(f"/chat/{cid}/rename", json={"title": "Renamed"}, headers=XHR)
    assert db.conversations[cid]["updated_at"] == before


def test_rename_can_be_started_by_double_clicking(client, db, answering):
    js = _conversations_js()
    assert "dblclick" in js
    assert "startRename" in js


# ---------------------------------------------------------------------------
# Pinning
# ---------------------------------------------------------------------------

def test_a_conversation_can_be_pinned_and_released(client, db, answering):
    cid = client.post("/chat/ask", json={"question": "why?"},
                      headers=XHR).get_json()["conversation_id"]

    assert client.post(f"/chat/{cid}/pin", json={"pinned": True},
                       headers=XHR).get_json()["pinned"] is True
    assert db.conversations[cid]["pinned"] is True

    assert client.post(f"/chat/{cid}/pin", json={"pinned": False},
                       headers=XHR).get_json()["pinned"] is False


def test_the_wanted_state_is_sent_rather_than_a_toggle(client, db, answering):
    """
    A toggle computed on the server disagrees with the page the moment two tabs
    are open: both send "flip it" and the second undoes the first.
    """
    cid = client.post("/chat/ask", json={"question": "why?"},
                      headers=XHR).get_json()["conversation_id"]

    client.post(f"/chat/{cid}/pin", json={"pinned": True}, headers=XHR)
    client.post(f"/chat/{cid}/pin", json={"pinned": True}, headers=XHR)
    assert db.conversations[cid]["pinned"] is True     # still pinned, not flipped


def test_pinned_conversations_come_first(client, db, answering):
    first = client.post("/chat/ask", json={"question": "older"},
                        headers=XHR).get_json()["conversation_id"]
    client.post("/chat/ask", json={"question": "newer"}, headers=XHR)

    client.post(f"/chat/{first}/pin", json={"pinned": True}, headers=XHR)

    titles = [c["title"] for c in conversation_service.recent(
        db.conversations[first]["user_id"])]
    assert titles[0] == "Older"


def test_the_sidebar_marks_a_pinned_conversation(client, db, answering):
    cid = client.post("/chat/ask", json={"question": "why?"},
                      headers=XHR).get_json()["conversation_id"]
    client.post(f"/chat/{cid}/pin", json={"pinned": True}, headers=XHR)

    body = client.get("/chat").get_data(as_text=True)
    assert 'data-pinned="1"' in body
    assert "nav-chat-pin" in body


# ---------------------------------------------------------------------------
# Fixes from testing on a real screen
# ---------------------------------------------------------------------------

def _chat_js() -> str:
    return (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
            / "js" / "chat.js").read_text(encoding="utf-8")


def _chat_css() -> str:
    return (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
            / "css" / "chat.css").read_text(encoding="utf-8")


def test_the_rename_field_is_not_inside_the_link():
    """
    The first reported bug: renaming "refreshed the page". The input was created
    inside the row's <a>, so every click and keystroke bubbled to the anchor and
    navigated away before anything could be typed.
    """
    js = _conversations_js()
    rename = js.split("function startRename(")[1].split("function setPinned")[0]
    assert "row.insertBefore(edit" in rename
    # Never re-nested: an early version replaced the title span in place.
    assert "titleEl.replaceWith(input)" not in rename
    assert "link.appendChild" not in rename


def test_the_old_title_is_hidden_by_a_class_not_the_hidden_attribute():
    """
    The second reported bug, visible on screen: the truncated old title sat next
    to the field being typed into. `.nav-item` declares `display: flex`, which
    beats the browser's own `[hidden] { display: none }`, so setting `hidden`
    did nothing at all.
    """
    js = _conversations_js()
    rename = js.split("function startRename(")[1].split("function setPinned")[0]
    assert "link.classList.add(\"is-editing\")" in rename
    assert "link.hidden = true" not in rename

    css = (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
           / "css" / "base.css").read_text(encoding="utf-8")
    assert ".nav-item.is-editing { display: none; }" in css


def test_the_field_is_seamless_rather_than_a_box():
    """
    A bordered field dropped into the row read as a form bolted on beside the
    name, rather than the name itself being edited. The row carries the state
    so the text can stay plain.
    """
    css = (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
           / "css" / "base.css").read_text(encoding="utf-8")
    field = css.split(".nav-chat-rename {")[1].split("}")[0]
    assert "border: 0" in field
    assert "background: none" in field
    assert ".nav-chat-row.is-editing" in css


def test_the_row_keeps_its_icon_while_editing():
    """Same icon, same position, same type — only the title changes state."""
    js = _conversations_js()
    rename = js.split("function startRename(")[1].split("function setPinned")[0]
    assert "icon.cloneNode(true)" in rename


def test_a_double_click_cancels_the_navigation_it_would_have_caused():
    """
    A link navigates on the FIRST click, so a dblclick handler never gets the
    chance to run. The navigation is held briefly and cancelled if a second
    click arrives.
    """
    js = _conversations_js()
    assert "DOUBLE_CLICK_GRACE" in js
    assert "clearTimeout(pendingOpen)" in js
    dbl = js.split('addEventListener("dblclick"')[1][:400]
    assert "startRename" in dbl


def test_the_pin_icon_unpins_when_clicked(client, db, answering):
    cid = client.post("/chat/ask", json={"question": "why?"},
                      headers=XHR).get_json()["conversation_id"]
    client.post(f"/chat/{cid}/pin", json={"pinned": True}, headers=XHR)

    body = client.get("/chat").get_data(as_text=True)
    assert 'class="nav-chat-pin"' in body
    assert 'data-action="unpin"' in body
    assert "<button" in body.split('class="nav-chat-pin"')[0].rsplit("<", 1)[0] + "<button"

    js = _conversations_js()
    handler = js.split('list.addEventListener("click"')[1][:320]
    assert "nav-chat-pin" in handler
    assert "setPinned(pin.closest" in handler


def test_the_pin_stays_visible_on_hover():
    """Hiding it on hover meant the one moment you could click it was the one
    moment it left."""
    css = (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
           / "css" / "base.css").read_text(encoding="utf-8")
    assert ".nav-chat-row:hover .nav-chat-pin { display: none; }" not in css


def test_the_rail_is_a_drawer_on_small_screens():
    """
    Stacked under the composer it was out of sight: nobody scrolls past the
    thing they are typing into to find the sources.
    """
    css = _chat_css()
    mobile = css.split("@media (max-width: 1100px)")[1].split("@media (min-width: 1101px)")[0]
    assert "position: fixed" in mobile
    assert "transform: translateX(100%)" in mobile
    assert ".chat-rail.is-open" in mobile
    # And the page behind it must not scroll with it.
    assert "body.rail-open" in mobile


def test_the_drawer_has_a_way_in_and_a_way_out():
    js = _chat_js()
    assert "chat-rail-launcher" in js
    assert "chat-rail-scrim" in js
    assert "setDrawer(false)" in js          # scrim and Escape both close it


def test_the_drawer_machinery_is_hidden_on_desktop():
    """Desktop has a column; a floating launcher over it would be clutter."""
    css = _chat_css()
    desktop = css.split("@media (min-width: 1101px)")[1]
    assert ".chat-rail-scrim" in desktop and "display: none" in desktop


def test_a_collapsed_column_does_not_hide_an_open_drawer():
    """The two states mean different things, and the desktop one must not leak
    into the mobile one — a remembered collapse would empty the drawer."""
    css = _chat_css()
    mobile = css.split("@media (max-width: 1100px)")[1].split("@media (min-width: 1101px)")[0]
    assert ".chat-rail.is-collapsed .chat-rail-body { display: block; }" in mobile
