"""
tests/test_mobile_chrome.py — what a phone actually gets.

Three regressions, all from the same habit: hiding something on a narrow screen
instead of finding it a place to go.

`.topbar-center { display: none }` at 420px removed two different controls at
once, and the rule read as though it removed one. On chat and search that slot
holds the Research/Wick mode switch, so the toggle was absent on a phone. On
every other page it holds the palette trigger, so the only way to search was
Ctrl+K — on devices whose keyboards mostly have no Ctrl key.

The scrollbar rule is the same shape of mistake: styling applied to a list of
selectors rather than to everything that scrolls, so each new scroll container
silently got Windows' default furniture.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSS = (ROOT / "dashboard" / "static" / "css" / "base.css").read_text(encoding="utf-8")


def _mobile_block(marker: str = ".topbar-search") -> str:
    """
    The narrow-screen media query that owns the header row.

    Found by what it CONTAINS, not by its width: there are several `max-width:
    720px` blocks in this stylesheet, and picking the first one silently
    inspected an unrelated rule.
    """
    for match in re.finditer(r"@media \(max-width: \d+px\)", CSS):
        i = CSS.index("{", match.end())
        depth = 0
        for j in range(i, len(CSS)):
            if CSS[j] == "{":
                depth += 1
            elif CSS[j] == "}":
                depth -= 1
                if depth == 0:
                    block = CSS[i:j]
                    if marker in block:
                        return block
                    break
    raise AssertionError(f"no media query contains {marker}")


# ---------------------------------------------------------------------------
# The centre slot
# ---------------------------------------------------------------------------

def test_the_centre_slot_is_not_simply_hidden_on_a_phone():
    block = _mobile_block(".topbar-center")
    assert ".topbar-center { display: none; }" not in block


def test_the_mode_switch_stays_in_the_header_row(client, db):
    """
    It has been wrong twice. `display: none` removed it outright, so chat and
    search had no toggle at all on a phone. Wrapping it to a line of its own put
    it back floating under the icons, attached to nothing — worse than absent,
    because it looks like a mistake rather than an omission.

    Inline in the row is where every application with this control puts it, and
    there is room because the page title is already hidden at this width.
    """
    assert "mode-switch" in client.get("/search").get_data(as_text=True)

    block = _mobile_block(".topbar-center")
    assert "position: static" in block
    assert "flex-basis: 100%" not in block          # no longer a row of its own
    assert not re.search(r"\.mode-switch\s*\{[^}]*display:\s*none", block)


def test_the_title_it_replaces_is_the_one_already_repeated_below():
    """
    The slot is free because `.topbar-title` is hidden on a narrow screen — the
    page name is in the heading directly underneath. Taking the space of
    something still needed would be a different trade.
    """
    assert ".topbar-title { display: none; }" in CSS


def test_help_leaves_the_row_rather_than_being_lost(client, db):
    """
    Room for the toggle comes from dropping a duplicate, not a feature: the same
    link sits three items down the sidebar.
    """
    body = client.get("/dashboard").get_data(as_text=True)
    assert body.count("/help") >= 2                 # sidebar and topbar
    assert "topbar-help" in body
    assert ".topbar-help { display: none; }" in _mobile_block(".topbar-help")


# ---------------------------------------------------------------------------
# Reaching the palette without a keyboard
# ---------------------------------------------------------------------------

def test_a_phone_gets_a_tappable_search_button(client, db):
    body = client.get("/dashboard").get_data(as_text=True)
    assert 'id="palette-open-mobile"' in body


def test_the_mobile_button_is_hidden_on_a_wide_screen():
    """The labelled pill says more than an icon; two affordances is one too many."""
    assert ".topbar-search { display: none; }" in CSS
    assert ".topbar-search { display: grid; }" in _mobile_block()


def test_both_triggers_open_the_same_palette():
    js = (ROOT / "dashboard" / "static" / "js"
          / "palette.js").read_text(encoding="utf-8")
    assert "palette-open-mobile" in js
    assert "triggers.forEach" in js


def test_the_search_button_is_reachable_signed_out(anon_client):
    """Searching is not a privilege, and the demo is the shop window."""
    assert 'id="palette-open-mobile"' in anon_client.get("/dashboard").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Scrollbars
# ---------------------------------------------------------------------------

def test_scrollbar_styling_is_global_rather_than_a_list_of_selectors():
    """
    The rule that stops this recurring. Named selectors meant every scroll
    container added afterwards — the sidebar, the settings nav, the palette —
    got the default 17px of high-contrast furniture, because nothing made the
    omission visible.
    """
    assert re.search(r"^\*\s*\{[^}]*scrollbar-width:\s*thin", CSS, re.M | re.S)
    assert re.search(r"^::-webkit-scrollbar\s*\{", CSS, re.M)
    assert re.search(r"^::-webkit-scrollbar-thumb\s*\{", CSS, re.M)


def test_no_scroll_container_has_to_opt_in():
    """
    No element-specific scrollbar rules left. One would mean the styling is
    something to remember again.
    """
    specific = re.findall(r"^([.#][\w.#\- ]+)::-webkit-scrollbar", CSS, re.M)
    assert not specific, f"scrollbar styling still named for: {specific}"


# ---------------------------------------------------------------------------
# The empty chat, on a phone
# ---------------------------------------------------------------------------

CHAT_CSS = (ROOT / "dashboard" / "static" / "css" / "chat.css").read_text(encoding="utf-8")


def _chat_mobile() -> str:
    for match in re.finditer(r"@media \(max-width: \d+px\)", CHAT_CSS):
        i = CHAT_CSS.index("{", match.end())
        depth = 0
        for j in range(i, len(CHAT_CSS)):
            if CHAT_CSS[j] == "{":
                depth += 1
            elif CHAT_CSS[j] == "}":
                depth -= 1
                if depth == 0:
                    block = CHAT_CSS[i:j]
                    if ".chat-page.is-empty" in block and "order:" in block:
                        return block
                    break
    raise AssertionError("no mobile empty-chat block")


def test_the_composer_reaches_the_bottom_of_the_screen():
    """
    It sat near the top under the greeting, with everything below it blank to
    the foot of the page. The composer is what the page is for and a thumb is
    already at the bottom edge.
    """
    block = _chat_mobile()
    assert "display: flex" in block
    assert "min-height: calc(100svh" in block


def test_the_height_excludes_browser_chrome_that_comes_and_goes():
    """
    `svh` rather than `vh`: the large viewport height assumes the URL bar is
    hidden, so a composer sized against it is parked underneath that bar for the
    first half of every visit.
    """
    block = _chat_mobile()
    assert "100svh" in block
    assert "100vh" not in block


def test_the_greeting_floats_in_the_space_that_used_to_be_blank():
    """Auto margins either side, so it centres without anyone measuring."""
    block = _chat_mobile()
    assert "margin-top: auto" in block
    assert "margin-bottom: auto" in block


def test_the_starters_sit_between_the_greeting_and_the_composer():
    """
    Above the composer here, below it on a wide screen. The partial's own
    comment is right that suggestions under an empty box read as the primary
    control — but that assumed the box was mid-page. Pinned to the bottom edge,
    "above the composer" IS under the greeting, and the reading order holds.
    """
    block = _chat_mobile()
    starters = re.search(r"\.chat-starters \{[^}]*order: (\d)", block, re.S)
    composer = re.search(r"\.composer \{[^}]*order: (\d)", block, re.S)
    assert starters and composer
    assert int(starters.group(1)) < int(composer.group(1))


def test_the_conversation_layout_is_left_alone():
    """
    Only the empty state changed. A live thread already pins its composer by
    sticking, which is the right mechanism when there is something to scroll.
    """
    assert ".chat-page.has-conversation .chat-stage" in CHAT_CSS
    assert "position: sticky" in CHAT_CSS
