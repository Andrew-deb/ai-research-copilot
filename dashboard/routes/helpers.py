"""
dashboard/routes/helpers.py — Shared request/response helpers for route modules.

Kept separate from routes/__init__.py to avoid a circular import (the package
__init__ imports the route modules, which import these helpers).
"""

from flask import flash, jsonify, redirect, request


def wants_json() -> bool:
    """True for fetch()/XHR callers — they get JSON; browsers get a redirect + flash."""
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


def form_or_json(*keys: str) -> dict:
    """Pull the named fields from either a JSON body or a classic form post."""
    src = request.get_json(silent=True) or request.form
    return {key: (src.get(key) or "").strip() if isinstance(src.get(key), str) else src.get(key)
            for key in keys}


def action_response(payload: dict, *, redirect_to: str, flash_message: str | None = None,
                    flash_category: str = "success"):
    """Return JSON for XHR callers, or flash + redirect for a plain form submit."""
    if wants_json():
        return jsonify(payload)
    if flash_message:
        flash(flash_message, flash_category)
    return redirect(redirect_to)
