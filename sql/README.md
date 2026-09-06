# sql/ — Database Schema

Three SQL files that define the complete Lakebase schema. Run in order via `setup_db.py` from the project root.

## Files

| File | Purpose |
|------|---------|
| `01_create_tables.sql` | Core relational tables, including `paper_sections` |
| `02_create_embedding_tables.sql` | pgvector tables + HNSW indexes for semantic search |
| `03_create_trace_table.sql` | MCP server telemetry table |
| `04_migrate_embeddings_768.sql` | **Migration.** 384-dim (all-MiniLM) → 768-dim (modernbert-embed-base) |
| `05_paper_sections.sql` | **Migration.** Adds `paper_sections`, `papers.fulltext_status`, `paper_embeddings.section_name` |
| `06_pipeline_runs.sql` | **Migration.** Adds `pipeline_runs`, `topic_watermarks`, `papers.fulltext_attempts` |

The `0X_` files numbered 4 and above are **migrations for an existing database**. A
fresh install needs only `01`–`03`, which already contain everything the migrations
add — `setup_db.py` runs those three.

## Setup

```bash
# Fresh install — runs 01-03 in order + seeds demo data
python setup_db.py
```

An **existing** database instead applies the migrations in order, once each:

```bash
psql "$DATABASE_URL" -f sql/04_migrate_embeddings_768.sql   # destroys embeddings; re-run the pipeline after
psql "$DATABASE_URL" -f sql/05_paper_sections.sql           # additive; destroys nothing
psql "$DATABASE_URL" -f sql/06_pipeline_runs.sql            # additive; destroys nothing
```

## Key Design Decisions

**UUID primary keys** — All tables use `gen_random_uuid()` instead of `SERIAL`. Safe across multiple concurrent writers (MCP server, dashboard, Spark) with no collision risk.

**`ON DELETE CASCADE`** — Child rows (goals, notes, progress, collections) are automatically removed when a user is deleted. Prevents orphaned data without manual cleanup logic.

**CHECK constraints** — `status` columns (e.g., `reading_progress.status`) enforce valid values at the database level as a last line of defence, regardless of which service layer writes the row.

**Partial index on DOI** — `WHERE doi IS NOT NULL` keeps the index small since many preprints and theses have no DOI.

**`VECTOR(768)` dimension** — Fixed to match `nomic-ai/modernbert-embed-base` output size. Postgres rejects any insert with a mismatched dimension, so a model change requires recreating these tables — see `04_migrate_embeddings_768.sql` for the 384→768 migration. 768 is well under pgvector's 2000-dimension index ceiling, so HNSW still applies.

**HNSW over IVFFlat** — HNSW gives O(log N) query time at the cost of a slower build. For our pattern (infrequent batch inserts via Spark, frequent real-time queries), this trade-off is optimal. Uses `vector_cosine_ops` because semantic similarity uses cosine distance.

**`paper_sections` as a durable intermediate** — Extracting a Conclusion costs a PDF download and a parse. Storing the extracted text means re-chunking, or changing the embedding model, is a re-encode rather than a re-crawl. The table is the reason a model migration is cheap.

**`paper_embeddings.section_name` is NULL for the abstract** — Chosen over a literal `'abstract'` so every embedding row written before Phase 2 was already correct with no backfill. The ingestion anti-join and the dashboard both treat NULL as "the abstract".

**`papers.fulltext_status` is explicit, not inferred** — `no_url`, `fetch_failed`, `parse_failed`, `no_sections`, `ok`. Without the column, all five collapse into "this paper has no sections", which is indistinguishable from a crawler that silently stopped working.

**`pipeline_runs` as the schedule's feedback loop** — A job cluster's stdout dies with the cluster, so without a ledger there is no way to answer *"did last night's run add anything?"* after the fact. Counts are granular because a run that harvests 40 papers and embeds 0 chunks is a very different problem from one that harvests 0.

**`topic_watermarks` is keyed per topic, not global** — A seed topic added later must start from the canon rather than inherit the other topics' watermark and silently skip everything published before today.

**`papers.fulltext_attempts`** — Without a retry, a host down for one afternoon is written off permanently; without a ceiling, a dead URL is retried on every scheduled run forever. The counter bounds one failure mode against the other.

**Trace table** — `mcp_traces` is written by `TraceMiddleware` automatically on every tool call. Individual MCP tools never write to it directly (cross-cutting concern).
