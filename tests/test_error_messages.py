"""
tests/test_error_messages.py — what a visitor reads when something breaks.

Three rules, and the file is organised around them.

**No status codes.** `Request failed (503)` and `Internal server error.` tell
somebody researching protein folding about HTTP. The code still exists — on the
response, in the log, on the exception — it is simply never the thing displayed.

**No blame.** "Bad request" and "Access denied" are accurate and accusatory. A
person who typed a question and got a 500 did not cause it, and the sentence
should say so.

**Nothing diagnostic escapes.** Below 500 an exception's text was written for
the visitor and is shown. At 500 and above it was written for whoever has to fix
it — a driver error, a broker timeout, a failed assertion — and it goes to the
log instead.
"""

import pathlib
import re

import pytest

from exceptions import ExternalAPIError, ResearchCopilotError
from middleware import error_handler

ROOT = pathlib.Path(__file__).resolve().parents[1]
JS = ROOT / "dashboard" / "static" / "js"
XHR = {"X-Requested-With": "XMLHttpRequest"}

# Read rather than retyped: a copy here would keep passing after someone
# improved the wording, and the test would be asserting history.
FALLBACK = error_handler._FALLBACK_MESSAGE


# ---------------------------------------------------------------------------
# The gap that produced a bare white page
# ---------------------------------------------------------------------------

def test_an_unexpected_exception_still_gets_the_application_page(client, db, monkeypatch):
    """
    There was no handler for anything that was neither a domain error nor an
    HTTPException. Lakebase refusing a connection, a driver raising mid-query,
    any genuine bug — all of it bypassed error.html and reached the visitor as
    Werkzeug's own page: bare white, "Internal Server Error", no chrome, no way
    back.
    """
    from repositories import lakebase
    monkeypatch.setattr(lakebase, "get_dashboard_stats",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("connection refused")))

    response = client.get("/dashboard")

    assert response.status_code == 500
    body = response.get_data(as_text=True)
    assert "error-page" in body            # our template, not Werkzeug's
    assert "Back to dashboard" in body     # a way out


def test_an_unexpected_exception_never_shows_its_own_words(client, db, monkeypatch):
    from repositories import lakebase
    monkeypatch.setattr(lakebase, "get_dashboard_stats",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("psycopg2 OperationalError: no password supplied")))

    body = client.get("/dashboard").get_data(as_text=True)

    assert "psycopg2" not in body
    assert "OperationalError" not in body
    assert "no password supplied" not in body
    assert FALLBACK in body


def test_the_traceback_reaches_the_log(client, db, monkeypatch, caplog):
    """
    This is the handler that fires when something is genuinely wrong, and the
    stack is the only thing that will explain it.
    """
    from repositories import lakebase
    monkeypatch.setattr(lakebase, "get_dashboard_stats",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    client.get("/dashboard")

    assert "Unhandled RuntimeError" in caplog.text
    assert "boom" in caplog.text


def test_a_failing_fetch_gets_json_not_a_page(client, db, monkeypatch):
    """
    A fetch() handed an HTML error page cannot do anything useful with it — the
    caller is looking for a message to display, and gets markup.
    """
    from repositories import lakebase
    monkeypatch.setattr(lakebase, "get_dashboard_stats",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    response = client.get("/dashboard", headers=XHR)

    assert response.status_code == 500
    assert response.is_json
    assert response.get_json()["detail"] == FALLBACK
    assert "boom" not in response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Which exceptions speak for themselves
# ---------------------------------------------------------------------------

def test_a_validation_message_is_shown_because_it_was_written_for_the_reader(client, db):
    """
    Replacing it would lose the only thing that makes the refusal actionable.
    """
    client.get("/dashboard")
    response = client.post("/settings/profile",
                           data={"display_name": "x" * 200}, headers=XHR)

    assert response.status_code == 400
    assert "80 characters" in response.get_data(as_text=True)


def test_a_server_side_message_is_replaced_because_it_was_not(client, db, monkeypatch):
    from services import settings_service
    monkeypatch.setattr(settings_service, "update_display_name",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ResearchCopilotError("pool exhausted at repositories.lakebase:71")))

    client.get("/dashboard")
    response = client.post("/settings/profile", data={"display_name": "Andy"}, headers=XHR)

    body = response.get_data(as_text=True)
    assert response.status_code == 500
    assert "pool exhausted" not in body
    assert "repositories.lakebase" not in body


def test_an_upstream_failure_keeps_its_own_better_sentence(client, db, monkeypatch):
    """
    This asserted the opposite at first, and the opposite was wrong.

    `ExternalAPIError` separates its two audiences by construction: the message
    is "the research assistant is unavailable, try again in a few minutes" and
    the diagnostics live in `detail`. Flattening that to "something went wrong
    at our end" because the status happens to be 502 throws away the half a
    visitor can act on — whether waiting and retrying is worth doing.

    What stops this trusting a message it should not is
    test_every_upstream_failure_is_written_for_the_visitor below, which holds
    every raise site to the contract rather than cleaning up after it here.
    """
    from services import settings_service
    monkeypatch.setattr(settings_service, "update_display_name",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ExternalAPIError("The research assistant is unavailable right now.",
                                             status_code=502, detail="bad gateway")))

    client.get("/dashboard")
    body = client.post("/settings/profile", data={"display_name": "Andy"},
                       headers=XHR).get_data(as_text=True)

    assert "unavailable right now" in body
    assert "bad gateway" not in body          # detail is for the log
    assert "502" not in body


# Words that mean something to whoever runs the deployment and nothing to
# whoever asked a question. Each one has appeared in a visitor-facing message
# at some point in this file's history.
_OPERATOR_WORDS = (
    "HTTPError", "Traceback", "status_code", "psycopg", "OPENROUTER",
    "API_KEY", "credential", "endpoint", "catalog", "authenticate",
    "not configured", "failed:", "None", "null", "exception",
)


def test_every_upstream_failure_is_written_for_the_visitor():
    """
    The contract that lets the handlers trust an ExternalAPIError's message.

    Enforced at the raise sites, because that is where it can be kept. A
    handler cannot tell "the assistant is unavailable, try again shortly" from
    "LLM request failed: {'code': 429}" — both are strings — so a rule that
    tried to would either show the bad one or suppress the good one.
    """
    import ast

    offenders = []
    for path in sorted([*(ROOT / "dashboard").rglob("*.py"),
                        *(ROOT / "mcp_server").rglob("*.py")]):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
                continue
            if getattr(node.exc.func, "id", "") != "ExternalAPIError":
                continue
            if not node.exc.args:
                continue
            first = node.exc.args[0]
            # A named constant or a pass-through of a message raised deeper is
            # checked where it is defined, not here.
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            message = first.value
            for word in _OPERATOR_WORDS:
                if word.lower() in message.lower():
                    offenders.append(f"{path.name}:{node.lineno} {word!r} in {message!r}")

    assert not offenders, "messages written for an operator: " + "; ".join(offenders)


# ---------------------------------------------------------------------------
# The HTTP pages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/not-a-page", "/collections/not-a-real-id"])
def test_a_missing_page_says_what_to_do(client, db, path):
    body = client.get(path).get_data(as_text=True)
    assert "Internal server error" not in body
    assert "Bad request" not in body


def test_no_message_blames_the_visitor():
    """
    "Bad request." and "Access denied." are both accurate and both read as an
    accusation to somebody who typed a URL slightly wrong.
    """
    source = (ROOT / "dashboard" / "middleware"
              / "error_handler.py").read_text(encoding="utf-8")
    messages = source.split("_HTTP_MESSAGES = {")[1].split("}")[0]

    for accusatory in ("Bad request.", "Access denied.", "not authorised"):
        assert accusatory not in messages


def test_werkzeugs_own_wording_is_never_displayed():
    """
    `exc.description` is "The browser (or proxy) sent a request that this
    server could not understand" — implementation detail, and in the second
    person. Logged, never rendered.
    """
    source = (ROOT / "dashboard" / "middleware"
              / "error_handler.py").read_text(encoding="utf-8")
    handler = source.split("def handle_http_exception")[1]

    assert "exc.description" in handler            # still logged
    render = handler.split("render_template")[1][:200]
    assert "description" not in render


# ---------------------------------------------------------------------------
# The browser side
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["main.js", "chat.js", "conversations.js", "settings.js"])
def test_no_script_displays_a_status_code(name):
    """
    `"Request failed (" + res.status + ")"` was the fallback in three of these.
    It is the easiest thing to reach for when a response carries no explanation,
    and it is the server's vocabulary.
    """
    source = (JS / name).read_text(encoding="utf-8")
    code = re.sub(r"//.*|/\*[\s\S]*?\*/", "", source)

    assert "res.status + \")\"" not in code
    assert "response.status + \")\"" not in code


def test_the_servers_explanation_is_preferred_when_there_is_one():
    """
    The fallback is for a response that carried nothing. An endpoint that did
    explain itself should have its own words shown.
    """
    source = (JS / "main.js").read_text(encoding="utf-8")
    assert "body.detail || body.error || body.message" in source


def test_the_status_still_travels_for_code_that_needs_it():
    """
    Not displayed is not the same as not available. A caller that must BEHAVE
    differently on a 403 still can.
    """
    source = (JS / "main.js").read_text(encoding="utf-8")
    assert "err.status = res.status" in source


# ---------------------------------------------------------------------------
# The paths that never reach a handler
# ---------------------------------------------------------------------------
#
# Both of these catch exceptions somewhere the error handlers cannot see them —
# a worker thread serving a streamed agent turn, and a route that catches
# broadly in order to flash and redirect. Each used `str(exc)`, so the rule
# above simply did not apply to them.

def test_the_shared_rule_shows_a_refusal_written_for_the_visitor():
    from exceptions import QuotaExceededError

    exc = QuotaExceededError("You have used your research answers for today.",
                             metric="agent_query", scope="user", used=5, limit=5)
    assert error_handler.visitor_message(exc) == str(exc)


def test_the_shared_rule_hides_a_message_written_for_an_operator():
    assert error_handler.visitor_message(
        ResearchCopilotError("pool exhausted at repositories.lakebase:71")) == FALLBACK


def test_the_shared_rule_hides_an_exception_it_knows_nothing_about():
    """
    The account route catches a bare Exception. Anything unrecognised is, by
    definition, not something whose text was written for a reader.
    """
    assert error_handler.visitor_message(KeyError("user_id")) == FALLBACK


def test_the_agent_worker_thread_uses_the_shared_rule():
    """
    A streamed turn collects its failure into `outcome["error"]`, which becomes
    the chat bubble. Nothing between there and the browser applies the 500 rule.
    """
    source = (ROOT / "dashboard" / "routes" / "chat.py").read_text(encoding="utf-8")

    assert 'outcome["error"] = str(exc)' not in source
    assert 'outcome["error"] = visitor_message(exc)' in source


def test_account_deletion_does_not_flash_whatever_it_caught():
    source = (ROOT / "dashboard" / "routes" / "account.py").read_text(encoding="utf-8")

    assert "flash_message=str(exc)" not in source
    assert "flash_message=visitor_message(exc)" in source


# ---------------------------------------------------------------------------
# Upstream wording
# ---------------------------------------------------------------------------

def test_the_providers_error_body_is_not_the_visitors_sentence(monkeypatch):
    """
    A 200 response carrying an error body. `LLM request failed: {...}` put the
    provider's own JSON into a chat bubble.
    """
    import llm_client

    source = pathlib.Path(llm_client.__file__).read_text(encoding="utf-8")
    assert 'ExternalAPIError(f"LLM request failed' not in source
    assert "raise ExternalAPIError(_UNAVAILABLE_MESSAGE, detail=message[:200])" in source


def test_a_misconfigured_deployment_is_not_the_visitors_problem():
    """
    "OPENROUTER_API_KEY is not configured" names an environment variable to
    somebody who cannot set it. The operator needs those words; they belong in
    the log and in `detail`.
    """
    import llm_client

    source = pathlib.Path(llm_client.__file__).read_text(encoding="utf-8")
    for operator_words in ('ExternalAPIError("OPENROUTER_API_KEY',
                           'ExternalAPIError("No OpenRouter credential',
                           'ExternalAPIError("LLM returned an empty answer'):
        assert operator_words not in source


def test_an_unexpected_response_shape_raises_the_intended_error(monkeypatch):
    """
    This raise referenced `resp.status_code`, and `resp` was not in scope —
    `_post` returns the parsed body. So an unreadable response shape raised
    NameError from inside the except block instead of the ExternalAPIError the
    code was written to raise.
    """
    import llm_client
    from exceptions import ExternalAPIError

    monkeypatch.setattr(llm_client, "_post", lambda *a, **k: {"choices": []})

    with pytest.raises(ExternalAPIError) as raised:
        llm_client.chat_with_tools([{"role": "user", "content": "hi"}], tools=[])

    assert "choices" in raised.value.detail


def test_a_failed_tool_does_not_print_its_output_into_the_trace():
    """
    chat.js renders `event.error` into a chat-step-error span verbatim. A tool's
    own failure text is a stack trace or a provider status as often as it is a
    sentence.
    """
    source = (ROOT / "dashboard" / "services"
              / "mcp_client.py").read_text(encoding="utf-8")

    assert 'f"Tool \'{name}\' failed: {_text(result)}"' not in source
    assert "That step could not be completed." in source


def test_the_discovery_layer_keeps_writing_for_the_visitor():
    """
    workspace_paper_service passes the domain layer's ExternalAPIError text
    straight through, which is correct only while that text stays human. This
    is the contract that makes the pass-through safe, so it is asserted rather
    than assumed.
    """
    import ast

    path = (ROOT / "mcp_server" / "shared_resource" / "services"
            / "paper_discovery_service.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))

    messages = [node.exc.args[0].value
                for node in ast.walk(tree)
                if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                and getattr(node.exc.func, "id", "") == "ExternalAPIError"
                and node.exc.args and isinstance(node.exc.args[0], ast.Constant)]

    assert messages, "no ExternalAPIError messages found to check"
    for message in messages:
        assert message[0].isupper() and message.rstrip().endswith("."), message
        for jargon in ("Error", "Exception", "None", "null", "status", "HTTP"):
            assert jargon not in message, f"{jargon!r} in {message!r}"
