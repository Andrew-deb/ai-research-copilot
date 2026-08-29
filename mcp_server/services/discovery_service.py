"""
mcp_server/services/discovery_service.py — Paper Discovery & Exploration Service.

Orchestrates multi-source search (OpenAlex + Semantic Scholar + Lakebase vector search),
paper details retrieval, neural recommendations, paper comparison, and Wikipedia topic caching.
"""

import logging
from typing import List, Optional

from brokers import openalex_broker, semantic_scholar_broker, wikipedia_broker
from exceptions import PaperNotFoundError, ValidationError
from repositories import lakebase

logger = logging.getLogger(__name__)


def search_papers(query: str, limit: int = 10) -> List[dict]:
    """
    Search research papers by keyword or semantic query.
    1. Fetches candidate papers from OpenAlex.
    2. Enriches records with DOIs via Semantic Scholar (TLDRs, influence scores).
    3. Upserts results into Lakebase to keep the catalog fresh.
    """
    if not query or not query.strip():
        raise ValidationError("Search query cannot be empty.")

    logger.info(f"Searching papers for query: '{query}' (limit={limit})")
    
    # 1. Discover candidates from OpenAlex
    candidates = openalex_broker.search_works(query=query, per_page=limit)
    if not candidates:
        # Fallback to local text search in Lakebase if external API returned no results
        return lakebase.search_papers_by_text(query, limit=limit)

    # 2. Enrich papers that have DOIs with Semantic Scholar metadata
    enriched_papers = []
    for p in candidates:
        doi = p.get("doi")
        if doi:
            try:
                s2_data = semantic_scholar_broker.enrich_paper_by_doi(doi)
                if s2_data:
                    p["semantic_scholar_id"] = s2_data.get("semantic_scholar_id")
                    p["tldr"] = s2_data.get("tldr")
                    p["influence_score"] = s2_data.get("influence_score")
            except Exception as e:
                logger.debug(f"S2 enrichment skipped for DOI {doi}: {e}")
        enriched_papers.append(p)

    # 3. Persist papers and author relations in Lakebase
    persisted_papers = []
    for paper in enriched_papers:
        saved = lakebase.upsert_paper(paper)
        if paper.get("_authors"):
            lakebase.upsert_paper_authors(saved["paper_id"], paper["_authors"])
        saved["authors"] = lakebase.get_authors_for_paper(saved["paper_id"])
        persisted_papers.append(saved)

    return persisted_papers


def get_paper_details(paper_id_or_doi: str) -> dict:
    """
    Fetch comprehensive paper details by UUID, DOI, or OpenAlex ID.
    Queries Lakebase first, then falls back to external APIs if not found.
    """
    if not paper_id_or_doi or not paper_id_or_doi.strip():
        raise ValidationError("Paper identifier cannot be empty.")

    identifier = paper_id_or_doi.strip()
    
    # 1. Try local database by UUID, DOI, or OpenAlex ID
    paper = lakebase.get_paper(identifier)
    if not paper:
        paper = lakebase.get_paper_by_doi(identifier)
    if not paper:
        paper = lakebase.get_paper_by_openalex_id(identifier)

    # 2. If not found locally, attempt external fetch
    if not paper:
        if "10." in identifier:  # Looks like a DOI
            oa_paper = openalex_broker.get_work_by_doi(identifier)
        else:
            oa_paper = openalex_broker.get_work(identifier)

        if oa_paper:
            # Enrich and persist
            doi = oa_paper.get("doi")
            if doi:
                s2 = semantic_scholar_broker.enrich_paper_by_doi(doi)
                if s2:
                    oa_paper["semantic_scholar_id"] = s2.get("semantic_scholar_id")
                    oa_paper["tldr"] = s2.get("tldr")
                    oa_paper["influence_score"] = s2.get("influence_score")
            
            paper = lakebase.upsert_paper(oa_paper)
            if oa_paper.get("_authors"):
                lakebase.upsert_paper_authors(paper["paper_id"], oa_paper["_authors"])

    if not paper:
        raise PaperNotFoundError(f"Paper '{paper_id_or_doi}' not found in Lakebase or external repositories.")

    # Attach authors and notes
    paper["authors"] = lakebase.get_authors_for_paper(paper["paper_id"])
    return paper


def get_similar_papers(paper_id: str, limit: int = 5) -> List[dict]:
    """
    Retrieve semantically similar papers using Semantic Scholar recommendations.
    """
    paper = get_paper_details(paper_id)
    s2_id = paper.get("semantic_scholar_id")
    doi = paper.get("doi")

    recommendations = []
    if s2_id:
        recommendations = semantic_scholar_broker.get_recommendations(s2_id, limit=limit)
    elif doi:
        s2_paper = semantic_scholar_broker.get_paper(f"DOI:{doi}")
        if s2_paper and s2_paper.get("semantic_scholar_id"):
            recommendations = semantic_scholar_broker.get_recommendations(s2_paper["semantic_scholar_id"], limit=limit)

    if not recommendations:
        # Fallback to topic search if recommendations are unavailable
        return search_papers(paper.get("title", ""), limit=limit)

    # Persist and return
    results = []
    for rec in recommendations:
        saved = lakebase.upsert_paper(rec)
        results.append(saved)
    return results


def compare_papers(paper_ids: List[str]) -> dict:
    """
    Compare multiple papers side-by-side across key dimensions.
    """
    if not paper_ids or len(paper_ids) < 2:
        raise ValidationError("At least 2 paper IDs are required for comparison.")

    papers = []
    for pid in paper_ids:
        try:
            papers.append(get_paper_details(pid))
        except PaperNotFoundError:
            continue

    if len(papers) < 2:
        raise PaperNotFoundError("Could not find at least 2 valid papers to compare.")

    comparison = {
        "count": len(papers),
        "papers": [
            {
                "paper_id": p["paper_id"],
                "title": p["title"],
                "publication_year": p.get("publication_year"),
                "venue": p.get("venue"),
                "citation_count": p.get("citation_count", 0),
                "influence_score": p.get("influence_score"),
                "tldr": p.get("tldr") or (p.get("abstract", "")[:200] + "..."),
                "authors": [a.get("display_name") for a in p.get("authors", [])],
                "open_access_url": p.get("open_access_url"),
            }
            for p in papers
        ]
    }
    return comparison


def explain_topic(topic: str) -> dict:
    """
    Provide prerequisite topic explanations by retrieving/caching Wikipedia summaries.
    """
    if not topic or not topic.strip():
        raise ValidationError("Topic name cannot be empty.")

    clean_topic = topic.strip()
    
    # 1. Check Lakebase cache
    cached = lakebase.get_topic_context(clean_topic)
    if cached and cached.get("wikipedia_summary"):
        return {
            "topic": cached["topic_name"],
            "summary": cached["wikipedia_summary"],
            "wiki_url": cached.get("wiki_url"),
            "cached": True
        }

    # 2. Fetch from Wikipedia REST API
    wiki_data = wikipedia_broker.get_topic_summary(clean_topic)
    if not wiki_data or not wiki_data.get("wikipedia_summary"):
        # Try searching if exact title didn't resolve
        candidates = wikipedia_broker.search_topics(clean_topic, limit=1)
        if candidates:
            wiki_data = wikipedia_broker.get_topic_summary(candidates[0]["title"])

    if not wiki_data or not wiki_data.get("wikipedia_summary"):
        return {
            "topic": clean_topic,
            "summary": f"No formal Wikipedia summary found for topic '{clean_topic}'.",
            "wiki_url": None,
            "cached": False
        }

    # 3. Cache into Lakebase topic_context
    saved = lakebase.upsert_topic_context(
        topic_name=wiki_data["topic_name"],
        summary=wiki_data["wikipedia_summary"],
        wiki_url=wiki_data.get("wiki_url")
    )

    return {
        "topic": saved["topic_name"],
        "summary": saved["wikipedia_summary"],
        "wiki_url": saved.get("wiki_url"),
        "cached": False
    }
