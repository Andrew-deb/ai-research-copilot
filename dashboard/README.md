# dashboard/ — Flask Dashboard (Databricks App #2)

Server-rendered web UI for the AI Research & Learning Copilot. Same layered architecture as `mcp_server/` — see the project implementation plan for the full layer contract. The dashboard talks **directly to Lakebase** (it does not call the MCP server); the agent and the dashboard are independent Databricks Apps that share one database.

## Directory Structure

```
dashboard/
├── app.py                      # Flask application factory (create_app / app)
├── config.py                   # Single source of truth for configuration
├── exceptions.py               # Re-exports the shared domain exceptions
├── embedding.py                # Lazy all-MiniLM-L6-v2 singleton — encode_query()
├── llm_client.py               # OpenRouter chat client for RAG synthesis
├── middleware/                 # Cross-cutting concerns
│   ├── auth.py                 # X-Forwarded-Email → g.user / g.user_id
│   └── error_handler.py        # Domain exception → JSON or flash+redirect
├── services/                   # Business logic (no Flask, no SQL)
│   ├── home_service.py
│   ├── goal_service.py
│   ├── search_service.py       # keyword / semantic / RAG + paper detail
│   ├── collection_service.py   # CRUD + reading-plan heuristic
│   └── progress_service.py
├── repositories/               # Data access layer (all SQL lives here)
│   └── lakebase.py
├── routes/                     # Flask blueprints (interface layer)
│   ├── home.py  goals.py  search.py  collections.py  progress.py
│   └── helpers.py              # wants_json / action_response
├── templates/                  # 9 Jinja2 templates (base + 7 pages + error)
├── static/
│   ├── css/                    # base + 4 page stylesheets
│   └── js/                     # main + 4 page scripts (vanilla, no build step)
└── app.yaml                    # Databricks App deployment config (gunicorn)
```

Dependencies live in the project-root `requirements.txt` (one unified file for the whole project). `middleware/README.md`, `services/README.md`, and `routes/README.md` document each layer.

## Setup

```bash
# From ai-research-copilot/, with .env containing DATABASE_URL and OPENROUTER_API_KEY
pip install -r requirements.txt

# Run locally
flask --app dashboard.app run --debug --port 8080
# or
python -m dashboard.app
```

Open <http://localhost:8080>. With `REQUIRE_FORWARDED_AUTH` unset (local default) and no `X-Forwarded-Email` header, every request is attributed to the seeded `demo@research-copilot.dev` user. See `context/setup/setup_guide.md` for the full local + Databricks walkthrough.

## Pages

| Route | Template | What it does |
|-------|----------|--------------|
| `/` | `index.html` | Stat cards, reading-status breakdown, recent goals & papers |
| `/goals` | `goals.html` | Create goals; each row shows a pgvector match count and an expandable list of matching papers |
| `/search` | `search.html` | Keyword (ILIKE, paginated) or semantic (pgvector) search; a RAG "ask a question" box when an LLM key is configured |
| `/paper/<id>` | `paper_detail.html` | Abstract, TLDR, authors, links; reading-status buttons; add-to-collection; inline notes; vector-similar papers |
| `/collections` | `collections.html` | Create and browse collections |
| `/collection/<id>` | `collection_detail.html` | Ordered reading list, drag-to-reorder, one-click reading-plan generation |
| `/progress` | `progress.html` | Kanban board — drag papers between `not_started / reading / completed / skipped` |

## Key Design Decisions

**`config.py` as single config source** — Same pattern as `mcp_server/config.py`: secret scope first (`database`, `openrouter`), then `.env` fallback. No other module calls `os.getenv()`.

**Direct-to-Lakebase, logic mirrored not shared** — The reading-plan heuristic in `services/collection_service.py` is a deliberate copy of the MCP `planning_service` algorithm. A runtime HTTP dependency between the two Databricks Apps would couple their uptime; the shared contract is the algorithm, kept identical in both files.

**Query embedding matches the pipeline** — `embedding.py` loads the identical model and `normalize_embeddings=True` the Spark pipeline used for the stored vectors. Anything else silently degrades cosine ranking.

**Thin routes, typed exceptions** — Routes validate input, call one service function, and return. Services raise `ValidationError` / `PaperNotFoundError` / `ExternalAPIError`; `middleware/error_handler.py` turns them into JSON (for `fetch`), an error page (for `GET` navigations), or a flashed redirect (for form posts). No route contains `try/except`.

**Two auth modes** — `REQUIRE_FORWARDED_AUTH=false` (local): missing `X-Forwarded-Email` ⇒ demo user. `REQUIRE_FORWARDED_AUTH=true` (Databricks App): missing header ⇒ `401`. `/healthz` and `/static/*` are always exempt so deploy health checks pass before the database is reachable. Covered by `tests/test_auth.py`.

**Progressive enhancement** — Every action works as a plain HTML form. JavaScript upgrades the same endpoints to `fetch` calls (drag-reorder, Kanban drag, RAG answers, inline notes) and the routes detect the caller and respond with JSON instead of a redirect.

**Design system** — `static/css/base.css` defines one token set (`--bg`, `--surface`, `--brand`, semantic `--info/--ok/--warn/--danger`, …) for light and a `[data-theme="dark"]` override. Layout is a fixed left sidebar + sticky top bar (Supabase / MongoDB Atlas console idiom). Type: **Inter** for UI, **JetBrains Mono** for identifiers, similarity scores, counts, and sequence numbers. The theme toggle lives in the top bar; `main.js` stamps `data-theme` on `<html>` before first paint (saved choice, else `prefers-color-scheme`) and persists changes to `localStorage`.

**Near-self-contained frontend** — Hand-written CSS + vanilla JS, no build step. The only external asset is Google Fonts (`fonts.googleapis.com`); a full system-font fallback stack is declared so the app degrades cleanly if that host is blocked.

## Secret Scope Names

| Scope | Key | Value |
|-------|-----|-------|
| `database` | `lakebase-url` | Lakebase Postgres connection URL |
| `openrouter` | `api-key` | OpenRouter API key (RAG synthesis) |
