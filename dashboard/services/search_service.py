"""
dashboard/services/search_service.py — Paper search, semantic retrieval, and RAG.

Three retrieval modes over one catalog:
  1. keyword_search  — Postgres ILIKE, paginated, for exact-term lookups.
  2. semantic_paper_matches — pgvector cosine search, chunk rows folded back to
     one row per paper (best-matching chunk wins).
  3. rag_answer — semantic retrieval + OpenRouter synthesis with inline citations.

Also assembles the paper detail page (metadata + authors + notes + reading
status + vector-similar papers).
"""

import logging

from dashboard import embedding, llm_client
from exceptions import PaperNotFoundError, ValidationError
from repositories import lakebase

logger = logging.getLogger(__name__)

_DEFAULT_PAGE_SIZE = 20
_RAG_SYSTEM_PROMPT = (
    "You are a research assistant. Answer the user's question using ONLY the numbered "
    "sources provided. Cite claims inline as [1], [2], etc. matching the source numbers. "
    "If the sources do not contain the answer, say so plainly. Never invent papers, "
    "authors, or findings that are not in the sources. Keep the answer under 200 words."
)


# =============================================================================
# Keyword search
# =============================================================================

def keyword_search(query: str, page: int = 1, page_size: int = _DEFAULT_PAGE_SIZE) -> dict:
    """Paginated ILIKE search over title + abstract."""
    if not query or not query.strip():
        raise ValidationError("Search query cannot be empty.")

    page = max(page, 1)
    offset = (page - 1) * page_size
    # Fetch one extra row to know whether a next page exists.
    rows = lakebase.search_papers_by_text(query.strip(), limit=page_size + 1, offset=offset)
    has_next = len(rows) > page_size

    return {
        "query": query.strip(),
        "mode": "keyword",
        "page": page,
        "has_next": has_next,
        "has_prev": page > 1,
        "results": rows[:page_size],
    }


# =============================================================================
# Semantic search (pgvector)
# =============================================================================

def semantic_paper_matches(query: str, top_k: int = 10, min_similarity: float = 0.0) -> list[dict]:
    """
    Embed the query, cosine-search paper_embeddings, and fold chunk rows back to
    one row per paper keeping the highest-similarity chunk as the snippet.
    """
    if not query or not query.strip():
        raise ValidationError("Search query cannot be empty.")

    query_vector = embedding.encode_query(query)
    # Over-fetch chunks because several may belong to the same paper.
    chunk_rows = lakebase.semantic_search_papers(query_vector, top_k=top_k * 3)

    best_by_paper: dict[str, dict] = {}
    for row in chunk_rows:
        similarity = float(row.get("similarity") or 0.0)
        if similarity < min_similarity:
            continue
        paper_id = str(row["paper_id"])
        existing = best_by_paper.get(paper_id)
        if existing is None or similarity > existing["similarity"]:
            paper = {k: v for k, v in row.items() if k not in ("chunk_text", "chunk_index", "similarity")}
            paper["similarity"] = round(similarity, 4)
            paper["snippet"] = row.get("chunk_text")
            best_by_paper[paper_id] = paper

    ranked = sorted(best_by_paper.values(), key=lambda p: p["similarity"], reverse=True)
    return ranked[:top_k]


def semantic_search(query: str, top_k: int = 10) -> dict:
    """Semantic search wrapped in the same envelope keyword_search returns."""
    return {
        "query": query.strip(),
        "mode": "semantic",
        "page": 1,
        "has_next": False,
        "has_prev": False,
        "results": semantic_paper_matches(query, top_k=top_k),
    }


# =============================================================================
# RAG — retrieval-augmented synthesis
# =============================================================================

def rag_answer(query: str, top_k: int = 6) -> dict:
    """Vector-retrieve supporting chunks, then ask the LLM to synthesise a cited answer."""
    if not query or not query.strip():
        raise ValidationError("Question cannot be empty.")

    matches = semantic_paper_matches(query, top_k=top_k)
    if not matches:
        return {"query": query.strip(), "answer": None, "sources": [],
                "message": "No papers in the catalog matched that question yet."}

    context_blocks = []
    sources = []
    for i, paper in enumerate(matches, start=1):
        year = paper.get("publication_year") or "n.d."
        context_blocks.append(
            f"[{i}] {paper['title']} ({year}). {paper.get('snippet') or paper.get('abstract') or ''}"
        )
        sources.append({
            "number": i,
            "paper_id": str(paper["paper_id"]),
            "title": paper["title"],
            "publication_year": paper.get("publication_year"),
            "venue": paper.get("venue"),
            "similarity": paper.get("similarity"),
        })

    user_prompt = f"Question: {query.strip()}\n\nSources:\n" + "\n\n".join(context_blocks)
    answer = llm_client.chat(_RAG_SYSTEM_PROMPT, user_prompt)

    return {"query": query.strip(), "answer": answer, "sources": sources}


# =============================================================================
# Paper detail page
# =============================================================================

def get_paper_detail(user_id: str, paper_id: str) -> dict:
    """Metadata + authors + this user's notes + reading status + similar papers."""
    paper = lakebase.get_paper(paper_id)
    if not paper:
        raise PaperNotFoundError(f"Paper '{paper_id}' not found.")

    progress = lakebase.get_progress_for_paper(user_id, paper_id)

    related: list[dict] = []
    try:
        related = [
            p for p in semantic_paper_matches(paper["title"], top_k=6)
            if str(p["paper_id"]) != str(paper_id)
        ][:5]
    except Exception as exc:  # similarity is a nice-to-have, never blocks the page
        logger.debug("Related-papers lookup failed for %s: %s", paper_id, exc)

    return {
        "paper": paper,
        "authors": lakebase.get_authors_for_paper(paper_id),
        "notes": lakebase.get_notes_for_paper(user_id, paper_id),
        "reading_status": progress["status"] if progress else "not_started",
        "related_papers": related,
        "collections": lakebase.get_collections(user_id),
        "reading_statuses": ["not_started", "reading", "completed", "skipped"],
        "rag_available": llm_client.is_available(),
    }
