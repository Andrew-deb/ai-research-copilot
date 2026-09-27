"""
tests/test_collection_layout.py — a row whose columns match its cells.

A shared example collection rendered with every paper title wrapping one word
per line, straight down the page. The markup was fine; the grid was not:

    .reading-item { grid-template-columns: 20px 28px 1fr auto; }

Four columns, for a row that only has four children when the collection is
editable. A read-only one renders no drag handle and no Remove button, so the
body landed in the 28px column and wrapped to nothing.

CSS Grid places children in source order and cannot know that two of them were
never written. Whenever a template renders cells conditionally, the column
definition has to be conditional too — which is what these tests hold.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _css() -> str:
    return (ROOT / "dashboard" / "static" / "css"
            / "collections.css").read_text(encoding="utf-8")


def _rule(selector: str, css: str) -> str:
    """The declarations of the first rule matching this exact selector."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, f"no rule for {selector}"
    return match.group(1)


def test_a_read_only_row_declares_only_the_columns_it_renders():
    """
    Two cells — the sequence badge and the body. A third or fourth column here
    is one the body gets pushed out of.
    """
    columns = _rule(".reading-item.is-static", _css())
    assert columns.count("px") <= 1, f"too many fixed columns for two cells: {columns}"
    assert "1fr" in columns


def test_an_editable_row_keeps_its_handle_and_action_columns():
    columns = _rule(".reading-item.is-editable", _css())
    assert "1fr" in columns
    assert "auto" in columns            # the Remove button's column


def test_the_shared_definition_sets_no_column_count():
    """
    The base rule must not guess. It applied to both shapes, so whichever count
    it picked was wrong for one of them.
    """
    base = _rule(".reading-item", _css())
    assert "grid-template-columns" not in base


def test_the_body_column_can_actually_shrink():
    """
    `1fr` floors at min-content, so a single long title can still push its
    column past its share. minmax(0, 1fr) is what lets it wrap instead.
    """
    css = _css()
    for selector in (".reading-item.is-editable", ".reading-item.is-static"):
        assert "minmax(0, 1fr)" in _rule(selector, css), selector


# ---------------------------------------------------------------------------
# The template tells the stylesheet which shape it built
# ---------------------------------------------------------------------------

def test_the_row_says_whether_it_is_editable():
    markup = (ROOT / "dashboard" / "templates"
              / "collection_detail.html").read_text(encoding="utf-8")
    row = re.search(r'<li class="reading-item[^"]*"', markup)
    assert row, "reading-item row not found"
    assert "is-editable" in row.group(0) and "is-static" in row.group(0)


def test_a_shared_collection_renders_the_static_shape(anon_client, db):
    """
    End to end, on the page that was broken: the curated collections an
    anonymous visitor sees are exactly the read-only case.
    """
    paper = db.seed_paper(title="A Long Title That Used To Wrap One Word Per Line")
    system = db.get_or_create_user("system@research-copilot.dev", "Research Copilot")
    collection = db.create_collection(system["user_id"], "Shared example")
    db.collections[collection["collection_id"]]["is_curated"] = True
    db.add_paper_to_collection(collection["collection_id"], str(paper["paper_id"]), 1)

    body = anon_client.get(f"/collection/{collection['collection_id']}").get_data(as_text=True)

    assert "reading-item is-static" in body
    assert "is-editable" not in body
    assert "drag-handle" not in body      # the cell the grid was reserving space for
