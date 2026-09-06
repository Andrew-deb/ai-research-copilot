# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # Ingest Research Papers -> Vector Embeddings (Lakebase)
# MAGIC
# MAGIC This notebook is part of the **AI Research & Learning Copilot** capstone project.
# MAGIC
# MAGIC It:
# MAGIC 1. **Discovers research papers** from the OpenAlex API across seed topics (or user-defined topics via widgets), reconstructs inverted-index abstracts, and optionally enriches papers with Semantic Scholar AI TLDRs and influential citation metrics.
# MAGIC 2. **Upserts papers** into the `papers` table in Lakebase with multi-source deduplication (`ON CONFLICT (openalex_id) DO UPDATE`).
# MAGIC 3. **Downloads open-access PDFs** and extracts the Conclusion, Discussion and Methods sections into `paper_sections`. Every outcome is recorded in `papers.fulltext_status` (`ok` | `no_url` | `fetch_failed` | `parse_failed` | `no_sections`), and every failure is non-fatal.
# MAGIC 4. **Extracts and chunks** abstracts, extracted sections and user notes using a sliding character window (`chunk_size=4000`, `chunk_overlap=400`) with title prepending for optimal semantic context.
# MAGIC 5. **Computes 768-dimensional dense vectors** using `nomic-ai/modernbert-embed-base` in memory-efficient batches, each chunk prefixed `search_document: `.
# MAGIC 6. **Upserts embeddings** into `paper_embeddings` and `note_embeddings` using the `pgvector` Postgres extension and HNSW indexes for sub-second cosine similarity search.
# MAGIC
# MAGIC It re-uses the Databricks secret scopes (`database`, `openalex`, `semantic-scholar`) configured during Phase 1.

# COMMAND ----------

# DBTITLE 1,Install required packages
# MAGIC %pip uninstall -y psycopg2 psycopg2-binary
# MAGIC %pip install -q 'databricks-sdk>=0.118.0' sentence-transformers requests pandas python-dotenv pypdf

# COMMAND ----------

# DBTITLE 1,Restart Python environment
try:
    dbutils.library.restartPython()
except NameError:
    # Not running in Databricks interactive environment
    pass

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Config & Parameters
# MAGIC
# MAGIC Widgets allow overriding configuration parameters (topics, batch sizes, chunking settings, embedding models) without editing notebook code.

# COMMAND ----------

# DBTITLE 1,Configure Widgets & Parameters
import os
from dotenv import load_dotenv

load_dotenv()

# Setup Databricks widgets with fallback for local runs
try:
    dbutils.widgets.text("papers_table_name", "papers", "Destination table (raw papers)")
    dbutils.widgets.text("embeddings_table_name", "paper_embeddings", "Destination table (paper vectors)")
    dbutils.widgets.text("note_embeddings_table_name", "note_embeddings", "Destination table (note vectors)")
    dbutils.widgets.text("embedding_model", "nomic-ai/modernbert-embed-base", "Embedding model")
    dbutils.widgets.text("topics", "transformer neural network, retrieval augmented generation, reinforcement learning from human feedback, vector database indexing, large language model agents", "Seed search topics (comma-separated)")
    dbutils.widgets.text("papers_per_topic", "15", "Max papers to fetch per topic")
    dbutils.widgets.text("chunk_size", "4000", "Text chunk size (chars)")
    dbutils.widgets.text("chunk_overlap", "400", "Text chunk overlap (chars)")
    dbutils.widgets.text("batch_size", "32", "Embedding batch size")
    dbutils.widgets.dropdown("fetch_new_papers", "true", ["true", "false"], "Fetch new papers from OpenAlex?")
    dbutils.widgets.dropdown("fetch_fulltext", "true", ["true", "false"], "Fetch open-access PDFs and extract sections?")
    dbutils.widgets.text("fulltext_sections", "conclusion,discussion,methods", "Sections to index (comma-separated)")
    dbutils.widgets.text("fulltext_timeout", "30", "PDF download timeout (seconds)")
    dbutils.widgets.text("fulltext_delay", "1.0", "Polite delay between PDF downloads (seconds)")
    dbutils.widgets.text("fulltext_max_mb", "25", "Skip PDFs larger than this (MB)")
    dbutils.widgets.text("section_min_chars", "200", "Discard extracted sections shorter than this")
    dbutils.widgets.dropdown("discovery_mode", "auto", ["auto", "seed", "recent"], "Discovery mode")
    dbutils.widgets.text("max_new_papers_per_run", "60", "Hard cap on papers harvested per run")
    dbutils.widgets.text("default_recent_from_date", "2024-01-01", "Fallback window start for 'recent' mode")
    dbutils.widgets.text("s2_batch_size", "100", "DOIs per Semantic Scholar batch request")
    dbutils.widgets.text("s2_batch_delay", "1.1", "Pause between S2 batch requests (seconds)")
    dbutils.widgets.text("fulltext_retry_after_days", "7", "Retry fetch_failed papers older than this")
    dbutils.widgets.text("fulltext_max_attempts", "3", "Give up on a PDF after this many attempts")
    dbutils.widgets.dropdown("run_trigger", "manual", ["manual", "scheduled"], "How this run was started")

    PAPERS_TABLE_NAME = dbutils.widgets.get("papers_table_name")
    EMBEDDINGS_TABLE_NAME = dbutils.widgets.get("embeddings_table_name")
    NOTE_EMBEDDINGS_TABLE_NAME = dbutils.widgets.get("note_embeddings_table_name")
    EMBEDDING_MODEL_NAME = dbutils.widgets.get("embedding_model")
    TOPICS = [t.strip() for t in dbutils.widgets.get("topics").split(",") if t.strip()]
    PAPERS_PER_TOPIC = int(dbutils.widgets.get("papers_per_topic"))
    CHUNK_SIZE = int(dbutils.widgets.get("chunk_size"))
    CHUNK_OVERLAP = int(dbutils.widgets.get("chunk_overlap"))
    BATCH_SIZE = int(dbutils.widgets.get("batch_size"))
    FETCH_NEW_PAPERS = dbutils.widgets.get("fetch_new_papers").lower() == "true"
    FETCH_FULLTEXT = dbutils.widgets.get("fetch_fulltext").lower() == "true"
    WANTED_SECTIONS = [x.strip().lower() for x in dbutils.widgets.get("fulltext_sections").split(",") if x.strip()]
    FULLTEXT_TIMEOUT = int(dbutils.widgets.get("fulltext_timeout"))
    FULLTEXT_DELAY = float(dbutils.widgets.get("fulltext_delay"))
    FULLTEXT_MAX_BYTES = int(float(dbutils.widgets.get("fulltext_max_mb")) * 1024 * 1024)
    SECTION_MIN_CHARS = int(dbutils.widgets.get("section_min_chars"))
    DISCOVERY_MODE = dbutils.widgets.get("discovery_mode").strip().lower()
    MAX_NEW_PAPERS_PER_RUN = int(dbutils.widgets.get("max_new_papers_per_run"))
    DEFAULT_RECENT_FROM_DATE = dbutils.widgets.get("default_recent_from_date").strip()
    S2_BATCH_SIZE = int(dbutils.widgets.get("s2_batch_size"))
    S2_BATCH_DELAY = float(dbutils.widgets.get("s2_batch_delay"))
    FULLTEXT_RETRY_AFTER_DAYS = int(dbutils.widgets.get("fulltext_retry_after_days"))
    FULLTEXT_MAX_ATTEMPTS = int(dbutils.widgets.get("fulltext_max_attempts"))
    RUN_TRIGGER = dbutils.widgets.get("run_trigger").strip().lower()
except NameError:
    PAPERS_TABLE_NAME = "papers"
    EMBEDDINGS_TABLE_NAME = "paper_embeddings"
    NOTE_EMBEDDINGS_TABLE_NAME = "note_embeddings"
    EMBEDDING_MODEL_NAME = "nomic-ai/modernbert-embed-base"
    TOPICS = [
        "transformer neural network",
        "retrieval augmented generation",
        "reinforcement learning from human feedback",
        "vector database indexing",
        "large language model agents",
    ]
    PAPERS_PER_TOPIC = 15
    CHUNK_SIZE = 4000
    CHUNK_OVERLAP = 400
    BATCH_SIZE = 32
    FETCH_NEW_PAPERS = True
    FETCH_FULLTEXT = True
    WANTED_SECTIONS = ["conclusion", "discussion", "methods"]
    FULLTEXT_TIMEOUT = 30
    FULLTEXT_DELAY = 1.0
    FULLTEXT_MAX_BYTES = 25 * 1024 * 1024
    SECTION_MIN_CHARS = 200
    DISCOVERY_MODE = "auto"
    MAX_NEW_PAPERS_PER_RUN = 60
    DEFAULT_RECENT_FROM_DATE = "2024-01-01"
    S2_BATCH_SIZE = 100
    S2_BATCH_DELAY = 1.1
    FULLTEXT_RETRY_AFTER_DAYS = 7
    FULLTEXT_MAX_ATTEMPTS = 3
    RUN_TRIGGER = "manual"

# Match embedding dimension to the selected model
match EMBEDDING_MODEL_NAME:
    case "sentence-transformers/all-MiniLM-L6-v2" | "sentence-transformers/all-MiniLM-L12-v2" | "BAAI/bge-small-en-v1.5":
        EMBEDDING_DIM = 384
    case "nomic-ai/modernbert-embed-base" | "sentence-transformers/all-mpnet-base-v2" | "BAAI/bge-base-en-v1.5":
        EMBEDDING_DIM = 768
    case "BAAI/bge-large-en-v1.5":
        EMBEDDING_DIM = 1024
    case "text-embedding-3-small":
        EMBEDDING_DIM = 1536
    case _:
        raise ValueError(f"Unknown embedding model {EMBEDDING_MODEL_NAME!r}. Add its output dimension to match/case.")

# Asymmetric-retrieval prefixes. modernbert-embed-base (and the bge/e5 families)
# expect documents and queries to be marked differently. Omitting the prefix does
# not error - it silently degrades ranking against the dashboard's query vectors,
# which use "search_query: ".
#
# CRITICAL: the prefix is applied at ENCODE time only. chunk_text is stored clean,
# because the dashboard renders it directly as the search-result snippet.
match EMBEDDING_MODEL_NAME:
    case "nomic-ai/modernbert-embed-base":
        DOCUMENT_PREFIX = "search_document: "
        QUERY_PREFIX = "search_query: "
    case s if s.startswith("BAAI/bge-"):
        DOCUMENT_PREFIX = ""          # bge prefixes the query only
        QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
    case _:
        DOCUMENT_PREFIX = ""          # symmetric models (all-MiniLM, mpnet)
        QUERY_PREFIX = ""

# QUERY_PREFIX is used ONLY by the verification cell at the end of this notebook.
# The notebook is a document producer; the dashboard and MCP server own the query
# side in production (dashboard/embedding.py). It exists here so the smoke test
# searches the way a real user does - an unprefixed test query would score lower
# against these vectors and make a healthy index look broken.

print(f"✅ Configuration Loaded:")
print(f"  • Model: {EMBEDDING_MODEL_NAME} ({EMBEDDING_DIM}-dim)")
print(f"  • Chunking: size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}")
print(f"  • Document prefix: {DOCUMENT_PREFIX!r}  (query side: {QUERY_PREFIX!r})")
print(f"  • Topics: {len(TOPICS)} seed queries")
print(f"  • Fetch new papers: {FETCH_NEW_PAPERS}")
print(f"  • Fetch full text: {FETCH_FULLTEXT} -> sections {WANTED_SECTIONS}")
print(f"  • Discovery: {DISCOVERY_MODE}, cap {MAX_NEW_PAPERS_PER_RUN} papers/run")
print(f"  • S2 batching: {S2_BATCH_SIZE} DOIs/request")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Resolve Lakebase Secrets & Test Connection
# MAGIC
# MAGIC Reads the PostgreSQL connection URL from the `database/lakebase-url` secret scope (or local `.env` fallback) and verifies connection health.

# COMMAND ----------

# DBTITLE 1,Connect & Verify Lakebase Connection
import base64
from urllib.parse import urlparse
import psycopg2
import psycopg2.extras

def get_lakebase_url() -> str:
    """Retrieve connection URL from Databricks Secret Scope or .env fallback."""
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        secret = w.secrets.get_secret(scope="database", key="lakebase-url")
        return base64.b64decode(secret.value).decode("utf-8")
    except Exception:
        pass
    url = os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL not found in secret scope 'database/lakebase-url' or .env")
    return url

lakebase_url = get_lakebase_url()
parsed = urlparse(lakebase_url)

db_host = parsed.hostname
db_port = parsed.port or 5432
db_name = parsed.path.lstrip('/')
db_user = parsed.username
db_password = parsed.password

print(f"🔌 Connecting to Lakebase on {db_host}:{db_port}/{db_name} as {db_user}...")

try:
    conn = psycopg2.connect(
        host=db_host,
        port=db_port,
        dbname=db_name,
        user=db_user,
        password=db_password,
        sslmode='require',
        connect_timeout=10
    )
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {PAPERS_TABLE_NAME};")
        count = cur.fetchone()[0]
        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector';")
        vec_ver = cur.fetchone()
        pgvector_str = f"pgvector v{vec_ver[0]}" if vec_ver else "pgvector NOT FOUND"
        print(f"✅ Connection successful! Found {count} existing papers in '{PAPERS_TABLE_NAME}'. ({pgvector_str})")
    conn.close()
except Exception as e:
    print(f"❌ Connection failed: {e}")
    raise

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2b. Claim the Run (advisory lock + ledger row)
# MAGIC
# MAGIC A schedule tighter than the runtime overlaps. Two concurrent runs would both
# MAGIC harvest, both download the same PDFs, and race on the same upserts.
# MAGIC
# MAGIC A Postgres **session advisory lock** is the right tool: it costs nothing, it is
# MAGIC released automatically if the cluster dies mid-run — no stale-lock cleanup to write
# MAGIC — and a second run that cannot take it exits cleanly rather than queueing.
# MAGIC
# MAGIC The same cell opens the `pipeline_runs` row. A job cluster's stdout dies with the
# MAGIC cluster, so without the ledger there is no way to answer *"did last night's run add
# MAGIC anything?"* after the fact.

# COMMAND ----------

# DBTITLE 1,Acquire Advisory Lock and Open the Run Ledger
import json
import time as _time

# Arbitrary but fixed: any two runs of this notebook must choose the same number.
PIPELINE_LOCK_KEY = 883_1207

# Held for the life of the run. Closing this connection releases the lock, which is
# exactly the behaviour we want if the driver dies.
lock_conn = psycopg2.connect(
    host=db_host, port=db_port, dbname=db_name,
    user=db_user, password=db_password, sslmode='require'
)
lock_conn.autocommit = True

with lock_conn.cursor() as cur:
    cur.execute("SELECT pg_try_advisory_lock(%s);", (PIPELINE_LOCK_KEY,))
    RUN_LOCK_ACQUIRED = bool(cur.fetchone()[0])

RUN_ID = None
RUN_STARTED = _time.perf_counter()

if not RUN_LOCK_ACQUIRED:
    with lock_conn.cursor() as cur:
        cur.execute("""
            INSERT INTO pipeline_runs (status, trigger, finished_at, duration_seconds, error)
            VALUES ('skipped_locked', %s, now(), 0, 'Another run holds the pipeline lock.')
            RETURNING run_id;
        """, (RUN_TRIGGER,))
        skipped_id = cur.fetchone()[0]
    lock_conn.close()
    raise SystemExit(
        f"Another ingestion run is already in progress. Exiting cleanly "
        f"(recorded as {skipped_id}). Nothing was changed."
    )

with lock_conn.cursor() as cur:
    cur.execute("""
        INSERT INTO pipeline_runs (status, trigger, notes)
        VALUES ('running', %s, %s)
        RETURNING run_id;
    """, (RUN_TRIGGER, json.dumps({
        "embedding_model": EMBEDDING_MODEL_NAME,
        "embedding_dim": EMBEDDING_DIM,
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "topics": TOPICS,
        "discovery_mode": DISCOVERY_MODE,
        "max_new_papers_per_run": MAX_NEW_PAPERS_PER_RUN,
        "wanted_sections": WANTED_SECTIONS,
        "fetch_new_papers": FETCH_NEW_PAPERS,
        "fetch_fulltext": FETCH_FULLTEXT,
    })))
    RUN_ID = cur.fetchone()[0]

print(f"Run {RUN_ID} claimed the pipeline lock (trigger={RUN_TRIGGER}).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Harvest Papers from OpenAlex (incremental)
# MAGIC
# MAGIC Queries the OpenAlex API (high-throughput polite pool) and reconstructs
# MAGIC inverted-index abstracts into clean text.
# MAGIC
# MAGIC **Incremental since Phase 2.5.** A topic is *seeded* by relevance the first time
# MAGIC it is seen - a cold corpus needs the canonical papers, not last week's preprints -
# MAGIC and thereafter queried by `from_publication_date` sorted newest-first, from a
# MAGIC per-topic watermark. Without this a scheduled run returns the same ~75 works
# MAGIC every time, because relevance ranking is stable.
# MAGIC
# MAGIC Semantic Scholar enrichment moved to the next cell, where it is batched.

# COMMAND ----------

# DBTITLE 1,Fetch & Normalize Academic Papers
import json
import time

import pandas as pd
import requests

def get_openalex_email() -> str:
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        sec = w.secrets.get_secret(scope="openalex", key="email")
        return base64.b64decode(sec.value).decode("utf-8")
    except Exception:
        return os.getenv("OPENALEX_EMAIL", "user@research-copilot.dev")

def get_semantic_scholar_api_key() -> str | None:
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        sec = w.secrets.get_secret(scope="semantic-scholar", key="api-key")
        return base64.b64decode(sec.value).decode("utf-8")
    except Exception:
        return os.getenv("SEMANTIC_SCHOLAR_API_KEY")

OPENALEX_EMAIL = get_openalex_email()
S2_API_KEY = get_semantic_scholar_api_key()

OPENALEX_SELECT = (
    "id,doi,title,publication_year,publication_date,cited_by_count,"
    "primary_location,abstract_inverted_index,open_access"
)


def _standardize_openalex(item: dict) -> dict | None:
    """One OpenAlex work -> our row shape, or None if it is unusable."""
    inv_idx = item.get("abstract_inverted_index") or {}
    pos_word = {pos: word for word, positions in inv_idx.items() for pos in positions}
    abstract = " ".join(pos_word[i] for i in sorted(pos_word)) if pos_word else None

    raw_id = item.get("id", "")
    openalex_id = raw_id.replace("https://openalex.org/", "") if raw_id else None
    raw_doi = item.get("doi", "")
    doi = raw_doi.replace("https://doi.org/", "") if raw_doi else None

    loc = item.get("primary_location") or {}
    venue = (loc.get("source") or {}).get("display_name")
    oa_url = (item.get("open_access") or {}).get("oa_url")

    # A paper with no abstract is not ingested at all: the abstract is the only
    # text guaranteed to exist for every paper, and the whole index rests on it.
    if not (openalex_id and item.get("title") and abstract):
        return None

    return {
        "openalex_id": openalex_id,
        "doi": doi,
        "title": item.get("title"),
        "abstract": abstract,
        "publication_year": item.get("publication_year"),
        "publication_date": item.get("publication_date"),
        "venue": venue,
        "citation_count": item.get("cited_by_count", 0),
        "source_api": "openalex",
        "open_access_url": oa_url,
        "payload": item,
    }


def fetch_openalex_works(query: str, limit: int, from_date: str | None = None) -> list[dict]:
    """
    Fetch up to `limit` works for one topic.

    Two modes, and the distinction is the point of Phase 2.5:

      from_date is None  -> SEED. Sort by relevance. A cold corpus needs the
                            canonical papers for a topic, not last week's preprints.
                            Uses basic `page` paging: OpenAlex does not support
                            cursor paging together with relevance sorting.

      from_date is set   -> INCREMENTAL. filter=from_publication_date, sorted newest
                            first, cursor-paged. This is what makes a scheduled run
                            return something a previous run did not already have.
    """
    url = "https://api.openalex.org/works"
    headers = {"User-Agent": f"ResearchCopilot/1.0 (mailto:{OPENALEX_EMAIL})"}
    base = {
        "search": query,
        "mailto": OPENALEX_EMAIL,
        "select": OPENALEX_SELECT,
        "per-page": min(max(limit, 1), 200),
    }

    collected: list[dict] = []
    cursor = "*"
    page = 1

    while len(collected) < limit:
        params = dict(base)
        params["per-page"] = min(base["per-page"], limit - len(collected))
        if from_date:
            params["filter"] = f"from_publication_date:{from_date}"
            params["sort"] = "publication_date:desc"
            params["cursor"] = cursor
        else:
            params["page"] = page

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=20)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            print(f"  [warn] OpenAlex query failed for '{query}' (page {page}): {exc}")
            break

        results = body.get("results", [])
        if not results:
            break

        for item in results:
            row = _standardize_openalex(item)
            if row:
                collected.append(row)

        if from_date:
            cursor = (body.get("meta") or {}).get("next_cursor")
            if not cursor:
                break
        else:
            page += 1
            # Basic paging is capped at 10k results; our per-run caps are far below
            # that, but stop anyway rather than loop on a misbehaving response.
            if page > 25:
                break

        time.sleep(0.2)   # OpenAlex polite pool spacing

    return collected[:limit]


def _load_watermarks(topics: list[str]) -> dict[str, dict]:
    """Per-topic discovery cursors. A topic with no row has never been seeded."""
    conn = psycopg2.connect(
        host=db_host, port=db_port, dbname=db_name,
        user=db_user, password=db_password, sslmode='require'
    )
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM topic_watermarks WHERE topic = ANY(%s);", (topics,))
            return {r["topic"]: dict(r) for r in cur.fetchall()}
    finally:
        conn.close()


harvested_papers = []

if not FETCH_NEW_PAPERS:
    print("Skipping external paper fetch (fetch_new_papers = false). Processing existing database records.")
else:
    watermarks = _load_watermarks(TOPICS)
    remaining = MAX_NEW_PAPERS_PER_RUN
    seen_openalex_ids: set[str] = set()
    topic_results: dict[str, list[dict]] = {}

    print(f"Harvesting up to {MAX_NEW_PAPERS_PER_RUN} papers across {len(TOPICS)} topics...")

    for idx, topic in enumerate(TOPICS, start=1):
        if remaining <= 0:
            print(f"  [{idx}/{len(TOPICS)}] '{topic}' skipped - per-run cap reached.")
            topic_results[topic] = []
            continue

        mark = watermarks.get(topic) or {}
        if DISCOVERY_MODE == "seed":
            from_date = None
        elif DISCOVERY_MODE == "recent":
            from_date = str(mark.get("watermark_date") or DEFAULT_RECENT_FROM_DATE)
        else:                                   # "auto"
            from_date = str(mark["watermark_date"]) if mark.get("last_seeded_at") and mark.get("watermark_date") else None

        mode = "seed (relevance)" if from_date is None else f"incremental (since {from_date})"
        budget = min(PAPERS_PER_TOPIC, remaining)
        print(f"  [{idx}/{len(TOPICS)}] '{topic}' - {mode}, budget {budget}")

        works = fetch_openalex_works(topic, limit=budget, from_date=from_date)

        # De-duplicate across topics within this run: seed topics overlap heavily,
        # and upserting the same work five times is five S2 lookups for nothing.
        fresh = [w for w in works if w["openalex_id"] not in seen_openalex_ids]
        seen_openalex_ids.update(w["openalex_id"] for w in fresh)

        topic_results[topic] = fresh
        harvested_papers.extend(fresh)
        remaining -= len(fresh)
        print(f"      -> {len(works)} returned, {len(fresh)} new to this run")

    print(f"\nHarvested {len(harvested_papers)} candidate papers from OpenAlex.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Enrich via Semantic Scholar, then Upsert to Lakebase
# MAGIC
# MAGIC Enrichment is **batched and conditional** (Phase 2.5). The previous version called
# MAGIC S2 once per DOI with `time.sleep(1.1)` before each call, on every run — roughly 83
# MAGIC seconds of sleeping per run to re-fetch TLDRs already in the table, most of which
# MAGIC `COALESCE` then discarded.
# MAGIC
# MAGIC Now: papers that already carry `semantic_scholar_id` **and** `tldr` are skipped
# MAGIC entirely, and whatever remains goes through `POST /graph/v1/paper/batch` in groups.
# MAGIC
# MAGIC **The alignment trap.** The batch response is a JSON list *positionally aligned with
# MAGIC the ids you sent*, with `null` in the slot for a miss — not a keyed object. Matching
# MAGIC by index rather than by id is what makes it fast; getting it wrong attaches
# MAGIC enrichment to the **wrong papers**, which is worse than no enrichment at all.

# COMMAND ----------

# DBTITLE 1,Batch S2 Enrichment (positionally aligned)
S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
S2_FIELDS = "paperId,externalIds,tldr,influentialCitationCount"


def _already_enriched_dois() -> set[str]:
    """
    DOIs whose S2 data is already stored. Skipping these is where nearly all of the
    saving comes from: on a steady-state run the corpus barely changes, so almost
    every harvested paper is already enriched.
    """
    conn = psycopg2.connect(
        host=db_host, port=db_port, dbname=db_name,
        user=db_user, password=db_password, sslmode='require'
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT lower(doi) FROM {PAPERS_TABLE_NAME}
                 WHERE doi IS NOT NULL
                   AND semantic_scholar_id IS NOT NULL
                   AND tldr IS NOT NULL;
            """)
            return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def enrich_batch(dois: list[str]) -> dict[str, dict]:
    """
    Look up many DOIs in one request. Returns {lowercased doi: fields}.

    Only DOIs that came back with data appear in the result, so a caller reading it
    with .get() naturally treats a miss as "no enrichment" rather than as an error.
    """
    if not dois:
        return {}

    headers = {"Accept": "application/json"}
    if S2_API_KEY:
        headers["x-api-key"] = S2_API_KEY

    out: dict[str, dict] = {}
    global s2_request_count

    for start in range(0, len(dois), S2_BATCH_SIZE):
        window = dois[start:start + S2_BATCH_SIZE]
        try:
            resp = requests.post(
                S2_BATCH_URL,
                params={"fields": S2_FIELDS},
                headers=headers,
                json={"ids": [f"DOI:{d}" for d in window]},
                timeout=30,
            )
            s2_request_count += 1
            if resp.status_code != 200:
                print(f"  [warn] S2 batch returned {resp.status_code} for {len(window)} ids; skipping this batch.")
                continue
            payload = resp.json()
        except Exception as exc:
            print(f"  [warn] S2 batch request failed: {type(exc).__name__}: {str(exc)[:120]}")
            continue

        if not isinstance(payload, list) or len(payload) != len(window):
            # Positional alignment is the entire contract. If the response is not a
            # list of the same length, we cannot say which record belongs to which
            # DOI - so attach nothing rather than guess.
            print(f"  [warn] S2 batch response was not aligned with the request "
                  f"({type(payload).__name__}, len {len(payload) if isinstance(payload, list) else 'n/a'} "
                  f"vs {len(window)}); discarding this batch.")
            continue

        for doi, record in zip(window, payload):
            if not isinstance(record, dict):
                continue                      # null slot = S2 does not know this DOI
            tldr_obj = record.get("tldr") or {}
            out[doi.lower()] = {
                "semantic_scholar_id": record.get("paperId"),
                "influence_score": record.get("influentialCitationCount"),
                "tldr": tldr_obj.get("text"),
            }

        time.sleep(S2_BATCH_DELAY)            # one pause per batch, not per paper

    return out


s2_request_count = 0
s2_by_doi: dict[str, dict] = {}

if harvested_papers:
    enriched_already = _already_enriched_dois()
    candidate_dois = [
        p["doi"] for p in harvested_papers
        if p.get("doi") and p["doi"].lower() not in enriched_already
    ]
    candidate_dois = list(dict.fromkeys(candidate_dois))    # de-dupe, keep order

    skipped = sum(1 for p in harvested_papers if p.get("doi")) - len(candidate_dois)
    print(f"S2 enrichment: {len(candidate_dois)} DOI(s) to look up, {skipped} already enriched.")

    s2_by_doi = enrich_batch(candidate_dois)
    print(f"  -> {len(s2_by_doi)} enriched in {s2_request_count} request(s).")

# COMMAND ----------

# DBTITLE 1,Upsert Papers to Lakebase
papers_inserted = 0

if harvested_papers:
    print(f"💾 Upserting {len(harvested_papers)} papers into '{PAPERS_TABLE_NAME}'...")

    conn = psycopg2.connect(
        host=db_host, port=db_port, dbname=db_name,
        user=db_user, password=db_password, sslmode='require'
    )

    upsert_sql = f"""
    INSERT INTO {PAPERS_TABLE_NAME} (
        openalex_id, semantic_scholar_id, doi, title, abstract,
        publication_year, venue, citation_count, tldr, influence_score,
        source_api, open_access_url, payload, synced_at
    ) VALUES (
        %(openalex_id)s, %(semantic_scholar_id)s, %(doi)s, %(title)s, %(abstract)s,
        %(publication_year)s, %(venue)s, %(citation_count)s, %(tldr)s, %(influence_score)s,
        %(source_api)s, %(open_access_url)s, %(payload)s, now()
    )
    ON CONFLICT (openalex_id) DO UPDATE SET
        semantic_scholar_id = COALESCE(EXCLUDED.semantic_scholar_id, {PAPERS_TABLE_NAME}.semantic_scholar_id),
        doi                 = COALESCE(EXCLUDED.doi, {PAPERS_TABLE_NAME}.doi),
        title               = EXCLUDED.title,
        abstract            = COALESCE(EXCLUDED.abstract, {PAPERS_TABLE_NAME}.abstract),
        citation_count      = EXCLUDED.citation_count,
        tldr                = COALESCE(EXCLUDED.tldr, {PAPERS_TABLE_NAME}.tldr),
        influence_score     = COALESCE(EXCLUDED.influence_score, {PAPERS_TABLE_NAME}.influence_score),
        open_access_url     = COALESCE(EXCLUDED.open_access_url, {PAPERS_TABLE_NAME}.open_access_url),
        synced_at           = now()
    RETURNING (xmax = 0) AS inserted;
    """

    with conn.cursor() as cur:
        for p in harvested_papers:
            s2 = s2_by_doi.get(p["doi"].lower(), {}) if p.get("doi") else {}
            params = {
                "openalex_id": p.get("openalex_id"),
                "semantic_scholar_id": s2.get("semantic_scholar_id"),
                "doi": p.get("doi"),
                "title": p.get("title", ""),
                "abstract": p.get("abstract"),
                "publication_year": p.get("publication_year"),
                "venue": p.get("venue"),
                "citation_count": p.get("citation_count", 0),
                "tldr": s2.get("tldr"),
                "influence_score": s2.get("influence_score"),
                "source_api": p.get("source_api", "openalex"),
                "open_access_url": p.get("open_access_url"),
                "payload": json.dumps(p.get("payload")) if p.get("payload") else None,
            }
            cur.execute(upsert_sql, params)
            # xmax = 0 distinguishes a fresh INSERT from an ON CONFLICT UPDATE, which
            # is the difference between "this run found something" and "this run
            # re-ran". The ledger needs that distinction to be worth reading.
            row = cur.fetchone()
            if row and row[0]:
                papers_inserted += 1
        conn.commit()
    conn.close()
    print(f"✅ Upsert complete - {papers_inserted} new, {len(harvested_papers) - papers_inserted} refreshed.")

# COMMAND ----------

# DBTITLE 1,Advance Per-Topic Watermarks
# Only after a successful upsert: a watermark moved before the data lands would
# skip those papers forever on the next run.
if FETCH_NEW_PAPERS and harvested_papers:
    conn = psycopg2.connect(
        host=db_host, port=db_port, dbname=db_name,
        user=db_user, password=db_password, sslmode='require'
    )
    with conn:
        with conn.cursor() as cur:
            for topic, rows in topic_results.items():
                dates = [r["publication_date"] for r in rows if r.get("publication_date")]
                newest = max(dates) if dates else None
                cur.execute("""
                    INSERT INTO topic_watermarks
                        (topic, last_seeded_at, watermark_date, last_run_at, papers_ingested)
                    VALUES (%s, now(), %s, now(), %s)
                    ON CONFLICT (topic) DO UPDATE SET
                        last_seeded_at  = COALESCE(topic_watermarks.last_seeded_at, now()),
                        -- Never move a watermark backwards: a run that happened to
                        -- return only older papers must not re-open a window that a
                        -- previous run already closed.
                        watermark_date  = GREATEST(
                            topic_watermarks.watermark_date,
                            COALESCE(EXCLUDED.watermark_date, topic_watermarks.watermark_date)
                        ),
                        last_run_at     = now(),
                        papers_ingested = topic_watermarks.papers_ingested + EXCLUDED.papers_ingested;
                """, (topic, newest, len(rows)))
    conn.close()
    print("Watermarks advanced.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Fetch Open-Access PDFs
# MAGIC
# MAGIC For papers with an `open_access_url` and no full-text attempt yet, download the PDF
# MAGIC into memory. Every failure is **recorded, not raised** — `papers.fulltext_status`
# MAGIC distinguishes `no_url` / `fetch_failed` / `parse_failed` / `no_sections` / `ok`, so a
# MAGIC missing section is always explainable. One bad PDF must never fail the run.

# COMMAND ----------

# DBTITLE 1,Download Open-Access PDFs (per-paper try/except)
import time
import requests

PDF_HEADERS = {
    # Identify the crawler. Several OA hosts return 403 to an unlabelled client.
    "User-Agent": f"ai-research-copilot/1.0 (mailto:{os.getenv('OPENALEX_EMAIL', 'research@example.com')})",
    "Accept": "application/pdf,*/*",
}

pdf_bytes_by_paper: dict[str, bytes] = {}
fulltext_status: dict[str, str] = {}

if not FETCH_FULLTEXT:
    print("⏭️  FETCH_FULLTEXT=false — skipping PDF acquisition.")
    candidates_df = pd.DataFrame(columns=["paper_id", "open_access_url"])
else:
    conn = psycopg2.connect(
        host=db_host, port=db_port, dbname=db_name,
        user=db_user, password=db_password, sslmode='require'
    )

    # Two populations (Phase 2.5):
    #   1. never attempted            -> fulltext_status IS NULL
    #   2. transiently failed         -> fetch_failed, cooled off, budget remaining
    #
    # parse_failed and no_sections are NOT retried: an HTML landing page or a scanned
    # PDF will not become parseable on its own, and heading extraction is deterministic
    # given the same text and the same SECTION_SYNONYMS. Re-running those would burn
    # bandwidth on a guaranteed identical outcome. To re-attempt them after improving
    # SECTION_SYNONYMS, clear their fulltext_status by hand - that is the deliberate
    # act the exclusion is protecting.
    candidates_df = pd.read_sql_query(f"""
        SELECT paper_id, open_access_url
        FROM {PAPERS_TABLE_NAME}
        WHERE fulltext_status IS NULL
           OR (
                fulltext_status = 'fetch_failed'
            AND fulltext_attempts < %(max_attempts)s
            AND (fulltext_checked_at IS NULL
                 OR fulltext_checked_at < now() - make_interval(days => %(retry_days)s))
           )
        ORDER BY citation_count DESC NULLS LAST
    """, conn, params={"max_attempts": FULLTEXT_MAX_ATTEMPTS,
                       "retry_days": FULLTEXT_RETRY_AFTER_DAYS})
    conn.close()

    print(f"📥 {len(candidates_df)} paper(s) awaiting a full-text attempt "
          f"(new + retryable fetch_failed, max {FULLTEXT_MAX_ATTEMPTS} attempts, "
          f"{FULLTEXT_RETRY_AFTER_DAYS}d cooling-off).")

    for _, row in candidates_df.iterrows():
        paper_id = str(row["paper_id"])
        url = row["open_access_url"]

        if not url or not str(url).strip():
            fulltext_status[paper_id] = "no_url"
            continue

        try:
            resp = requests.get(str(url).strip(), headers=PDF_HEADERS,
                                timeout=FULLTEXT_TIMEOUT, stream=True)
            resp.raise_for_status()

            # An open_access_url is frequently an HTML landing page, not the PDF.
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "pdf" not in content_type:
                fulltext_status[paper_id] = "parse_failed"
                resp.close()
                continue

            # Read with a ceiling so one pathological file cannot exhaust driver memory.
            body = bytearray()
            for block in resp.iter_content(chunk_size=64 * 1024):
                body.extend(block)
                if len(body) > FULLTEXT_MAX_BYTES:
                    break
            resp.close()

            if len(body) > FULLTEXT_MAX_BYTES:
                fulltext_status[paper_id] = "fetch_failed"   # oversized; treated as unavailable
                continue

            pdf_bytes_by_paper[paper_id] = bytes(body)

        except Exception as exc:                       # noqa: BLE001 - one paper must not stop the run
            fulltext_status[paper_id] = "fetch_failed"
            print(f"  ⚠️  fetch_failed {paper_id}: {type(exc).__name__}: {str(exc)[:120]}")

        time.sleep(FULLTEXT_DELAY)                     # polite to OA hosts

    print(f"✅ Downloaded {len(pdf_bytes_by_paper)} PDF(s); "
          f"{sum(1 for v in fulltext_status.values() if v == 'no_url')} without a URL, "
          f"{sum(1 for v in fulltext_status.values() if v == 'fetch_failed')} fetch failures, "
          f"{sum(1 for v in fulltext_status.values() if v == 'parse_failed')} non-PDF responses.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Extract Sections from PDFs
# MAGIC
# MAGIC `pypdf` -> raw text -> heading regex -> slice out the wanted sections, stopping at
# MAGIC References. `pypdf` is chosen over PyMuPDF deliberately: it is pure Python, so
# MAGIC `%pip install` on a Databricks cluster has no binary dependency to fight.
# MAGIC
# MAGIC Extracted text is **persisted** to `paper_sections`. Re-chunking or changing the
# MAGIC embedding model is then a re-encode, never a re-crawl.

# COMMAND ----------

# DBTITLE 1,Parse PDFs and Upsert paper_sections
import io
import re
import unicodedata
from collections import Counter

# Canonical name -> heading variants seen in the wild. Longest variants are listed
# first so that "results and discussion" is not shadowed by "discussion".
SECTION_SYNONYMS = {
    "conclusion": ("conclusions and future work", "conclusion and future work",
                   "concluding remarks", "summary and conclusions",
                   "conclusions", "conclusion"),
    "discussion": ("results and discussion", "discussion and limitations",
                   "discussions", "discussion"),
    "methods":    ("materials and methods", "methods and materials",
                   "experimental setup", "experimental design",
                   "methodology", "methods", "method", "approach"),
}

# Headings that end the body. Everything after them is citations or back matter.
STOP_HEADINGS = ("references", "bibliography", "acknowledgements", "acknowledgments",
                 "appendix", "appendices", "supplementary material")

# A heading line: optional numbering ("4", "4.2", "IV."), then the words, and little
# else on the line. Anchored to a whole line so prose mentioning "the discussion
# above" cannot be mistaken for a section start.
_HEADING_RE = re.compile(
    r"^[ \t]*(?:\d{1,2}(?:\.\d{1,2})*\.?|[IVXivx]{1,5}\.)?[ \t]*"
    r"([A-Za-z][A-Za-z \t&/-]{2,60}?)[ \t]*:?[ \t]*$",
    re.MULTILINE,
)


def _clean_pdf_text(raw: str) -> str:
    """Normalise ligatures, hyphenated line breaks and runaway whitespace."""
    text = unicodedata.normalize("NFKC", raw)
    text = text.replace("­", "")            # soft hyphen                 # soft hyphen
    text = re.sub(r"-\n(?=[a-z])", "", text)          # de-hyphenate across line breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _canonical_heading(line: str) -> str | None:
    """Map a candidate heading to a canonical section name, or to a stop marker."""
    norm = re.sub(r"[^a-z ]", "", line.lower()).strip()
    if not norm:
        return None
    for stop in STOP_HEADINGS:
        if norm == stop or norm.startswith(stop):
            return "__stop__"
    for canonical, variants in SECTION_SYNONYMS.items():
        for variant in variants:
            if norm == variant:
                return canonical
    return None


def extract_sections(pdf_text: str, wanted: list[str]) -> dict[str, str]:
    """
    Slice `pdf_text` into {canonical_section_name: text} for the wanted sections.

    Every recognised heading is a boundary - including ones we do not want - so a
    section ends where the *next* section begins rather than running to the end of
    the document. Returns {} when nothing is recognisable; that is `no_sections`,
    not an error.
    """
    boundaries = []
    for match in _HEADING_RE.finditer(pdf_text):
        canonical = _canonical_heading(match.group(1))
        if canonical:
            boundaries.append((match.start(), match.end(), canonical))

    if not boundaries:
        return {}

    sections: dict[str, str] = {}
    for i, (_start, end, canonical) in enumerate(boundaries):
        if canonical == "__stop__":
            break
        if canonical not in wanted:
            continue
        next_start = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(pdf_text)
        body = pdf_text[end:next_start].strip()
        # A paper may repeat a heading (per-experiment "Methods"); keep the longest.
        if body and len(body) > len(sections.get(canonical, "")):
            sections[canonical] = body
    return sections


section_rows = []

if FETCH_FULLTEXT and pdf_bytes_by_paper:
    from pypdf import PdfReader

    for paper_id, blob in pdf_bytes_by_paper.items():
        try:
            reader = PdfReader(io.BytesIO(blob))
            raw = "\n".join((page.extract_text() or "") for page in reader.pages)
            text = _clean_pdf_text(raw)

            # A scanned PDF parses without error and yields almost nothing.
            if len(text) < SECTION_MIN_CHARS:
                fulltext_status[paper_id] = "parse_failed"
                continue

            found = extract_sections(text, WANTED_SECTIONS)
            found = {k: v for k, v in found.items() if len(v) >= SECTION_MIN_CHARS}

            if not found:
                fulltext_status[paper_id] = "no_sections"
                continue

            for name, body in found.items():
                section_rows.append((paper_id, name, body, len(body)))
            fulltext_status[paper_id] = "ok"

        except Exception as exc:            # noqa: BLE001 - one bad PDF must not stop the run
            fulltext_status[paper_id] = "parse_failed"
            print(f"  [warn] parse_failed {paper_id}: {type(exc).__name__}: {str(exc)[:120]}")

# Persist the sections and the per-paper outcome together.
if fulltext_status or section_rows:
    conn = psycopg2.connect(
        host=db_host, port=db_port, dbname=db_name,
        user=db_user, password=db_password, sslmode='require'
    )
    with conn:
        with conn.cursor() as cur:
            if section_rows:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO paper_sections (paper_id, section_name, section_text, char_count)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (paper_id, section_name) DO UPDATE SET
                        section_text = EXCLUDED.section_text,
                        char_count   = EXCLUDED.char_count,
                        extracted_at = now();
                """, section_rows, page_size=50)

            # The attempt counter increments on every attempt, successful or not:
            # it is a record of work done, and it is what stops a permanently dead
            # URL from being retried on every scheduled run forever.
            psycopg2.extras.execute_batch(cur, f"""
                UPDATE {PAPERS_TABLE_NAME}
                   SET fulltext_status = %s,
                       fulltext_checked_at = now(),
                       fulltext_attempts = fulltext_attempts + 1
                 WHERE paper_id = %s;
            """, [(status, pid) for pid, status in fulltext_status.items()], page_size=100)
    conn.close()

status_counts = Counter(fulltext_status.values())
print(f"Extracted {len(section_rows)} section(s) from {len(pdf_bytes_by_paper)} PDF(s).")
print(f"  - Section mix: {dict(Counter(r[1] for r in section_rows))}")
print(f"  - Status mix:  {dict(status_counts)}")
if status_counts.get("no_sections"):
    print(f"  - NOTE: {status_counts['no_sections']} paper(s) parsed but had no recognisable "
          f"headings. They stay searchable by abstract. If this count is high, add the "
          f"missing heading variants to SECTION_SYNONYMS and clear their fulltext_status.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Incremental Delta: Identify Unembedded Abstracts, Sections & Notes
# MAGIC
# MAGIC Uses an SQL **anti-join** (`LEFT JOIN ... WHERE id IS NULL`) to retrieve only records without corresponding vector representations in pgvector.
# MAGIC
# MAGIC **Granularity changed in Phase 2.** The anti-join used to ask *"does this paper have
# MAGIC any embedding?"*, which would permanently skip every paper whose abstract was already
# MAGIC embedded — exactly the papers whose new sections need embedding. It now asks
# MAGIC *"does an embedding exist for this (paper, section)?"*, with `section_name IS NULL`
# MAGIC meaning the abstract.

# COMMAND ----------

# DBTITLE 1,Fetch Unembedded Records (Anti-Join)
import pandas as pd

conn = psycopg2.connect(
    host=db_host, port=db_port, dbname=db_name,
    user=db_user, password=db_password, sslmode='require'
)

# 1. Abstracts with no abstract-embedding yet (section_name IS NULL = the abstract)
papers_query = f"""
SELECT p.paper_id, p.title, p.abstract
FROM {PAPERS_TABLE_NAME} p
LEFT JOIN {EMBEDDINGS_TABLE_NAME} pe
       ON pe.paper_id = p.paper_id
      AND pe.section_name IS NULL
WHERE pe.id IS NULL 
  AND p.abstract IS NOT NULL 
  AND trim(p.abstract) != '';
"""
unembedded_papers_df = pd.read_sql_query(papers_query, conn)

# 2. Extracted sections with no embedding yet, for this run's wanted sections
sections_query = f"""
SELECT s.paper_id, s.section_name, s.section_text, p.title
FROM paper_sections s
JOIN {PAPERS_TABLE_NAME} p ON p.paper_id = s.paper_id
LEFT JOIN {EMBEDDINGS_TABLE_NAME} pe
       ON pe.paper_id = s.paper_id
      AND pe.section_name = s.section_name
WHERE pe.id IS NULL
  AND s.section_name = ANY(%(wanted)s);
"""
unembedded_sections_df = pd.read_sql_query(sections_query, conn, params={"wanted": WANTED_SECTIONS})

# 3. Unembedded notes
notes_query = f"""
SELECT n.note_id, n.note_text
FROM notes n
LEFT JOIN {NOTE_EMBEDDINGS_TABLE_NAME} ne ON ne.note_id = n.note_id
WHERE ne.id IS NULL 
  AND n.note_text IS NOT NULL 
  AND trim(n.note_text) != '';
"""
unembedded_notes_df = pd.read_sql_query(notes_query, conn)

conn.close()

print(f"📊 Delta Status:")
print(f"  • Unembedded Abstracts Found: {len(unembedded_papers_df)}")
print(f"  • Unembedded Sections Found:  {len(unembedded_sections_df)}")
print(f"  • Unembedded Notes Found:     {len(unembedded_notes_df)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Sliding Window Text Chunking
# MAGIC
# MAGIC Breaks abstracts, extracted sections and note texts into overlapping character
# MAGIC chunks (`chunk_size=4000`, `chunk_overlap=400`). Prepends the paper title so the
# MAGIC first chunk of every unit carries its topical anchor.
# MAGIC
# MAGIC At 4000 characters a whole abstract is a single chunk. The window earns its keep on
# MAGIC section text, which is far longer. Each chunk records the `section_name` it came
# MAGIC from (`None` = abstract), so the UI and the agent can say *"from the Conclusion"*.

# COMMAND ----------

# DBTITLE 1,Execute Sliding Window Chunking
def chunk_document(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping character windows."""
    if not text or not text.strip():
        return []
    text = text.strip()
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    step = chunk_size - overlap
    while start < len(text):
        chunk = text[start : start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        start += step
    return chunks

paper_chunk_rows = []

# 1. Abstracts -> section_name None
for _, row in unembedded_papers_df.iterrows():
    paper_id = str(row["paper_id"])
    title = str(row["title"]) if row["title"] else ""
    abstract = str(row["abstract"]) if row["abstract"] else ""
    full_text = f"{title}. {abstract}" if title else abstract

    for idx, c in enumerate(chunk_document(full_text, CHUNK_SIZE, CHUNK_OVERLAP)):
        paper_chunk_rows.append({
            "paper_id": paper_id,
            "chunk_index": idx,
            "chunk_text": c,
            "section_name": None,
        })

# 2. Extracted sections -> section_name set
#
# The title is prepended here for the same reason it is on abstracts: a Conclusion
# chunk often reads "we find that X improves Y by 3%" with no mention of the domain,
# which makes it nearly unretrievable by a topical query. chunk_index restarts per
# section; (paper_id, section_name, chunk_index) is what identifies a chunk now.
for _, row in unembedded_sections_df.iterrows():
    paper_id = str(row["paper_id"])
    title = str(row["title"]) if row["title"] else ""
    section_name = str(row["section_name"])
    section_text = str(row["section_text"]) if row["section_text"] else ""
    anchored = f"{title}. {section_text}" if title else section_text

    for idx, c in enumerate(chunk_document(anchored, CHUNK_SIZE, CHUNK_OVERLAP)):
        paper_chunk_rows.append({
            "paper_id": paper_id,
            "chunk_index": idx,
            "chunk_text": c,
            "section_name": section_name,
        })

paper_chunks_df = pd.DataFrame(
    paper_chunk_rows, columns=["paper_id", "chunk_index", "chunk_text", "section_name"]
)
_n_abstract = int((paper_chunks_df["section_name"].isna()).sum()) if len(paper_chunks_df) else 0
print(f"✅ Generated {len(paper_chunks_df)} paper chunks "
      f"({_n_abstract} abstract, {len(paper_chunks_df) - _n_abstract} section) "
      f"from {len(unembedded_papers_df)} abstract(s) and {len(unembedded_sections_df)} section(s).")

# Chunk notes
note_chunk_rows = []
for _, row in unembedded_notes_df.iterrows():
    note_id = str(row["note_id"])
    note_text = str(row["note_text"]) if row["note_text"] else ""
    chunks = chunk_document(note_text, CHUNK_SIZE, CHUNK_OVERLAP)
    for c in chunks:
        note_chunk_rows.append({
            "note_id": note_id,
            "chunk_text": c
        })

note_chunks_df = pd.DataFrame(note_chunk_rows)
print(f"✅ Generated {len(note_chunks_df)} chunks from {len(unembedded_notes_df)} notes.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Batch Vector Encoding with `sentence-transformers`
# MAGIC
# MAGIC Computes 768-dimensional dense vectors with unit-normalization (`normalize_embeddings=True`).
# MAGIC
# MAGIC Each chunk is prefixed with `DOCUMENT_PREFIX` **at encode time only** - the text
# MAGIC stored in `chunk_text` stays clean, because the dashboard renders it directly as
# MAGIC the search-result snippet.

# COMMAND ----------

# DBTITLE 1,Generate Dense Neural Embeddings
from sentence_transformers import SentenceTransformer

# Set cache directories
os.environ["HF_HOME"] = "/tmp/.cache/huggingface"
os.environ["TRANSFORMERS_CACHE"] = "/tmp/.cache/huggingface"

print(f"🧠 Loading embedding model '{EMBEDDING_MODEL_NAME}'...")
embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME, cache_folder="/tmp/.cache/huggingface")

# Fail fast rather than writing vectors the schema will reject (or, worse, accept
# at the wrong width because EMBEDDING_DIM drifted from the model).
_actual_dim = embedding_model.get_sentence_embedding_dimension()
if _actual_dim != EMBEDDING_DIM:
    raise ValueError(
        f"{EMBEDDING_MODEL_NAME} outputs {_actual_dim} dims but EMBEDDING_DIM is {EMBEDDING_DIM}. "
        f"Fix the match/case above and the VECTOR(n) columns before ingesting."
    )
print(f"✅ Model ready: {_actual_dim}-dim, document prefix {DOCUMENT_PREFIX!r}")

# Encode paper chunks
if len(paper_chunks_df) > 0:
    print(f"Computing embeddings for {len(paper_chunks_df)} paper chunks in batches of {BATCH_SIZE}...")
    paper_vectors = embedding_model.encode(
        [DOCUMENT_PREFIX + t for t in paper_chunks_df["chunk_text"].tolist()],
        batch_size=BATCH_SIZE,
        show_progress_bar=True,
        normalize_embeddings=True,
    )
    paper_chunks_df["embedding"] = [v.tolist() for v in paper_vectors]
    print(f"✅ Generated {len(paper_vectors)} paper chunk vectors.")

# Encode note chunks
if len(note_chunks_df) > 0:
    print(f"Computing embeddings for {len(note_chunks_df)} note chunks...")
    note_vectors = embedding_model.encode(
        [DOCUMENT_PREFIX + t for t in note_chunks_df["chunk_text"].tolist()],
        batch_size=BATCH_SIZE,
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    note_chunks_df["embedding"] = [v.tolist() for v in note_vectors]
    print(f"✅ Generated {len(note_vectors)} note chunk vectors.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10. Batch Insert Embeddings into Lakebase pgvector
# MAGIC
# MAGIC Uses `psycopg2.extras.execute_batch` to bulk persist vectors into `paper_embeddings` and `note_embeddings` with `%s::vector(768)` type casting (the cast is interpolated from `EMBEDDING_DIM`, so it follows the model).

# COMMAND ----------

# DBTITLE 1,Persist Vectors to pgvector Tables
conn = psycopg2.connect(
    host=db_host, port=db_port, dbname=db_name,
    user=db_user, password=db_password, sslmode='require'
)

# 1. Insert paper chunk embeddings
if len(paper_chunks_df) > 0:
    print(f"💾 Inserting {len(paper_chunks_df)} paper chunk vectors into '{EMBEDDINGS_TABLE_NAME}'...")
    paper_insert_data = [
        (
            row["paper_id"],
            int(row["chunk_index"]),
            row["chunk_text"],
            # pandas turns a column of None into NaN; pass a real NULL instead, since
            # section_name IS NULL is what the delta anti-join tests for.
            None if pd.isna(row["section_name"]) else str(row["section_name"]),
            f"[{','.join(str(float(x)) for x in row['embedding'])}]"
        )
        for _, row in paper_chunks_df.iterrows()
    ]
    
    paper_insert_sql = f"""
    INSERT INTO {EMBEDDINGS_TABLE_NAME} (paper_id, chunk_index, chunk_text, section_name, embedding)
    VALUES (%s, %s, %s, %s, %s::vector({EMBEDDING_DIM}));
    """
    
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, paper_insert_sql, paper_insert_data, page_size=100)
    conn.commit()
    print(f"✅ Successfully inserted {len(paper_insert_data)} paper vectors.")

# 2. Insert note chunk embeddings
if len(note_chunks_df) > 0:
    print(f"💾 Inserting {len(note_chunks_df)} note chunk vectors into '{NOTE_EMBEDDINGS_TABLE_NAME}'...")
    note_insert_data = [
        (
            row["note_id"],
            row["chunk_text"],
            f"[{','.join(str(float(x)) for x in row['embedding'])}]"
        )
        for _, row in note_chunks_df.iterrows()
    ]
    
    note_insert_sql = f"""
    INSERT INTO {NOTE_EMBEDDINGS_TABLE_NAME} (note_id, chunk_text, embedding)
    VALUES (%s, %s, %s::vector({EMBEDDING_DIM}));
    """
    
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, note_insert_sql, note_insert_data, page_size=50)
    conn.commit()
    print(f"✅ Successfully inserted {len(note_insert_data)} note vectors.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 11. Verification & Similarity Search Test
# MAGIC
# MAGIC Corpus composition, full-text acquisition outcomes, and two cosine searches.
# MAGIC
# MAGIC **Two query types, deliberately.** A *depth* query should surface Conclusion or
# MAGIC Discussion chunks; a *discovery* query should still surface abstracts. Adding body
# MAGIC text is only a win if it does not push topical discovery out of the top results,
# MAGIC so both are run and both are printed.

# COMMAND ----------

# DBTITLE 1,Corpus Composition & Full-Text Outcomes
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute(f"""
        SELECT coalesce(section_name, 'abstract') AS part, count(*) AS chunks
        FROM {EMBEDDINGS_TABLE_NAME} GROUP BY 1 ORDER BY 2 DESC;
    """)
    part_mix = cur.fetchall()

    cur.execute(f"""
        SELECT coalesce(fulltext_status, 'not_attempted') AS status, count(*) AS papers
        FROM {PAPERS_TABLE_NAME} GROUP BY 1 ORDER BY 2 DESC;
    """)
    status_mix = cur.fetchall()

    cur.execute(f"SELECT count(*) AS n, vector_dims(embedding) AS dims FROM {EMBEDDINGS_TABLE_NAME} GROUP BY 2;")
    dim_mix = cur.fetchall()

print("📦 Chunks by part of paper:")
for row in part_mix:
    print(f"  • {row['part']:<12} {row['chunks']}")

print("\n📄 Full-text acquisition outcome:")
for row in status_mix:
    print(f"  • {row['status']:<14} {row['papers']}")

print("\n📐 Stored vector dimensions:")
for row in dim_mix:
    print(f"  • {row['dims']}-dim: {row['n']} rows")
if len(dim_mix) > 1:
    print("  ⚠️  MIXED DIMENSIONS — the corpus spans two models. Re-run the 768 migration.")

# COMMAND ----------

# DBTITLE 1,Run Test Cosine Similarity Queries
# The query prefix matters. modernbert-embed is asymmetric, and an unprefixed test
# query scores lower against these search_document: vectors - making a perfectly
# healthy index look broken. This mirrors dashboard/embedding.py::encode_query.
TEST_QUERIES = [
    ("depth",     "what limitations did the authors report"),
    ("discovery", "how do transformers reduce attention cost"),
]

search_sql = f"""
SELECT
    p.title,
    p.publication_year,
    p.venue,
    pe.chunk_index,
    coalesce(pe.section_name, 'abstract') AS part,
    pe.chunk_text,
    1 - (pe.embedding <=> %s::vector({EMBEDDING_DIM})) AS similarity_score
FROM {EMBEDDINGS_TABLE_NAME} pe
JOIN {PAPERS_TABLE_NAME} p ON p.paper_id = pe.paper_id
ORDER BY pe.embedding <=> %s::vector({EMBEDDING_DIM})
LIMIT 3;
"""

for kind, test_query in TEST_QUERIES:
    print(f"\n{'=' * 72}\n🔍 [{kind}] {test_query!r}\n{'=' * 72}")

    query_vector = embedding_model.encode(
        QUERY_PREFIX + test_query, normalize_embeddings=True
    ).tolist()
    query_vector_str = f"[{','.join(str(float(x)) for x in query_vector)}]"

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(search_sql, (query_vector_str, query_vector_str))
        top_matches = cur.fetchall()

    for rank, match in enumerate(top_matches, 1):
        print(f"\n[{rank}] {match['similarity_score']:.4f} | {match['part'].upper():<10} | "
              f"{match['title']} ({match.get('publication_year', 'N/A')})")
        print(f"    Venue: {match.get('venue')}")
        print(f"    Excerpt: {match['chunk_text'][:200]}...")
        if "search_document:" in match["chunk_text"] or "search_query:" in match["chunk_text"]:
            print("    ⚠️  PREFIX LEAKED INTO chunk_text — it must be applied at encode time only.")

conn.close()
print("\n🎉 Notebook execution complete! Lakebase vector index is active and ready for Agentic RAG.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 12. Close the Run Ledger
# MAGIC
# MAGIC Writes what this run actually did, then releases the advisory lock.
# MAGIC
# MAGIC This is the cell that makes the schedule tunable: two rows of `pipeline_runs`
# MAGIC answer *"was last night worth it?"* without reading driver logs that no longer
# MAGIC exist. A run that harvests 40 papers and embeds 0 chunks is a very different
# MAGIC problem from one that harvests 0, and only the granular counts distinguish them.

# COMMAND ----------

# DBTITLE 1,Record Run Outcome and Release the Lock
_counts = {
    "papers_harvested": len(harvested_papers),
    "papers_inserted": papers_inserted,
    "papers_enriched": len(s2_by_doi),
    "s2_requests": s2_request_count,
    "pdfs_fetched": len(pdf_bytes_by_paper),
    "sections_extracted": len(section_rows),
    "chunks_embedded": len(paper_chunks_df),
    "notes_embedded": len(note_chunks_df),
}

with lock_conn.cursor() as cur:
    cur.execute("""
        UPDATE pipeline_runs SET
            status             = 'ok',
            finished_at        = now(),
            duration_seconds   = %(duration)s,
            papers_harvested   = %(papers_harvested)s,
            papers_inserted    = %(papers_inserted)s,
            papers_enriched    = %(papers_enriched)s,
            s2_requests        = %(s2_requests)s,
            pdfs_fetched       = %(pdfs_fetched)s,
            sections_extracted = %(sections_extracted)s,
            chunks_embedded    = %(chunks_embedded)s,
            notes_embedded     = %(notes_embedded)s
        WHERE run_id = %(run_id)s;
    """, {**_counts, "duration": _time.perf_counter() - RUN_STARTED, "run_id": RUN_ID})

print(f"Run {RUN_ID} recorded:")
for key, value in _counts.items():
    print(f"  • {key:<19} {value}")
print(f"  • {'duration_seconds':<19} {_time.perf_counter() - RUN_STARTED:.1f}")

# Releasing the lock explicitly is tidier than relying on the close, and closing the
# connection releases it regardless - which is the failure path we actually want.
with lock_conn.cursor() as cur:
    cur.execute("SELECT pg_advisory_unlock(%s);", (PIPELINE_LOCK_KEY,))
lock_conn.close()

print("\nLock released. Compare against the previous run:")
print("  SELECT started_at, papers_inserted, chunks_embedded, s2_requests, duration_seconds")
print("    FROM pipeline_runs WHERE status = 'ok' ORDER BY started_at DESC LIMIT 5;")
