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
    dbutils.widgets.text("max_pdf_candidates", "3", "Candidate PDF URLs to try per paper")
    dbutils.widgets.dropdown("model_source", "volume", ["volume", "huggingface"], "Where to load the embedding model from")
    dbutils.widgets.text("model_volume_path",
                         "/Volumes/workspace/default/models/modernbert-embed-base",
                         "Persisted model directory (see setup_embedding_model.py)")
    dbutils.widgets.dropdown("run_trigger", "manual", ["manual", "scheduled"], "How this run was started")
    dbutils.widgets.text("research_fields", "computer science",
                         "Research fields (comma-separated; blank = all of science)")
    dbutils.widgets.dropdown("relevance_gate_enabled", "true", ["true", "false"], "Apply the semantic relevance gate?")
    dbutils.widgets.text("relevance_threshold", "0.40", "Minimum cosine similarity to accept a candidate")
    dbutils.widgets.dropdown("score_unscored_papers", "true", ["true", "false"], "Score existing unscored papers?")

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
    MAX_PDF_CANDIDATES = int(dbutils.widgets.get("max_pdf_candidates"))
    MODEL_SOURCE = dbutils.widgets.get("model_source").strip().lower()
    MODEL_VOLUME_PATH = dbutils.widgets.get("model_volume_path").strip().rstrip("/")
    RUN_TRIGGER = dbutils.widgets.get("run_trigger").strip().lower()
    RESEARCH_FIELDS = [f.strip() for f in dbutils.widgets.get("research_fields").split(",") if f.strip()]
    RELEVANCE_GATE_ENABLED = dbutils.widgets.get("relevance_gate_enabled").lower() == "true"
    RELEVANCE_THRESHOLD = float(dbutils.widgets.get("relevance_threshold"))
    SCORE_UNSCORED_PAPERS = dbutils.widgets.get("score_unscored_papers").lower() == "true"
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
    MAX_PDF_CANDIDATES = 3
    MODEL_SOURCE = os.getenv("MODEL_SOURCE", "huggingface")
    MODEL_VOLUME_PATH = os.getenv("MODEL_VOLUME_PATH", "/tmp/models/modernbert-embed-base")
    RUN_TRIGGER = "manual"
    RESEARCH_FIELDS = ["computer science"]
    RELEVANCE_GATE_ENABLED = True
    RELEVANCE_THRESHOLD = 0.40
    SCORE_UNSCORED_PAPERS = True

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
print(f"  • Research fields: {RESEARCH_FIELDS or '(all of science)'}")
print(f"  • Relevance gate: {'on' if RELEVANCE_GATE_ENABLED else 'off'} @ {RELEVANCE_THRESHOLD}")
print(f"  • Model source: {MODEL_SOURCE}"
      + (f" -> {MODEL_VOLUME_PATH}" if MODEL_SOURCE == "volume" else " (Hugging Face Hub)"))

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
        # Recorded so a run that fell back to the Hub is visible afterwards, not
        # only in the driver log that dies with the cluster.
        "research_fields": RESEARCH_FIELDS,
        "relevance_gate_enabled": RELEVANCE_GATE_ENABLED,
        "relevance_threshold": RELEVANCE_THRESHOLD,
        "model_source": MODEL_SOURCE,
        "model_volume_path": MODEL_VOLUME_PATH if MODEL_SOURCE == "volume" else None,
    })))
    RUN_ID = cur.fetchone()[0]

print(f"Run {RUN_ID} claimed the pipeline lock (trigger={RUN_TRIGGER}).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2c. Load the Embedding Model
# MAGIC
# MAGIC The model is loaded from a **Unity Catalog Volume**, written once by
# MAGIC `notebooks/setup_embedding_model.py`. It is not downloaded from the Hugging Face
# MAGIC Hub on a normal run: a job cluster is created fresh per run, so the Hub download
# MAGIC (568 MB, throttled when unauthenticated) was costing ~18 minutes *per execution* —
# MAGIC more time acquiring the model than processing data.
# MAGIC
# MAGIC ```text
# MAGIC Hugging Face -> setup_embedding_model.py -> UC Volume -> here
# MAGIC ```
# MAGIC
# MAGIC **This cell fails rather than falling back.** A missing path in `volume` mode
# MAGIC raises, because an automatic re-download would let a production misconfiguration
# MAGIC present as a merely slow run — which is the exact dependency this design removes.
# MAGIC `model_source=huggingface` exists for development and recovery, is deliberate, and
# MAGIC announces itself loudly.
# MAGIC
# MAGIC **Loaded before discovery (Phase 2.8).** The relevance gate scores every
# MAGIC candidate at discovery time, so the model has to exist by then. It used to load
# MAGIC just before encoding.
# MAGIC
# MAGIC ### The contract check
# MAGIC
# MAGIC `embedding_contract.json` sits beside the model and records what the stored
# MAGIC vectors mean: base model, dimension, both task prefixes, normalisation. Those four
# MAGIC values are otherwise kept aligned **by hand** across this notebook,
# MAGIC `dashboard/config.py`, `mcp_server/config.py` and `sql/`, with only the dimension
# MAGIC checked at runtime. Validating the contract closes the other three.

# COMMAND ----------

# DBTITLE 1,Load the Model and Validate the Embedding Contract
import json

from sentence_transformers import SentenceTransformer

CONTRACT_FILENAME = "embedding_contract.json"

# Databricks renders tqdm through ipywidgets, which spammed the run log with
# "Loading the widget is taking longer than expected" a dozen times and rendered
# nothing useful. The batch counter below is more informative anyway.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")


def embedding_dimension(model) -> int:
    """
    sentence-transformers 6.0 renamed get_sentence_embedding_dimension() to
    get_embedding_dimension(). Support both, so this notebook is not pinned to one
    runtime's library version.
    """
    for name in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
        fn = getattr(model, name, None)
        if callable(fn):
            return int(fn())
    raise AttributeError("SentenceTransformer exposes no embedding-dimension accessor.")


def validate_embedding_contract(stored: dict, expected: dict) -> None:
    """
    Compare the contract saved beside the model against this notebook's constants.

    Raises on the first disagreement, naming the field. A mismatch here means the
    vectors about to be written would not share a space with the ones already
    stored - which nothing downstream can detect, because a wrong-prefix or
    wrong-model vector is still a perfectly valid 768-dim unit vector.
    """
    if not isinstance(stored, dict):
        raise ValueError(f"{CONTRACT_FILENAME} is not a JSON object.")

    for field, want in expected.items():
        if field not in stored:
            raise ValueError(
                f"{CONTRACT_FILENAME} is missing {field!r}. It was written by an older "
                f"version of setup_embedding_model.py - re-run it to refresh the contract."
            )
        got = stored[field]
        if got != want:
            raise ValueError(
                f"Embedding contract mismatch on {field!r}: the stored model says {got!r}, "
                f"this pipeline expects {want!r}.\n"
                f"Vectors written under a mismatched contract are silently unusable. "
                f"Either re-run setup_embedding_model.py with the right values, or fix "
                f"this notebook's configuration - do not proceed."
            )


EXPECTED_CONTRACT = {
    "base_model": EMBEDDING_MODEL_NAME,
    "embedding_dim": EMBEDDING_DIM,
    "document_prefix": DOCUMENT_PREFIX,
    "query_prefix": QUERY_PREFIX,
    "normalize": True,
}

if MODEL_SOURCE == "volume":
    if not os.path.isdir(MODEL_VOLUME_PATH):
        raise RuntimeError(
            f"Embedding model not found at {MODEL_VOLUME_PATH}.\n\n"
            f"Run notebooks/setup_embedding_model.py once to persist it there.\n\n"
            f"This is deliberately fatal: falling back to a Hugging Face download would "
            f"hide the misconfiguration behind a slow run, and re-introduce the per-run "
            f"download this path exists to remove. For a development run, set "
            f"model_source=huggingface explicitly."
        )

    contract_path = os.path.join(MODEL_VOLUME_PATH, CONTRACT_FILENAME)
    if not os.path.isfile(contract_path):
        raise RuntimeError(
            f"{contract_path} is missing. The directory holds a model but nothing "
            f"records what its vectors mean. Re-run setup_embedding_model.py."
        )

    with open(contract_path, encoding="utf-8") as fh:
        stored_contract = json.load(fh)

    # Cheap check first: a mismatch fails in milliseconds rather than after loading
    # 568 MB of weights.
    validate_embedding_contract(stored_contract, EXPECTED_CONTRACT)
    print(f"Embedding contract verified against {contract_path}")

    print(f"Loading embedding model from {MODEL_VOLUME_PATH} (no Hub request)...")
    started = time.perf_counter()
    embedding_model = SentenceTransformer(MODEL_VOLUME_PATH)
    print(f"  loaded in {time.perf_counter() - started:.1f}s")

elif MODEL_SOURCE == "huggingface":
    print("=" * 72)
    print("DEVELOPMENT MODE: downloading the embedding model from the Hugging Face Hub.")
    print("This is not the production path. A scheduled run should use model_source=volume")
    print("with a model persisted by notebooks/setup_embedding_model.py.")
    print("=" * 72)
    started = time.perf_counter()
    embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    print(f"  downloaded and loaded in {time.perf_counter() - started:.1f}s")

else:
    raise ValueError(f"Unknown model_source {MODEL_SOURCE!r}. Expected 'volume' or 'huggingface'.")

# Retained from Phase 1: fail fast rather than writing vectors the schema will
# reject - or, worse, accept at the wrong width because EMBEDDING_DIM drifted.
_actual_dim = embedding_dimension(embedding_model)
if _actual_dim != EMBEDDING_DIM:
    raise ValueError(
        f"{EMBEDDING_MODEL_NAME} outputs {_actual_dim} dims but EMBEDDING_DIM is {EMBEDDING_DIM}. "
        f"Fix the match/case in the config cell and the VECTOR(n) columns before ingesting."
    )
print(f"✅ Model ready: {_actual_dim}-dim, document prefix {DOCUMENT_PREFIX!r}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Paper Discovery
# MAGIC
# MAGIC Finds candidate papers for each topic. **OpenAlex is currently the only discovery
# MAGIC provider, but it is not baked in.** Everything OpenAlex-specific — the `search`
# MAGIC parameter, the field filter syntax, cursor paging, inverted-index abstracts, the
# MAGIC `W`-prefix — lives behind `discover_openalex(...)`, which returns provider-neutral
# MAGIC `PaperCandidate` records. Nothing downstream knows where a candidate came from
# MAGIC except through its `source` field.
# MAGIC
# MAGIC Adding arXiv or PubMed/PMC later means writing another `discover_*` function that
# MAGIC returns the same shape. See plan §2.8.7.
# MAGIC
# MAGIC ### Field context
# MAGIC
# MAGIC `research_fields` is a **generic** setting written in plain names
# MAGIC (`computer science, mathematics`). Translating it into
# MAGIC `primary_topic.field.id:fields/17` is OpenAlex's private business; arXiv would
# MAGIC translate the same input into `cat:cs.*` and PubMed into MeSH terms.
# MAGIC
# MAGIC Without it, `search=` matches words across all of science: measured 2026-09-16,
# MAGIC *"vector database indexing"* returned MegaBLAST, the Ribosomal Database Project
# MAGIC and BLAST+.
# MAGIC
# MAGIC ### Incremental discovery
# MAGIC
# MAGIC A topic is *seeded* by relevance the first time it is seen — a cold corpus needs
# MAGIC the canonical papers, not last week's preprints — and thereafter queried by
# MAGIC `from_publication_date`, newest first, from a per-topic watermark.

# COMMAND ----------

# DBTITLE 1,Discovery Provider: OpenAlex
import datetime
import json
import time

import pandas as pd
import requests

OPENALEX_EMAIL = get_openalex_email()
S2_API_KEY = get_semantic_scholar_api_key()

OPENALEX_SELECT = (
    "id,doi,title,publication_year,publication_date,cited_by_count,"
    "primary_location,abstract_inverted_index,open_access,"
    # Phase 2.6: the repository copies. open_access.oa_url alone is frequently the
    # publisher's landing page, which is both robot-blocked and not a PDF.
    "best_oa_location,locations"
)

# OpenAlex's own field taxonomy (https://api.openalex.org/fields). This map is the
# ONLY place a generic research field becomes an OpenAlex concept - which is the
# point: another provider translates the same names its own way.
OPENALEX_FIELD_IDS = {
    "agricultural and biological sciences": 11,
    "arts and humanities": 12,
    "biochemistry, genetics and molecular biology": 13,
    "business, management and accounting": 14,
    "chemical engineering": 15,
    "chemistry": 16,
    "computer science": 17,
    "decision sciences": 18,
    "earth and planetary sciences": 19,
    "economics, econometrics and finance": 20,
    "energy": 21,
    "engineering": 22,
    "environmental science": 23,
    "immunology and microbiology": 24,
    "materials science": 25,
    "mathematics": 26,
    "medicine": 27,
    "neuroscience": 28,
    "nursing": 29,
    "pharmacology, toxicology and pharmaceutics": 30,
    "physics and astronomy": 31,
    "psychology": 32,
    "social sciences": 33,
    "veterinary": 34,
    "dentistry": 35,
    "health professions": 36,
}

# Everyday shorthand, so the widget does not demand OpenAlex's exact wording.
OPENALEX_FIELD_ALIASES = {
    "cs": "computer science",
    "computing": "computer science",
    "math": "mathematics",
    "maths": "mathematics",
    "physics": "physics and astronomy",
    "astronomy": "physics and astronomy",
    "biology": "agricultural and biological sciences",
    "biochemistry": "biochemistry, genetics and molecular biology",
    "genetics": "biochemistry, genetics and molecular biology",
    "molecular biology": "biochemistry, genetics and molecular biology",
    "economics": "economics, econometrics and finance",
    "finance": "economics, econometrics and finance",
    "business": "business, management and accounting",
    "earth science": "earth and planetary sciences",
    "pharmacology": "pharmacology, toxicology and pharmaceutics",
    "microbiology": "immunology and microbiology",
    "immunology": "immunology and microbiology",
}


def openalex_field_filter(research_fields: list[str]) -> str | None:
    """
    Translate generic field names into OpenAlex's filter syntax.

    Returns None when no fields are configured, which means "search all of science"
    - the pre-Phase-2.8 behaviour, available deliberately but not by accident.

    An unrecognised name RAISES rather than being skipped. A typo like
    "comptuer science" would otherwise silently drop the filter and reintroduce
    exactly the bug this function exists to fix, with no symptom except a corpus
    slowly filling with biology again.
    """
    if not research_fields:
        return None

    ids, unknown = [], []
    for raw in research_fields:
        key = (raw or "").strip().lower()
        if not key:
            continue
        key = OPENALEX_FIELD_ALIASES.get(key, key)
        if key in OPENALEX_FIELD_IDS:
            ids.append(f"fields/{OPENALEX_FIELD_IDS[key]}")
        else:
            unknown.append(raw)

    if unknown:
        raise ValueError(
            f"Unknown research field(s): {unknown}. "
            f"Known fields: {', '.join(sorted(OPENALEX_FIELD_IDS))}. "
            f"Fix the research_fields widget - an unrecognised name would otherwise "
            f"disable field filtering entirely."
        )
    if not ids:
        return None

    # "|" is OR in OpenAlex filter syntax, so interdisciplinary work can span fields.
    return "primary_topic.field.id:" + "|".join(dict.fromkeys(ids))


def _openalex_to_candidate(item: dict) -> dict | None:
    """
    One OpenAlex work -> a provider-neutral PaperCandidate, or None if unusable.

    PaperCandidate is the contract every discovery provider returns:

        source, source_id, doi, title, abstract, publication_year,
        publication_date, venue, citation_count, open_access_url, raw

    `raw` carries the provider payload through for enrichment and traceability.
    """
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

    # A paper with no abstract is not ingested at all: the abstract is the only text
    # guaranteed to exist for every paper, the whole index rests on it, and the
    # relevance gate has nothing to score without it.
    if not (openalex_id and item.get("title") and abstract):
        return None

    return {
        "source": "openalex",
        "source_id": openalex_id,
        "doi": doi,
        "title": item.get("title"),
        "abstract": abstract,
        "publication_year": item.get("publication_year"),
        "publication_date": item.get("publication_date"),
        "venue": venue,
        "citation_count": item.get("cited_by_count", 0),
        "open_access_url": oa_url,
        "raw": item,
    }


def discover_openalex(topic: str, research_fields: list[str], limit: int,
                      from_date: str | None = None) -> list[dict]:
    """
    THE DISCOVERY BOUNDARY. Everything OpenAlex-specific stops here.

    Returns up to `limit` PaperCandidate records for one topic.

      from_date is None  -> SEED. Sorted by relevance. A cold corpus needs the
                            canonical papers for a topic, not last week's preprints.
                            Basic `page` paging: OpenAlex does not support cursor
                            paging together with relevance sorting.

      from_date is set   -> INCREMENTAL. filter=from_publication_date, newest first,
                            cursor-paged. This is what makes a scheduled run return
                            something a previous run did not already have.
    """
    url = "https://api.openalex.org/works"
    headers = {"User-Agent": f"ResearchCopilot/1.0 (mailto:{OPENALEX_EMAIL})"}
    field_filter = openalex_field_filter(research_fields)

    base = {
        "search": topic,
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

        filters = [field_filter] if field_filter else []
        if from_date:
            filters.append(f"from_publication_date:{from_date}")
            params["sort"] = "publication_date:desc"
            params["cursor"] = cursor
        else:
            params["page"] = page
        if filters:
            params["filter"] = ",".join(filters)      # "," is AND in OpenAlex

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=20)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            print(f"  [warn] OpenAlex query failed for '{topic}' (page {page}): {exc}")
            break

        results = body.get("results", [])
        if not results:
            break

        for item in results:
            candidate = _openalex_to_candidate(item)
            if candidate:
                collected.append(candidate)

        if from_date:
            cursor = (body.get("meta") or {}).get("next_cursor")
            if not cursor:
                break
        else:
            page += 1
            if page > 25:          # basic paging caps at 10k; stop well before
                break

        time.sleep(0.2)            # OpenAlex polite pool spacing

    return collected[:limit]

# COMMAND ----------

# DBTITLE 1,Semantic Relevance Gate (provider-agnostic)
# MAGIC %md is deliberately not used here: this cell is the gate, and it operates on
# MAGIC PaperCandidate records regardless of which provider produced them.


def candidate_text(candidate: dict) -> str:
    """
    The text a candidate is judged on: title plus abstract.

    Deliberately the same shape the *document* embedding will eventually see, so the
    gate's score and the retrieval score mean the same thing.
    """
    title = (candidate.get("title") or "").strip()
    abstract = (candidate.get("abstract") or "").strip()
    if title and abstract:
        return f"{title}. {abstract}"
    return title or abstract


def relevance_scores(model, topic: str, candidates: list[dict]) -> list[float]:
    """
    Cosine similarity of each candidate against the topic.

    Uses the asymmetric prefixes exactly as retrieval does - the topic is a *query*
    and the paper is a *document*. Scoring them symmetrically would produce numbers
    that do not correspond to what semantic search will later return.

    Vectors are unit-normalised, so the dot product is the cosine.
    """
    if not candidates:
        return []
    query_vec = model.encode(QUERY_PREFIX + topic, normalize_embeddings=True)
    doc_vecs = model.encode(
        [DOCUMENT_PREFIX + candidate_text(c)[:CHUNK_SIZE] for c in candidates],
        batch_size=BATCH_SIZE,
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    return [float(query_vec @ doc_vec) for doc_vec in doc_vecs]


def partition_by_relevance(candidates: list[dict], scores: list[float],
                           threshold: float) -> tuple[list[dict], list[dict]]:
    """
    Split candidates into (accepted, rejected) at `threshold`.

    Each candidate is annotated in place with the score and the topic it was scored
    against, so the verdict stays interpretable after the fact - a bare 0.31 means
    nothing without knowing what it was compared to.
    """
    accepted, rejected = [], []
    for candidate, score in zip(candidates, scores):
        candidate["relevance_score"] = score
        (accepted if score >= threshold else rejected).append(candidate)
    return accepted, rejected

# COMMAND ----------

# DBTITLE 1,Harvest Candidates, Then Gate Them
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
candidates_found = 0
candidates_rejected = 0
topic_results: dict[str, list[dict]] = {}

if not FETCH_NEW_PAPERS:
    print("Skipping discovery (fetch_new_papers = false). Processing existing database records.")
else:
    # Fail before any network call if a field name is wrong.
    _filter_preview = openalex_field_filter(RESEARCH_FIELDS)
    print(f"Field context: {RESEARCH_FIELDS or ['(all of science)']}"
          f"  ->  {_filter_preview or 'no filter'}")
    print(f"Relevance gate: {'on' if RELEVANCE_GATE_ENABLED else 'OFF'}"
          f" (threshold {RELEVANCE_THRESHOLD})")

    watermarks = _load_watermarks(TOPICS)
    remaining = MAX_NEW_PAPERS_PER_RUN
    seen_source_ids: set[str] = set()

    print(f"\nDiscovering up to {MAX_NEW_PAPERS_PER_RUN} papers across {len(TOPICS)} topics...")

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

        candidates = discover_openalex(topic, RESEARCH_FIELDS, limit=budget, from_date=from_date)
        candidates_found += len(candidates)

        # De-duplicate across topics within this run: seed topics overlap heavily,
        # and upserting the same work five times is five S2 lookups for nothing.
        fresh = [c for c in candidates if c["source_id"] not in seen_source_ids]

        if RELEVANCE_GATE_ENABLED and fresh:
            scores = relevance_scores(embedding_model, topic, fresh)
            accepted, rejected = partition_by_relevance(fresh, scores, RELEVANCE_THRESHOLD)
            candidates_rejected += len(rejected)
            for c in accepted:
                c["relevance_topic"] = topic
            if rejected:
                worst = min(c["relevance_score"] for c in rejected)
                best = max(c["relevance_score"] for c in rejected)
                print(f"      gate: rejected {len(rejected)} (scores {worst:.3f}-{best:.3f})")
                for c in sorted(rejected, key=lambda x: -x["relevance_score"])[:3]:
                    print(f"        {c['relevance_score']:.3f}  {str(c['title'])[:64]}")
            fresh = accepted
        else:
            for c in fresh:
                c["relevance_topic"] = topic
                c.setdefault("relevance_score", None)

        seen_source_ids.update(c["source_id"] for c in fresh)
        topic_results[topic] = fresh
        harvested_papers.extend(fresh)
        remaining -= len(fresh)
        print(f"      -> {len(candidates)} found, {len(fresh)} accepted")

    print(f"\nDiscovered {candidates_found} candidates, "
          f"rejected {candidates_rejected}, accepted {len(harvested_papers)}.")

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
        source_api, open_access_url, payload, synced_at,
        relevance_score, relevance_topic, relevance_threshold,
        relevance_status, relevance_scored_at
    ) VALUES (
        %(openalex_id)s, %(semantic_scholar_id)s, %(doi)s, %(title)s, %(abstract)s,
        %(publication_year)s, %(venue)s, %(citation_count)s, %(tldr)s, %(influence_score)s,
        %(source_api)s, %(open_access_url)s, %(payload)s, now(),
        %(relevance_score)s, %(relevance_topic)s, %(relevance_threshold)s,
        %(relevance_status)s, %(relevance_scored_at)s
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
        synced_at           = now(),
        -- A paper re-found under a different topic keeps its BEST score. Letting a
        -- weaker match overwrite a stronger one would flag papers that a previous
        -- run had correctly accepted.
        relevance_score     = CASE
            WHEN EXCLUDED.relevance_score IS NOT NULL
             AND (papers.relevance_score IS NULL
                  OR EXCLUDED.relevance_score > papers.relevance_score)
            THEN EXCLUDED.relevance_score ELSE papers.relevance_score END,
        relevance_topic     = CASE
            WHEN EXCLUDED.relevance_score IS NOT NULL
             AND (papers.relevance_score IS NULL
                  OR EXCLUDED.relevance_score > papers.relevance_score)
            THEN EXCLUDED.relevance_topic ELSE papers.relevance_topic END,
        relevance_threshold = CASE
            WHEN EXCLUDED.relevance_score IS NOT NULL
             AND (papers.relevance_score IS NULL
                  OR EXCLUDED.relevance_score > papers.relevance_score)
            THEN EXCLUDED.relevance_threshold ELSE papers.relevance_threshold END,
        relevance_status    = CASE
            WHEN EXCLUDED.relevance_score IS NOT NULL
             AND (papers.relevance_score IS NULL
                  OR EXCLUDED.relevance_score > papers.relevance_score)
            THEN EXCLUDED.relevance_status ELSE papers.relevance_status END,
        relevance_scored_at = CASE
            WHEN EXCLUDED.relevance_score IS NOT NULL
             AND (papers.relevance_score IS NULL
                  OR EXCLUDED.relevance_score > papers.relevance_score)
            THEN EXCLUDED.relevance_scored_at ELSE papers.relevance_scored_at END
    RETURNING (xmax = 0) AS inserted;
    """

    with conn.cursor() as cur:
        for p in harvested_papers:
            s2 = s2_by_doi.get(p["doi"].lower(), {}) if p.get("doi") else {}
            # PaperCandidate -> papers row. source_id and raw are the provider-neutral
            # names; the papers table still calls them openalex_id and payload, which
            # is fine while OpenAlex is the only discovery provider. A second provider
            # is what would force a column rename, not this.
            score = p.get("relevance_score")
            params = {
                "openalex_id": p.get("source_id"),
                "semantic_scholar_id": s2.get("semantic_scholar_id"),
                "doi": p.get("doi"),
                "title": p.get("title", ""),
                "abstract": p.get("abstract"),
                "publication_year": p.get("publication_year"),
                "venue": p.get("venue"),
                "citation_count": p.get("citation_count", 0),
                "tldr": s2.get("tldr"),
                "influence_score": s2.get("influence_score"),
                "source_api": p.get("source", "openalex"),
                "open_access_url": p.get("open_access_url"),
                "payload": json.dumps(p.get("raw")) if p.get("raw") else None,
                "relevance_score": score,
                "relevance_topic": p.get("relevance_topic"),
                "relevance_threshold": RELEVANCE_THRESHOLD if score is not None else None,
                # Everything reaching the upsert passed the gate, so it is accepted.
                # Rejected candidates are never inserted at all.
                "relevance_status": "accepted" if score is not None else "unscored",
                "relevance_scored_at": datetime.datetime.now(datetime.timezone.utc) if score is not None else None,
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
# MAGIC For papers with at least one candidate PDF URL and no completed full-text attempt,
# MAGIC download the PDF into memory. Every failure is **recorded, not raised**, and the
# MAGIC status says which kind of failure it was — because only some kinds are worth
# MAGIC retrying.
# MAGIC
# MAGIC **Repository copies first (Phase 2.6).** The first real run downloaded 56 PDFs out
# MAGIC of 152 papers. 24 attempts came back `403 Forbidden` from publishers — ACM, Oxford
# MAGIC University Press, Elsevier, MDPI, PNAS, Science, RSC — and 42 returned an HTML
# MAGIC landing page rather than a PDF. Both have the same root cause: `open_access.oa_url`
# MAGIC often points at the *publisher's* page for a work, which is exactly the copy that
# MAGIC blocks robots.
# MAGIC
# MAGIC OpenAlex also knows about repository copies — arXiv, PubMed Central, institutional
# MAGIC archives — and publishes a direct `pdf_url` for them. Those exist to be fetched
# MAGIC programmatically. So candidates are now tried repository-first.
# MAGIC
# MAGIC A `403` is a publisher declining automated download. The response to that is to
# MAGIC use the open copy they have already deposited elsewhere — **not** to disguise the
# MAGIC client as a browser.

# COMMAND ----------

# DBTITLE 1,Download Open-Access PDFs (repository-first, classified failures)
import time

import pandas as pd
import requests

PDF_HEADERS = {
    # Identify the crawler honestly. Several OA hosts return 403 to an unlabelled
    # client, and the polite-pool contact is what earns the higher rate limits.
    "User-Agent": f"ai-research-copilot/1.0 (mailto:{os.getenv('OPENALEX_EMAIL', 'research@example.com')})",
    "Accept": "application/pdf,*/*",
}

# Retrying these could plausibly succeed later. Everything else is permanent, and
# the whole point of the taxonomy is that the retry query can tell them apart.
RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}


def pdf_candidates(payload: dict | None, fallback_url: str | None) -> list[str]:
    """
    Candidate PDF URLs for one paper, best first.

    Order is deliberate:
      1. repository `pdf_url`  - arXiv, PMC, institutional archives. Deposited to
                                 be fetched; they do not block robots.
      2. best_oa_location      - OpenAlex's own pick, often the publisher.
      3. any other `pdf_url`   - whatever else is on record.
      4. open_access.oa_url    - the old behaviour, kept as a last resort. It is
                                 frequently a landing page, which is why it is last.

    Papers ingested before Phase 2.6 have a payload without `locations`, so they
    fall through to (4) and behave exactly as before.
    """
    payload = payload or {}
    repository, best, other = [], [], []

    best_loc = payload.get("best_oa_location") or {}
    if best_loc.get("pdf_url"):
        best.append(best_loc["pdf_url"])

    for loc in payload.get("locations") or []:
        if not isinstance(loc, dict) or not loc.get("pdf_url"):
            continue
        source = loc.get("source") or {}
        if source.get("type") == "repository":
            repository.append(loc["pdf_url"])
        else:
            other.append(loc["pdf_url"])

    ordered = repository + best + other + ([fallback_url] if fallback_url else [])

    seen, out = set(), []
    for url in ordered:
        url = (url or "").strip()
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out[:MAX_PDF_CANDIDATES]


def classify_http_error(exc: Exception) -> str:
    """Map a request failure to a status that says whether retrying could help."""
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if code in (401, 403):
        return "access_denied"          # the publisher blocks robots; it will again
    if code in (404, 410):
        return "not_found"              # the URL is wrong or the copy is gone
    if code is not None and code not in RETRYABLE_HTTP:
        return "fetch_failed"           # unexpected 4xx: record it, retry once or twice
    return "fetch_failed"               # 5xx, timeout, connection reset - transient


def try_download(url: str) -> tuple[bytes | None, str]:
    """Fetch one URL. Returns (bytes, 'ok') or (None, <status>)."""
    try:
        resp = requests.get(url, headers=PDF_HEADERS, timeout=FULLTEXT_TIMEOUT, stream=True)
        resp.raise_for_status()
    except Exception as exc:            # noqa: BLE001 - one URL must not stop the run
        return None, classify_http_error(exc)

    try:
        # An open_access_url is frequently an HTML landing page, not the PDF.
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "pdf" not in content_type:
            return None, "not_pdf"

        # Read with a ceiling so one pathological file cannot exhaust driver memory.
        body = bytearray()
        for block in resp.iter_content(chunk_size=64 * 1024):
            body.extend(block)
            if len(body) > FULLTEXT_MAX_BYTES:
                return None, "too_large"
        return bytes(body), "ok"
    finally:
        resp.close()


pdf_bytes_by_paper: dict[str, bytes] = {}
fulltext_status: dict[str, str] = {}

if not FETCH_FULLTEXT:
    print("Skipping PDF acquisition (fetch_fulltext = false).")
    candidates_df = pd.DataFrame(columns=["paper_id", "open_access_url", "payload"])
else:
    conn = psycopg2.connect(
        host=db_host, port=db_port, dbname=db_name,
        user=db_user, password=db_password, sslmode='require'
    )

    # Two populations:
    #   1. never attempted    -> fulltext_status IS NULL
    #   2. transiently failed -> fetch_failed, cooled off, budget remaining
    #
    # access_denied, not_found, not_pdf, too_large, parse_failed and no_sections are
    # NOT retried. A publisher that blocks robots will block them next week; a dead
    # URL stays dead; a scanned PDF will not grow a text layer; and heading
    # extraction is deterministic given the same text and the same SECTION_SYNONYMS.
    # To re-attempt any of them - after improving SECTION_SYNONYMS, say - clear their
    # fulltext_status by hand. That deliberate act is what the exclusion protects.
    candidates_df = pd.read_sql_query(f"""
        SELECT paper_id, open_access_url, payload
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

    print(f"{len(candidates_df)} paper(s) awaiting a full-text attempt "
          f"(new + retryable, max {FULLTEXT_MAX_ATTEMPTS} attempts, "
          f"{FULLTEXT_RETRY_AFTER_DAYS}d cooling-off).")

    for _, row in candidates_df.iterrows():
        paper_id = str(row["paper_id"])
        urls = pdf_candidates(row.get("payload"), row.get("open_access_url"))

        if not urls:
            fulltext_status[paper_id] = "no_url"
            continue

        # Try candidates in order and keep the *most informative* failure: a 403 on
        # the publisher copy is less interesting than "we also tried the repository
        # and it was a landing page". Later statuses in this list win.
        severity = ["access_denied", "not_found", "fetch_failed", "too_large", "not_pdf"]
        worst = None

        for url in urls:
            blob, status = try_download(url)
            if status == "ok":
                pdf_bytes_by_paper[paper_id] = blob
                worst = None
                break
            if worst is None or severity.index(status) > severity.index(worst):
                worst = status
            time.sleep(FULLTEXT_DELAY)

        if worst is not None:
            fulltext_status[paper_id] = worst
            print(f"  [{worst}] {paper_id} after {len(urls)} candidate URL(s)")

        time.sleep(FULLTEXT_DELAY)      # polite to OA hosts

    from collections import Counter as _Counter
    _mix = _Counter(fulltext_status.values())
    print(f"\nDownloaded {len(pdf_bytes_by_paper)} PDF(s). Failures: {dict(_mix)}")
    if _mix.get("access_denied"):
        print(f"  NOTE: {_mix['access_denied']} paper(s) blocked by the publisher. "
              f"These are never retried - the abstract they already have is unaffected.")

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
    # Expanded in Phase 2.6: the first real run left 13 of 56 parsed PDFs at
    # `no_sections` (23%). Every variant below was chosen because it is a heading
    # a real paper uses, not because it might appear somewhere in the text - the
    # match is exact against a whole line, so a loose entry here is how References
    # ends up indexed as a Conclusion.
    "conclusion": ("conclusions and future work", "conclusion and future work",
                   "discussion and conclusion", "discussion and conclusions",
                   "conclusions and future directions", "conclusion and future work",
                   "summary and conclusions", "summary and conclusion",
                   "concluding remarks", "closing remarks",
                   "conclusions", "conclusion", "summary"),
    "discussion": ("results and discussion", "discussion and limitations",
                   "limitations and future work", "general discussion",
                   "discussions", "discussion", "limitations"),
    "methods":    ("materials and methods", "methods and materials",
                   "data and methods", "methods and data",
                   "experimental setup", "experimental section",
                   "experimental design", "experimental procedure",
                   "study design", "research design", "research methodology",
                   "methodology", "methods", "method", "approach",
                   "implementation details", "model architecture"),
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
    text = text.replace("\x00", "")          # NUL bytes from malformed PDFs
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
    # Longest variant wins across the whole table, so a combined heading like
    # "discussion and conclusion" resolves once and deterministically rather than
    # depending on which canonical name dict iteration reaches first.
    best = None
    for canonical, variants in SECTION_SYNONYMS.items():
        for variant in variants:
            if norm == variant and (best is None or len(variant) > len(best[1])):
                best = (canonical, variant)
    return best[0] if best else None


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
    import logging as _logging

    from pypdf import PdfReader

    # pypdf logs "Exceeded 5000 form XObject invocations" per complex PDF. It means
    # some vector graphics were skipped, which is irrelevant to text extraction, and
    # it buries the status lines that do matter.
    _logging.getLogger("pypdf").setLevel(_logging.ERROR)

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
# MAGIC ## 9. Batch Vector Encoding
# MAGIC
# MAGIC Computes 768-dimensional dense vectors with unit-normalization (`normalize_embeddings=True`).
# MAGIC
# MAGIC Each chunk is prefixed with `DOCUMENT_PREFIX` **at encode time only** - the text
# MAGIC stored in `chunk_text` stays clean, because the dashboard renders it directly as
# MAGIC the search-result snippet.
# MAGIC
# MAGIC This is the pipeline's real compute cost and no storage decision changes it:
# MAGIC hundreds of chunks of up to 4000 characters through ModernBERT-base on CPU is
# MAGIC genuinely minutes of work.

# COMMAND ----------

# DBTITLE 1,Generate Dense Neural Embeddings
# Encode paper chunks
if len(paper_chunks_df) > 0:
    print(f"Computing embeddings for {len(paper_chunks_df)} paper chunks in batches of {BATCH_SIZE}...")
    _started = time.perf_counter()
    paper_vectors = embedding_model.encode(
        [DOCUMENT_PREFIX + t for t in paper_chunks_df["chunk_text"].tolist()],
        batch_size=BATCH_SIZE,
        # Off deliberately: Databricks renders this through ipywidgets, which
        # produced a dozen "Loading the widget is taking longer than expected"
        # messages and no progress.
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    paper_chunks_df["embedding"] = [v.tolist() for v in paper_vectors]
    print(f"✅ Generated {len(paper_vectors)} paper chunk vectors "
          f"in {time.perf_counter() - _started:.1f}s.")

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
# MAGIC ## 12. Score Existing Papers (flag only — nothing is deleted)
# MAGIC
# MAGIC Papers ingested before the relevance gate existed carry
# MAGIC `relevance_status = 'unscored'`. This cell scores them so the threshold can be
# MAGIC calibrated against the corpus we already have, **before** anyone decides what to
# MAGIC do about the low scorers.
# MAGIC
# MAGIC **Nothing is deleted, and nothing is hidden.** A `flagged` paper stays fully
# MAGIC searchable; no query in the dashboard or the MCP server filters on
# MAGIC `relevance_status`. The flag is a note to a human.
# MAGIC
# MAGIC ### The honest limitation
# MAGIC
# MAGIC Papers ingested before Phase 2.8 never recorded *which topic found them* — the
# MAGIC column did not exist. So a historical paper is scored against **every** configured
# MAGIC topic and keeps its best match, with `relevance_topic` naming the winner.
# MAGIC
# MAGIC That is a fair substitute and slightly generous: scoring against all topics can
# MAGIC only raise a paper's score, never lower it. A paper that scores low against all
# MAGIC five topics really is unrelated to this corpus.
# MAGIC
# MAGIC New papers do not have this problem — the topic is known at discovery time.

# COMMAND ----------

# DBTITLE 1,Backfill Relevance Scores for Unscored Papers
if not SCORE_UNSCORED_PAPERS:
    print("Skipping relevance backfill (score_unscored_papers = false).")
elif not TOPICS:
    print("Skipping relevance backfill - no topics configured to score against.")
else:
    conn = psycopg2.connect(
        host=db_host, port=db_port, dbname=db_name,
        user=db_user, password=db_password, sslmode='require'
    )
    unscored_df = pd.read_sql_query(f"""
        SELECT paper_id, title, abstract
        FROM {PAPERS_TABLE_NAME}
        WHERE relevance_status = 'unscored'
        ORDER BY citation_count DESC NULLS LAST
    """, conn)
    conn.close()

    print(f"{len(unscored_df)} paper(s) to score against {len(TOPICS)} topic(s).")

    if len(unscored_df) == 0:
        print("  Nothing to do.")
    else:
        candidates = [
            {"title": row["title"], "abstract": row["abstract"]}
            for _, row in unscored_df.iterrows()
        ]

        # Score every paper against every topic, keep the best. One encode pass per
        # topic rather than per paper: the document vectors are recomputed each time,
        # which is wasteful, but the corpus is small and the alternative is holding
        # every vector in memory for a one-off backfill.
        best_score = [float("-inf")] * len(candidates)
        best_topic = [None] * len(candidates)

        for topic in TOPICS:
            scores = relevance_scores(embedding_model, topic, candidates)
            for i, score in enumerate(scores):
                if score > best_score[i]:
                    best_score[i] = score
                    best_topic[i] = topic
            print(f"  scored against '{topic}'")

        now = datetime.datetime.now(datetime.timezone.utc)
        updates = [
            (float(best_score[i]), best_topic[i], RELEVANCE_THRESHOLD,
             "accepted" if best_score[i] >= RELEVANCE_THRESHOLD else "flagged",
             now, str(row["paper_id"]))
            for i, (_, row) in enumerate(unscored_df.iterrows())
        ]

        conn = psycopg2.connect(
            host=db_host, port=db_port, dbname=db_name,
            user=db_user, password=db_password, sslmode='require'
        )
        with conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, f"""
                    UPDATE {PAPERS_TABLE_NAME}
                       SET relevance_score = %s,
                           relevance_topic = %s,
                           relevance_threshold = %s,
                           relevance_status = %s,
                           relevance_scored_at = %s
                     WHERE paper_id = %s;
                """, updates, page_size=100)
        conn.close()

        flagged = sum(1 for u in updates if u[3] == "flagged")
        print(f"\nScored {len(updates)} paper(s): "
              f"{len(updates) - flagged} accepted, {flagged} flagged.")
        print("Nothing was deleted. Flagged papers remain fully searchable.")

        # The rows either side of the threshold are what tell you whether the
        # threshold is right. Print them rather than making you write the query.
        ranked = sorted(zip(best_score, best_topic, unscored_df["title"].tolist()),
                        reverse=True)
        near = [r for r in ranked if r[0] < RELEVANCE_THRESHOLD][:5]
        weakest = [r for r in ranked if r[0] >= RELEVANCE_THRESHOLD][-5:]

        if near:
            print(f"\nHighest-scoring FLAGGED papers (just below {RELEVANCE_THRESHOLD}) -")
            print("if these look on-topic, the threshold is too high:")
            for score, topic, title in near:
                print(f"  {score:.3f}  [{str(topic)[:24]}]  {str(title)[:60]}")

        if weakest:
            print(f"\nLowest-scoring ACCEPTED papers (just above {RELEVANCE_THRESHOLD}) -")
            print("if these look off-topic, the threshold is too low:")
            for score, topic, title in weakest:
                print(f"  {score:.3f}  [{str(topic)[:24]}]  {str(title)[:60]}")
# COMMAND ----------

# MAGIC %md
# MAGIC ## 13. Close the Run Ledger
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
    "candidates_found": candidates_found,
    "candidates_rejected": candidates_rejected,
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
            candidates_found   = %(candidates_found)s,
            candidates_rejected = %(candidates_rejected)s,
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