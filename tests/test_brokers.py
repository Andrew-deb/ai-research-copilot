"""
tests/test_brokers.py — regression tests for the broker standardizers.

Guards the bug that broke `search_papers` in the deployed agent:

    Error executing tool search_papers: 'NoneType' object has no attribute 'replace'

Root cause: `d.get(key, default)` returns the *stored* value whenever the key
exists — even when that value is None. The default only fires when the key is
*absent*. OpenAlex uses explicit JSON nulls, so every `.get(k, "")` followed by
`.replace(...)` was a latent crash.

The field that actually fired in production was `authorships[].author.id`:
OpenAlex returns an author object with `"id": null` for unmatched authors, and
all three of the agent's queries had 1-3 such authorships. `doi` is the same
trap on preprints/theses. Because the whole result set was standardized in one
list comprehension, a single bad record failed the entire search.

The assertions live in `tests/_broker_checks.py` and run in a subprocess with
`mcp_server/` as the import root: both apps use flat imports, so their top-level
module names collide and cannot share one interpreter.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
MCP_DIR = PROJECT_ROOT / "mcp_server"
CHECKS = pathlib.Path(__file__).parent / "_broker_checks.py"


@pytest.fixture(scope="module")
def check_results() -> dict[str, str]:
    """Run every check once inside mcp_server/ and return {check_name: "ok" | traceback}."""
    env = {**os.environ, "PYTHONPATH": str(MCP_DIR), "PYTHONUNBUFFERED": "1"}
    proc = subprocess.run(
        [sys.executable, str(CHECKS)],
        cwd=str(MCP_DIR), env=env, capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        pytest.fail(f"broker checks failed to run (rc={proc.returncode})\n"
                    f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
    return json.loads(proc.stdout)


def _assert_ok(results: dict[str, str], name: str) -> None:
    assert name in results, f"check {name!r} did not run; got {sorted(results)}"
    assert results[name] == "ok", f"{name}:\n{results[name]}"


# --- the production regression ---------------------------------------------

def test_doi_explicit_null_does_not_crash(check_results):
    _assert_ok(check_results, "check_doi_explicit_null_does_not_crash")


def test_id_explicit_null_does_not_crash(check_results):
    _assert_ok(check_results, "check_id_explicit_null_does_not_crash")


def test_batch_survives_one_unparseable_record(check_results):
    _assert_ok(check_results, "check_batch_survives_one_unparseable_record")


# --- everything else --------------------------------------------------------

@pytest.mark.parametrize("check", [
    "check_doi_prefix_stripped",
    "check_id_prefix_stripped",
    "check_author_id_null_keeps_the_author",
    "check_author_object_null_is_skipped",
    "check_author_without_name_is_skipped",
    "check_citation_count_null_becomes_zero",
    "check_title_null_becomes_empty_string",
    "check_missing_abstract_is_none",
    "check_all_optional_fields_null",
    "check_batch_of_only_bad_records_returns_empty",
    "check_strip_prefix_is_null_safe",
    "check_s2_citation_count_null_becomes_zero",
    "check_s2_unnamed_author_is_dropped",
    "check_s2_all_nulls_does_not_crash",
    "check_wikipedia_title_null_becomes_empty_string",
])
def test_standardizer_edge_case(check_results, check):
    _assert_ok(check_results, check)


def test_no_get_with_literal_default_in_brokers():
    """
    Static guard for the root cause: `d.get(k, default)` returns the *stored*
    value when the key exists, even if that value is None — so it is the wrong
    tool for JSON that carries explicit nulls. Use `d.get(k) or default`.
    """
    import re
    pattern = re.compile(r"""\.get\(\s*["'][A-Za-z_]+["']\s*,\s*[^)]""")
    offenders = []
    for path in sorted(MCP_DIR.glob("brokers/*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Use `d.get(k) or default` — `.get(k, default)` does not protect against "
        "an explicit JSON null:\n  " + "\n  ".join(offenders)
    )
