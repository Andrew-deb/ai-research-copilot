# dashboard/services/ — Business Logic Layer (Dashboard App)

All validation rules, retrieval orchestration, and domain flows for the Flask dashboard. No service module imports Flask or writes SQL — routes handle transport, `repositories/lakebase.py` handles persistence.

## Service Modules

| File | Purpose |
|------|---------|
| `home_service.py` | Assembles the landing-page overview — stat cards, reading-status breakdown, recent activity — in one call. |
| `goal_service.py` | Learning goal CRUD plus pgvector matching of each goal against the paper catalog ("papers that match this goal"). |
| `search_service.py` | Three retrieval modes — paginated keyword (ILIKE), semantic (pgvector cosine), and RAG (retrieval + OpenRouter synthesis with inline citations) — and paper-detail assembly. |
| `collection_service.py` | Collection CRUD, membership, manual drag-reorder, and the reading-plan generator (heuristic mirrored from the MCP `planning_service`). |
| `progress_service.py` | Kanban board bucketing, reading-status transitions, and note annotations. |

---

## Key Design & Implementation Decisions

### 1. Same Layer Contract as the MCP Server
* Services operate on plain `dict` / `list` / `str`; they never touch `request`, `session`, `render_template`, or `psycopg2`.
* They raise typed exceptions from `dashboard/exceptions.py` (`ValidationError`, `PaperNotFoundError`, …). Routes never wrap calls in `try/except` — `middleware/error_handler.py` converts exceptions to responses.

### 2. Query Embedding Matches the Pipeline Exactly
* `search_service` embeds the *query* through `dashboard/embedding.py`, which loads the identical model (`all-MiniLM-L6-v2`) with the identical `normalize_embeddings=True` the Spark pipeline used for the stored vectors. A mismatch here silently degrades every cosine ranking.
* Vector search returns one row per *chunk*; `semantic_paper_matches` folds those back to one row per *paper*, keeping the highest-similarity chunk as the display snippet.

### 3. Retrieval-Augmented Generation, Not Free Generation
* `rag_answer` retrieves supporting chunks first, numbers them, and instructs the LLM (via `llm_client`) to cite claims as `[n]` and to answer only from those sources. The route returns both the prose answer and the structured source list so the UI can link each citation back to its paper.

### 4. Reading-Plan Heuristic Duplicated on Purpose
* `collection_service.generate_reading_plan` re-implements the MCP `planning_service` heuristic (publication year ascending, then `influence_score * 10 + citation_count` descending) rather than calling the MCP server over HTTP.
* Each Databricks App is an independent process with its own deploy lifecycle; a runtime dependency between them would make either app's outage the other's outage. The shared contract is the *algorithm*, kept identical in both files.

### 5. Degradations Never Block a Page
* Goal match counts, "related papers" on the detail page, and other vector-powered extras are wrapped so an embedding failure logs at debug level and the page still renders with the core data.
