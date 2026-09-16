# Databricks notebook source
# MAGIC %md
# MAGIC # One-Time Setup — Persist the Embedding Model to a Unity Catalog Volume
# MAGIC
# MAGIC Run this **once**, and again only when the embedding model deliberately changes.
# MAGIC
# MAGIC ## Why
# MAGIC
# MAGIC `ingest_papers_embeddings.py` used to download `nomic-ai/modernbert-embed-base`
# MAGIC (568 MB) from the Hugging Face Hub on every run. A job cluster is created fresh
# MAGIC per run and destroyed afterwards, so a scheduled pipeline spent more time
# MAGIC acquiring the model than processing data — roughly 18 minutes, throttled, because
# MAGIC unauthenticated Hub requests are rate-limited.
# MAGIC
# MAGIC This notebook downloads the model once and writes it to a Unity Catalog Volume as
# MAGIC a plain directory. The ingestion pipeline then loads it straight from that path
# MAGIC and never contacts the Hub again.
# MAGIC
# MAGIC ```text
# MAGIC Hugging Face  ->  this notebook  ->  UC Volume  ->  ingestion pipeline
# MAGIC ```
# MAGIC
# MAGIC ## What it writes
# MAGIC
# MAGIC ```
# MAGIC /Volumes/<catalog>/<schema>/<volume>/models/modernbert-embed-base/
# MAGIC     config.json, model.safetensors, tokenizer.json, ...   <- the model
# MAGIC     embedding_contract.json                               <- the contract
# MAGIC ```
# MAGIC
# MAGIC `embedding_contract.json` records model identity, dimension, both task prefixes
# MAGIC and normalisation. The ingestion pipeline validates it against its own constants
# MAGIC before encoding anything, so a mismatch fails loudly instead of quietly writing
# MAGIC vectors into the wrong space.

# COMMAND ----------

# MAGIC %pip install -q sentence-transformers

# COMMAND ----------

try:
    dbutils.library.restartPython()
except NameError:
    pass

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Configuration
# MAGIC
# MAGIC `model_volume_path` is the only value you must set. Nothing is hard-coded to a
# MAGIC particular catalog, schema or volume — create the volume first if you have not:
# MAGIC
# MAGIC ```sql
# MAGIC CREATE VOLUME IF NOT EXISTS <catalog>.<schema>.<volume>;
# MAGIC ```
# MAGIC
# MAGIC The prefixes are widgets rather than derived, because they are part of the
# MAGIC contract this notebook is publishing. A wrong value here is caught by the
# MAGIC ingestion pipeline's validation rather than silently degrading retrieval.

# COMMAND ----------

# DBTITLE 1,Configure Widgets & Parameters
import os

try:
    dbutils.widgets.text("embedding_model", "nomic-ai/modernbert-embed-base", "Source model (Hugging Face)")
    dbutils.widgets.text("model_volume_path",
                         "/Volumes/workspace/default/models/modernbert-embed-base",
                         "Destination UC Volume path")
    dbutils.widgets.text("document_prefix", "search_document: ", "Document-side prefix")
    dbutils.widgets.text("query_prefix", "search_query: ", "Query-side prefix")
    dbutils.widgets.dropdown("normalize", "true", ["true", "false"], "Unit-normalise embeddings?")
    dbutils.widgets.dropdown("overwrite", "false", ["true", "false"], "Overwrite if the path already exists?")

    EMBEDDING_MODEL_NAME = dbutils.widgets.get("embedding_model").strip()
    MODEL_VOLUME_PATH = dbutils.widgets.get("model_volume_path").strip().rstrip("/")
    DOCUMENT_PREFIX = dbutils.widgets.get("document_prefix")
    QUERY_PREFIX = dbutils.widgets.get("query_prefix")
    NORMALIZE = dbutils.widgets.get("normalize").lower() == "true"
    OVERWRITE = dbutils.widgets.get("overwrite").lower() == "true"
except NameError:
    EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "nomic-ai/modernbert-embed-base")
    MODEL_VOLUME_PATH = os.getenv("MODEL_VOLUME_PATH", "/tmp/models/modernbert-embed-base")
    DOCUMENT_PREFIX = os.getenv("EMBEDDING_DOCUMENT_PREFIX", "search_document: ")
    QUERY_PREFIX = os.getenv("EMBEDDING_QUERY_PREFIX", "search_query: ")
    NORMALIZE = True
    OVERWRITE = False

CONTRACT_FILENAME = "embedding_contract.json"

print("Setup configuration:")
print(f"  • Source model : {EMBEDDING_MODEL_NAME}")
print(f"  • Destination  : {MODEL_VOLUME_PATH}")
print(f"  • Prefixes     : document={DOCUMENT_PREFIX!r}  query={QUERY_PREFIX!r}")
print(f"  • Normalise    : {NORMALIZE}")
print(f"  • Overwrite    : {OVERWRITE}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Check the destination
# MAGIC
# MAGIC Stops before a 568 MB download if the volume does not exist or the path is
# MAGIC already populated. Re-running this notebook should be safe and boring.

# COMMAND ----------

# DBTITLE 1,Validate the Destination Before Downloading
import os

parent = os.path.dirname(MODEL_VOLUME_PATH)

if MODEL_VOLUME_PATH.startswith("/Volumes/") and not os.path.isdir("/Volumes"):
    raise RuntimeError(
        "/Volumes is not mounted on this compute. Unity Catalog Volumes are "
        "unavailable here - check the cluster's access mode, or set "
        "model_volume_path to a local directory for a development run."
    )

# Create the intermediate directories, not the volume itself: a volume must be
# created with SQL, and silently creating a look-alike local directory would be a
# confusing way to fail.
# /Volumes/<catalog>/<schema>/<volume> - five segments once the leading empty
# string from the leading slash is counted. Slicing at 4 lands on the schema,
# which is not a directory and yields invalid SQL in the error below.
volume_root = "/".join(MODEL_VOLUME_PATH.split("/")[:5]) if MODEL_VOLUME_PATH.startswith("/Volumes/") else None
if volume_root and not os.path.isdir(volume_root):
    raise RuntimeError(
        f"The volume {volume_root} does not exist. Create it first:\n"
        f"    CREATE VOLUME IF NOT EXISTS "
        f"{volume_root.replace('/Volumes/', '').replace('/', '.')};"
    )

already_there = os.path.isdir(MODEL_VOLUME_PATH) and os.listdir(MODEL_VOLUME_PATH)
if already_there and not OVERWRITE:
    raise RuntimeError(
        f"{MODEL_VOLUME_PATH} already exists and is not empty.\n"
        f"Nothing was changed. Set overwrite=true if you intend to replace it - "
        f"note that the ingestion pipeline pins this path, so replacing the model "
        f"without re-embedding the corpus would leave old vectors in a different space."
    )

os.makedirs(parent, exist_ok=True)
print(f"Destination is ready: {MODEL_VOLUME_PATH}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Download and save
# MAGIC
# MAGIC The model is saved to **local disk first**, then copied to the Volume. UC Volumes
# MAGIC are FUSE-backed object storage and do not support random writes; writing a
# MAGIC multi-file model directly into one is the kind of thing that works until it
# MAGIC doesn't. A local save followed by a sequential copy sidesteps that entirely.

# COMMAND ----------

# DBTITLE 1,Download from Hugging Face and Copy to the Volume
import json
import shutil
import tempfile
import time

from sentence_transformers import SentenceTransformer


def embedding_dimension(model) -> int:
    """
    sentence-transformers 6.0 renamed get_sentence_embedding_dimension() to
    get_embedding_dimension(). Support both so this notebook is not pinned to one
    runtime's library version.
    """
    for name in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
        fn = getattr(model, name, None)
        if callable(fn):
            return int(fn())
    raise AttributeError("SentenceTransformer exposes no embedding-dimension accessor.")


print(f"Downloading {EMBEDDING_MODEL_NAME} from the Hugging Face Hub (~568 MB)...")
started = time.perf_counter()
model = SentenceTransformer(EMBEDDING_MODEL_NAME)
print(f"  downloaded and loaded in {time.perf_counter() - started:.1f}s")

# The dimension is MEASURED here, not asserted. It becomes the contract; the
# ingestion pipeline then checks its own EMBEDDING_DIM against this value.
dimension = embedding_dimension(model)
print(f"  model reports {dimension} dimensions")

contract = {
    "base_model": EMBEDDING_MODEL_NAME,
    "embedding_dim": dimension,
    "document_prefix": DOCUMENT_PREFIX,
    "query_prefix": QUERY_PREFIX,
    "normalize": NORMALIZE,
    "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}

with tempfile.TemporaryDirectory() as staging:
    local_dir = os.path.join(staging, "model")
    print(f"Saving to local staging: {local_dir}")
    model.save(local_dir)

    with open(os.path.join(local_dir, CONTRACT_FILENAME), "w", encoding="utf-8") as fh:
        json.dump(contract, fh, indent=2)

    if os.path.isdir(MODEL_VOLUME_PATH):
        print(f"Removing existing {MODEL_VOLUME_PATH} (overwrite=true)")
        shutil.rmtree(MODEL_VOLUME_PATH)

    print(f"Copying to {MODEL_VOLUME_PATH}...")
    started = time.perf_counter()
    shutil.copytree(local_dir, MODEL_VOLUME_PATH)
    print(f"  copied in {time.perf_counter() - started:.1f}s")

total_bytes = sum(
    os.path.getsize(os.path.join(root, f))
    for root, _dirs, files in os.walk(MODEL_VOLUME_PATH)
    for f in files
)
print(f"\nWritten {total_bytes / 1024**2:.0f} MB")
print(json.dumps(contract, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Verify by loading it back
# MAGIC
# MAGIC Proves the Volume copy is loadable *as a model*, not merely that the files are
# MAGIC present. A truncated copy passes a file listing and fails here.

# COMMAND ----------

# DBTITLE 1,Load From the Volume and Encode a Probe Sentence
import math

print(f"Loading from {MODEL_VOLUME_PATH}...")
started = time.perf_counter()
reloaded = SentenceTransformer(MODEL_VOLUME_PATH)
print(f"  loaded in {time.perf_counter() - started:.1f}s (no Hub request)")

reloaded_dim = embedding_dimension(reloaded)
assert reloaded_dim == dimension, f"reloaded model reports {reloaded_dim}, expected {dimension}"

probe = reloaded.encode(DOCUMENT_PREFIX + "attention mechanisms in transformers",
                        normalize_embeddings=NORMALIZE)
norm = math.sqrt(sum(float(x) * float(x) for x in probe))

print(f"  dimensions : {len(probe)}")
print(f"  L2 norm    : {norm:.6f}" + ("  (expected 1.0)" if NORMALIZE else ""))
assert len(probe) == dimension
if NORMALIZE:
    assert abs(norm - 1.0) < 1e-5, f"expected unit-normalised vectors, got norm {norm}"

print("\n" + "=" * 70)
print("Setup complete.")
print("=" * 70)
print("Set these in ingest_papers_embeddings.py (and in the Databricks Job, if scheduled):")
print(f"    model_source      = volume")
print(f"    model_volume_path = {MODEL_VOLUME_PATH}")
print("\nThe ingestion pipeline will now fail loudly if this path disappears, rather")
print("than quietly re-downloading from Hugging Face.")
