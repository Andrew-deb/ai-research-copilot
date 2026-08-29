"""dashboard/routes/search.py — Search, semantic retrieval, RAG, and paper detail."""

from flask import Blueprint, jsonify, render_template, request

from dashboard import llm_client
from dashboard.middleware.auth import current_user_id
from dashboard.routes.helpers import form_or_json
from dashboard.services import search_service

bp = Blueprint("search", __name__)


@bp.get("/search")
def search_page():
    """Keyword or semantic search results, server-rendered."""
    query = (request.args.get("q") or "").strip()
    mode = request.args.get("mode", "keyword")
    page = request.args.get("page", 1, type=int)

    results = None
    if query:
        if mode == "semantic":
            results = search_service.semantic_search(query, top_k=20)
        else:
            results = search_service.keyword_search(query, page=page)

    return render_template("search.html", query=query, mode=mode, results=results,
                           rag_available=llm_client.is_available())


@bp.get("/search/semantic")
def semantic_json():
    """JSON semantic results — used for the live 'search as you type' panel."""
    query = (request.args.get("q") or "").strip()
    top_k = request.args.get("top_k", 10, type=int)
    return jsonify(search_service.semantic_search(query, top_k=top_k))


@bp.post("/search/ask")
def rag_ask():
    """JSON RAG answer — vector retrieval + cited LLM synthesis."""
    data = form_or_json("question")
    return jsonify(search_service.rag_answer(data["question"]))


@bp.get("/paper/<paper_id>")
def paper_detail(paper_id: str):
    detail = search_service.get_paper_detail(current_user_id(), paper_id)
    return render_template("paper_detail.html", **detail)
