"""
tests/test_provider_failover.py — more than one credential, and what a visitor sees.

A single credential is a single point of failure for the only part of this
application that cannot degrade gracefully. Revoke it, let it expire, exhaust
whatever the provider meters, or have the provider reject it for a reason of its
own, and every AI feature stops at once.

Two properties, and the second matters more than the first:

**Failover is for credentials, not for requests.** A credential that cannot
serve a request is worth replacing; a request that is simply wrong will be wrong
for every credential, and retrying it spends the next one to learn nothing.

**A visitor never reads a status code.** `LLM request failed (429): ...` told
somebody researching protein folding about HTTP semantics and gave them nothing
to do about it. The provider's own words still exist — in the log and in
`ai_operations`, where whoever runs the deployment can act on them.
"""

import httpx
import pytest

import llm_client
from exceptions import ExternalAPIError, LLMTimeoutError


class FakeResponse:
    def __init__(self, status_code: int, body=None, text: str = ""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def ok_body():
    return {"choices": [{"message": {"content": "an answer"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


@pytest.fixture
def three_keys(monkeypatch):
    monkeypatch.setattr(llm_client, "OPENROUTER_API_KEYS", ["key-a", "key-b", "key-c"])
    monkeypatch.setattr(llm_client, "_preferred", 0)
    return ["key-a", "key-b", "key-c"]


@pytest.fixture
def one_key(monkeypatch):
    monkeypatch.setattr(llm_client, "OPENROUTER_API_KEYS", ["only-key"])
    monkeypatch.setattr(llm_client, "_preferred", 0)
    return ["only-key"]


def responder(monkeypatch, by_credential):
    """Route each credential to a canned response, recording the order tried."""
    used = []

    async def fake_request(payload, timeout, credential):
        used.append(credential)
        outcome = by_credential[credential]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(llm_client, "_request_openrouter", fake_request)
    return used


# ---------------------------------------------------------------------------
# One credential behaves exactly as it always did
# ---------------------------------------------------------------------------

def test_a_single_credential_is_tried_once(one_key, monkeypatch):
    """
    The guarantee for every deployment that has one key, which is most of them
    including every self-hosted one with a paid key. Nothing to configure and
    nothing new to understand.
    """
    used = responder(monkeypatch, {"only-key": FakeResponse(200, ok_body())})

    llm_client._post({"messages": []})
    assert used == ["only-key"]


def test_a_single_credential_failing_does_not_retry_itself(one_key, monkeypatch):
    used = responder(monkeypatch, {"only-key": FakeResponse(429, {"error": "slow down"})})

    with pytest.raises(ExternalAPIError):
        llm_client._post({"messages": []})
    assert used == ["only-key"]


def test_no_credential_at_all_says_so_plainly(monkeypatch):
    monkeypatch.setattr(llm_client, "OPENROUTER_API_KEYS", [])

    assert llm_client.is_available() is False
    with pytest.raises(ExternalAPIError, match="No OpenRouter credential"):
        llm_client._post({"messages": []})


# ---------------------------------------------------------------------------
# Failover
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 402, 403, 429])
def test_an_unusable_credential_is_replaced(three_keys, monkeypatch, status):
    used = responder(monkeypatch, {
        "key-a": FakeResponse(status, {"error": "no"}),
        "key-b": FakeResponse(200, ok_body()),
        "key-c": FakeResponse(200, ok_body()),
    })

    llm_client._post({"messages": []})
    assert used == ["key-a", "key-b"]        # stopped as soon as one worked


@pytest.mark.parametrize("status", [400, 404, 422, 500])
def test_a_bad_request_is_not_retried_elsewhere(three_keys, monkeypatch, status):
    """
    The important half. A malformed request, or a model slug that no longer
    exists, fails identically for every credential — so trying the next one
    spends it to learn something already known.
    """
    used = responder(monkeypatch, {
        "key-a": FakeResponse(status, {"error": "bad request"}),
        "key-b": FakeResponse(200, ok_body()),
        "key-c": FakeResponse(200, ok_body()),
    })

    with pytest.raises(ExternalAPIError):
        llm_client._post({"messages": []})
    assert used == ["key-a"]


def test_every_credential_unusable_raises_once(three_keys, monkeypatch):
    used = responder(monkeypatch, {
        "key-a": FakeResponse(429, {"error": {"message": "rate limit"}}),
        "key-b": FakeResponse(429, {"error": {"message": "rate limit"}}),
        "key-c": FakeResponse(402, {"error": {"message": "no credit"}}),
    })

    with pytest.raises(ExternalAPIError) as raised:
        llm_client._post({"messages": []})

    assert used == ["key-a", "key-b", "key-c"]
    assert raised.value.status_code == 402          # the last one's status
    assert "no credit" in raised.value.detail


def test_a_timeout_does_not_burn_the_other_credentials(three_keys, monkeypatch):
    """
    A transport failure is not a credential's fault. Trying the next one would
    spend it to learn nothing and double a wait somebody is already enduring.
    """
    used = responder(monkeypatch, {
        "key-a": httpx.ConnectTimeout("too slow"),
        "key-b": FakeResponse(200, ok_body()),
        "key-c": FakeResponse(200, ok_body()),
    })

    with pytest.raises(LLMTimeoutError):
        llm_client._post({"messages": []})
    assert used == ["key-a"]


# ---------------------------------------------------------------------------
# Which credential is reached for next
# ---------------------------------------------------------------------------

def test_a_working_credential_keeps_being_used(three_keys, monkeypatch):
    """
    Otherwise every request would start at a credential already known to be
    unusable, paying one wasted call each time to rediscover it.
    """
    used = responder(monkeypatch, {
        "key-a": FakeResponse(429, {"error": "no"}),
        "key-b": FakeResponse(200, ok_body()),
        "key-c": FakeResponse(200, ok_body()),
    })

    llm_client._post({"messages": []})
    used.clear()
    llm_client._post({"messages": []})

    assert used == ["key-b"]                 # key-a is not tried again


def test_the_preference_is_not_persisted():
    """
    In-process on purpose. Provider limits reset on their own schedule, and a
    restart rediscovering a now-working credential is correct behaviour rather
    than a bug to engineer around.
    """
    import pathlib
    source = (pathlib.Path(llm_client.__file__)).read_text(encoding="utf-8")
    preference = source.split("_preferred = 0")[0][-600:]

    assert "lakebase" not in preference.lower()
    assert "localStorage" not in preference


# ---------------------------------------------------------------------------
# What a visitor is told
# ---------------------------------------------------------------------------

def test_a_visitor_never_reads_a_status_code(three_keys, monkeypatch):
    responder(monkeypatch, {k: FakeResponse(429, {"error": {"message": "rate limit exceeded"}})
                            for k in three_keys})

    with pytest.raises(ExternalAPIError) as raised:
        llm_client._post({"messages": []})

    message = str(raised.value)
    assert "429" not in message
    assert "rate limit" not in message.lower()
    assert "OpenRouter" not in message
    assert "try again" in message.lower()


def test_the_providers_own_words_survive_for_diagnosis(three_keys, monkeypatch):
    """
    "Rate limit exceeded" and "key disabled" need very different responses from
    whoever runs the deployment, and both look identical once flattened into a
    friendly sentence. The detail is kept beside it, not instead of it.
    """
    responder(monkeypatch, {k: FakeResponse(403, {"error": {"message": "key disabled"}})
                            for k in three_keys})

    with pytest.raises(ExternalAPIError) as raised:
        llm_client._post({"messages": []})

    assert raised.value.detail == "key disabled"
    assert raised.value.status_code == 403


def test_a_non_failover_rejection_is_also_friendly(three_keys, monkeypatch):
    responder(monkeypatch, {"key-a": FakeResponse(400, {"error": {"message": "bad model"}})})

    with pytest.raises(ExternalAPIError) as raised:
        llm_client._post({"messages": []})

    assert "400" not in str(raised.value)
    assert raised.value.status_code == 400
    assert "bad model" in raised.value.detail


def test_an_unreadable_body_still_produces_a_sentence(three_keys, monkeypatch):
    responder(monkeypatch, {k: FakeResponse(429, None, text="<html>nope</html>")
                            for k in three_keys})

    with pytest.raises(ExternalAPIError) as raised:
        llm_client._post({"messages": []})

    assert "try again" in str(raised.value).lower()
    assert "nope" in raised.value.detail


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_env(monkeypatch):
    """
    No real credentials. The developer's own `.env` defines these, and without
    clearing them a test about parsing asserts against whatever happens to be
    on the machine — and prints a live key into the failure diff when it is
    wrong.
    """
    monkeypatch.delenv("OPENROUTER_API_KEYS", raising=False)
    for index in range(1, 11):
        monkeypatch.delenv(f"OPENROUTER_API_KEY_{index}", raising=False)


def test_credentials_are_collected_from_every_spelling(monkeypatch, clean_env):
    import config

    monkeypatch.setenv("OPENROUTER_API_KEYS", "list-1, list-2")
    monkeypatch.setenv("OPENROUTER_API_KEY_1", "numbered-1")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "single")

    found = config._credentials()
    assert found == ["list-1", "list-2", "numbered-1", "single"]


def test_the_same_credential_twice_is_not_resilience(monkeypatch, clean_env):
    """
    Trying it again after it has just failed wastes a request to learn
    something already known.
    """
    import config

    monkeypatch.setenv("OPENROUTER_API_KEYS", "same, same")
    monkeypatch.setenv("OPENROUTER_API_KEY_1", "same")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "same")

    assert config._credentials() == ["same"]


def test_the_legacy_name_alone_still_works(monkeypatch, clean_env):
    import config

    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "just-the-one")

    assert config._credentials() == ["just-the-one"]
