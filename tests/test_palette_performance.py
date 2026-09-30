"""
tests/test_palette_performance.py — why the palette hung, and what stops it.

The first version took 38 seconds on one keystroke and then answered every
later one with an empty list. Measured against the deployed Lakebase:

    one round trip, doing nothing          438 ms
    SELECT *  title OR abstract ILIKE     3574 ms
    the same predicate, four columns       319 ms

Three separate faults, and each hid the next.

**`SELECT *` was the cost, not the scan.** `papers.payload` is the whole API
response as JSONB; shipping it per keystroke is what took three and a half
seconds. Asking for four columns made it eleven times faster.

**Four queries where one would do.** At a 438 ms floor, one lookup per section
costs 1.7 seconds before anything is searched — and with a 120 ms debounce,
several of those overlap per word, each holding a pooled connection until it
finishes. That is what exhausted the pool.

**The SQL was invalid and nobody found out.** Each UNION branch carried its own
ORDER BY and LIMIT without parentheses, which Postgres rejects outright. Every
search raised; `except Exception` turned that into an empty palette that read
as "nothing matches". The test suite could not see it because the fake answers
in Python and never parses the SQL.

The last one is the reason this file exists: correctness that only the real
database can judge needs a test that looks at the statement itself.
"""

import pathlib
import re

import pytest

from repositories import palette as palette_repo

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _repo_source() -> str:
    return (ROOT / "dashboard" / "repositories"
            / "palette.py").read_text(encoding="utf-8")


def _repo_queries() -> str:
    """
    The part of the module that holds SQL.

    NOT "the source with docstrings stripped": the queries are triple-quoted
    strings too, so that regex removed the very thing being tested and every
    assertion passed against an empty string.
    """
    source = _repo_source()
    return source[source.index("def search("):]


def _js() -> str:
    return (ROOT / "dashboard" / "static" / "js"
            / "palette.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The SQL the fake cannot check
# ---------------------------------------------------------------------------

def test_every_union_branch_is_parenthesised():
    """
    A branch carrying its own ORDER BY or LIMIT must be wrapped, or those bind
    to the whole union and Postgres answers `syntax error at or near "UNION"`.

    This shipped unwrapped. Every search raised, the caller swallowed it, and
    the palette showed an empty list — a query that never ran, presented as a
    result. The suite was green throughout, because the fake answers in Python.
    """
    joined = palette_repo._union(["SELECT 1 ORDER BY 1 LIMIT 5",
                                  "SELECT 2 ORDER BY 1 LIMIT 5"])

    for branch in joined.split("UNION ALL"):
        branch = branch.strip()
        assert branch.startswith("(") and branch.endswith(")"), branch


def test_the_union_helper_is_what_builds_every_statement():
    """Guards the test above from being true of a helper nobody calls."""
    source = _repo_source()
    assert source.count("_union(parts)") == 2      # search and recent


def test_no_query_selects_everything():
    """
    `SELECT *` on papers pulls `payload` — the entire upstream API response —
    across the public internet, per keystroke. Four named columns are eleven
    times faster and are all the palette shows.
    """
    code = _repo_queries()
    assert "SELECT *" not in code
    assert "SELECT p.*" not in code


def test_each_branch_limits_itself():
    """One noisy kind must not crowd the others out of a shortlist."""
    code = _repo_queries()
    branches = code.count("SELECT '")
    assert branches >= 5
    assert code.count("LIMIT %(per)s") >= branches - 1


def test_one_round_trip_per_search():
    """
    At a 438 ms floor, four sequential lookups cost 1.7 seconds before any work
    happens. Both entry points issue exactly one statement.
    """
    assert _repo_queries().count("run_query(") == 2


def test_private_kinds_are_not_queried_for_a_stranger():
    """
    An anonymous visitor should not pay for a notes lookup that could only ever
    return nothing — and the scoping is in the SQL, not only in a later filter.
    """
    code = _repo_source()
    assert "if user_id:" in code
    assert "n.user_id = %(user)s" in code


# ---------------------------------------------------------------------------
# What the browser does about it
# ---------------------------------------------------------------------------

def test_an_obsolete_request_is_cancelled_not_merely_ignored():
    """
    An ignored response still holds a server thread and a pooled connection
    until it finishes. That is the difference between a wasted answer and an
    exhausted pool.
    """
    js = _js()
    assert "AbortController" in js
    assert "inflight.abort()" in js


def test_an_abort_is_not_reported_as_a_failure():
    """It is this code's own doing."""
    assert 'err.name === "AbortError"' in _js()


def test_the_debounce_outlasts_a_round_trip():
    js = _js()
    wait = re.search(r"setTimeout\(function \(\) \{ query\(input\.value\.trim\(\)\); \}, (\d+)\)", js)
    assert wait, "debounce not found"
    assert int(wait.group(1)) >= 250


def test_a_short_query_does_not_reach_the_database():
    """
    One or two characters match most of the corpus, so the round trip buys a
    list of everything. Destinations and recent work fill the gap.
    """
    from services import command_service
    assert command_service.MIN_QUERY >= 2


# ---------------------------------------------------------------------------
# Failing out loud
# ---------------------------------------------------------------------------

def test_a_failed_lookup_is_not_reported_as_an_empty_library(client, db, monkeypatch):
    """
    The first version wrapped each section so one bad lookup could not take the
    palette down. Under load every section failed at once and it returned an
    empty list — which reads as "you have nothing" rather than "this is broken".
    """
    from repositories import palette as repo

    def explode(*a, **k):
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(repo, "search", explode)
    client.get("/dashboard")

    # "notes" matches a destination, so the half that never touches the
    # database is visible in the answer.
    data = client.get("/command?q=notes",
                      headers={"X-Requested-With": "XMLHttpRequest"}).get_json()

    assert data["ok"] is False
    # Navigation still works, because it never touched the database.
    assert any(s["title"] == "Go to" for s in data["sections"])


def test_the_browser_says_so_rather_than_showing_nothing():
    js = _js()
    assert "data.ok === false" in js
    assert "unavailable" in js.lower()


def test_a_working_search_says_so_too(client, db):
    db.seed_paper(title="Retrieval-Augmented Generation")
    client.get("/dashboard")

    data = client.get("/command?q=retrieval",
                      headers={"X-Requested-With": "XMLHttpRequest"}).get_json()
    assert data["ok"] is True


# ---------------------------------------------------------------------------
# The shortcut, and what it collided with
# ---------------------------------------------------------------------------

# These used to read the key-matching code out of palette.js and main.js. The
# chord moved into the registry in shortcuts.js, which is the point of having
# one — so they ask it instead. The collision rules themselves are enforced in
# tests/test_shortcuts.py; what is checked here is that THIS feature still owns
# the chord it is supposed to.

def test_the_palette_owns_control_k():
    from tests.test_shortcuts import registry

    entry = next(e for e in registry() if e["id"] == "palette.open")
    assert entry["keys"] == "Mod+K"
    assert entry["scope"] == "global"
    assert 'register("palette.open"' in _js()


def test_the_chat_history_search_no_longer_answers_it():
    """
    Both were bound to Ctrl+K and main.js won by registering first, so the
    global palette was unreachable by its own shortcut. Chat search — a filter
    over one list — moved to Ctrl+Shift+F.
    """
    from tests.test_shortcuts import registry

    assert next(e for e in registry() if e["id"] == "chat.search")["keys"] == "Mod+Shift+F"


def test_the_palette_ignores_the_chat_combination():
    """
    Ctrl+Shift+K must not open both. The dispatcher compares every modifier,
    including the ones a chord does not ask for, so a chord without Shift does
    not match an event that has it.
    """
    shortcuts = (ROOT / "dashboard" / "static" / "js"
                 / "shortcuts.js").read_text(encoding="utf-8")
    assert "event.shiftKey === want.shift" in shortcuts


# ---------------------------------------------------------------------------
# Rows that fit the dialog
# ---------------------------------------------------------------------------

def _css() -> str:
    return (ROOT / "dashboard" / "static" / "css" / "base.css").read_text(encoding="utf-8")


def _rules(selector: str) -> str:
    found = re.findall(re.escape(selector) + r"\s*\{([^}]*)\}", _css())
    assert found, f"no rule for {selector}"
    return "\n".join(found)


def test_a_long_detail_cannot_push_the_row_sideways():
    """
    A note's detail IS its paper's title, and `nowrap` with no width let one
    widen the row until the whole dialog scrolled horizontally.
    """
    rule = _rules(".palette-item-detail")
    assert "max-width" in rule
    assert "text-overflow: ellipsis" in rule


def test_nothing_in_the_palette_scrolls_sideways():
    """A horizontal scrollbar under a result list is a layout bug, not a way
    to see the rest of a row."""
    assert "overflow-x: hidden" in _rules(".palette-results")


def test_a_tile_label_cannot_widen_its_column():
    assert "text-overflow: ellipsis" in _rules(".palette-tile > span")


# ---------------------------------------------------------------------------
# One row of destinations
# ---------------------------------------------------------------------------

def test_the_destinations_are_one_row():
    """
    At 104px they took two rows and pushed the results — what people opened the
    palette for — below the fold.
    """
    assert "repeat(6, minmax(0, 1fr))" in _rules(".palette-tiles")


def test_the_rest_fold_away_until_asked_for():
    js = _js()
    assert "TILES_SHOWN" in js
    assert "is-extra" in js
    assert "data-tile-more" in js
    assert ".palette-tiles:not(.is-expanded) .palette-tile.is-extra" in _css()


def test_the_folded_tiles_are_still_in_the_page():
    """
    Hidden by CSS, not withheld: they stay arrow-reachable and expanding costs
    no request and shifts no index.
    """
    js = _js()
    render = js.split("section.items.forEach")[1][:300]
    assert "n >= TILES_SHOWN" in render      # rendered, flagged, not skipped


# ---------------------------------------------------------------------------
# A note result opens that note
# ---------------------------------------------------------------------------

def test_a_note_result_links_to_the_note_itself(client, db):
    from services import progress_service

    client.get("/dashboard")
    note = progress_service.save_note(next(iter(db.users_by_id)), None,
                                      "the retriever is the weak point")

    data = client.get("/command?q=retriever",
                      headers={"X-Requested-With": "XMLHttpRequest"}).get_json()
    notes = next(s for s in data["sections"] if s["title"] == "Notes")

    assert f"note={note['note_id']}" in notes["items"][0]["url"]


def test_the_notes_page_opens_the_note_it_was_sent(client, db):
    """
    Landing on a list to find again the thing you just picked out of a list
    undoes the point of picking it.
    """
    js = (ROOT / "dashboard" / "static" / "js"
          / "notes-page.js").read_text(encoding="utf-8")

    assert 'get("note")' in js
    assert "showPad({" in js.split('get("note")')[1][:800]


def test_an_unreachable_note_leaves_you_on_the_list(client, db):
    """Filtered out or past the limit: the list beats an empty notepad."""
    js = (ROOT / "dashboard" / "static" / "js"
          / "notes-page.js").read_text(encoding="utf-8")
    block = js.split('get("note")')[1][:600]
    assert "if (!item) { return; }" in block
