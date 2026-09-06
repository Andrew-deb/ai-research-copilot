"""
tests/test_section_extraction.py — the Phase 2 PDF heading parser.

Why these tests exist
---------------------
Section extraction is the only place in Phase 2 where a *wrong* answer looks
exactly like a right one. A regex that matches too eagerly silently indexes the
References list as a "Conclusion"; one that matches too narrowly silently indexes
nothing and reports `no_sections`. Both produce a corpus that loads, searches, and
returns plausible-looking rubbish — the same failure shape as the unprefixed query
in Phase 1 and the null broker field before it.

How it is loaded
----------------
The parser lives inside `notebooks/ingest_papers_embeddings.py` because that file
is a Databricks notebook and has to run standalone — a sibling-module import would
add a deployment dependency for no benefit. So these tests lift the pure functions
out of the notebook source with `ast` and exec only those. Nothing else in the
notebook runs: no widgets, no `%pip`, no database.
"""

import ast
import pathlib

import pytest

NOTEBOOK = (pathlib.Path(__file__).resolve().parents[1]
            / "notebooks" / "ingest_papers_embeddings.py")

# The pure, side-effect-free pieces of the extraction cell.
_WANTED_NAMES = {
    "SECTION_SYNONYMS", "STOP_HEADINGS", "_HEADING_RE",
    "_clean_pdf_text", "_canonical_heading", "extract_sections",
}


def _load_parser():
    """Exec only the parser definitions from the notebook, nothing else."""
    tree = ast.parse(NOTEBOOK.read_text(encoding="utf-8"))
    keep = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _WANTED_NAMES:
            keep.append(node)
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & _WANTED_NAMES:
                keep.append(node)

    # One dict for globals *and* locals: the extracted functions call each other
    # and read module-level constants, which only resolve through globals.
    namespace: dict = {"re": __import__("re"), "unicodedata": __import__("unicodedata")}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(NOTEBOOK), "exec"), namespace)

    missing = _WANTED_NAMES - namespace.keys()
    assert not missing, f"notebook no longer defines {missing} — update this loader"
    return namespace


@pytest.fixture(scope="module")
def parser():
    return _load_parser()


# A miniature paper with the shape real ones have: numbered headings, an unwanted
# section between two wanted ones, and a References list at the end.
PAPER = """\
Attention Is Cheap: Linear Transformers Revisited

Abstract
We revisit linear attention and show it is competitive.

1. Introduction
Transformers are expensive. Prior work has attacked this from several angles and
we build on that line of research in the sections that follow.

2. Related Work
Vaswani et al. proposed the original architecture. Others extended it.

3. Methods
We train on a corpus of 40B tokens using a linear attention kernel with a decay
factor, and we evaluate on eight downstream benchmarks with three random seeds.

4. Results and Discussion
Linear attention matches softmax attention within one point on six of eight tasks.
The gap widens on long-context retrieval, which we attribute to the decay factor.

5. Conclusions
Linear attention is a practical default below 4k context. Beyond that, the decay
factor costs more than it saves and softmax attention remains preferable.

References
[1] Vaswani et al. Attention Is All You Need. 2017.
[2] Katharopoulos et al. Transformers are RNNs. 2020.
"""

ALL = ["conclusion", "discussion", "methods"]


# ---------------------------------------------------------------------------
# Heading recognition
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("line,expected", [
    ("Conclusion", "conclusion"),
    ("Conclusions", "conclusion"),
    ("CONCLUSIONS", "conclusion"),
    ("Concluding Remarks", "conclusion"),
    ("Discussion", "discussion"),
    ("Results and Discussion", "discussion"),
    ("Methods", "methods"),
    ("Materials and Methods", "methods"),
    ("Methodology", "methods"),
    ("References", "__stop__"),
    ("Bibliography", "__stop__"),
    ("Acknowledgements", "__stop__"),
    ("Introduction", None),
    ("Related Work", None),
    ("", None),
])
def test_heading_is_mapped_to_a_canonical_name(parser, line, expected):
    assert parser["_canonical_heading"](line) == expected


def test_numbering_and_punctuation_are_stripped(parser):
    for variant in ("3. Methods", "3 Methods", "III. Methods", "3.1 Methods", "Methods:"):
        matched = parser["_HEADING_RE"].match(variant.strip())
        assert matched, f"{variant!r} was not recognised as a heading line"
        assert parser["_canonical_heading"](matched.group(1)) == "methods", variant


# ---------------------------------------------------------------------------
# Slicing
# ---------------------------------------------------------------------------

def test_wanted_sections_are_extracted(parser):
    got = parser["extract_sections"](PAPER, ALL)
    assert set(got) == {"methods", "discussion", "conclusion"}


def test_section_stops_at_the_next_heading(parser):
    """
    The bug this guards: a section that runs to the end of the document. Methods
    must not swallow Discussion, or every chunk becomes the whole paper.
    """
    got = parser["extract_sections"](PAPER, ALL)
    assert "40B tokens" in got["methods"]
    assert "Linear attention matches softmax" not in got["methods"]


def test_unwanted_sections_still_act_as_boundaries(parser):
    """
    Related Work is not extracted, but it must still terminate whatever precedes
    it — otherwise an unwanted section is silently appended to a wanted one.
    """
    got = parser["extract_sections"](PAPER, ["methods"])
    assert "Vaswani et al. proposed" not in got["methods"]


def test_references_are_never_included(parser):
    got = parser["extract_sections"](PAPER, ALL)
    assert "Attention Is All You Need" not in got["conclusion"]
    assert "practical default below 4k" in got["conclusion"]


def test_only_requested_sections_are_returned(parser):
    got = parser["extract_sections"](PAPER, ["conclusion"])
    assert set(got) == {"conclusion"}


def test_repeated_heading_keeps_the_longest_body(parser):
    """Per-experiment 'Methods' headings are common; the fullest one wins."""
    text = "1. Methods\nShort.\n\n2. Results and Discussion\nX.\n\n3. Methods\n" + ("Long. " * 40)
    got = parser["extract_sections"](text, ["methods"])
    assert got["methods"].startswith("Long.")


def test_no_headings_returns_empty_not_an_error(parser):
    """A scanned or unstructured PDF is `no_sections`, a recorded status — not a crash."""
    assert parser["extract_sections"]("Just a wall of prose with no headings.", ALL) == {}


def test_prose_mentioning_a_section_name_is_not_a_heading(parser):
    """
    'as we discuss in the conclusion below' sits mid-sentence. Only a line that is
    *entirely* a heading may open a section.
    """
    text = "We show, as noted in the conclusion of prior work, that decay factors matter."
    assert parser["extract_sections"](text, ALL) == {}


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

def test_hyphenated_line_breaks_are_rejoined(parser):
    """PDF text layers break words across lines; 'atten-\\ntion' must become one token."""
    assert "attention" in parser["_clean_pdf_text"]("linear atten-\ntion kernels")


def test_soft_hyphens_are_removed(parser):
    assert parser["_clean_pdf_text"]("trans­former") == "transformer"


def test_ligatures_are_normalised(parser):
    # NFKC folds the 'fi' ligature that PDF fonts emit, so search matches "efficient".
    assert parser["_clean_pdf_text"]("eﬃcient") == "efficient"


def test_runaway_whitespace_is_collapsed(parser):
    assert parser["_clean_pdf_text"]("a     b\n\n\n\n\nc") == "a b\n\nc"
