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

Run the baseline inside that notebook process:

```python
import probe
probe.main(['--model-path', local, '--output', '/tmp/embedding-local.json'])
```

The dependency pins now match the user's successful notebook baseline:
`transformers==5.17.0` and `sentence-transformers==6.0.1`. That run measured
0.45 s model load, 0.14–0.17 s short-query medians, 0.65 s for a 1,188-character
passage and 1,088 MiB process peak RSS. This proves local repeated-vector
consistency, not historical-vector or remote-App compatibility. Live App
installation/measurements with these dependency versions remain pending.

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

The fingerprint is a SHA-256 identifier of model artifacts, **not an access
credential**. It is safe to keep in `app.yaml` and Git for this public model.
Knowing it grants no Volume, App, Azure or database access. It verifies content
consistency; it is not a signature or a defense against someone who can modify
both model files and the configured fingerprint. Credentials stay separate.

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
The initial probe used Flask development serving; the Render integration update
switches the App to one-process Waitress serving. Do not
expose this process directly on an unprotected host. Request bodies are not
logged. Admission is nonblocking: one inference at a time; simultaneous calls get
429/Retry-After. Payloads are capped at 64 KiB/16,000 characters and the saved
model token limit, with rejection instead of silent truncation.

## 4. Run the end-to-end comparison

**Notebook authentication needs an exchange.** A notebook's default
`WorkspaceClient().config.authenticate()` headers are not sufficient for this
App request. Databricks documents exchanging the notebook's internal token for
an OAuth token scoped to the App's OAuth client ID. The original guide omitted
this step; a 401 here does not diagnose model loading or the fingerprint.

After pulling this fix into your Git folder, run these cells in the same notebook
as the staged `local` model and `fingerprint` variables. If you previously imported
`probe`, reload it after pulling the change:

```python
import importlib
import requests
from databricks.sdk import WorkspaceClient
from notebook_auth import NotebookAppAuth
import probe
importlib.reload(probe)

app_name = 'alfred-embeddings-probe'
app_url = 'https://YOUR_APP.aws.databricksapps.com'
client = WorkspaceClient()

def notebook_token():
    return (dbutils.notebook.entry_point.getDbutils()
            .notebook().getContext().apiToken().get())

app_auth = NotebookAppAuth(client, app_name, notebook_token)
response = requests.get(
    app_url.rstrip('/') + '/api/ready',
    headers=app_auth.authenticate(),
    timeout=30, allow_redirects=False,
)
print('HTTP status:', response.status_code)
print(response.text)
```

The helper keeps credentials only in memory and exchanges again before expiry.
Do not print tokens or authentication headers. `200` with `state=ready` permits
the benchmark. `503/loading` means wait and retry readiness. `503/failed` means
check App logs. A remaining 401 or 403 requires checking App access/scopes and
the exchange status. A token-exchange failure is distinct from an App 401.
Your notebook identity needs CAN USE on the App. A Py4J/notebook context error
means that runtime cannot expose the documented notebook token; use an OAuth
CLI profile or service principal from a supported environment instead of
printing/reusing credentials from notebook internals.

Then run:

```python
probe.main([
    '--model-path', local,
    '--url', app_url,
    '--version', fingerprint,
    '--output', '/tmp/embedding-app.json',
], auth=app_auth)
```

`auth=app_auth` is essential: omitting it reverts to SDK default authentication.
The regular CLI remains supported for environments configured with actual OAuth
credentials. The notebook test does not require any App or Render redeployment
if only the client/helper changed. Ensure the App itself deploys the updated
model dependency pins before using its results for compatibility acceptance.

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


Render integration and staged activation: see [databricks_embeddings.md](databricks_embeddings.md).
