"""
tests/test_shortcuts.py — one place that knows what every key does.

This file exists because of a specific bug. Ctrl+K was claimed twice: `main.js`
bound it for chat-history search and won by registering first, so the command
palette was unreachable by the shortcut people arrive already expecting. Nothing
was wrong with either file on its own — the bug only existed between them, and
neither could see the other.

The collision check below is the part that could not have been written before,
because there was nothing to check. A duplicate chord is now a failing test
rather than a race between script tags.

The rest asserts the registry's two other jobs: it dispatches the global chords,
and the reference sheet is generated from it rather than written alongside it.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
JS = ROOT / "dashboard" / "static" / "js"

# entry("id", "Mod+K", "scope", "Group", "Description"[, assignable])
_ENTRY = re.compile(
    r'entry\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,'
    r'\s*"([^"]+)"\s*(?:,\s*(true|false)\s*)?\)')


def registry() -> list[dict]:
    source = (JS / "shortcuts.js").read_text(encoding="utf-8")
    return [{"id": i, "keys": k, "scope": sc, "group": g, "description": d,
             "assignable": a == "true"}
            for i, k, sc, g, d, a in _ENTRY.findall(source)]


def _chord(keys: str) -> frozenset:
    """Order-independent: "Mod+Shift+F" and "Shift+Mod+F" are one chord."""
    return frozenset(part.lower() for part in keys.split("+"))


# ---------------------------------------------------------------------------
# The registry is real
# ---------------------------------------------------------------------------

def test_the_registry_parses():
    entries = registry()
    assert len(entries) >= 12
    assert {"palette.open", "chat.search", "help.shortcuts"} <= {e["id"] for e in entries}


def test_every_entry_is_uniquely_named():
    ids = [e["id"] for e in registry()]
    assert len(ids) == len(set(ids))


def test_every_entry_says_what_it_does():
    """The sheet is generated from these, so a blank one is a blank row."""
    for item in registry():
        assert item["description"].strip()
        assert item["group"].strip()


# ---------------------------------------------------------------------------
# The check that could not exist before
# ---------------------------------------------------------------------------

def test_no_two_shortcuts_claim_the_same_chord():
    """
    Within a scope, two bindings on one chord means the keystroke has no defined
    answer — whichever registered first wins, which is not a decision anybody
    made.
    """
    seen: dict[tuple, str] = {}
    for item in registry():
        key = (item["scope"], _chord(item["keys"]))
        assert key not in seen, (
            f'{item["id"]} and {seen[key]} both claim {item["keys"]} '
            f'in scope "{item["scope"]}"')
        seen[key] = item["id"]


def test_no_local_shortcut_is_shadowed_by_a_global_one():
    """
    The subtler half, and the shape of the original bug. A global chord fires
    everywhere, including inside the editor or the palette — so a local binding
    sharing one is unreachable wherever the global listener runs first.
    """
    globals_ = {_chord(e["keys"]): e["id"] for e in registry() if e["scope"] == "global"}

    for item in registry():
        if item["scope"] == "global":
            continue
        chord = _chord(item["keys"])
        assert chord not in globals_, (
            f'{item["id"]} ({item["keys"]}) is shadowed by the global '
            f'{globals_[chord]}')


def test_the_two_shortcuts_that_collided_are_still_distinct():
    """
    The original bug, named. Ctrl+K belongs to the palette, which is the broader
    of the two and what people expect; chat search is a filter over one list and
    takes the narrower chord.
    """
    by_id = {e["id"]: e for e in registry()}
    assert by_id["palette.open"]["keys"] == "Mod+K"
    assert by_id["chat.search"]["keys"] != "Mod+K"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def test_the_colliding_files_no_longer_bind_their_own_chords():
    """
    Both used to add a document keydown listener for their own combination.
    Leaving either in place would restore the race the registry exists to end.
    """
    palette = (JS / "palette.js").read_text(encoding="utf-8")
    main = (JS / "main.js").read_text(encoding="utf-8")

    assert 'register("palette.open"' in palette
    assert 'register("chat.search"' in main

    # No hand-rolled modifier test for a letter key left in either file. Escape
    # and Tab handling stay, which is why this looks for the modifier form.
    for source in (palette, main):
        assert not re.search(r'(metaKey|ctrlKey)[^\n]*key(\.toLowerCase\(\))?\s*===?\s*"[a-z]"',
                             source, re.I)


@pytest.mark.parametrize("registered", ["palette.open", "chat.search", "help.shortcuts"])
def test_every_global_chord_has_something_registered_against_it(registered):
    """
    A global entry with no handler is a documented shortcut that does nothing —
    worse than an undocumented one, because the sheet promises it.
    """
    sources = "".join((JS / name).read_text(encoding="utf-8")
                      for name in ("main.js", "palette.js", "shortcuts-sheet.js"))
    assert f'register("{registered}"' in sources


def test_a_chord_checks_the_modifiers_it_does_not_want():
    """
    Without that, Mod+Shift+F would also fire Mod+F — one keystroke answered
    twice, which is the bug again in miniature.
    """
    source = (JS / "shortcuts.js").read_text(encoding="utf-8")
    assert "event.shiftKey === want.shift" in source
    assert "event.altKey === want.alt" in source


# ---------------------------------------------------------------------------
# The sheet
# ---------------------------------------------------------------------------

def test_the_sheet_is_generated_rather_than_written():
    """
    A hand-maintained list of shortcuts is a list that is wrong. Nothing in the
    sheet knows what any key does; it reads the registry, so adding a shortcut
    documents it.
    """
    sheet = (JS / "shortcuts-sheet.js").read_text(encoding="utf-8")
    assert "RCShortcuts" in sheet and "REGISTRY" in sheet

    # No literal chord text in the renderer — that would be a second source.
    assert not re.search(r'"(Mod|Ctrl|Cmd)\+', sheet)


def test_the_registry_loads_before_anything_that_registers_against_it():
    """
    Every other script calls RCShortcuts.register. One loaded first would find it
    undefined and silently bind nothing — a shortcut that fails by doing exactly
    as much as a working one until you press it.
    """
    base = (ROOT / "dashboard" / "templates"
            / "base.html").read_text(encoding="utf-8")

    order = [name for name in re.findall(r"js/([\w-]+\.js)", base)]
    assert order.index("shortcuts.js") < order.index("main.js")
    assert order.index("shortcuts.js") < order.index("palette.js")


def test_the_sheet_is_reachable_by_key_and_by_hand(client, db):
    """
    Mod+/ for people who know it, and Settings for people who do not — which is
    most people, and the reason that section exists at all.
    """
    by_id = {e["id"]: e for e in registry()}
    assert by_id["help.shortcuts"]["keys"] == "Mod+/"

    client.get("/dashboard")
    assert "data-shortcuts-list" in client.get("/settings").get_data(as_text=True)


def test_settings_shows_the_list_rather_than_a_button_that_reveals_it(client, db):
    """
    Pressing a button to reveal a reference list, on a page whose entire subject
    is that list, is a click that buys nothing. The button was right while this
    lived under Appearance and wrong the moment it got a section of its own.
    """
    client.get("/dashboard")
    body = client.get("/settings").get_data(as_text=True)

    keyboard = body.split('data-settings-panel="keyboard"')[1]
    assert "data-shortcuts-list" in keyboard
    assert "data-shortcuts-open" not in keyboard


def test_the_page_and_the_dialog_are_the_same_list(client, db):
    """
    One renderer for both. Two would be two lists to keep in step — the problem
    the registry exists to remove, reintroduced one level up.
    """
    sheet = (JS / "shortcuts-sheet.js").read_text(encoding="utf-8")

    assert sheet.count("function render(") == 1
    assert "render(dialog)" in sheet
    assert "render(inline)" in sheet


def test_the_sheet_points_at_where_keys_are_changed(client, db):
    """
    It used to say they could not be. That became untrue the moment any of them
    could, and a blanket claim that is true of most rows is worse than none —
    it stops people looking.
    """
    sheet = (JS / "shortcuts-sheet.js").read_text(encoding="utf-8")
    assert "cannot be reassigned" not in sheet
    assert "Settings" in sheet


# ---------------------------------------------------------------------------
# Reassignment
#
# The registry was built so the collision check could exist; rebinding is what
# it was always one step away from. What matters is that the same check now runs
# against a person's choice rather than only against the shipped defaults.
# ---------------------------------------------------------------------------

def _shortcuts_js() -> str:
    return (JS / "shortcuts.js").read_text(encoding="utf-8")


def test_only_shortcuts_something_consults_are_assignable():
    """
    A shortcut can be reassigned only if something reads the registry when the
    key is pressed. Escape, Enter and the arrows are dispatched by the component
    they belong to — and are conventions people bring with them rather than
    preferences they hold.
    """
    assignable = {e["id"] for e in registry() if e["assignable"]}

    assert {"palette.open", "chat.search", "help.shortcuts"} <= assignable
    assert {"note.bold", "note.italic"} <= assignable
    assert not assignable & {"ui.dismiss", "chat.send", "chat.newline",
                             "list.up", "list.down", "list.choose"}


def test_every_assignable_shortcut_is_actually_wired_through_the_registry():
    """
    Marking something assignable that nobody looks up produces a control that
    appears to work and changes nothing.
    """
    sources = {
        "palette.open": (JS / "palette.js").read_text(encoding="utf-8"),
        "chat.search": (JS / "main.js").read_text(encoding="utf-8"),
        "help.shortcuts": (JS / "shortcuts-sheet.js").read_text(encoding="utf-8"),
        "note.bold": (JS / "notes.js").read_text(encoding="utf-8"),
        "note.italic": (JS / "notes.js").read_text(encoding="utf-8"),
    }
    for item in registry():
        if not item["assignable"]:
            continue
        source = sources.get(item["id"], "")
        assert (f'register("{item["id"]}"' in source
                or f'keysFor("{item["id"]}")' in source), item["id"]


def test_dispatch_follows_the_override_not_the_default():
    """
    The point of the whole thing. The loop reads keysFor, so a reassignment
    takes effect without the dispatcher knowing reassignment exists.
    """
    js = _shortcuts_js()
    dispatch = js.split("document.addEventListener(\"keydown\"")[1]
    assert "keysFor(item.id)" in dispatch
    assert "matches(event, item.keys)" not in dispatch


def test_a_new_binding_is_checked_against_the_live_ones():
    """
    Against keysFor, not against the shipped keys: otherwise two reassignments
    could be made to collide with each other, which is the original bug with
    extra steps.
    """
    js = _shortcuts_js()
    conflict = js.split("function conflictFor")[1].split("function setBinding")[0]
    assert "keysFor(other.id)" in conflict
    assert 'other.scope === "global"' in conflict


def test_choosing_the_original_back_clears_the_override():
    """
    Otherwise the row reads "changed" while matching the default exactly.
    """
    js = _shortcuts_js()
    assert "delete map[id]" in js.split("function setBinding")[1]


def test_a_fixed_shortcut_refuses_reassignment_at_the_source():
    """
    Hiding the button is presentation. The refusal has to be in the function, or
    the only thing stopping it is the page not offering it.
    """
    js = _shortcuts_js()
    setter = js.split("function setBinding")[1].split("function resetBinding")[0]
    assert "item.assignable" in setter


def test_reassignments_are_per_device():
    """
    Same choice as the theme, for a sharper reason: a chord is a property of the
    keyboard in front of you, and one chosen on a laptop may be untypeable on a
    phone.
    """
    js = _shortcuts_js()
    assert "localStorage" in js
    assert "rc-shortcuts" in js

    body = (ROOT / "dashboard" / "templates" / "settings"
            / "_keyboard.html").read_text(encoding="utf-8")
    assert "on this device" in body


def test_storage_failures_do_not_take_the_page_down():
    """localStorage throws in a private window rather than returning null."""
    js = _shortcuts_js()
    store = js.split("const STORE")[1].split("function find")[0]
    assert store.count("catch") >= 2


def test_capture_ignores_the_modifier_being_held():
    """
    A listener taking the first keydown records "Control" the instant somebody
    reaches for Ctrl+J, before they have finished pressing it.
    """
    sheet = (JS / "shortcuts-sheet.js").read_text(encoding="utf-8")
    assert "MODIFIERS.indexOf(event.key) !== -1" in sheet


def test_capture_can_be_backed_out_of():
    sheet = (JS / "shortcuts-sheet.js").read_text(encoding="utf-8")
    assert 'event.key === "Escape"' in sheet


def test_a_bare_letter_is_refused():
    """It would fire while somebody is typing it into the page."""
    sheet = (JS / "shortcuts-sheet.js").read_text(encoding="utf-8")
    assert "Use a combination that includes" in sheet


def test_the_settings_page_offers_the_controls(client, db):
    client.get("/dashboard")
    body = client.get("/settings").get_data(as_text=True)
    keyboard = body.split('data-settings-panel="keyboard"')[1]

    assert "data-shortcuts-reset" in keyboard
    assert "cannot be reassigned" not in keyboard
