"""
tests/test_search_modes.py — one box, three modes.

Ask used to be a second panel further down the page. On a phone it sat below six
suggestion chips and was reliably never seen, which is a strange fate for the
feature the product is named after.

It is the same gesture as the other two — you have a subject in mind and you
want the corpus consulted — so it belongs in the same control. What genuinely
differs is the SHAPE of the answer: Keyword and Semantic return a list of papers
to read, Ask returns prose with citations and spends a model call. That
difference is carried by the hint line, by what appears underneath, and by the
quota it draws on; not by a separate form.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
JS = (ROOT / "dashboard" / "static" / "js" / "search.js").read_text(encoding="utf-8")
CSS = (ROOT / "dashboard" / "static" / "css" / "search.css").read_text(encoding="utf-8")


def _page(client) -> str:
    return client.get("/search").get_data(as_text=True)


# ---------------------------------------------------------------------------
# One control
# ---------------------------------------------------------------------------

def test_all_three_modes_are_in_the_same_control(client, db):
    body = _page(client)
    modes = body.split('class="search-modes"')[1].split("</div>")[0]

    for value in ("keyword", "semantic", "ask"):
        assert f'value="{value}"' in modes


def test_there_is_no_longer_a_second_form(client, db):
    """
    The old rag-box carried its own textarea and submit. Two forms asking for
    the same thing in different words is what pushed one of them off-screen.
    """
    body = _page(client)
    assert 'id="rag-form"' not in body

    # Scoped to the answer panel: the notes composer in the shared chrome has a
    # textarea of its own, and a page-wide check would only ever find that.
    panel = body.split('id="rag-answer"')[1].split("</section>")[0]
    assert "<textarea" not in panel
    assert "<form" not in panel


def test_the_answer_panel_starts_hidden(client, db):
    body = _page(client)
    assert re.search(r'id="rag-answer"[^>]*hidden', body)


# ---------------------------------------------------------------------------
# What differs between them
# ---------------------------------------------------------------------------

def test_each_mode_says_what_it_does():
    """
    The hint is the only place the difference is stated now, so all three need
    one — and Ask's has to mention the cost, because spending a daily question
    is not what a search box usually does.
    """
    for mode in ("keyword", "semantic", "ask"):
        assert re.search(mode + r":\s*\{", JS)

    ask = JS.split("ask: {")[1].split("}")[0]
    assert "cites" in ask
    assert "daily" in ask


def test_the_button_says_ask_in_ask_mode():
    """"Search" over a box that writes you an essay is the wrong promise."""
    assert 'label: "Ask"' in JS


def test_only_ask_is_intercepted():
    """
    Keyword and Semantic stay ordinary GET submissions — the server renders
    them and the URL stays shareable. Hijacking all three would have cost that
    for no gain.
    """
    assert 'if (currentMode() !== "ask"' in JS
    assert "preventDefault" in JS


def test_switching_away_from_ask_clears_the_answer():
    """
    An answer produced by Ask, left on screen under a keyword search, reads as
    that search's result. It is not.
    """
    assert 'currentMode() !== "ask"' in JS
    assert "answerBox.hidden = true" in JS


# ---------------------------------------------------------------------------
# The server side
# ---------------------------------------------------------------------------

def test_asking_over_a_page_load_does_not_run_a_keyword_search(client, db):
    """
    `?mode=ask` arriving as a GET must not fall through to keyword search. That
    would answer a question nobody asked AND spend a search quota doing it.
    """
    body = client.get("/search?q=why+do+transformers+scale&mode=ask").get_data(as_text=True)
    assert "result" not in body.split("search-modes")[1][:400].lower()

    source = (ROOT / "dashboard" / "routes" / "search.py").read_text(encoding="utf-8")
    assert 'mode != "ask"' in source


def test_ask_is_absent_when_no_model_is_configured(client, db, monkeypatch):
    """
    A mode that cannot answer should not be offered. The other two work without
    a model and keep working.
    """
    from routes import search as search_routes
    monkeypatch.setattr(search_routes.llm_client, "is_available", lambda: False)

    body = _page(client)
    assert 'value="ask"' not in body
    assert 'value="keyword"' in body
    assert 'value="semantic"' in body


# ---------------------------------------------------------------------------
# The phone
# ---------------------------------------------------------------------------

def test_the_suggestions_stop_dominating_a_small_screen():
    """
    Six chips ran to six rows and pushed everything under them off the screen.
    Three is enough to show what a query looks like, which is all they are for.
    """
    assert "nth-child(n + 4)" in CSS


def test_the_bar_stacks_rather_than_squeezing():
    mobile = CSS.split("@media (max-width: 560px)")[1]
    assert "flex-direction: column" in mobile
    assert ".search-modes { width: 100%; }" in mobile
