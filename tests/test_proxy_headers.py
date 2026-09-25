"""
tests/test_proxy_headers.py — the app's idea of its own address.

Render terminates TLS and forwards plain HTTP, so the app sees an `http` request
from a visitor who is on `https`. Exactly one line cares, and it is the one that
matters most:

    routes/auth.py:120   redirect_uri = url_for("auth.google_callback", _external=True)

Built as `http://`, that is an address Google refuses to register for any host but
localhost — so Google sign-in works perfectly on a laptop and fails on Render
with `redirect_uri_mismatch`. The bug cannot appear in development, which is
exactly why it needs a test rather than a troubleshooting note.

ProxyFix is applied in every environment and `TRUSTED_PROXY_HOPS` decides how
much it believes, so these tests drive the same code path production runs.
"""

import importlib

import pytest
from werkzeug.middleware.proxy_fix import ProxyFix

FORWARDED = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "copilot.example.com"}


def _app_with_hops(monkeypatch, hops: int):
    """A freshly built app at a given hop count. The wrapper is applied in the
    factory, so the value has to be in place before the app is made."""
    import config

    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", hops)
    app_module = importlib.import_module("app")
    monkeypatch.setattr(app_module, "TRUSTED_PROXY_HOPS", hops)

    app = app_module.create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


def _callback_url(app) -> str:
    """
    What the app would hand Google as its return address.

    Fetched through the test client rather than a test request context, because
    ProxyFix is WSGI middleware: a request context builds the environ directly
    and never passes through it. Testing that way would assert nothing about the
    thing under test - it would pass whether or not the wrapper existed.
    """
    from flask import url_for

    @app.get("/__probe_callback")
    def probe():
        return url_for("auth.google_callback", _external=True)

    return app.test_client().get("/__probe_callback",
                                 headers=FORWARDED).get_data(as_text=True)


# ---------------------------------------------------------------------------
# Behind a proxy
# ---------------------------------------------------------------------------

def test_the_callback_is_https_behind_the_proxy(db, monkeypatch):
    """
    The deployed case. Render says `X-Forwarded-Proto: https`; the app has to
    believe it, or Google rejects the address it sends.
    """
    app = _app_with_hops(monkeypatch, 1)
    assert _callback_url(app).startswith("https://")


def test_the_callback_uses_the_public_hostname(db, monkeypatch):
    """The visitor's hostname, not the internal one the proxy dialled."""
    app = _app_with_hops(monkeypatch, 1)
    assert "copilot.example.com" in _callback_url(app)


# ---------------------------------------------------------------------------
# With nothing in front
# ---------------------------------------------------------------------------

def test_an_unproxied_app_ignores_the_headers(db, monkeypatch):
    """
    At zero hops the headers are somebody's claim, not a proxy's report. Anyone
    can send them, so the app must not act on them.
    """
    app = _app_with_hops(monkeypatch, 0)
    url = _callback_url(app)

    assert url.startswith("http://")
    assert "copilot.example.com" not in url


def test_the_wrapper_is_applied_either_way(db, monkeypatch):
    """
    The point of the whole design: one code path. A production-only branch means
    the request handling exercised in development is not the one that ships,
    which is how a bug that only appears behind TLS survives every local test.
    """
    for hops in (0, 1):
        app = _app_with_hops(monkeypatch, hops)
        assert isinstance(app.wsgi_app, ProxyFix), hops


# ---------------------------------------------------------------------------
# The count is a fact about the deployment
# ---------------------------------------------------------------------------

def test_nothing_is_trusted_by_default():
    """
    A developer machine has no proxy. Defaulting to 1 would trust headers any
    local process could set, and would make the default a guess rather than a
    fact.
    """
    import os
    monkey = os.environ.pop("TRUSTED_PROXY_HOPS", None)
    try:
        import config
        importlib.reload(config)
        assert config.TRUSTED_PROXY_HOPS == 0
    finally:
        if monkey is not None:
            os.environ["TRUSTED_PROXY_HOPS"] = monkey
        importlib.reload(config)


def test_every_forwarded_header_is_named_explicitly():
    """
    ProxyFix defaults x_for and x_proto to 1. Passing only the ones you happen to
    be thinking about leaves the others silently trusted, so the call names all
    five.
    """
    import pathlib
    source = (pathlib.Path(__file__).resolve().parents[1] / "dashboard"
              / "app.py").read_text(encoding="utf-8")
    call = source.split("ProxyFix(")[1].split(")")[0]

    for knob in ("x_for", "x_proto", "x_host", "x_port", "x_prefix"):
        assert knob in call, knob


def test_the_deployment_declares_one_hop():
    """Render puts exactly one proxy in front. Naming it here is what makes the
    deployed app build an https callback."""
    import io
    import pathlib

    import yaml

    path = pathlib.Path(__file__).resolve().parents[1] / "render.yaml"
    service = yaml.safe_load(io.open(path, encoding="utf-8").read())["services"][0]
    env = {e["key"]: e.get("value") for e in service["envVars"]}

    assert env["TRUSTED_PROXY_HOPS"] == "1"
