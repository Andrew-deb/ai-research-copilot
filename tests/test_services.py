"""
tests/test_services.py — service-layer logic without the HTTP layer.
"""

import pytest

from exceptions import ValidationError
from services import collection_service, progress_service, search_service


def test_reading_plan_sequences_by_year_then_impact(db):
    user = db.get_or_create_user("planner@example.com")
    uid = user["user_id"]
    coll = db.create_collection(uid, "Curriculum")
    cid = coll["collection_id"]

    old_seminal = db.seed_paper(title="Seminal 2015", publication_year=2015, citation_count=9000)
    new_big = db.seed_paper(title="Big 2020", publication_year=2020, citation_count=8000, influence_score=50.0)
    new_niche = db.seed_paper(title="Niche 2020", publication_year=2020, citation_count=12)

    for p in (new_big, new_niche, old_seminal):
        db.add_paper_to_collection(cid, p["paper_id"])

    plan = collection_service.generate_reading_plan(uid, cid)
    titles = [item["title"] for item in plan["reading_plan"]]

    assert titles[0] == "Seminal 2015"          # earliest year first
    assert titles[1] == "Big 2020"              # same year → higher impact first
    assert titles[2] == "Niche 2020"
    assert plan["reading_plan"][0]["stage"] == "Foundations"
    # persisted back to the link rows
    assert db.collection_papers[(cid, old_seminal["paper_id"])]["sequence_order"] == 1


def test_reading_plan_empty_collection_raises(db):
    user = db.get_or_create_user("planner@example.com")
    coll = db.create_collection(user["user_id"], "Empty")
    with pytest.raises(ValidationError):
        collection_service.generate_reading_plan(user["user_id"], coll["collection_id"])


def test_semantic_matches_fold_chunks_to_one_row_per_paper(db, monkeypatch):
    from repositories import lakebase
    paper = db.seed_paper(title="Multi-chunk Paper")

    def chunky(query_embedding, top_k=10):
        return [
            {**paper, "chunk_text": "chunk A", "chunk_index": 0, "similarity": 0.42},
            {**paper, "chunk_text": "chunk B", "chunk_index": 1, "similarity": 0.88},
        ]

    monkeypatch.setattr(lakebase, "semantic_search_papers", chunky)

    matches = search_service.semantic_paper_matches("attention", top_k=5)
    assert len(matches) == 1
    assert matches[0]["similarity"] == 0.88          # best chunk wins
    assert matches[0]["snippet"] == "chunk B"


def test_snippet_section_is_reported_and_defaults_to_abstract(db, monkeypatch):
    """
    Phase 2: a chunk knows which part of the paper it came from. NULL in the
    database means the abstract, and the service resolves that here so templates
    and the agent never special-case None.
    """
    from repositories import lakebase
    abstract_paper = db.seed_paper(title="Abstract-only Paper")
    section_paper = db.seed_paper(title="Paper With Sections")

    monkeypatch.setattr(lakebase, "semantic_search_papers", lambda query_embedding, top_k=10: [
        {**section_paper, "chunk_text": "we conclude", "chunk_index": 0,
         "section_name": "conclusion", "similarity": 0.91},
        {**abstract_paper, "chunk_text": "this paper", "chunk_index": 0,
         "section_name": None, "similarity": 0.55},
    ])

    matches = search_service.semantic_paper_matches("q", top_k=5)
    assert matches[0]["snippet_section"] == "conclusion"
    assert matches[1]["snippet_section"] == "abstract"
    # It describes the snippet, not the paper - the raw column must not leak through.
    assert "section_name" not in matches[0]


def test_best_chunk_decides_the_reported_section(db, monkeypatch):
    """
    The fold keeps the highest-similarity chunk, so the section badge must follow
    that chunk. Reporting the first chunk's section would mislabel the excerpt.
    """
    from repositories import lakebase
    paper = db.seed_paper(title="Multi-section Paper")

    monkeypatch.setattr(lakebase, "semantic_search_papers", lambda query_embedding, top_k=10: [
        {**paper, "chunk_text": "abstract text", "chunk_index": 0,
         "section_name": None, "similarity": 0.40},
        {**paper, "chunk_text": "limitations we found", "chunk_index": 0,
         "section_name": "discussion", "similarity": 0.87},
    ])

    match = search_service.semantic_paper_matches("q", top_k=5)[0]
    assert match["snippet"] == "limitations we found"
    assert match["snippet_section"] == "discussion"


def test_semantic_matches_respect_min_similarity(db, monkeypatch):
    from repositories import lakebase
    paper = db.seed_paper()

    monkeypatch.setattr(
        lakebase, "semantic_search_papers",
        lambda query_embedding, top_k=10: [{**paper, "chunk_text": "x", "chunk_index": 0, "similarity": 0.1}],
    )
    assert search_service.semantic_paper_matches("q", top_k=5, min_similarity=0.5) == []


def test_keyword_search_pagination_flags(db):
    for i in range(25):
        db.seed_paper(title=f"Paper about vectors {i}")

    page1 = search_service.keyword_search("vectors", page=1, page_size=20)
    assert len(page1["results"]) == 20
    assert page1["has_next"] is True
    assert page1["has_prev"] is False

    page2 = search_service.keyword_search("vectors", page=2, page_size=20)
    assert page2["has_next"] is False
    assert page2["has_prev"] is True


def test_empty_query_raises(db):
    with pytest.raises(ValidationError):
        search_service.keyword_search("   ")


# ---------------------------------------------------------------------------
# Reading board — what belongs on it
# ---------------------------------------------------------------------------

def test_a_collected_paper_appears_on_the_board_as_not_started(db):
    """
    The bug this fixes: the board inner-joined reading_progress, so a paper only
    appeared *after* its status had been set somewhere else. "Not Started" was
    permanently empty and the page told users to drag cards it never rendered.
    """
    user = db.get_or_create_user("board@test.dev")
    paper = db.seed_paper(title="Collected But Untouched")
    coll = db.create_collection(user["user_id"], "Reading list")
    db.add_paper_to_collection(coll["collection_id"], paper["paper_id"])

    board = progress_service.get_board(user["user_id"])

    titles = [p["title"] for p in board["columns"]["not_started"]]
    assert "Collected But Untouched" in titles
    assert board["stats"]["not_started"] == 1


def test_an_explicit_status_wins_over_the_not_started_default(db):
    user = db.get_or_create_user("board2@test.dev")
    paper = db.seed_paper(title="Started Reading")
    coll = db.create_collection(user["user_id"], "Reading list")
    db.add_paper_to_collection(coll["collection_id"], paper["paper_id"])
    db.upsert_reading_progress(user["user_id"], paper["paper_id"], "reading")

    board = progress_service.get_board(user["user_id"])

    assert [p["title"] for p in board["columns"]["reading"]] == ["Started Reading"]
    assert board["columns"]["not_started"] == []
    assert board["total"] == 1          # counted once, not twice


def test_discovery_history_does_not_land_on_the_board(db):
    """
    `papers` is everything the system has ever encountered. Putting all of it on the
    board would bury the user's actual reading list under search residue.
    """
    user = db.get_or_create_user("board3@test.dev")
    db.seed_paper(title="Merely Encountered")

    board = progress_service.get_board(user["user_id"])
    assert board["total"] == 0


def test_a_status_set_outside_any_collection_still_shows(db):
    """Marking a paper from its detail page is deliberate engagement too."""
    user = db.get_or_create_user("board4@test.dev")
    paper = db.seed_paper(title="Marked From Detail Page")
    db.upsert_reading_progress(user["user_id"], paper["paper_id"], "completed")

    board = progress_service.get_board(user["user_id"])
    assert [p["title"] for p in board["columns"]["completed"]] == ["Marked From Detail Page"]


# ---------------------------------------------------------------------------
# Home page — round trips, not just correctness
# ---------------------------------------------------------------------------

def test_the_home_page_does_not_query_the_same_thing_twice(db, monkeypatch):
    """
    get_dashboard_stats was four statements and home_service then issued a fifth
    that was byte-identical to one of them. Lakebase is remote, so each statement is
    a TLS round trip - this is a latency test wearing a correctness test's clothes.
    """
    from repositories import lakebase
    from services import home_service

    calls: list[str] = []
    for name in ("get_dashboard_stats", "get_progress_stats", "get_recent_papers",
                 "get_learning_goals", "get_user_progress"):
        original = getattr(lakebase, name)

        def spy(*args, _name=name, _orig=original, **kwargs):
            calls.append(_name)
            return _orig(*args, **kwargs)

        monkeypatch.setattr(lakebase, name, spy)

    user = db.get_or_create_user("home@test.dev")
    home_service.get_overview(user["user_id"])

    assert "get_progress_stats" not in calls, (
        "the reading breakdown now comes back inside get_dashboard_stats; "
        f"calls were {calls}")
    assert len(calls) == 4, f"expected 4 repository calls, got {len(calls)}: {calls}"


def test_the_home_page_asks_for_only_what_it_shows(db, monkeypatch):
    """Both lists render 5 rows; fetching all of them and slicing wastes the wire."""
    from repositories import lakebase
    from services import home_service

    seen: dict[str, object] = {}
    for name in ("get_learning_goals", "get_user_progress"):
        original = getattr(lakebase, name)

        def spy(*args, _name=name, _orig=original, **kwargs):
            seen[_name] = kwargs.get("limit")
            return _orig(*args, **kwargs)

        monkeypatch.setattr(lakebase, name, spy)

    user = db.get_or_create_user("home2@test.dev")
    home_service.get_overview(user["user_id"])

    assert seen["get_learning_goals"] == 5
    assert seen["get_user_progress"] == 5


# ---------------------------------------------------------------------------
# Collections — round trips
# ---------------------------------------------------------------------------

def _count_writes(monkeypatch, *names):
    """Record how many times each named repository function is called."""
    from repositories import lakebase
    calls: list[str] = []
    for name in names:
        original = getattr(lakebase, name)

        def spy(*args, _name=name, _orig=original, **kwargs):
            calls.append(_name)
            return _orig(*args, **kwargs)

        monkeypatch.setattr(lakebase, name, spy)
    return calls


def test_reordering_is_one_statement_not_one_per_paper(db, monkeypatch):
    """
    A 20-paper collection was 20 sequential UPDATEs to a remote database. Worse, a
    failure part-way left the collection half-renumbered with no way to tell.
    """
    user = db.get_or_create_user("reorder@test.dev")
    coll = db.create_collection(user["user_id"], "Big list")
    papers = [db.seed_paper(title=f"P{i}") for i in range(20)]
    for p in papers:
        db.add_paper_to_collection(coll["collection_id"], p["paper_id"])

    calls = _count_writes(monkeypatch, "update_paper_sequence", "update_paper_sequences")
    reversed_ids = [p["paper_id"] for p in reversed(papers)]
    collection_service.reorder(user["user_id"], coll["collection_id"], reversed_ids)

    assert calls == ["update_paper_sequences"], f"expected one bulk write, got {calls}"
    assert db.collection_papers[(coll["collection_id"], reversed_ids[0])]["sequence_order"] == 1
    assert db.collection_papers[(coll["collection_id"], reversed_ids[-1])]["sequence_order"] == 20


def test_the_reading_plan_persists_its_order_in_one_statement(db, monkeypatch):
    user = db.get_or_create_user("plan@test.dev")
    coll = db.create_collection(user["user_id"], "Plan me")
    for i, year in enumerate([2020, 2015, 2018]):
        p = db.seed_paper(title=f"Paper {year}", publication_year=year)
        db.add_paper_to_collection(coll["collection_id"], p["paper_id"])

    calls = _count_writes(monkeypatch, "update_paper_sequence", "update_paper_sequences")
    plan = collection_service.generate_reading_plan(user["user_id"], coll["collection_id"])

    assert calls == ["update_paper_sequences"]
    assert [p["title"] for p in plan["reading_plan"]] == ["Paper 2015", "Paper 2018", "Paper 2020"]


def test_adding_a_paper_does_not_read_the_whole_collection(db, monkeypatch):
    """
    Computing the next position in Python meant fetching every paper in the
    collection - a join across papers and reading_progress - to produce one integer.
    """
    user = db.get_or_create_user("add@test.dev")
    coll = db.create_collection(user["user_id"], "Target")
    # Seed through the append path so the existing rows carry real positions;
    # add_paper_to_collection defaults sequence_order to 0, which would make the
    # assertion below test the fixture rather than the code.
    for i in range(5):
        db.append_paper_to_collection(coll["collection_id"], db.seed_paper(title=f"Old {i}")["paper_id"])
    newcomer = db.seed_paper(title="Newcomer")

    calls = _count_writes(monkeypatch, "get_collection_papers", "append_paper_to_collection")
    result = collection_service.add_paper(user["user_id"], coll["collection_id"], newcomer["paper_id"])

    assert "get_collection_papers" not in calls, f"still reading the collection: {calls}"
    assert result["sequence_order"] == 6


def test_re_adding_a_paper_keeps_its_position(db):
    """DO UPDATE with a no-op assignment, so the statement still returns a row."""
    user = db.get_or_create_user("readd@test.dev")
    coll = db.create_collection(user["user_id"], "Target")
    paper = db.seed_paper(title="Only One")

    first = collection_service.add_paper(user["user_id"], coll["collection_id"], paper["paper_id"])
    again = collection_service.add_paper(user["user_id"], coll["collection_id"], paper["paper_id"])

    assert first["sequence_order"] == again["sequence_order"] == 1
    assert len(db.collection_papers) == 1
