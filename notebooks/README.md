# notebooks/ — Databricks Batch Processing & Embedding Pipelines

This directory contains the batch data ingestion and embedding pipeline structured directly as a **Databricks Notebook** (`# Databricks notebook source` format with `%md` cells, `# DBTITLE` headers, and `# COMMAND ----------` boundaries).

## Files

| File | Purpose |
|------|---------|
| `ingest_papers_embeddings.py` | Databricks notebook: Ingests research papers from OpenAlex & Semantic Scholar, chunks text, generates 768-d embeddings with `nomic-ai/modernbert-embed-base`, and batch-persists vectors to Lakebase pgvector with HNSW index verification. |

## Databricks Notebook Cell guide

1. **Install packages** (`%pip install ...` & `dbutils.library.restartPython()`)
2. **Widgets & Config** (`dbutils.widgets` with dynamic `match/case` dimension detection)
3. **Resolve Lakebase URL** (Databricks SDK secret resolution from scope `database/lakebase-url`)
4. **Test Connection** (Verifies connection & pgvector extension)
5. **Harvest & Normalize Papers** (OpenAlex polite pool API + Semantic Scholar TLDRs & citation metrics)
6. **Upsert Raw Papers** (`ON CONFLICT (openalex_id) DO UPDATE` with `COALESCE` protection)
7. **Incremental Delta Detection** (`LEFT JOIN ... WHERE pe.id IS NULL` anti-join)
8. **Sliding Window Chunking** (800 chars / 100 overlap + title prepending)
9. **Batch Vector Encoding** (`nomic-ai/modernbert-embed-base` with `normalize_embeddings=True` and the `search_document: ` prefix)
10. **Batch Vector Upsert** (`psycopg2.extras.execute_batch` with `%s::vector(768)`)
11. **Verification & Similarity Test** (Live test query executing cosine similarity search)

---

## Key Design & Implementation Decisions

### 1. Sliding Window Character Chunking (`CHUNK_SIZE=4000`, `CHUNK_OVERLAP=400`)
* **Context Preservation:** Abstract texts are divided into overlapping chunks. The 400-character overlap prevents key semantic clauses from being split across chunk boundaries.
* **Sized to the model, not to a habit:** ModernBERT-embed reads 8192 tokens (~32k characters), so a 4000-character window keeps an entire abstract in a single chunk instead of fragmenting one argument across five vectors. The window still exists for the longer section text Phase 2 ingests.
* **Title Context Augmentation:** Prepends the paper's title to the first chunk of each paper so that domain context is tightly bound to the introductory vector.

### 2. Normalized Vectors with `nomic-ai/modernbert-embed-base` (768 Dimensions)
* `normalize_embeddings=True` forces output vectors to unit Euclidean length (L2 norm = 1.0).
* **Asymmetric prefixes:** every stored chunk is encoded as `search_document: <text>`; the dashboard and MCP server encode queries as `search_query: <text>`. The prefix is applied at encode time only — `chunk_text` stores clean text, because it is rendered directly as the UI result snippet. Omitting a prefix does not error; it silently degrades ranking.
* **Mathematical Property:** For unit-normalized vectors, Cosine Distance simplifies directly to dot product distance:
  $$\text{Cosine Distance}(\mathbf{u}, \mathbf{v}) = 1 - (\mathbf{u} \cdot \mathbf{v})$$
* **Quality over raw throughput:** ModernBERT-embed is slower per sentence than a 384-dim MiniLM, but the corpus is small (tens to low thousands of chunks) so encoding is minutes either way, and 768 dimensions materially improve retrieval on dense research abstracts. 768 also stays under pgvector's 2000-dimension HNSW index ceiling.

### 3. Incremental Delta Ingestion (Anti-Join Pattern)
* The pipeline avoids re-embedding existing papers. It performs a `LEFT JOIN ... WHERE pe.id IS NULL` anti-join query to identify only records without corresponding vector representations.

---

## Execution Instructions

### In Databricks Workspace
1. Import `ingest_papers_embeddings.py` into your Databricks workspace (`Workspace > Import > File`).
2. Attach the notebook to any Single Node or Multi-Node cluster.
3. Use the interactive widgets at the top to adjust topics, batch size, or embedding model.
4. Click **Run All**.
5. (Optional) Schedule as a recurring **Databricks Workflow Job** to continuously harvest and index literature.

### Local Standalone Run
```bash
python notebooks/ingest_papers_embeddings.py
```
*(The script includes automatic fallback for `dbutils` when run locally outside Databricks).*
