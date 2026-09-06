# notebooks/ — Databricks Batch Processing & Embedding Pipelines

This directory contains the batch data ingestion and embedding pipeline structured directly as a **Databricks Notebook** (`# Databricks notebook source` format with `%md` cells, `# DBTITLE` headers, and `# COMMAND ----------` boundaries).

## Files

| File | Purpose |
|------|---------|
| `ingest_papers_embeddings.py` | Databricks notebook: Ingests research papers from OpenAlex & Semantic Scholar, downloads open-access PDFs and extracts Conclusion/Discussion/Methods sections, chunks text, generates 768-d embeddings with `nomic-ai/modernbert-embed-base`, and batch-persists vectors to Lakebase pgvector with HNSW index verification. |

## Databricks Notebook Cell guide

1. **Install packages** (`%pip install ...` & `dbutils.library.restartPython()`)
2. **Widgets & Config** (`dbutils.widgets` with dynamic `match/case` dimension detection)
3. **Resolve Lakebase URL** (Databricks SDK secret resolution from scope `database/lakebase-url`)
4. **Test Connection** (Verifies connection & pgvector extension)
5. **Claim the Run** (Postgres advisory lock + open a `pipeline_runs` ledger row)
6. **Harvest Papers** (OpenAlex polite pool; relevance seed on first sight of a topic, then `from_publication_date` cursor paging from a per-topic watermark)
7. **Batch S2 Enrichment** (`POST /paper/batch`, skipping papers already enriched)
8. **Upsert Raw Papers** (`ON CONFLICT (openalex_id) DO UPDATE` with `COALESCE` protection; `xmax = 0` distinguishes insert from update)
9. **Fetch Open-Access PDFs** (per-paper `try/except`, content-type check, size ceiling, polite delay; retries `fetch_failed` after a cooling-off window, bounded by `fulltext_attempts`)
10. **Extract Sections** (`pypdf` -> heading regex -> `paper_sections`; every outcome recorded in `papers.fulltext_status`)
11. **Incremental Delta Detection** (per `(paper_id, section_name)` anti-join — `section_name IS NULL` means the abstract)
12. **Sliding Window Chunking** (4000 chars / 400 overlap + title prepending, over abstracts *and* sections)
13. **Batch Vector Encoding** (`nomic-ai/modernbert-embed-base` with `normalize_embeddings=True` and the `search_document: ` prefix)
14. **Batch Vector Upsert** (`psycopg2.extras.execute_batch` with `%s::vector(768)`, carrying `section_name`)
15. **Verification & Similarity Test** (corpus composition, full-text outcomes, and *two* live queries — depth and discovery)
16. **Close the Run Ledger** (record counts and duration, release the advisory lock)

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

### 3. Selective Section Extraction, Not Full Text

* **What is indexed:** abstract + Conclusion + Discussion + Methods. Introduction restates the abstract and Related Work describes *other* papers, so both are noise. References are never indexed.
* **Why not full text:** naive full-text ingestion is ~13x the abstract-only corpus; this is ~2.5x. The discarded sections are the ones that dilute retrieval, so the trade is quality *and* cost, not quality *versus* cost.
* **Sections are stored, not just chunked:** `paper_sections` holds the extracted text, so re-chunking or changing the embedding model is a re-encode — never a re-crawl.
* **Every failure is named:** `papers.fulltext_status` distinguishes `no_url`, `fetch_failed`, `parse_failed`, `no_sections` and `ok`. All five would otherwise look identical to "this paper has no sections", which is how a broken crawler hides.
* **`pypdf`, not PyMuPDF:** pure Python, so `%pip install` on a Databricks cluster has no binary dependency to fight.

### 4. Incremental Delta Ingestion (Anti-Join Pattern)
* The pipeline avoids re-embedding existing content. It performs a `LEFT JOIN ... WHERE pe.id IS NULL` anti-join on `(paper_id, section_name)` to identify only the units without corresponding vector representations.

### 5. Built to Be Scheduled, Not Just Re-Run

Before Phase 2.5 a scheduled run mostly re-did its last one: the harvest sent `search=<topic>` with no sort or date filter, and OpenAlex's relevance ranking is stable, so the same ~75 works came back every time — while ~83 seconds per run were spent sleeping between Semantic Scholar calls that re-fetched TLDRs already stored.

* **Discovery is incremental.** A topic is seeded by relevance the first time it is seen (a cold corpus needs the canonical papers, not last week's preprints), then queried by `from_publication_date` sorted newest-first from a per-topic watermark. Watermarks never move backwards.
* **Enrichment is conditional, then batched.** Papers already carrying `semantic_scholar_id` and `tldr` are skipped outright; the remainder go through `POST /graph/v1/paper/batch`. The response is *positionally aligned with the request ids* — matching by index is what makes it fast, and getting it wrong attaches enrichment to the wrong papers.
* **Transient failures are retried, permanent ones are not.** `fetch_failed` is retried after a cooling-off window while `fulltext_attempts` allows; `parse_failed` and `no_sections` are not, because neither changes on its own.
* **One run at a time.** A Postgres session advisory lock — released automatically if the driver dies, so there is no stale-lock cleanup to write.
* **Every run is recorded.** `pipeline_runs` holds counts and duration, so "was last night worth it?" is a query rather than a scroll through driver logs that no longer exist.

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
