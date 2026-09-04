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
# MAGIC 3. **Extracts and chunks** abstracts (and user notes) using a sliding character window (`chunk_size=800`, `chunk_overlap=100`) with title prepending for optimal semantic context.
# MAGIC 4. **Computes 384-dimensional dense vectors** using `sentence-transformers/all-MiniLM-L6-v2` in memory-efficient batches.
# MAGIC 5. **Upserts embeddings** into `paper_embeddings` and `note_embeddings` using the `pgvector` Postgres extension and HNSW indexes for sub-second cosine similarity search.
# MAGIC
# MAGIC It re-uses the Databricks secret scopes (`database`, `openalex`, `semantic-scholar`) configured during Phase 1.

# COMMAND ----------

# DBTITLE 1,Install required packages
# MAGIC %pip uninstall -y psycopg2 psycopg2-binary
# MAGIC %pip install -q 'databricks-sdk>=0.118.0' sentence-transformers requests pandas python-dotenv

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
    dbutils.widgets.text("embedding_model", "sentence-transformers/all-MiniLM-L6-v2", "Embedding model")
    dbutils.widgets.text("topics", "transformer neural network, retrieval augmented generation, reinforcement learning from human feedback, vector database indexing, large language model agents", "Seed search topics (comma-separated)")
    dbutils.widgets.text("papers_per_topic", "15", "Max papers to fetch per topic")
    dbutils.widgets.text("chunk_size", "800", "Text chunk size (chars)")
    dbutils.widgets.text("chunk_overlap", "100", "Text chunk overlap (chars)")
    dbutils.widgets.text("batch_size", "32", "Embedding batch size")
    dbutils.widgets.dropdown("fetch_new_papers", "true", ["true", "false"], "Fetch new papers from OpenAlex?")

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
except NameError:
    PAPERS_TABLE_NAME = "papers"
    EMBEDDINGS_TABLE_NAME = "paper_embeddings"
    NOTE_EMBEDDINGS_TABLE_NAME = "note_embeddings"
    EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
    TOPICS = [
        "transformer neural network",
        "retrieval augmented generation",
        "reinforcement learning from human feedback",
        "vector database indexing",
        "large language model agents",
    ]
    PAPERS_PER_TOPIC = 15
    CHUNK_SIZE = 800
    CHUNK_OVERLAP = 100
    BATCH_SIZE = 32
    FETCH_NEW_PAPERS = True

# Match embedding dimension to the selected model
match EMBEDDING_MODEL_NAME:
    case "sentence-transformers/all-MiniLM-L6-v2" | "sentence-transformers/all-MiniLM-L12-v2" | "BAAI/bge-small-en-v1.5":
        EMBEDDING_DIM = 384
    case "sentence-transformers/all-mpnet-base-v2" | "BAAI/bge-base-en-v1.5":
        EMBEDDING_DIM = 768
    case "BAAI/bge-large-en-v1.5":
        EMBEDDING_DIM = 1024
    case "text-embedding-3-small":
        EMBEDDING_DIM = 1536
    case _:
        raise ValueError(f"Unknown embedding model {EMBEDDING_MODEL_NAME!r}. Add its output dimension to match/case.")

print(f"✅ Configuration Loaded:")
print(f"  • Model: {EMBEDDING_MODEL_NAME} ({EMBEDDING_DIM}-dim)")
print(f"  • Chunking: size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}")
print(f"  • Topics: {len(TOPICS)} seed queries")
print(f"  • Fetch new papers: {FETCH_NEW_PAPERS}")

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
# MAGIC ## 3. Harvest Papers from OpenAlex & Enrich via Semantic Scholar
# MAGIC
# MAGIC Queries the OpenAlex API (using the high-throughput polite pool), reconstructs inverted-index abstracts into clean text, and calls Semantic Scholar for AI-generated TLDRs and influence scores.

# COMMAND ----------

# DBTITLE 1,Fetch & Normalize Academic Papers
import json
import time
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

def fetch_openalex_works(query: str, limit: int = 15) -> list[dict]:
    """Fetch works from OpenAlex and reconstruct abstracts."""
    url = "https://api.openalex.org/works"
    params = {
        "search": query,
        "per-page": min(limit, 50),
        "mailto": OPENALEX_EMAIL,
        "select": "id,doi,title,publication_year,cited_by_count,primary_location,abstract_inverted_index,open_access",
    }
    headers = {"User-Agent": f"ResearchCopilot/1.0 (mailto:{OPENALEX_EMAIL})"}
    
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as exc:
        print(f"  ⚠️  OpenAlex query failed for '{query}': {exc}")
        return []

    standardized = []
    for item in results:
        # Reconstruct abstract from inverted index
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

        if openalex_id and item.get("title") and abstract:
            standardized.append({
                "openalex_id": openalex_id,
                "doi": doi,
                "title": item.get("title"),
                "abstract": abstract,
                "publication_year": item.get("publication_year"),
                "venue": venue,
                "citation_count": item.get("cited_by_count", 0),
                "source_api": "openalex",
                "open_access_url": oa_url,
                "payload": item,
            })
    return standardized

def enrich_paper_s2(doi: str) -> dict:
    """Fetch TLDR and influence metrics from Semantic Scholar."""
    if not doi:
        return {}
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
    headers = {"Accept": "application/json"}
    if S2_API_KEY:
        headers["x-api-key"] = S2_API_KEY
    params = {"fields": "paperId,influentialCitationCount,tldr"}
    
    try:
        time.sleep(1.1)  # Respect S2 rate limit
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            tldr_obj = data.get("tldr") or {}
            return {
                "semantic_scholar_id": data.get("paperId"),
                "influence_score": data.get("influentialCitationCount"),
                "tldr": tldr_obj.get("text"),
            }
    except Exception:
        pass
    return {}

harvested_papers = []
if FETCH_NEW_PAPERS:
    print(f"🔍 Harvesting papers for {len(TOPICS)} topics from OpenAlex...")
    for idx, topic in enumerate(TOPICS):
        print(f"  [{idx+1}/{len(TOPICS)}] Querying topic: '{topic}'...")
        works = fetch_openalex_works(topic, limit=PAPERS_PER_TOPIC)
        harvested_papers.extend(works)
        time.sleep(0.2)  # OpenAlex polite pool spacing
    print(f"\n✅ Harvested {len(harvested_papers)} candidate papers from OpenAlex.")
else:
    print("ℹ️ Skipping external paper fetch (fetch_new_papers = false). Processing existing database records.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Upsert Harvested Papers to Lakebase
# MAGIC
# MAGIC Batch writes candidate papers to the `papers` table using `ON CONFLICT (openalex_id) DO UPDATE` with `COALESCE` protection for enrichment fields.

# COMMAND ----------

# DBTITLE 1,Upsert Papers to Lakebase
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
        synced_at           = now();
    """
    
    with conn.cursor() as cur:
        for p in harvested_papers:
            # S2 enrichment for papers with DOIs
            s2 = enrich_paper_s2(p["doi"]) if p.get("doi") else {}
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
        conn.commit()
    conn.close()
    print(f"✅ Upsert complete.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Incremental Delta: Identify Unembedded Papers & Notes
# MAGIC
# MAGIC Uses an SQL **anti-join** (`LEFT JOIN ... WHERE id IS NULL`) to retrieve only records without corresponding vector representations in pgvector.

# COMMAND ----------

# DBTITLE 1,Fetch Unembedded Records (Anti-Join)
import pandas as pd

conn = psycopg2.connect(
    host=db_host, port=db_port, dbname=db_name,
    user=db_user, password=db_password, sslmode='require'
)

# 1. Unembedded papers
papers_query = f"""
SELECT p.paper_id, p.title, p.abstract
FROM {PAPERS_TABLE_NAME} p
LEFT JOIN {EMBEDDINGS_TABLE_NAME} pe ON pe.paper_id = p.paper_id
WHERE pe.id IS NULL 
  AND p.abstract IS NOT NULL 
  AND trim(p.abstract) != '';
"""
unembedded_papers_df = pd.read_sql_query(papers_query, conn)

# 2. Unembedded notes
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
print(f"  • Unembedded Papers Found: {len(unembedded_papers_df)}")
print(f"  • Unembedded Notes Found:  {len(unembedded_notes_df)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Sliding Window Text Chunking
# MAGIC
# MAGIC Breaks long abstract and note texts into overlapping character chunks (`chunk_size=800`, `chunk_overlap=100`). Prepends paper title to the first chunk for enhanced context anchoring.

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

# Chunk papers
paper_chunk_rows = []
for _, row in unembedded_papers_df.iterrows():
    paper_id = str(row["paper_id"])
    title = str(row["title"]) if row["title"] else ""
    abstract = str(row["abstract"]) if row["abstract"] else ""
    full_text = f"{title}. {abstract}" if title else abstract
    
    chunks = chunk_document(full_text, CHUNK_SIZE, CHUNK_OVERLAP)
    for idx, c in enumerate(chunks):
        paper_chunk_rows.append({
            "paper_id": paper_id,
            "chunk_index": idx,
            "chunk_text": c
        })

paper_chunks_df = pd.DataFrame(paper_chunk_rows)
print(f"✅ Generated {len(paper_chunks_df)} chunks from {len(unembedded_papers_df)} papers.")

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
# MAGIC ## 7. Batch Vector Encoding with `sentence-transformers`
# MAGIC
# MAGIC Computes 384-dimensional dense vectors with unit-normalization (`normalize_embeddings=True`).

# COMMAND ----------

# DBTITLE 1,Generate Dense Neural Embeddings
from sentence_transformers import SentenceTransformer

# Set cache directories
os.environ["HF_HOME"] = "/tmp/.cache/huggingface"
os.environ["TRANSFORMERS_CACHE"] = "/tmp/.cache/huggingface"

print(f"🧠 Loading embedding model '{EMBEDDING_MODEL_NAME}'...")
embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME, cache_folder="/tmp/.cache/huggingface")

# Encode paper chunks
if len(paper_chunks_df) > 0:
    print(f"Computing embeddings for {len(paper_chunks_df)} paper chunks in batches of {BATCH_SIZE}...")
    paper_vectors = embedding_model.encode(
        paper_chunks_df["chunk_text"].tolist(),
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
        note_chunks_df["chunk_text"].tolist(),
        batch_size=BATCH_SIZE,
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    note_chunks_df["embedding"] = [v.tolist() for v in note_vectors]
    print(f"✅ Generated {len(note_vectors)} note chunk vectors.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Batch Insert Embeddings into Lakebase pgvector
# MAGIC
# MAGIC Uses `psycopg2.extras.execute_batch` to bulk persist vectors into `paper_embeddings` and `note_embeddings` with `%s::vector(384)` type casting.

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
            f"[{','.join(str(float(x)) for x in row['embedding'])}]"
        )
        for _, row in paper_chunks_df.iterrows()
    ]
    
    paper_insert_sql = f"""
    INSERT INTO {EMBEDDINGS_TABLE_NAME} (paper_id, chunk_index, chunk_text, embedding)
    VALUES (%s, %s, %s, %s::vector({EMBEDDING_DIM}));
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
# MAGIC ## 9. Verification & Similarity Search Test
# MAGIC
# MAGIC Runs a test vector query against the newly indexed vectors to verify HNSW cosine similarity search performance.

# COMMAND ----------

# DBTITLE 1,Run Test Cosine Similarity Query
TEST_QUERY = "Transformer multi-head self-attention mechanisms"

print(f"🔍 Testing vector search with query: '{TEST_QUERY}'...")

# Encode test query
query_vector = embedding_model.encode(TEST_QUERY, normalize_embeddings=True).tolist()
query_vector_str = f"[{','.join(str(float(x)) for x in query_vector)}]"

search_sql = f"""
SELECT 
    p.title,
    p.publication_year,
    p.venue,
    pe.chunk_index,
    pe.chunk_text,
    1 - (pe.embedding <=> %s::vector({EMBEDDING_DIM})) AS similarity_score
FROM {EMBEDDINGS_TABLE_NAME} pe
JOIN {PAPERS_TABLE_NAME} p ON p.paper_id = pe.paper_id
ORDER BY pe.embedding <=> %s::vector({EMBEDDING_DIM})
LIMIT 3;
"""

with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute(search_sql, (query_vector_str, query_vector_str))
    top_matches = cur.fetchall()

print(f"\n🎯 Top 3 Vector Matches:")
for rank, match in enumerate(top_matches, 1):
    sim = match['similarity_score']
    print(f"\n[{rank}] Score: {sim:.4f} | {match['title']} ({match.get('publication_year', 'N/A')})")
    print(f"    Venue: {match.get('venue')}")
    print(f"    Excerpt: {match['chunk_text'][:200]}...")

conn.close()
print("\n🎉 Notebook execution complete! Lakebase vector index is active and ready for Agentic RAG.")