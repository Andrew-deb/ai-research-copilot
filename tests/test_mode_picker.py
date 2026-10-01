"""
tests/test_mode_picker.py — choosing Research or Wick.

It was a native `<select>`. The closed control could be styled; its drop-down
could not, because that is drawn by the operating system — so inside a dark
composer it opened as a white box with black type.

Replacing it with a button and a menu fixes the appearance, and buys something
the old control had no room for: each mode can say what it does. "Research" and
"Wick" are two words that mean nothing to somebody who has not used this before,
and `<option>` has nowhere to put the sentence that would help.

The contract kept: the control still answers to `#chat-mode`, still has a
`.value`, and still fires `change` — so the code reading the mode never learns
any of this happened.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
JS = (ROOT / "dashboard" / "static" / "js" / "chat.js").read_text(encoding="utf-8")
CSS = (ROOT / "dashboard" / "static" / "css" / "chat.css").read_text(encoding="utf-8")


def _composer(client) -> str:
    body = client.get("/chat").get_data(as_text=True)
    return body.split('<form class="composer', 1)[1].split("</form>", 1)[0]


def test_the_native_dropdown_is_gone(client, db):
    composer = _composer(client)
    assert "<select" not in composer
    assert "<option" not in composer


def test_the_control_still_answers_to_the_same_name(client, db):
    """
    chat.js reads `#chat-mode`. Renaming it would have been a second change
    riding along with a visual one.
    """
    composer = _composer(client)
    assert 'id="chat-mode"' in composer
    assert 'value="' in composer


def test_it_still_reports_a_value_and_fires_change():
    """
    A button has `.value`, so the existing handler needs no rewrite — but it
    does not fire `change` on its own, which is the part that has to be
    dispatched deliberately.
    """
    assert "modePicker.value = option.dataset.value" in JS
    assert 'dispatchEvent(new Event("change"))' in JS


def test_each_mode_says_what_it_does(client, db):
    """
    The thing the old control could not do. Two words in a drop-down do not
    explain themselves to somebody who has not used this before.
    """
    composer = _composer(client)
    assert "Finds papers and cites them" in composer
    assert "Works on your collections and notes" in composer


def test_the_selected_mode_is_marked(client, db):
    body = client.get("/chat?mode=wick").get_data(as_text=True)
    assert 'data-value="wick"' in body
    assert 'aria-selected="true"' in body


def test_the_menu_opens_upward():
    """
    The composer sits at the foot of the page on every surface that has one, so
    a menu dropping downward opens off the bottom of the screen.
    """
    rule = CSS[CSS.index(".mode-pick-menu {"):]
    rule = rule[:rule.index("}")]
    assert "bottom: calc(100% + 6px)" in rule
    assert "top:" not in rule


def test_it_closes_the_ways_a_menu_should():
    assert "if (!modeWrap.contains(e.target))" in JS
    assert 'e.key === "Escape"' in JS


def test_it_is_announced_as_a_menu():
    """
    A button that opens a list of options is not a button as far as a screen
    reader is concerned, unless it says so.
    """
    widget = (ROOT / "dashboard" / "templates"
              / "_chat_composer.html").read_text(encoding="utf-8")
    assert 'aria-haspopup="listbox"' in widget
    assert 'role="listbox"' in widget
    assert 'role="option"' in widget
    assert 'aria-expanded' in widget
