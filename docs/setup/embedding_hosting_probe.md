# Databricks embedding App: deployment probe

Status: implementation for an isolated trial; no live benchmark or production switch.
Keep `DOCUMENT_INDEXING_ENABLED=false`. No Lakebase migration, MCP catalog update,
Render routing change, new paid endpoint or model download is part of this probe.

## 1. Prerequisites

Confirm the old MVP MCP App is deleted and a third Free Edition App slot is free.
Confirm the offered App allocation in your workspace; published standard Medium
sizing is up to 2 vCPU/6 GB, not a verified allocation in this account. Free Edition
Apps stop after up to 24 hours and fair-use limits can interrupt compute. Restart
handling is an operational requirement, not something an HTTP health ping fixes.
Do not upgrade the account or instance to make this test pass.

Use the existing **ingestion model directory**, saved by
`notebooks/setup_embedding_model.py` in a Unity Catalog Volume. It must include
weights, tokenizer, pooling, SentenceTransformer configuration and
`embedding_contract.json`. Do not replace it with a fresh Hub download: the old
contract does not attest an exact upstream revision. Preserve this snapshot and
record the fingerprint below. The benchmark compares against this artifact; it
cannot prove every historical database vector used that snapshot.

## 2. Establish the snapshot fingerprint and CPU baseline

Use a Databricks Python notebook with read access to the existing Volume and a
Git folder containing this branch (or main after merge). Install the probe's
`embedding_service/requirements.txt` in that notebook environment if needed;
restart Python after installation. These dependencies are deliberately separate
from Render and both MCP servers. No model is fetched from Hugging Face.

In a Python notebook cell, adjust the two paths:

```python
import sys, tempfile
from pathlib import Path
from databricks.sdk import WorkspaceClient

repo = '/Workspace/Users/YOUR_USER/ai-research-copilot'
sys.path.insert(0, str(Path(repo) / 'embedding_service'))
from staging import stage_model

source = '/Volumes/YOUR_CATALOG/YOUR_SCHEMA/YOUR_VOLUME/models/YOUR_MODEL'
local = str(Path(tempfile.mkdtemp()) / 'model')
fingerprint = stage_model(source, local, WorkspaceClient())
print('Local model:', local)
print('EMBEDDING_MODEL_VERSION:', fingerprint)
```

Run the local baseline from that same Python process (so `/tmp` is the same host):

```python
import subprocess
subprocess.run([
    sys.executable, str(Path(repo) / 'embedding_service/probe.py'),
    '--model-path', local, '--output', '/tmp/embedding-local.json'
], check=True)
```

This is notebook CPU evidence, **not App CPU evidence**. Save the JSON output
outside `/tmp` if needed. It contains timings and fixture comparisons, no private
research text or tokens. It reports process peak RSS on Linux; it is not a cgroup
memory-limit measurement. Hashing/copying includes up to 1 GiB of native model
files; ONNX/OpenVINO alternatives are excluded. Files API access consumes the
workspace's applicable allowances; this is not a paid inference provider call.

## 3. Deploy the probe from the Git folder

Use a Databricks Git folder connected to the repository. Pull the merged branch;
the App deployment source is its **embedding_service** subdirectory. This is an
isolated probe, not one of the generated `deploy/research` / `deploy/assistant`
branches. There is no new deployment-publication workflow in this increment.
No manual model upload to Git or Workspace is required.

Before deploying, configure these entries in `embedding_service/app.yaml` (the
App spec is the environment source; use a private local configuration copy if
preferred). Paths and fingerprint are configuration, not credentials:

```yaml
  - name: EMBEDDING_VOLUME_PATH
    value: /Volumes/YOUR_CATALOG/YOUR_SCHEMA/YOUR_VOLUME/models/YOUR_MODEL
  - name: EMBEDDING_MODEL_PATH
    value: /tmp/alfred-embedding-model
  - name: EMBEDDING_MODEL_VERSION
    value: THE_SHA256_FROM_STEP_2
```

Append these under the existing `env` list. Create an App such as
`alfred-embeddings-probe`, with that source folder. Add the Volume as a resource
with read permission; the App principal needs USE CATALOG, USE SCHEMA and
READ VOLUME. Do not grant Lakebase or Azure access. Grant the existing **Render
Dashboard caller principal** CAN USE on this App. That caller is distinct from
the App's own generated service principal.

On startup the service copies the snapshot via Files API into a fresh ephemeral
directory and verifies its fingerprint and metadata before loading offline. No
assumption is made that notebook `/Volumes` FUSE paths are mounted in Apps. Each
process start stages again; measure this cost. One App process loads one model.
Dependencies use CPU torch. The lightweight root health endpoint responds while
loading; `/api/ready` returns 503 until ready and also returns 503 after load
failure. No fallback to a different model is allowed.

All `/api/` endpoints are protected by Databricks App ingress OAuth and CAN USE.
The included Flask development server is for this controlled benchmark only;
production serving/hardening belongs to the later routing increment. Do not
expose this process directly on an unprotected host. Request bodies are not
logged. Admission is nonblocking: one inference at a time; simultaneous calls get
429/Retry-After. Payloads are capped at 64 KiB/16,000 characters and the saved
model token limit, with rejection instead of silent truncation.

## 4. Run the end-to-end comparison

From a Linux environment holding the staged reference model, configure Databricks
SDK authentication for a caller with CAN USE on the probe (notebook auto-auth or
an existing OAuth profile/service principal). Never paste access tokens into
commands. The probe uses refreshed SDK headers per request, HTTPS and no redirects.

Run in the notebook's Python process as in Step 2, adding:

```python
subprocess.run([
    sys.executable, str(Path(repo) / 'embedding_service/probe.py'),
    '--model-path', local,
    '--url', 'https://YOUR_APP.aws.databricksapps.com',
    '--version', fingerprint,
    '--output', '/tmp/embedding-app.json'
], check=True)
```

The client sends **unprefixed** text and query/document kind. Only the encoder
adds the prefix. It tests two short queries and two document passages, five
repetitions each, against local reference vectors. Reports include median and
maximum request time, cosine similarity and maximum component error. Pass gates
are cosine >= 0.9999 and max absolute error <= 0.001 with 768 finite unit-vector
components. These are proposed compatibility thresholds, not retrieval-quality
proof. Test actual Render-to-App latency in the later integration trial; a
notebook caller does not measure that route.

## 5. Record the decision before changing Render

Record App deployment duration, ready model-load seconds, startup peak RSS and
peak RSS after inference (`/api/ready`), actual memory limit, warm timings,
compatibility results and one stop/start recovery. Download/staging time is
outside the reported model-load time. Verify unauthenticated API refusal and
parallel calls returning busy without queuing unbounded work. Poll readiness
rather than sending inference while loading. If load fails, check dependencies,
Volume privileges and snapshot completeness in logs; do not enable paid fallback.

Proposed go/no-go: all fixture comparisons pass; warm query median <= 2 seconds;
no failed inference or OOM; peak memory below 75% of the **actual** limit; restart
returns to ready with the same fingerprint. Record slower document throughput to
budget indexing separately. Four fixtures are a smoke test: add representative
paper chunks and retrieval ranking comparisons before the production switch.
Test concurrent requests, long-input refusal and App restart explicitly.

If successful, the next increment adds a dedicated authenticated Databricks
backend in Render, failure messages, bounded retries and deployment publication.
Do not reuse HF_API_TOKEN for Databricks, repoint HF_EMBEDDING_URL, or activate
indexing based on mocked tests. Keep the existing backend until compatibility,
capacity, operational recovery and Render latency have been accepted.

Sources checked 22 October 2026:
- https://docs.databricks.com/aws/en/getting-started/free-edition-limitations
- https://docs.databricks.com/aws/en/dev-tools/databricks-apps/compute-size
- https://docs.databricks.com/aws/en/dev-tools/databricks-apps/connect-local
- https://docs.databricks.com/aws/en/dev-tools/databricks-apps/uc-volumes
- https://huggingface.co/nomic-ai/modernbert-embed-base
