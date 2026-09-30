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

# entry("id", "Mod+K", "scope", "Group", "Description")
_ENTRY = re.compile(
    r'entry\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)')


def registry() -> list[dict]:
    source = (JS / "shortcuts.js").read_text(encoding="utf-8")
    return [{"id": i, "keys": k, "scope": s, "group": g, "description": d}
            for i, k, s, g, d in _ENTRY.findall(source)]


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
    Mod+/ for people who know, a button in Settings for people who do not —
    which is most people, and the reason this page exists at all.
    """
    by_id = {e["id"]: e for e in registry()}
    assert by_id["help.shortcuts"]["keys"] == "Mod+/"

    client.get("/dashboard")
    assert "data-shortcuts-open" in client.get("/settings").get_data(as_text=True)


def test_the_sheet_says_the_keys_cannot_be_changed(client, db):
    """
    Rebinding is deliberately not built. Saying so beats letting somebody hunt
    for a control that was never there.
    """
    sheet = (JS / "shortcuts-sheet.js").read_text(encoding="utf-8")
    assert "cannot be reassigned" in sheet
