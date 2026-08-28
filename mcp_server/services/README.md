# mcp_server/services/ — Business Logic & Orchestration Layer

This layer encapsulates all business logic, validation rules, multi-broker orchestration, and domain flows. No service module deals directly with HTTP requests/responses (that belongs in tools/routes) or raw SQL strings (that belongs in repositories).

## Service Modules

| File | Purpose |
|------|---------|
| `discovery_service.py` | Multi-source literature discovery (OpenAlex + Semantic Scholar + Lakebase), paper inspection, neural recommendations, paper comparisons, and Wikipedia topic caching. |
| `collection_service.py` | Collection lifecycle management, paper membership, and sequence ordering. |
| `planning_service.py` | Pedagogical curriculum sequencing (foundational impact prioritization + publication chronology) and learning goal tracking. |
| `progress_service.py` | Reading status transitions (`not_started`, `reading`, `completed`, `skipped`) and researcher note annotations. |

---

## Key Design & Implementation Decisions

### 1. Separation from Transport & Persistence (Single Responsibility Principle)
* Services do not import `FastMCP` or `Flask` — they operate strictly on typed Python data structures (`dict`, `list`, `str`).
* Services do not construct SQL queries — they invoke typed repository methods in `lakebase.py`.

### 2. Multi-Broker Aggregation Pattern
* In `discovery_service.search_papers`, high-throughput exploration is delegated to `openalex_broker`, and matched DOIs are subsequently enriched via `semantic_scholar_broker` with AI TLDRs and influence metrics before persistence.
* In `explain_topic`, the database cache is inspected first; if missing, Wikipedia is queried and cached to `topic_context`.

### 3. Pedagogical Reading Plan Sequencing
* Rather than random or simple alphabetical ordering, `planning_service.generate_reading_plan` sequences papers using a composite heuristic:
  1. Primary key: Publication year (ascending) to establish historical foundation before modern variations.
  2. Secondary key: `(influence_score * 10) + citation_count` (descending) to ensure foundational seminal papers are read before specialized niche extensions.

### 4. Typed Domain Exception Contracts
* When validation or entity lookups fail, services raise typed exceptions from `exceptions.py` (e.g., `PaperNotFoundError`, `ValidationError`, `CollectionNotFoundError`).
* These bubble up to the MCP error handler without requiring repetitive `try/except` blocks across every function.
