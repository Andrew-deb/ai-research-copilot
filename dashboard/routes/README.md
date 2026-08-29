# dashboard/routes/ — Interface Layer (Flask Blueprints)

One blueprint per URL area. Route functions are thin: parse and validate request data, call exactly one service function, return a template / redirect / JSON body. No business logic, no SQL, no `try/except` — domain exceptions propagate to `middleware/error_handler.py`.

## Blueprints

| File | Prefix | Key endpoints |
|------|--------|---------------|
| `home.py` | `/` | `GET /` — dashboard overview |
| `goals.py` | `/goals` | `GET` list · `POST` create · `GET /<id>/matches` (JSON) · `POST /<id>/status` |
| `search.py` | *(none)* | `GET /search` (keyword or semantic) · `GET /search/semantic` (JSON) · `POST /search/ask` (RAG, JSON) · `GET /paper/<id>` — detail |
| `collections.py` | `/collections` | `GET` list · `POST` create · `GET /<id>` detail · `POST /<id>/papers` add · `POST /<id>/papers/<pid>/remove` · `POST /<id>/plan` generate · `POST /<id>/reorder` |
| `progress.py` | *(none)* | `GET /progress` — Kanban board · `POST /paper/<id>/status` · `POST /paper/<id>/notes` |

`helpers.py` holds shared request helpers (kept out of `__init__.py` to avoid a circular import).

---

## Key Design & Implementation Decisions

### 1. One Service Call Per Route
* Every handler resolves the user (`current_user_id()` from the auth middleware), reads request fields, and delegates. If a handler needs two service calls it usually means a service method is missing — the composition belongs in the service layer.

### 2. Dual Representation via `action_response()`
* Write endpoints are used two ways: by `fetch()` from the page's JavaScript, and by a plain `<form>` submit (no-JS fallback).
* `action_response()` returns JSON when the caller looks like XHR (`X-Requested-With`, `is_json`, or a JSON `Accept`), otherwise it flashes a message and redirects back. Handlers don't branch on this themselves.

### 3. Identity Comes From Middleware, Not Route Args
* No route takes a `user_id` parameter. `middleware/auth.py` resolves it once per request from the Databricks `X-Forwarded-Email` header (demo user locally) and exposes it on `g`. This makes every route implicitly user-scoped and impossible to call for "another" user.

### 4. JSON Sub-Endpoints for Progressive Enhancement
* `GET /search/semantic`, `POST /search/ask`, and `GET /goals/<id>/matches` return JSON so the templates can layer live search, the RAG answer box, and expandable goal-match panels on top of pages that already work without JavaScript.
