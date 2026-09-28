"""
tests/test_rails_and_notepad.py — the panel's geometry, resizable rails, and
not losing what somebody typed.

Six adjustments after seeing the docked panel in the real layout:

  * the panel hangs BELOW the header rather than covering it, so the topbar
    stays whole and its controls — including the pen that closes the panel —
    stay reachable;
  * both rails can be dragged to whatever width suits the work;
  * on the Notes page the notepad fills the page instead of sitting in a short
    box, with the title as its own field rather than sharing an edge with the
    body;
  * actions are icons, because two text buttons per note in a 392px column
    compete with the note itself;
  * the list is named, in the display face;
  * and nothing typed is dropped without being asked about.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _css() -> str:
    return (ROOT / "dashboard" / "static" / "css" / "base.css").read_text(encoding="utf-8")


def _js(name: str) -> str:
    return (ROOT / "dashboard" / "static" / "js" / name).read_text(encoding="utf-8")


def _code(name: str) -> str:
    """
    The file with its comments removed.

    These files explain at length what they no longer do, so a test asserting
    that some old string is gone keeps matching the sentence about why it went.
    Anything checking for ABSENCE has to read code, not prose.
    """
    text = re.sub(r"/\*.*?\*/", "", _js(name), flags=re.S)
    return re.sub(r"//.*", "", text)


def _rule(selector: str) -> str:
    """
    Every declaration for this selector, not just the first.

    A property can be introduced in one rule and refined in a later one, and a
    helper that stops at the first match tests whichever happened to be written
    first rather than what the browser ends up applying.
    """
    found = re.findall(re.escape(selector) + r"\s*\{([^}]*)\}", _css())
    assert found, f"no rule for {selector}"
    return "\n".join(found)


# ---------------------------------------------------------------------------
# 1. Below the header, not over it
# ---------------------------------------------------------------------------

def test_the_panel_takes_the_whole_column():
    """
    Hanging the panel below the header was built, looked at, and withdrawn.
    The topbar is sticky, so on any page where it scrolled the panel appeared
    to come adrift from it — floating over the content with a gap above where
    the header used to be. A full-height column has no seam to come apart.
    """
    rule = _rule(".notes-panel")
    assert "inset: 0 0 0 auto" in rule
    assert "top: var(--topbar-h)" not in rule


def test_the_whole_column_narrows_including_the_header():
    """
    The margin goes back on `.main-col`, so the topbar narrows with everything
    else and its controls sit BESIDE the panel rather than underneath it. The
    pen that closes the panel has to stay reachable, and a full-height panel
    over an unnarrowed header would have covered it.
    """
    css = _css()
    assert "body.notes-docked .main-col" in css
    assert "body.notes-docked .app-footer" not in css


# ---------------------------------------------------------------------------
# 2. Both rails resize
# ---------------------------------------------------------------------------

def test_both_rails_have_a_handle(client, db):
    body = client.get("/dashboard").get_data(as_text=True)
    assert 'id="sidebar-resize"' in body
    assert 'id="notes-resize"' in body


def test_a_handle_is_a_separator_a_keyboard_can_move(client, db):
    """
    A rail only a mouse can resize is one half the people using it cannot.
    Arrow keys move it, Home and End snap to the limits.
    """
    body = client.get("/dashboard").get_data(as_text=True)
    handle = re.search(r'<div class="rail-handle[^>]*id="sidebar-resize"[^>]*>', body)
    assert handle, "sidebar handle not found"
    assert 'role="separator"' in handle.group(0)
    assert 'tabindex="0"' in handle.group(0)

    js = _js("rails.js")
    assert '"ArrowLeft"' in js and '"Home"' in js


def test_the_width_is_one_number_the_layout_already_follows():
    """
    Both rails are driven by the custom property the stylesheet already uses,
    so moving it moves the rail, the margin the page carries and everything
    positioned against it — with no layout code.
    """
    js = _js("rails.js")
    assert '"--sidebar-w"' in js and '"--notes-w"' in js
    assert "setProperty(rail.prop" in js


def test_no_rail_can_swallow_the_page():
    """Two rails that between them leave no page are two rails nobody can use."""
    js = _js("rails.js")
    clamp = js.split("function clamp(")[1][:400]
    assert "innerWidth * 0.5" in clamp
    assert "Math.max(rail.min" in clamp


def test_a_dropped_gesture_does_not_leave_the_page_stuck():
    """
    pointercancel fires when the browser takes the gesture over. Without it the
    document keeps `rail-resizing` — selection disabled, cursor wrong — with no
    way back.
    """
    assert "pointercancel" in _js("rails.js")


def test_the_chosen_width_is_remembered():
    js = _js("rails.js")
    assert "localStorage" in js
    assert "catch" in js.split("function remember(")[1][:300]


def test_a_narrower_window_reclaims_the_width():
    """A saved width from a wide screen would otherwise take most of a small one."""
    assert 'window.addEventListener("resize"' in _js("rails.js")


# ---------------------------------------------------------------------------
# 3. The notepad fills the page
# ---------------------------------------------------------------------------

def test_the_page_notepad_claims_the_height_while_writing():
    css = _css()
    assert "body.notes-writing .notes-page-pad" in css
    assert "flex: 1" in _rule("body.notes-writing .notes-page-pad")


def test_only_while_writing():
    """The list does not want the class; it scrolls normally."""
    js = _js("notes-page.js")
    assert 'classList.add("notes-writing"' in js
    assert 'classList.remove("notes-writing"' in js


def test_the_title_is_its_own_field():
    """
    Separated by a rule rather than sharing an edge with the body — they are
    different fields, and a long title running into the first line reads as one
    paragraph.
    """
    assert "border-bottom" in _rule(".notes-pad-title")


# ---------------------------------------------------------------------------
# 4. Icons, not words
# ---------------------------------------------------------------------------

def test_the_note_actions_are_icons(client, db):
    paper = db.seed_paper(title="A Paper")
    client.get("/dashboard")
    db.save_note(next(iter(db.users_by_id)), str(paper["paper_id"]), "a note", None)

    body = client.get("/notes").get_data(as_text=True)
    actions = body.split('class="note-actions"')[1][:1200]

    assert ">Edit</button>" not in actions
    assert ">Delete</button>" not in actions
    assert 'aria-label="Edit note"' in actions
    assert 'aria-label="Delete note"' in actions


def test_an_icon_action_still_has_a_name(client, db):
    """
    An icon-only button with no accessible name is a button a screen reader
    announces as "button". The word survives as the label and the tooltip.
    """
    body = client.get("/dashboard").get_data(as_text=True)
    open_all = re.search(r'<a class="icon-btn" id="notes-open-all"[^>]*>', body)
    assert open_all, "open-all control not found"
    assert 'aria-label="Open all notes"' in open_all.group(0)
    assert 'title="Open all notes"' in open_all.group(0)


def test_the_panel_list_renders_icons_too():
    js = _js("notes-panel.js")
    assert "ICON.pencil" in js and "ICON.trash" in js
    assert '>Edit</button>' not in js


# ---------------------------------------------------------------------------
# 5. The list is named
# ---------------------------------------------------------------------------

def test_the_panel_opens_on_a_named_list(client, db):
    body = client.get("/dashboard").get_data(as_text=True)
    assert ">Recent notes</h2>" in body


def test_the_heading_is_set_in_the_display_face():
    assert "Poppins" in _rule("#notes-panel-title")


def test_the_display_face_is_actually_loaded(client, db):
    body = client.get("/dashboard").get_data(as_text=True)
    assert "family=Poppins" in body


# ---------------------------------------------------------------------------
# 6. Nothing typed is dropped in silence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["notes-panel.js", "notes-page.js"])
def test_leaving_the_notepad_asks_first(name):
    js = _js(name)
    assert "function mayLeave()" in js
    assert "has not been saved" in js


@pytest.mark.parametrize("name", ["notes-panel.js", "notes-page.js"])
def test_every_way_out_asks_the_same_question(name):
    """
    One answer to "may this writing be dropped?" rather than one per route out,
    or the back arrow and the close button end up disagreeing.
    """
    js = _js(name)
    assert js.count("mayLeave()") >= 2


@pytest.mark.parametrize("name", ["notes-panel.js", "notes-page.js"])
def test_closing_the_tab_asks_too(name):
    assert 'addEventListener("beforeunload"' in _js(name)


@pytest.mark.parametrize("name", ["notes-panel.js", "notes-page.js"])
def test_an_untouched_notepad_asks_nothing(name):
    """
    Dirty is measured against what the notepad held when it opened, so opening
    a note to read it and closing it again is silent — and so is typing a word
    and deleting it.
    """
    js = _js(name)
    body = js.split("function dirty()")[1][:300]
    assert "opened.title" in body and "opened.body" in body


def test_saving_is_not_leaving():
    """
    The page navigates on save. Without clearing the baseline first the browser
    would ask them to confirm discarding what they had just saved.
    """
    js = _js("notes-page.js")
    submit = js.split('padView.addEventListener("submit"')[1][:400]
    assert "opened = {" in submit


# ---------------------------------------------------------------------------
# The heading is the note's title
# ---------------------------------------------------------------------------

def test_the_panel_list_is_actually_named(client, db):
    """
    The markup said "Recent notes" and `showList()` wrote "Notes" over it the
    moment the panel opened, so the name shipped but was never seen.
    """
    assert ">Recent notes</h2>" in client.get("/dashboard").get_data(as_text=True)
    assert '"Recent notes"' in _js("notes-panel.js")
    assert '"Notes";' not in _code("notes-panel.js")


@pytest.mark.parametrize("name", ["notes-panel.js", "notes-page.js"])
def test_the_heading_becomes_the_title_while_writing(name):
    """
    "Edit note" spent the largest type on the page on a label, and then asked
    for the title again in a field below it.
    """
    js = _js(name)
    assert "titleField(" in js
    assert "asTitle(" in js and "asLabel(" in js

    # The heading is no longer written to directly — asserting that "Edit note"
    # is absent entirely would be wrong, because it is still the Edit button's
    # accessible name, which is exactly where that phrase belongs.
    assert "heading.textContent" not in _code(name)


def test_a_double_click_renames_it():
    js = _js("notes.js")
    assert 'addEventListener("dblclick"' in js


def test_renaming_is_reachable_without_a_double_click():
    """
    A double-click is undiscoverable on its own and impossible on a touch
    screen. Enter or Space on the focused heading opens the same editor.
    """
    js = _js("notes.js")
    keys = js.split("heading.addEventListener(\"keydown\"")[1][:200]
    assert '"Enter"' in keys and '" "' in keys


def test_escape_abandons_a_rename_without_touching_the_note():
    js = _js("notes.js")
    assert '"Escape"' in js.split('input.addEventListener("keydown"')[1][:220]


def test_an_unnamed_note_says_so_rather_than_showing_nothing():
    js = _js("notes.js")
    assert "Untitled note" in js
    assert "is-untitled" in js and "is-untitled" in _css()


def test_the_title_still_posts_from_outside_the_form(client, db):
    """
    The input moved into the header, which is not inside the form element. The
    `form=` attribute is what keeps it a field of that form.
    """
    body = client.get("/notes").get_data(as_text=True)
    field = re.search(r'<input[^>]*id="page-pad-title"[^>]*>', body)
    assert field, "page title field not found"
    assert 'form="page-view-pad"' in field.group(0)
    assert 'name="title"' in field.group(0)


def test_the_shared_helper_loads_before_the_files_that_call_it(client, db):
    """
    notes.js defines window.RCNotes; the panel calls it as it initialises.
    Loaded after, it would be defined a moment too late and the panel would
    throw on every page.
    """
    body = client.get("/dashboard").get_data(as_text=True)
    assert body.index("js/notes.js") < body.index("js/notes-panel.js")
