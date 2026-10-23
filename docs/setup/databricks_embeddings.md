# Render → Databricks embeddings: controlled rollout

The adapter is implemented; production activation and Render-to-App measurements
are operator steps. The prior notebook run passed all 20 vector comparisons,
with 77–81 ms query medians, 260 ms for a 1,188-character passage and about
1,001 MiB peak App process RSS. These are notebook-to-App results, not Render
latency. The user reports restarting and rerunning; the new restart attachment
was unavailable in the implementation workspace, so its contents remain
unverified here. Check that its comparisons passed with the same fingerprint.

No re-embedding, database migration, model download or paid service is activated
by this change. Existing paper vectors still require their original model space.
The fixture comparison supports that choice; it does not attest every historical
paper's provenance or establish full retrieval quality.

## 1. Deploy the embedding App update

Merge this PR and pull the Git folder used by the App. Preserve the configured
Volume path, temporary model path and snapshot fingerprint in `app.yaml`.
Redeploy **the embedding App**, using its `embedding_service` source directory.
The server now uses Waitress with one process/four HTTP threads, preserving one
model instance and one inference admission slot. No reloader or multiple model
workers are started. Dependencies still belong only to the App; Render does not
install torch, transformers, sentence-transformers or Waitress.

Readiness must return 200/ready with the fingerprint used for the passing
comparison. App start/root health does not establish model readiness. Keep the
existing notebook OAuth helper for notebook tests; it is not Render's auth path.
No research/assistant MCP App redeployment is required.

## 2. Share the App with Render's caller

Open the embedding App's Share dialog. Add the existing **Render Dashboard**
service principal with **Can use**. Use the principal whose client ID/secret
already configure Render's MCP connection. This caller is separate from the
embedding App's generated principal, which reads the model Volume.

Do not replace the App's automatic credentials or copy its generated client
secret to Render. Keep the existing dashboard-user Google authentication intact.
Browser sessions never authenticate directly to this App.

## 3. Configure Render

Wait for the merged release to build, then set:

| Variable | Value |
| --- | --- |
| `EMBEDDING_BACKEND` | `databricks` |
| `DATABRICKS_EMBEDDING_URL` | Embedding App HTTPS origin, without `/api/embed` |
| `DATABRICKS_EMBEDDING_VERSION` | Exact fingerprint from the successful benchmark and App config |
| `DATABRICKS_EMBEDDING_TIMEOUT_SECONDS` | `15` initially; allowed range >0 to 60 seconds |
| `EMBEDDING_PRELOAD` | `true` for an asynchronous readiness check at serving-process startup |
| `DOCUMENT_INDEXING_ENABLED` | `false` during this rollout |

Keep the existing `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID` and
`DATABRICKS_CLIENT_SECRET`. The host is the **workspace origin**, not the App URL.
No new secret scope is introduced. The adapter explicitly uses OAuth M2M, caches
the SDK credential provider and lets it refresh tokens. It resets state after
fork; importing configuration/adapter does not request a token or contact an App.

The tested App origin was:
`https://alfred-embeddings-probe-7474651285449311.aws.databricksapps.com`.
Use the actual deployed URL rather than copying this if the App was recreated.

The Blueprint now leaves `EMBEDDING_BACKEND` operator-managed (`sync: false`) so
future synchronization cannot overwrite this selection with `hf_api`. Existing
services should retain their configured value; set it explicitly for this rollout
and for fresh services. No Render build/start command changes are necessary.
Save and redeploy Render after setting the environment. HF variables may stay
configured for a deliberate rollback, but `databricks` never falls back to HF or
loads a local model. Do not put Databricks tokens in HF_API_TOKEN.

## 4. Test the actual Alfred request path

1. Confirm Render `/healthz` still serves. Databricks warmup only checks readiness;
   it performs no dummy inference and cannot block the Flask app from starting.
2. Search a known topic such as `retrieval augmented generation` in **Semantic**
   mode. Confirm useful results and that no HF 402 appears for this operation.
3. Repeat five times after startup. Use browser Network timing or service logs
   to measure the actual user request; this includes database/render work, unlike
   the notebook benchmark. Embedding telemetry labels this backend `databricks`.
4. Exercise a Research retrieval workflow separately. Keyword search does not
   exercise the new embedding adapter. An LLM provider failure is independent of
   embedding success; inspect the operation that actually failed.
5. Briefly stop the embedding App and attempt a semantic search: expect a friendly
   unavailable response with HTTP 503. Restart the App, wait for ready and retry.
   A subsequent successful request should recover without restarting Render.
6. Check logs for busy/authentication/contract diagnostics. Only sanitized reasons
   and HTTP status are logged by this transport, never text, tokens or bodies.

The adapter checks model name/dimension/version at readiness, and verifies
version/dimension and 768 finite unit-vector components on every inference.
Unprefixed text and query/document kind are sent; the App adds the prefix once.
Wrong-model responses fail before database similarity comparisons.

There is one retry after at most one second **only for a short 429 busy response**.
Long Retry-After values, timeouts, auth failures, redirects and stopped/loading
Apps fail without repeated inference attempts. Connection timeout is 5 seconds;
read timeout is the configured value per attempt, not a hard end-to-end deadline.
The initial readiness check and OAuth acquisition add time to a cold request.
A short contention retry permits two HTTP attempts. No automatic App restart or
extra user chat question is issued. App peak RSS is not the full container memory
limit; verify actual headroom before increasing traffic.

## 5. Activate one document index only after acceptance

Once semantic search, recovery and real-path latency pass:

- Set `DOCUMENT_INDEX_MODEL_VERSION` to the same fingerprint as
  `DATABRICKS_EMBEDDING_VERSION`. Mismatches are rejected before queueing/retrieval.
- Keep upload/extraction workers enabled; set `DOCUMENT_INDEXING_ENABLED=true`
  intentionally and redeploy Render.
- Index one small saved TXT/PDF, confirm Ready, then test a semantic Documents
  search and its real-page preview.
- Confirm deletion removes retrieval access and test worker recovery separately.

A successful small index does not justify bulk indexing: one file can require
many sequential encodes and contend with interactive queries. Calibrate throughput,
actual Lakebase capacity and Free Edition quotas before broader activation.

## Failure diagnosis and rollback

- Auth diagnostic: verify Render's caller and Can use on **this embedding App**;
  notebook token exchange is not the fix for a Render service principal.
- Contract mismatch: correct the URL/fingerprint or App snapshot; do not suppress
  the guard or compare vectors across model spaces.
- Busy: wait briefly and retry; do not add unbounded parallel workers.
- Unavailable: confirm the App is running and ready. Free Edition may stop Apps
  after up to 24 hours; health pings do not guarantee continued availability.
- Oversized text: shorten the passage; App bounds remain 16,000 characters/64 KiB
  request JSON and the saved model token limit.

For rollback, disable document indexing first. Choose `hf_api` only if its current
permissions/credit availability have been verified; the earlier HF 402 is not
fixed by this rollout. `auto` can choose local when no HF token exists and is
unsuitable on a constrained Render instance. Do not silently activate a paid
provider or install a local model as an outage workaround.

Official references:
- https://docs.databricks.com/aws/en/dev-tools/databricks-apps/connect-local
- https://docs.databricks.com/aws/en/dev-tools/databricks-apps/permissions
- https://docs.databricks.com/aws/en/getting-started/free-edition-limitations
