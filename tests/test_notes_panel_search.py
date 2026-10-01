"""
tests/test_notes_panel_search.py — searching from the panel, and three bugs
that shipped with the last round.

The search built in the previous step was invisible in practice:

  * on the page it was gated behind "more than four notes", which hid it on
    exactly the pages where somebody would look to find out whether it existed;
  * in the panel it did not exist at all;
  * and the panel's Back button showed in the list view, where there is nothing
    to go back to, because `.icon-btn { display: grid }` outranks the browser's
    own `[hidden] { display: none }`.

That last one is the third component to hit the same footgun — after
`.nav-item` and `.notes-panel` — so it is fixed once for the whole stylesheet
rather than a third time in one component.

The Notes page's notepad also stayed short: `flex: 1` distributes free space
and cannot stretch inside a box that grows to fit its contents, so without a
definite height above it every stretch rule was inert. The same trap the chat
composer fell into.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _css() -> str:
    return (ROOT / "dashboard" / "static" / "css" / "base.css").read_text(encoding="utf-8")


def _panel_js() -> str:
    return (ROOT / "dashboard" / "static" / "js"
            / "notes-panel.js").read_text(encoding="utf-8")


def _me(client, db) -> str:
    client.get("/dashboard")
    return next(iter(db.users_by_id))


# ---------------------------------------------------------------------------
# [hidden] actually hides
# ---------------------------------------------------------------------------

def test_hidden_is_enforced_once_for_the_whole_stylesheet():
    """
    Three components have now set `display` and silently defeated `[hidden]`.
    One rule that outranks all of them ends the class of bug, rather than a
    fourth per-component override.
    """
    assert "[hidden] { display: none !important; }" in _css()


def test_the_component_overrides_it_replaces_are_gone():
    css = _css()
    assert ".notes-panel[hidden]" not in css
    assert ".notes-view[hidden]" not in css


def test_the_back_button_is_absent_from_the_list(client, db):
    """
    It shipped visible beside the + in a view with nothing to go back to. The
    markup always said `hidden`; the stylesheet was overruling it.
    """
    body = client.get("/dashboard").get_data(as_text=True)
    back = re.search(r'<button[^>]*id="notes-back"[^>]*>', body)
    assert back and "hidden" in back.group(0)


# ---------------------------------------------------------------------------
# Search in the panel
# ---------------------------------------------------------------------------

def test_the_panel_has_a_search_control(client, db):
    body = client.get("/dashboard").get_data(as_text=True)
    assert 'id="notes-search-toggle"' in body
    assert 'id="notes-search"' in body


def test_search_and_back_share_one_slot():
    """
    Neither is useful in the other's view: a list has nothing to go back to,
    and a half-written note is no place to start searching.
    """
    js = _panel_js()
    assert "searchBtn.hidden = false" in js      # in the list
    assert "searchBtn.hidden = true" in js       # in the notepad


def test_the_search_box_starts_collapsed(client, db):
    """The panel is 392px wide and the list is what people open it to see."""
    body = client.get("/dashboard").get_data(as_text=True)
    row = re.search(r'<div class="notes-search-row"[^>]*>', body)
    assert row and "hidden" in row.group(0)


def test_typing_is_debounced():
    """
    The query goes to the database. A round trip per keystroke would also let
    answers arrive out of order and overwrite a newer result with an older one.
    """
    js = _panel_js()
    assert "setTimeout" in js.split('searchBox.addEventListener("input"')[1][:400]


def test_the_query_reaches_the_server(client, db):
    """
    The panel reads the same route the page does, so a search in one is the
    same search as in the other.
    """
    assert 'encodeURIComponent(query)' in _panel_js()

    user = _me(client, db)
    from services import progress_service
    progress_service.save_note(user, None, "the retriever is the weak point")
    progress_service.save_note(user, None, "something else")

    data = client.get("/notes?q=retriever",
                      headers={"X-Requested-With": "XMLHttpRequest"}).get_json()
    assert sum(len(g["notes"]) for g in data["papers"]) == 1


def test_closing_the_box_clears_the_filter():
    """
    A hidden query would leave a filtered list with nothing on screen to
    explain why most of the notes are missing.
    """
    js = _panel_js()
    closing = js.split("searchBtn.addEventListener")[1][:600]
    assert 'searchBox.value = ""' in closing


def test_escape_in_the_search_box_does_not_close_the_panel():
    """
    Without stopping it, Escape bubbles to the document handler that closes the
    panel — so clearing a search would shut the thing being searched.
    """
    js = _panel_js()
    block = js.split('searchBox.addEventListener("keydown"')[1][:400]
    assert "stopPropagation" in block


def test_an_empty_search_says_so_rather_than_claiming_you_have_nothing():
    """
    "Nothing written yet" is false when a search simply matched nothing, and it
    is the sentence most likely to make somebody think their notes are gone.
    """
    assert "No notes match that." in _panel_js()


# ---------------------------------------------------------------------------
# Search on the page
# ---------------------------------------------------------------------------

def test_the_page_search_is_always_offered(client, db):
    """Gated on four notes, it was hidden from anyone with three."""
    _me(client, db)
    assert "notes-filter" in client.get("/notes").get_data(as_text=True)


def test_an_empty_result_is_not_reported_as_an_empty_library(client, db):
    from services import progress_service
    progress_service.save_note(_me(client, db), None, "a note about retrieval")

    body = client.get("/notes?q=nothingmatchesthis").get_data(as_text=True)
    assert "Nothing matches that" in body
    assert "No notes yet" not in body


# ---------------------------------------------------------------------------
# The notepad reaches the footer
# ---------------------------------------------------------------------------

def test_the_page_asks_for_a_definite_height_only_while_writing(client, db):
    """
    `flex: 1` distributes FREE space and cannot stretch inside a box that grows
    to fit its contents, so the notepad needs the shell capped at the viewport.

    But capping it also forces the LIST to scroll inside its own box, which put
    a second scrollbar down the middle of the page beside the notes. So the
    markup carries `content-fill` and the cap arrives from JavaScript only when
    the notepad opens — the list scrolls with the window and has no scrollbar
    of its own.
    """
    body = client.get("/notes").get_data(as_text=True)
    assert "content-fill" in body
    assert "app-fixed" not in body

    js = (ROOT / "dashboard" / "static" / "js"
          / "notes-page.js").read_text(encoding="utf-8")
    assert '"notes-writing", "app-fixed"' in js


def test_the_page_itself_is_a_flex_column():
    # Every .notes-page rule, not the first: the measure is set in one and the
    # stretch in another, and a helper that stops at the first match tests
    # whichever happened to be written earlier.
    rules = "\n".join(re.findall(r"\.notes-page\s*\{([^}]*)\}", _css()))
    assert "flex: 1" in rules
    assert "min-height: 0" in rules


def test_the_list_scrolls_rather_than_stretching():
    """Only the notepad claims the height; the list is a list."""
    assert "#page-view-list { overflow-y: auto; }" in _css()


# ---------------------------------------------------------------------------
# Filtering by what a note is about
# ---------------------------------------------------------------------------

def test_the_filter_offers_the_division_the_page_already_shows(client, db):
    """
    Whether a note is about a paper is the one split the page displays and
    could not act on: grouping put the two in separate sections and still
    rendered both.
    """
    _me(client, db)
    body = client.get("/notes").get_data(as_text=True)

    assert 'class="filter-menu"' in body
    for label in ("All notes", "About a paper", "Not about a paper"):
        assert label in body


def test_filtering_to_papers_drops_the_loose_notes(client, db):
    from services import progress_service

    paper = db.seed_paper(title="A Paper")
    user = _me(client, db)
    db.save_note(user, str(paper["paper_id"]), "onpaperonly", None, [])
    progress_service.save_note(user, None, "looseonly")

    body = client.get("/notes?scope=paper").get_data(as_text=True)
    assert "onpaperonly" in body
    assert "looseonly" not in body


def test_filtering_to_loose_notes_drops_the_attached_ones(client, db):
    from services import progress_service

    paper = db.seed_paper(title="A Paper")
    user = _me(client, db)
    db.save_note(user, str(paper["paper_id"]), "onpaperonly", None, [])
    progress_service.save_note(user, None, "looseonly")

    body = client.get("/notes?scope=standalone").get_data(as_text=True)
    assert "looseonly" in body
    assert "onpaperonly" not in body


def test_a_nonsense_scope_shows_everything(client, db):
    """
    It arrives from a URL anybody can edit. A mistyped filter should show all
    the notes rather than an error page.
    """
    from services import progress_service
    progress_service.save_note(_me(client, db), None, "still here")

    resp = client.get("/notes?scope=banana")
    assert resp.status_code == 200
    assert "still here" in resp.get_data(as_text=True)


def test_the_filter_composes_with_the_search(client, db):
    from services import progress_service

    paper = db.seed_paper(title="A Paper")
    user = _me(client, db)
    db.save_note(user, str(paper["paper_id"]), "retriever attached", None, [])
    progress_service.save_note(user, None, "retriever loose")

    body = client.get("/notes?q=retriever&scope=standalone").get_data(as_text=True)
    assert "retriever loose" in body
    assert "retriever attached" not in body


def test_the_filter_needs_no_javascript(client, db):
    """
    A <details> opens, closes, dismisses on Escape and takes keyboard focus on
    its own. A filter that still works with scripts off is worth more than one
    that animates.
    """
    body = client.get("/notes").get_data(as_text=True)
    assert "<details class=\"filter-menu\">" in body
    assert "<summary" in body


def test_the_filter_buttons_are_icons_with_names(client, db):
    body = client.get("/notes").get_data(as_text=True)
    filters = body.split('class="notes-filter"')[1].split("</form>")[0]

    assert ">Search</button>" not in filters
    assert 'aria-label="Search"' in filters
    assert 'aria-label="Filter notes"' in filters


# ---------------------------------------------------------------------------
# The scrollbar
# ---------------------------------------------------------------------------

def test_the_list_has_no_scrollbar_of_its_own():
    """
    It scrolls with the window. An inner scrollbar beside the notes was thicker
    than their own left rule and sat right against the text.
    """
    css = _css()
    # The qualified rule CONTAINS the unqualified one as a substring, so the
    # absence has to be checked against the start of the line.
    assert "\n#page-view-list {" not in css
    assert "body.notes-writing #page-view-list { overflow-y: auto; }" in css


def test_the_scrollbars_that_remain_are_thin():
    """
    The rule became global and gained a `height`, because the settings nav
    scrolls sideways on a phone and a horizontal scrollbar takes its size from
    height rather than width. Asserted as properties rather than as one exact
    declaration, which is what pinned this to a shape it has outgrown.
    """
    css = _css()
    assert "scrollbar-width: thin" in css

    rule = css[css.index("::-webkit-scrollbar {"):]
    rule = rule[:rule.index("}")]
    assert "width: 8px" in rule
    assert "height: 8px" in rule


def test_the_thumb_is_held_off_the_text():
    """
    A transparent border under background-clip leaves the thumb floating in the
    gutter rather than pressed against the words beside it.
    """
    css = _css()
    assert "background-clip: content-box" in css
