# Private uploads: Azure setup and rollout

Increment B implements private PDF, TXT and Markdown files in Research and Wick.
Original bytes and versioned extracted text live in Azure Blob. Lakebase stores
ownership, identifiers, hashes, reservation sizes, expiry and durable jobs. Files
do not enter the global paper corpus. Agent reading and citations are implemented by Increment C below.

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
   permission prompts still work. Files are read by agents only when explicitly referenced in the current prompt.

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

## Increment C: selected-file grounding

After a file reaches Ready, choose **Use in prompt** on its card. A clickable
reference is inserted at the current caret position in the shared inline editor for Research and Wick. Removing that reference only deselects it; deleting the stored
file uses the separate confirmed delete action. Sending a message selects only
the IDs in its inline references, not every file in the conversation. Uploading
alone does not send document text to the model.

The Render orchestration layer offers `read_attached_document` only for selected
attachments and signed-in upload-capable users. This is a local model capability,
not an MCP registration. It reuses the pure shared `document_passages` selector;
Render retains Azure credentials, ownership checks and bounded storage reads.
Neither MCP App needs Azure credentials or a catalog change. Research and Wick
keep separate prompts and their existing deployed tool sets.

Each read verifies owner, conversation association, Ready/unexpired state,
extraction hash and version; deletion/expiry is checked again after storage I/O.
Passages use real extracted page numbers and offsets. Lexical selection returns
at most four 1,800-character windows per tool call; JSON serialization is also
bounded without cutting an invalid JSON fragment. A page/query can narrow a
subsequent read. Figures, missing OCR and extraction gaps are not inferred.
No embeddings, vector migration or paid model calls are needed to process files.
Normal agent questions still use the configured LLM and existing request/tool
budgets; live cloud/model acceptance must be performed separately.

Research/Wick citations use document/page source IDs only after successful reads.
Their authorized links open bounded extracted-page previews with escaped text and
private/no-store responses. IDs invented by the model are not resolved as sources.
History retains selected IDs/labels in the user message's existing usage JSON;
no schema migration is necessary. Regeneration preserves its original selection,
edits may remove references, and reopened history labels expired/deleted sources
unavailable. Stored answers and explicitly created notes remain conversation or
workspace records; source-file expiry does not erase those user-created records.

Wick can create notes or goals through its existing tools. Read-source links are
included in the proposed note/goal fields before approval and remain in the
saved content. Selection/read access grants no write approval. The existing
checkpoint continuation and quota policy are reused; expiry is rechecked before
resuming and before writes. Uploaded instructions never change identity, mode,
permissions or tool policy. Uploads cannot be inserted as paper IDs in collections;
retained document assets, @ document search, collection membership and indexing
remain Increment D.

Rollout: merge the Increment C PR and redeploy Render only. Keep the existing
upload flags/configuration and migration 29. No Databricks redeployment, new OAuth
connection or Azure credential distribution is required. Confirm in both modes:
Ready file -> Use in prompt -> grounded response -> filename/page citation ->
source preview; refresh history, remove an attachment during prompt edit, and
regenerate a read-only response. Ask Wick for a note/goal, decline once and verify
no asset was created, then approve a fresh task and verify its saved content and
provenance. Confirm unavailable/foreign files fail without exposing bytes and
approval continuation does not consume another daily question.

## Conversation Files panel refinement

Uploads now select themselves in the draft. File cards above the composer represent
only that draft's selected references; sending moves their visible representation
into the user message and clears the composer. Removing a draft card deselects it;
it does not delete storage. The conversation's Files tab remains the place to find
previous uploads, including their processing state and expiry. Ready files also
appear in Wick's `@` Assets picker within that conversation. These are temporary,
owner-bound attachments, not global or retained workspace document assets.

Files and Sources share one resource rail. Desktop uses its existing collapsible
column; mobile uses one drawer and one draggable launcher, with keyboard activation
and Sources/Files tabs. A file opens a detail/preview view within Files. TXT/Markdown
are escaped text, bounded to 20,000 preview characters. PDFs use an authenticated
inline PDF response with private/no-store caching, nosniff and a restrictive CSP;
the browser's PDF renderer provides viewing controls. An extracted-text link is
available when the browser cannot render the PDF. Citation page links select the
corresponding PDF page. Previewing alone grants no model access or write permission.
Download, retry, reference and confirmed deletion are available as labelled icons.

Rollout: redeploy Render only. No schema, environment, Azure access or MCP catalog
changes. Check desktop/mobile tabs, upload -> Ready -> send, empty composer after
sending, file cards after reopening history, `@` reuse, PDF/text preview, expiry,
original download and confirmed deletion. Native PDF viewing support varies by
browser; use the extracted-text fallback if unavailable. Existing seven-day expiry
and three-active-file limit remain unchanged.

### File panel layout and archive export

The resource rail now has an intrinsic-width guard, a constrained desktop width
and a keyboard/pointer resize separator. Long filenames use ellipsis on compact
message cards outside the prompt bubble and in the preview header. Files uses a
tile grid, with confirmed delete actions on hover/focus and visible touch controls.
Preview content fills the remaining panel height; expand/restore works on desktop
and mobile, and Escape restores an expanded preview. The mobile draggable launcher
and Sources/Files tabs remain shared.

Download all calls an authenticated conversation ZIP endpoint. A dedicated
`upload_archive` service checks the owned conversation, excludes expired/deleting
files, enforces the existing three-file and per-file size bounds, and reuses the
owned/hash-verified download service for every member. It sanitizes and deduplicates
archive names. Output spools to temporary disk after 4 MiB and is closed with the
response. An unavailable file fails the export rather than silently returning a
partial ZIP. This does not change retention or permit cross-conversation exports.
Render redeployment only; no migration, new variables or MCP redeployment.

### Compact preview surface

While a document is open, the rail heading and Sources/Files tabs give way to one
46px toolbar: ellipsized filename, download, expand/restore, more actions and close.
Closing returns to the file list and restores the tabs. Reference, delete, retry,
status/expiry and extracted-text fallback live in the more-actions menu instead of
permanent toolbar/footer rows. The viewer takes the remaining height, and expanded
preview fills the chat viewport on desktop/mobile. PDF URLs request fit-to-width
and hide native navigation/toolbars where the browser supports those parameters;
PDF rendering remains browser-provided. No backend or deployment setting changes.

### Mobile chat viewport and tablet drawer correction

A later desktop `position: relative` rule had overridden the mobile resource
rail's fixed drawer positioning. Relative positioning is now desktop-only; mobile
and tablet drawers remain fixed and track the visible viewport. Launcher taps
and keyboard activation share a click path, while a drag suppresses its follow-up
click. Moved launchers are clamped again when the visible viewport changes.

Chat/landing surfaces omit the application footer, and the daily allowance is
above the shared composer. Other application pages retain their footer. A focused
`chat_viewport` module observes VisualViewport resize/scroll events, with an
innerHeight fallback and native pinch-zoom preservation. On mobile, the chat shell
uses that visible height/offset, the conversation scrolls independently, and the
composer stays outside the scrolling thread. Draft upload cards scroll horizontally
to limit keyboard-space usage. This applies to Research/Wick chat and the embedded
Wick composer; no request, quota or upload-policy changes are involved.

After Render redeployment, check an actual phone with keyboard open/closed and
browser chrome changes, a tablet tap/drag, rotation, a long conversation and a
multi-line draft. Simulated viewport checks do not replace physical-device testing.

### Mobile drawer hit-testing and preview-first cards

Mobile/tablet resource rails now move to the document body alongside their
backdrop and launcher. This avoids the fixed chat shell's stacking context placing
the body-level backdrop over the nested drawer. On desktop the same rail returns
to its original layout column; its resize calculations keep the original chat
wrapper. The launcher is hidden while the drawer is open and restored on close.

Message file-card links use the authorized extracted-page preview as a fallback,
not the original download URL. When the upload UI is available they open the
in-panel preview; missing cached metadata triggers a conversation-file refresh
before selecting the file. Expired/deleted items show an unavailable state rather
than falling through to a download. Explicit Download original/Download all
controls retain their download behaviour. Render-only rollout, with no backend,
permission, schema or MCP changes.

### Increment D1 — Save to workspace

Apply `sql/30_retained_documents.sql` as the schema owner before deploying this
release to Render. Deploy/restart every upload worker with the same release;
do not leave an old standalone worker running alongside retained-document code.
Use the existing `UPLOADS_ENABLED` / `UPLOAD_WORKER_ENABLED` settings. No new
Azure objects, credentials, environment variables or MCP deployment are needed.
Existing table-level SELECT/UPDATE permissions cover the new column; deployments
using column-specific grants must include `retained_at`.

Only Ready, unexpired uploads can be saved. Open Files, open the file preview,
then use the bookmark icon (Save to workspace) in the more-actions menu. Saving
is idempotent and serialized with expiry/deletion cleanup: once cleanup has won,
saving refuses rather than resurrecting the object. No original or extraction
artifact is copied and no embedding/model request is made.

The Documents navigation entry opens the owner's paginated saved-file library.
Files open in a dedicated preview with extracted text, download and delete icon
controls. Saved originals/artifacts remain private and count against the existing
100 MiB/user and 1 GiB/deployment defaults (or configured capacities). The existing
three-file limit in the original conversation is unchanged by saving. Retention
is not a storage quota bypass.

The original `expires_at` remains recorded, but saved files do not expire on that
date. They survive deletion of the original conversation and can still be opened
from Documents. Deleting a file makes it unavailable immediately; the durable
worker deletes both original and extracted artifact before releasing capacity.
Deleting the owner account also triggers cleanup, including saved files.

This first increment does not add cross-conversation @ mentions, document
collection membership, MCP write tools, content editing or semantic indexing.
Agent reads still require an explicitly selected file from the current chat.
Later increments can extend that selection boundary deliberately.

Live acceptance: save a Ready PDF and TXT; repeat Save; confirm the same document
ID/key and unchanged byte reservation; open Documents; delete the original chat;
verify saved preview/download still work; test another user's refusal; delete the
saved file and verify cleanup/capacity release. Separately verify that unsaved
expiry still removes files and account deletion cleans retained files. Local SQL
and mocked storage tests do not prove the production worker/migration was applied.
