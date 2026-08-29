# dashboard/repositories/ — Data Access Layer (Dashboard App)

This package contains all database queries and pgvector interactions specifically tailored for the Flask dashboard web application.

## Files

| File | Purpose |
|------|---------|
| `lakebase.py` | Database repository providing data access and specialized UI aggregation queries for the dashboard. |

## Key Design & Implementation Decisions

### 1. Pooled Connections, Context-Managed
* A per-process `psycopg2.pool.ThreadedConnectionPool` (size `DB_POOL_MIN`..`DB_POOL_MAX`, default 1..8) is created lazily on first query. `get_connection()` checks a connection out, commits on success / rolls back on error, and returns it — discarding it only if it actually broke.
* This is the main dashboard latency fix: a page runs 3-8 queries, and the old code opened a fresh TLS connection to Lakebase for each one. Pooling pays the handshake once per worker.
* TCP keepalives are set so pooled connections survive Lakebase / proxy idle timeouts. `close_pool()` runs at process exit (`atexit`).
* Operates in an independent process from the MCP server — its own pool, its own lifecycle.

### 2. UI-Specific Aggregations
* Contains optimized aggregate queries such as `get_dashboard_stats()` to compute real-time counts across active learning goals, curated collections, reading statuses (not started, reading, completed), and notes in minimal database round-trips.

### 3. Paginated Text & Vector Search
* Implements SQL `LIMIT` and `OFFSET` queries alongside pgvector cosine distance operations, powering responsive UI views for paper exploration and Kanban board status transitions.
