# mcp_server/repositories/ — Data Access Layer

This package isolates all database interactions and SQL queries for the MCP server. No module outside this layer is permitted to import `psycopg2`, construct SQL strings, or manage database connections.

## Files

| File | Purpose |
|------|---------|
| `lakebase.py` | Complete repository implementation for Lakebase (PostgreSQL + pgvector). |

## Key Design & Implementation Decisions

### 1. Connection Lifecycle via Context Manager
* Uses `@contextmanager` with Python's `yield` pattern.
* **Automatic Transactions:** Automatically calls `conn.commit()` upon successful exit of the `with` block.
* **Guaranteed Rollback & Cleanup:** Automatically invokes `conn.rollback()` on any uncaught exception and ensures `conn.close()` is executed in a `finally` block to prevent connection leaks on Databricks Lakebase.

### 2. Dict-Based Row Mapping (`RealDictCursor`)
* Standard psycopg2 cursors return tuples (e.g., `row[0]`, `row[1]`), making code brittle when table schemas evolve.
* Using `cursor_factory=psycopg2.extras.RealDictCursor` ensures all query results are dictionaries keyed by column name (e.g., `row['title']`), making data access clear, maintainable, and type-friendly.

### 3. Upsert Idempotency & Conflict Resolution
* Every write function utilizes `INSERT ... ON CONFLICT (...) DO UPDATE`.
* **Preserving Multi-Source Enrichment:** To prevent OpenAlex bulk ingests from overwriting Semantic Scholar enrichment fields with NULLs, we use `COALESCE(EXCLUDED.field, table.field)`. This guarantees re-running ingestion or enrichment jobs is safe, repeatable, and non-destructive.

### 4. Direct Cosine Similarity via pgvector
* Queries leverage the `<=>` cosine distance operator against `VECTOR(768)` columns.
* Cosine distance (range `0.0` to `2.0`, where `0.0` is identical) is dynamically translated to cosine similarity (`1.0 - distance`) so ranking scores are intuitive (higher = closer match).
* Queries are explicitly indexed with Hierarchical Navigable Small World (HNSW) graphs, maintaining sub-second `O(log N)` search latency.

### 5. Independent Repository per Databricks App
* Both `mcp_server` and `dashboard` maintain dedicated `lakebase.py` modules.
* **Process Isolation:** Each Databricks App runs in its own isolated container and process space.
* **App-Specific Query Optimization:** Prevents UI-specific aggregation queries (like home dashboard statistics and paginated search filters) from polluting the lightweight agent MCP server.
