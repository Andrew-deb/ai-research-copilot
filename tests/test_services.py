"""
tests/test_services.py — service-layer logic without the HTTP layer.
"""

import pytest

from exceptions import ValidationError
from services import collection_service, search_service


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
