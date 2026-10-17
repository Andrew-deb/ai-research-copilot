# Private uploads: Azure setup and rollout

Increment B implements private PDF, TXT and Markdown files in Research and Wick.
Original bytes and versioned extracted text live in Azure Blob. Lakebase stores
ownership, identifiers, hashes, reservation sizes, expiry and durable jobs. Files
do not enter the global paper corpus. Agent reading and citations are Increment C.

## Code boundaries

| Responsibility | Module |
| --- | --- |
| Provider-neutral byte contract | `mcp_server/shared_resource/storage/contract.py` |
| Azure byte operations | `shared_resource/storage/azure_blob.py` |
| Operator configuration and limits | `dashboard/upload_config.py` |
| Runtime provider/location resolution | `dashboard/services/upload_storage.py` |
| Ownership, capacity and job persistence | `dashboard/repositories/uploads.py` |
| Lifecycle orchestration and safe receipts | `dashboard/services/upload_service.py` |
| Bounded parser subprocess | `dashboard/services/upload_extractor.py` |
| Durable job processing and cleanup | `dashboard/services/upload_worker.py` |
| HTTP adapters and composer controls | `dashboard/routes/uploads.py`, `static/js/uploads.js` |

The adapter exposes put/read/inspect/delete. Logical location IDs and opaque keys
are stored, not public URLs or SAS tokens. Add an R2 adapter and retained-location
registry during migration; no R2 implementation, automatic failover or dual writes
are included here. Keep old locations resolvable until migration is complete.

## 1. Create private Azure storage

Use the active Azure student subscription. Confirm remaining credit and regional
pricing before creating resources; the user reports subscription expiry in August
2027. This implementation does not provision resources or upgrade subscriptions.

In Azure Portal, create or choose a StorageV2 account. For the initial prototype,
choose Standard performance, locally redundant storage (LRS), Hot access and HTTPS.
Disable anonymous blob access on the account. Create a container such as
`alfred-private-uploads` with **Private** access. The adapter refuses upload/read
when the container reports public access. The backend does not create containers.

In the storage account, open **Security + networking → Access keys**, reveal the
keys and copy the full **Connection string** for one key. Put it in Render as
`AZURE_STORAGE_CONNECTION_STRING`; do not paste it into Git, prompts or the browser.
The current adapter uses account-key authentication, so storage account key access
must be allowed. No Entra app registration or Blob Data Contributor assignment is
required for this authentication path. The connection string already contains
the account endpoint and key; configure the private container name separately.

Choose provider soft-delete/versioning/lifecycle retention deliberately. The
application quota covers its original and extraction objects, not extra copies
retained by Azure data-protection settings. Monitor actual Azure usage and credit.
Use a dedicated uploads account/container where practical; do not change data
protection for unrelated stored material just to configure this feature.

## 2. Apply the Lakebase migration

Apply `sql/29_private_uploads.sql` to the existing database before enabling uploads.
It requires the existing users/conversations schema and creates `private_uploads`
and `private_upload_jobs`. It does not alter the papers table. The Render database
role needs SELECT/INSERT/UPDATE on these tables and its existing conversation
permissions. User/conversation deletion leaves inaccessible orphan locators for
cleanup rather than discarding the keys before deleting their objects.

The migration is additive and safe to apply before deploying the new dashboard.
Keep `UPLOADS_ENABLED=false` until the migration, storage and credentials are ready.

## 3. Configure Render

Set these in Render's environment, not Git or the browser:

| Variable | Value |
| --- | --- |
| `UPLOADS_ENABLED` | `true` only after the migration/setup |
| `UPLOAD_WORKER_ENABLED` | `true` for the integrated durable worker |
| `UPLOAD_STORAGE_LOCATION` | `azure-primary`; retain this logical identity for existing rows |
| `AZURE_STORAGE_CONNECTION_STRING` | Full HTTPS connection string from the storage account's Access keys page |
| `AZURE_STORAGE_CONTAINER` | The private container name |

Render is still Blueprint-managed. Its existing build/start commands work; the
build now includes the storage package and small Azure/PDF dependencies. The
application defaults uploads to disabled. The Blueprint leaves `UPLOADS_ENABLED`
operator-managed (`sync: false`); set it intentionally in Render when rolling out.
No new MCP tools are registered. This increment needs the Render deployment and
Lakebase migration, not another Databricks OAuth setup or MCP App redeployment.
Azure credentials belong to Render for now, not either MCP App.

## 4. Initial limits and processing

Defaults: 10 MiB/file, three active files per conversation, 100 MiB/user, 1 GiB
across the deployment, seven-day expiry. These are conservative configurable
starting values, not usage calibration. Per-conversation is the initial attachment
scope; per-message reference selection belongs to Increment C.

`UPLOAD_MAX_FILE_BYTES`, `UPLOAD_USER_BYTES`, `UPLOAD_DEPLOYMENT_BYTES` and
`UPLOAD_RETENTION_DAYS` may tune limits. Current supported ceilings are 10 MiB/file
and 30 days. Reservations include up to 2 MiB of derived text and are serialized
with a database lock. Actual artifact size replaces the reservation after success;
failed processing retains the bounded reservation until deletion is confirmed.

Only UTF-8 text/Markdown and text-based PDFs are supported. File extension,
actual byte size, PDF signature and text encoding are checked. Password-protected,
scanned/no-text, malformed and oversized-text documents fail honestly. No OCR,
DOCX, archives, remote URL uploads, antivirus service or embeddings are included.

Extraction runs separately from the web request, in a child with a clean working
directory and stripped environment. Linux CPU/memory limits and a wall timeout
bound parsing; at most 200 PDF pages/200,000 text characters are accepted without
silent truncation. This is process/resource isolation, not a full OS sandbox or
malware scan. No model calls or agent-query allowance are used for uploads.

The integrated worker uses durable database leases, not an in-memory job queue.
A restarted worker can reclaim an expired lease; retries are bounded and user
retry is explicit. Deletion takes precedence over extraction completion. Keep
Render's existing one-worker configuration on the small instance so only one
parser child runs at once. An independently managed process can instead run
`python -m services.upload_worker` from the built Render root with the integrated
worker disabled; do not provision paid worker capacity implicitly.

## 5. Retention and deletion

Every authorized download checks ownership and expiry, returns an attachment,
and uses private/no-store caching with nosniff. Public locators/credentials never
appear in a file receipt. Expired/deleting files cannot be downloaded even if
physical cleanup is still pending.

Delete file queues deletion of both original and extracted artifact. Only confirmed
storage deletion releases quota. Cleanup also handles abandoned uploads and rows
orphaned by account/conversation deletion. Failed deletion remains queued; storage
outage does not silently free quota or make a file available. Physical cleanup
requires a running worker and functioning Azure access, so a sleeping/free Render
instance can delay physical removal. Preserve the existing readiness schedule and
monitor cleanup during rollout; consider a provider lifecycle backstop separately.
Expiry is never reset by retry or migration. Provider-retained copies remain subject
to the Azure data-protection policy configured above.

## 6. UI acceptance

1. Sign in; open Research, Wick's full page and the compact Wick panel.
2. Use plus → Upload file; on the landing composer the plus opens the file chooser.
3. Upload a short UTF-8 TXT/Markdown file and a text-based PDF. Expect Uploading,
   Waiting to process, Processing, then Ready. Prompt text must stay unchanged.
4. Refresh/reopen the conversation: file status and download remain available.
5. Try a scanned PDF/encrypted PDF, unsupported extension, oversized/binary text
   and a fourth active file: expect a clear refusal/failure, not a false Ready.
6. Try another user's file/conversation endpoint and an anonymous session: access
   must be refused. Verify upload/delete/retry require CSRF.
7. Delete a file; download becomes unavailable immediately, then cleanup removes
   both objects. Test expiry and interruption recovery in a controlled environment.
8. Verify Research/Wick text-only chat, mode switching, context mentions and existing
   permission prompts still work. Files are stored privately but agents cannot read
   them yet; the UI states that explicitly.

Cloud credentials, actual permissions, latency, migrations and cleanup need live
acceptance after setup. Mocked/local tests do not prove Azure authorization.

## Worker startup and recovery

Background tasks now start in Gunicorn's `post_worker_init` hook, after the
serving process is forked. The built Render root includes `gunicorn.conf.py`,
which Gunicorn automatically loads from that directory; preserve it when using
custom deployment commands. Flask CLI/local serving starts tasks on the first
request. Application creation starts no background threads. Upload work,
embedding warmup and database keep-warm are initialized once per serving process.
Database pool state and locks are reset in forked children.

Database connections default to a 10-second connect timeout, a 30-second SQL
statement timeout and a 5-second SQL lock timeout. Operators can override these
with `DB_CONNECT_TIMEOUT_SECONDS`, `DB_STATEMENT_TIMEOUT_MS`, and
`DB_LOCK_TIMEOUT_MS`. These bound individual database operations, not total page
rendering time. Upload worker failures log the exception class without private
file contents or credentials; durable leases allow recovery.

After deploying the startup fix on Render, restore `UPLOADS_ENABLED=true` and
`UPLOAD_WORKER_ENABLED=true`. Verify a `Background tasks started in serving
process pid=... uploads=True` log line after worker boot. Open a database-backed
page, upload a small TXT file, confirm it reaches Ready, then delete it and verify
cleanup. Keep embedding troubleshooting separate. No migration or Databricks
redeployment is required for this lifecycle fix.

For temporary diagnosis, `UPLOAD_WORKER_ENABLED=false` leaves upload UI/storage
operations enabled but pauses extraction and physical cleanup. Do not treat that
setting as a completed rollout: queued uploads and deletions require a worker.

## Composer file cards

Uploaded files appear as compact type/name/status cards above the prompt inside
the shared composer (Research, Wick and the compact Wick panel). Long names are
ellipsized; full names are available on hover and in the file-details dialog.
The delete control appears on hover/focus and remains visible on touch devices.
Deletion retains the existing confirmation and durable cleanup semantics.

Click a card for original download, processing retry when failed, deletion, and
a bounded plain-text preview for TXT/Markdown. Preview text is rendered as text,
never HTML. The dialog retains the retention and agent-access notice without
placing a permanent explanatory paragraph beneath the composer. PDF editing,
image editing, and writing modified contents back to storage are not implemented
by these controls. File-content editing needs its own supported mutation contract.
The UI clears completed upload notifications so Ready cards do not retain a
contradictory queued message. This is a frontend-only change; redeploy Render,
with no migration or Databricks redeployment.
