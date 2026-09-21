"""
dashboard/suggestions.py — what to offer someone facing an empty input.

One module because the same mistake keeps appearing in two places otherwise:
prompts drift apart between the landing page and the chat page, and a search
page ends up suggesting agent questions or the reverse. They are not
interchangeable, and the difference is the point:

    agent prompts    need several papers read and set against each other -
                     compare, trace, sequence. A single retrieval pass cannot
                     answer them, which is exactly why they belong on the
                     surfaces that sell the assistant.

    search queries   name a subject. Retrieval matches passages, so a phrase
                     works better here than a question, and suggesting
                     "compare X and Y" would advertise something search cannot
                     do.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

# Shown under the composer on the landing page. Written out in full: this is a
# visitor's first sight of the product, and the questions have to carry both the
# subject matter and the depth available.
AGENT_LANDING: list[str] = [
    "Compare the main approaches to retrieval-augmented generation and where they disagree",
    "What limitations do authors repeatedly report with LLM agents and tool use?",
    "Trace how the evaluation of human feedback has changed over time",
    "Build me a reading path into indexing dense vectors at scale",
]

# Shown on /chat, where the composer is already understood and the chips are a
# shortcut rather than an explanation - so they are labelled short and carry the
# full question behind them.
AGENT_STARTERS: list[dict[str, str]] = [
    {"label": "Main approaches to RAG",
     "prompt": "Compare the main approaches to retrieval-augmented generation and where they disagree"},
    {"label": "Limitations of LLM agents",
     "prompt": "What limitations do authors repeatedly report with LLM agents and tool use?"},
    {"label": "Comparing RLHF evaluations",
     "prompt": "Trace how the evaluation of human feedback has changed over time"},
]


def agent_landing_starters() -> list[dict[str, str]]:
    """The landing prompts in the shape the shared composer expects."""
    return [{"label": p, "prompt": p} for p in AGENT_LANDING]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

# Subjects rather than questions, and drawn from what the corpus actually holds.
# Suggesting a topic the corpus cannot answer teaches a visitor the wrong thing
# about its scope on their very first attempt.
SEARCH_CURATED: list[str] = [
    "retrieval-augmented generation",
    "reinforcement learning from human feedback",
    "dense passage retrieval",
    "chain-of-thought prompting",
    "vector index compression",
    "tool use in language models",
]


def search_suggestions(user_id: str | None) -> dict:
    """
    What to show under an empty search box.

    `user_id` is accepted and ignored, and that is the seam. Recent-search
    suggestions need a history table, a write on every semantic search and a
    retention decision - none of which exist yet - so this returns curated
    subjects for everyone today. When history lands, only this function changes:
    it reads the visitor's recent queries, falls back to the curated list when
    they have too few, and the template already renders whichever it is given.

    Returning the source alongside the items is what lets the page label them
    honestly. "Recent searches" over a curated list would be a small lie that
    nobody could act on.
    """
    return {"source": "curated", "items": list(SEARCH_CURATED)}
