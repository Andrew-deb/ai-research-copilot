"""
tests/test_stylesheets.py — the stylesheets parse.

Written after a stray `}` reached the settings page.

It arrived from an edit that merged two media queries: the replacement computed
where the old block ended by searching for the next `}`, which closed the inner
RULE rather than the media query around it, and left the outer closer behind.
The file still looked right — the stray brace sat on its own line between two
blocks, in a 3000-line stylesheet nobody reads top to bottom.

What made it expensive is that CSS has no compiler. A Python typo fails at
import and a JS typo fails `node --check`; an unbalanced stylesheet loads, and
the browser recovers from it in its own way, so the first report is a screenshot
of a page with its sidebar on top of its content.

This is the missing compiler. It is deliberately dumb — balance and nesting
only — because that is the class of error an editing script can introduce and a
human reading a diff will not see.
"""

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSS_DIR = ROOT / "dashboard" / "static" / "css"
SHEETS = sorted(CSS_DIR.glob("*.css"))


def _scan(text: str):
    """
    Walk the stylesheet, skipping comments, tracking depth.

    Returns (final_depth, first_unopened_line). Comments are skipped because
    they routinely contain braces — several in this codebase quote the CSS they
    are explaining.
    """
    depth, line, i, n = 0, 1, 0, len(text)
    first_extra = None

    while i < n:
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            line += text.count("\n", i, end)
            i = end
            continue

        char = text[i]
        if char == "\n":
            line += 1
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0 and first_extra is None:
                first_extra = line
        i += 1

    return depth, first_extra


def test_there_are_stylesheets_to_check():
    """A glob that matches nothing passes every test below it."""
    assert len(SHEETS) >= 5


@pytest.mark.parametrize("sheet", SHEETS, ids=lambda p: p.name)
def test_every_brace_is_closed(sheet):
    depth, _ = _scan(sheet.read_text(encoding="utf-8"))
    assert depth == 0, (
        f"{sheet.name} ends {abs(depth)} brace(s) "
        f"{'short' if depth > 0 else 'over'}")


@pytest.mark.parametrize("sheet", SHEETS, ids=lambda p: p.name)
def test_no_closer_without_an_opener(sheet):
    """
    The exact shape of the bug. A file can end balanced and still be wrong: an
    extra `}` early and a missing one later cancel out in the total.
    """
    _, first_extra = _scan(sheet.read_text(encoding="utf-8"))
    assert first_extra is None, (
        f"{sheet.name}:{first_extra} closes a block that was never opened")


@pytest.mark.parametrize("sheet", SHEETS, ids=lambda p: p.name)
def test_media_queries_are_not_nested_by_accident(sheet):
    """
    Nesting beyond one level means a media query swallowed the rules after it —
    the other way the same editing mistake shows up, and the reason desktop
    rules can silently become mobile-only.
    """
    text = sheet.read_text(encoding="utf-8")
    depth, i, n, worst = 0, 0, len(text), 0

    while i < n:
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if text[i] == "{":
            depth += 1
            worst = max(worst, depth)
        elif text[i] == "}":
            depth -= 1
        i += 1

    assert worst <= 2, f"{sheet.name} nests blocks {worst} deep"
