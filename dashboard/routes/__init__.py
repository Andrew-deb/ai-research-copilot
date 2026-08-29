"""
dashboard/routes/__init__.py — Flask blueprint registry.

Each module here owns one URL area and registers a blueprint. Route functions
are thin: read + validate request data, call one service function, hand back a
template / redirect / JSON body. No business logic, no SQL. Shared request
helpers live in `dashboard/routes/helpers.py`.
"""

from flask import Flask

from routes import collections, goals, home, progress, search

_BLUEPRINTS = (
    home.bp,
    goals.bp,
    search.bp,
    collections.bp,
    progress.bp,
)


def register_routes(app: Flask) -> None:
    for blueprint in _BLUEPRINTS:
        app.register_blueprint(blueprint)
