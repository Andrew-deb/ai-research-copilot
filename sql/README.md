# sql/ — Database Schema

Three SQL files that define the complete Lakebase schema. Run in order via `setup_db.py` from the project root.

## Files

| File | Purpose |
|------|---------|
| `01_create_tables.sql` | 10 core relational tables |
| `02_create_embedding_tables.sql` | pgvector tables + HNSW indexes for semantic search |
| `03_create_trace_table.sql` | MCP server telemetry table |

## Setup

```bash
# From project root — runs all three files in order + seeds demo data
python setup_db.py
```

## Key Design Decisions

**UUID primary keys** — All tables use `gen_random_uuid()` instead of `SERIAL`. Safe across multiple concurrent writers (MCP server, dashboard, Spark) with no collision risk.

**`ON DELETE CASCADE`** — Child rows (goals, notes, progress, collections) are automatically removed when a user is deleted. Prevents orphaned data without manual cleanup logic.

**CHECK constraints** — `status` columns (e.g., `reading_progress.status`) enforce valid values at the database level as a last line of defence, regardless of which service layer writes the row.

**Partial index on DOI** — `WHERE doi IS NOT NULL` keeps the index small since many preprints and theses have no DOI.

**`VECTOR(768)` dimension** — Fixed to match `nomic-ai/modernbert-embed-base` output size. Postgres rejects any insert with a mismatched dimension, so a model change requires recreating these tables — see `04_migrate_embeddings_768.sql` for the 384→768 migration. 768 is well under pgvector's 2000-dimension index ceiling, so HNSW still applies.

**HNSW over IVFFlat** — HNSW gives O(log N) query time at the cost of a slower build. For our pattern (infrequent batch inserts via Spark, frequent real-time queries), this trade-off is optimal. Uses `vector_cosine_ops` because semantic similarity uses cosine distance.

**Trace table** — `mcp_traces` is written by `TraceMiddleware` automatically on every tool call. Individual MCP tools never write to it directly (cross-cutting concern).
