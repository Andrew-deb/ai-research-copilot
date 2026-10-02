# Research and Wick deployment runbook

This release prepares three self-contained artifacts. It does not create Apps,
change grants, migrate data, or provision paid capacity. Keep the previous
Research source/deployment and Render settings available until the new release
passes live checks. The source remains authoritative; never edit business logic
inside generated artifacts.

## 1. GitHub publication and manual Databricks deployment (default)

After merging the publication workflow into `main`, open GitHub **Actions →
Validate and publish MCP deployments**. A main push runs it automatically; use
**Run workflow → main** to retry or publish on demand. Pull requests run validation
without permission to publish. The first successful main run creates:

| App | Git branch | Deployment source directory |
| --- | --- | --- |
| Existing Research App | `deploy/research` | Repository root (empty path) |
| `mcp-alfred-assistant` (Wick) | `deploy/assistant` | Repository root (empty path) |

Select the existing repository and corresponding branch in each App's Git
**deployment source**, then click **Deploy** manually. These branches already
contain `app.yaml`, requirements and all shared dependencies at their root. Do not
select `mcp_server/assistant` or `dist/release-candidate/assistant` in those branches.
The creation wizard's **Directory in repo** field is a template destination, not
this deployment-source setting. Avoid generating template files over existing code;
configure the existing-code Git source when deploying the created App.

The workflow tests the complete suite, builds all three artifacts, and transfers
the same validated artifact to a publication job. Only trusted main runs have
`contents: write`; no Databricks credentials or automatic App deployment are used.
Publication verifies source revision and file hashes, skips stale main builds,
keeps deployment history, and pushes both branches atomically without force.
Never edit generated branches: change main source and let the next run publish.
If repository settings prohibit workflow pushes or protect these generated branches,
the publish job fails visibly; grant this workflow branch publication access without
relaxing main's protection. For rollback, manually deploy a previous known-good
commit on the appropriate deployment branch and retain its manifest revision.

Render remains Blueprint-managed using `render.yaml`; its root/build/start settings
already build the dashboard artifact. This workflow does not deploy Render.

### Local build or workspace-upload fallback

From the repository root with Python 3.12 and a clean checkout:

```bash
python mcp_server/deploy/build.py --output dist/release-candidate
```

The output contains:

| Directory | Platform | Entry point | Catalog |
| --- | --- | --- | --- |
| `dist/release-candidate/research` | Existing Databricks Research App | `python -m research.server` | Existing 13 Research tools |
| `dist/release-candidate/assistant` | Separate Databricks Wick App | `python -m assistant.server` | Ten workspace tools |
| `dist/release-candidate/render` | Render dashboard | `gunicorn app:app` from this directory | Orchestration, both bundled system prompts |

Each MCP artifact has its own `app.yaml`, requirements, runtime adapters and
`shared_resource` copy generated from one source. Wick omits Research's entry
point, discovery orchestration, provider configuration and external-provider brokers. Shared module
presence is not a tool grant; only explicit registrations are exposed.

`deployment_manifest.json` records the Git revision, tool names and SHA-256 of
all shipped files. Build from a clean commit for a release: the revision identifies
the checkout, while file hashes identify the actual bytes (including uncommitted
changes in development). A build refuses an existing release directory. Choose a
new output path for a later candidate; outputs inside the repository must live
under ignored `dist/`. Environment files, bytecode, tests and local data are not
copied. No archive upload should include the repository root or a developer `.env`.

Verify locally using installed dashboard + MCP test dependencies:

```bash
PYTHONPATH=dashboard:. python -m pytest -q tests/test_deployment_packaging.py tests/test_mcp_server.py tests/test_wick_routing.py
```

PowerShell equivalent for the environment variable:

```powershell
$env:PYTHONPATH = "dashboard;."
python -m pytest -q tests/test_deployment_packaging.py tests/test_mcp_server.py tests/test_wick_routing.py
```

Both artifact servers are booted from their isolated directories during tests;
exact catalogs and anonymous/cross-mode refusals are checked through HTTP MCP.
The isolated dashboard loads both full prompts and serves health/static assets.
These tests intentionally do not access live Lakebase, Databricks, or model APIs.

## 2. Capacity and identity before provisioning

The user confirmed Databricks Free Edition allows three Apps, covering the planned
Apps. Keep deployments within that allowance and the $0 discretionary paid AI
budget. Render is managed through its Blueprint, not a manually configured service.

Keep three identities distinct:

- **Application user:** authenticated by Flask; trusted Render supplies that user's
  ID to the MCP server. A visitor does not need a Databricks account.
- **Render caller principal:** authenticates backend-to-App traffic using the existing
  Databricks OAuth client. Give it `CAN USE` on both Apps. A second caller principal
  is optional, not necessary for two target Apps. Do not copy an App's own credentials
  to Render; keep the caller's client ID/secret only in Render's secret settings.
- **App principals:** Databricks assigns a different service principal to each App.
  Grant each only its required resources. Do not copy one App's injected principal
  credentials into the other App.

The Databricks proxy must authenticate callers before accepting `X-RC-User-Id`.
Restrict App access to the trusted caller and necessary administrators. Granting
arbitrary callers App access also allows them to supply identity headers: the
current header model assumes a trusted backend, not delegated public API users.
Do not publish the raw ASGI process on an unauthenticated public endpoint.

## 3. Resource grants and configuration

Inventory the existing Research App's configuration and preserve it during
cutover. This code still uses `config._get_secret` with environment fallbacks;
it does not implement a new Lakebase OAuth connection or automatically create
PostgreSQL roles.

| Resource | Research | Wick |
| --- | --- | --- |
| `database/lakebase-url` secret or configured `DATABASE_URL` | Preserve working connection | Grant Wick access to its selected database connection |
| Provider secrets | Preserve existing OpenAlex/Semantic Scholar settings | No discovery provider grants needed for Wick's catalog |
| Lakebase global corpus | Existing reads/ingestion behavior | Read papers, authors and paper-author associations |
| Personal workspace tables | Existing Research operations | Read notes, collections, membership, progress, goals; create notes/collections, add/remove membership, upsert progress |
| `mcp_traces` | Preserve trace insert permission | Permit trace insertion; verify acting user attribution |

A database URI may encode a PostgreSQL role/password. In that case the **role in
the URI**, not the App's service-principal identity, determines SQL privileges.
For database-level mode isolation, configure a separate Wick role/connection with
minimum table privileges. Reusing the current connection preserves compatibility
but does not create DB privilege isolation. Review the actual schema, schema usage,
connection and sequence privileges with the database owner; do not run broad grants
blindly. Owner-scoped service checks remain mandatory either way.

Supply the database through the established secret scope, or add a Secret resource
and map its key to `DATABASE_URL` in `app.yaml`. For example, after defining a
resource key `lakebase-url`, use this environment entry in the **source**
`mcp_server/deploy/assistant.app.yaml` and rebuild the release:

```yaml
- name: DATABASE_URL
  valueFrom: lakebase-url
```

Do not place the connection value in the YAML or commit secrets. Resource mappings
are workspace-specific; the supplied app configs retain the existing secret-scope
workflow rather than inventing resource IDs. Existing schema prerequisites remain:
curated collections, note columns from migrations 16–19, and attributed trace columns.
No migration is introduced by packaging.

## 4. Upload and deploy MCP artifacts

Use the authenticated Databricks UI or a configured CLI. Upload **each artifact's
contents** to a separate, versioned Workspace folder. For example, from the
Research artifact directory use the CLI `databricks sync` workflow to upload to a
chosen `/Workspace/.../releases/<revision>/research` directory, and repeat from
Wick's artifact directory to its own folder. Verify uploads before deployment.
Do not select `mcp_server/research/` or `mcp_server/assistant/` directly as source
roots: they are source interfaces and do not contain sibling shared dependencies.

For an existing App, deploy the uploaded complete root:

```bash
databricks apps deploy <research-app-name> --source-code-path <uploaded-research-folder>
databricks apps deploy <wick-app-name> --source-code-path <uploaded-wick-folder>
```

Replace placeholders with real names/paths; first create the separate Wick App
through the UI only after the capacity and permission checks. Retain the prior
Research deployment folder. Switching its source does not require deleting or
recreating the existing App or rotating its working URL.

Generated `dist/` is ignored in Git. This workflow uses built-artifact upload;
a Git-source deployment needs CI to publish complete build outputs separately.
Do not configure Git-source paths to ignored build directories and expect them
to exist in the repository. The legacy `mcp_server/app.yaml` and root entry point
remain usable until the packaged cutover is verified.

## 5. Render cutover and prompt bundling

`render.yaml` now builds from repository root (`rootDir: .`):

```text
Build: python mcp_server/deploy/build.py --target render && pip install -r dist/deploy/render/requirements.txt
Start: cd dist/deploy/render && gunicorn app:app [existing gunicorn options]
```

The full command in `render.yaml` retains the existing worker/thread/timeout
settings. The artifact has the dashboard's flat imports, templates/static assets,
and `prompts/system_prompt.md` plus `prompts/wick_system_prompt.md`. Prompt loaders
prefer bundled files, then source-tree files for local development, then the
existing separate safe fallbacks. No runtime fetch of prompt files is required.

If Render settings are managed manually rather than through the Blueprint, update
Root Directory, Build Command and Start Command **together** using `render.yaml`.
Changing only one can break startup. Retain all current secret/auth/cookie settings.
Set endpoint environment values after MCP health/catalog verification:

| Setting | Value |
| --- | --- |
| `RESEARCH_MCP_SERVER_URL` | Research App's working URL with `/mcp` |
| `MCP_SERVER_URL` | Legacy Research alias, kept for rollback; explicit Research URL takes precedence |
| `WICK_MCP_SERVER_URL` | Separate Wick App URL with `/mcp`; never the Research URL |
| `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` | Existing trusted Render caller's OAuth settings |

Both URLs are server configuration, not browser inputs. Absent Wick configuration
returns unavailable without spending a query allowance. Credential or catalog
failure never redirects a write to Research. Restart/redeploy Render after endpoint
changes so active turns and credential/schema caches belong to the new configuration.

## 6. Live acceptance and rollback

Record the source revision, built-file hashes, App names, deployed folders, URLs
and release date without secrets. Verify the following with real accounts:

1. Both Apps start and answer `/healthz`; OAuth-authenticated MCP initialization and
   `tools/list` show exactly Research's 13 tools and Wick's ten.
2. Render logs show no missing prompt fallback warning; Research/Wick modes use
   their own catalog. Send a public read first to verify service connectivity.
3. Use two signed-in application accounts. Each can read its own workspace; the
   other user's note/collection/goal identifiers are denied. Anonymous lookup sees
   only global/public/curated assets and all personal mutations are refused.
4. In a deliberately created test collection/note, verify requested creation,
   membership/status changes and actual saved normalized fields. Collection removal
   follows the current conversational confirmation convention; structured Allow
   once/Always allow is still deferred. Clean up test assets using existing UI controls.
5. Check `mcp_traces.user_id` for the actual acting account, not the demo user; verify
   separate conversations/history, panel refresh, stop controls and no subsequent
   actions after stopping. A stop cannot undo a previously committed write.
6. Check endpoint outages/catalog mismatches, startup and turn latency, provider
   usage and actual free allowance before broad rollout. Health/catalog success
   alone does not prove database writes, ownership or cost constraints.

If Wick fails, remove/blank `WICK_MCP_SERVER_URL` and redeploy Render; keep Research
available. Do not point Wick at Research as a workaround. If Research packaging
fails, redeploy its previous source folder and restore the previous explicit
Research URL (or unset the explicit value to use `MCP_SERVER_URL`). If the Render
build fails, redeploy its previous commit and restore its earlier root/build/start
settings together. Do not delete Apps, mutate the database schema, or discard the
previous release during rollback.

## Official deployment references

Reviewed 1 October 2026; verify workspace-specific availability before release:

- [Databricks App deployment](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy)
- [App authorization and dedicated identities](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth)
- [App permissions](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/permissions)
- [App resource grants](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/resources)
- [Calling API Apps with token authentication](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/connect-local)
- [Render monorepo root-directory behavior](https://render.com/docs/monorepo-support)

## Collection paper picker and Wick corpus search

The collection page now offers Add papers for owned writable collections, with
corpus/saved scopes, bounded ranked keyword search, authors/year/abstract preview,
20-paper selection batches, pagination and already-present indicators. Additions
use the existing CSRF-protected endpoint sequentially; on a partial failure,
successful additions remain saved and remaining selections can be retried. Closing
the picker refreshes the page if membership changed. Curated/foreign/anonymous
mutation paths remain denied.

`search_workspace_papers` is Wick's tenth tool. It searches existing database
papers with PostgreSQL full-text stemming and partial term matches plus author
lookup; it is not semantic vector retrieval and does not call external providers.
Both interfaces use shared retrieval and owned membership services; append
transactions lock the collection row and keep duplicate positions unchanged.
No database migration, embedding credential or new environment variable is required.
For broad requests Wick proposes candidates before adding; explicit identified
paper additions are authorized directly. External discovery/import for both
interfaces remains a later increment.

After merge, wait for GitHub publication, manually deploy `deploy/assistant` to
Wick, and deploy the updated Render dashboard. Both must agree on the ten-tool
catalog; during staggered deployment Wick may report unavailable rather than use
an incompatible catalog. Research remains at 13 tools and needs no tool change.
Verify a topic search, saved scope, duplicate handling and add/retry/refresh in
the UI, then approve a Wick shortlist and verify the resulting collection.

Local checks: `PYTHONPATH=dashboard:. python -m pytest -q`. Optional browser check
requires Node, Playwright Chromium and `CODEX_PRIMARY_RUNTIME_NODE_MODULES` pointing
to the parent node_modules folder: `RUN_PICKER_BROWSER=1` enables its test.
The standard suite does not require browser dependencies or a live database.

### Durable Wick context and existing-asset picker

Apply `sql/26_wick_context.sql` to Lakebase **before deploying Render**. This
additive, repeatable migration stores only selected `{kind, id}` references on
conversations. Older conversations start with no selected context; new panel
conversations default to the current page. Explicit selections persist across
panel/full-chat navigation and devices. Up to five papers, notes, collections,
goals or supported pages may be selected. Context is shared by the conversation,
not by a particular message version; changing branches retains the conversation's
current selection.

Render calls the same shared workspace read services as Wick to produce fresh,
owner-checked previews. Previews are capped at 2,500 characters per asset and
marked when incomplete. Deleted/inaccessible selections are shown as unavailable;
new inaccessible references are refused. The browser supplies references, never
trusted content or an actor. Selection grants no mutation permission.

Deploy Render after the migration. No MCP catalog, Databricks App, environment
variable, dependency, provider or embedding change is required for this slice.
Uploaded-file attachment remains deferred. Rollback to the prior Render build
leaves the additive column in place harmlessly.

Acceptance: select and remove multiple assets in both surfaces, reopen a thread
from another browser/device, change pages without replacing saved context, check
foreign/deleted assets, inspect preview truncation, and verify Research-mode
requests do not use Wick context. Browser and live PostgreSQL checks remain
necessary in addition to repository tests.

## Shared-infrastructure source migration

Both server entrypoints now live directly in their own packages. Common runtime bindings, identity/tracing middleware, database connection and configuration live under `shared_resource`; Research-only provider clients and discovery remain in `research`. The obsolete root MVP modules are removed. Both MCP dependency installs use `mcp_server/shared_resource/requirements.txt` in source; generated artifacts still have root `requirements.txt`. Render packages only pure shared contracts and uses its own runtime infrastructure.

After merging, wait for the main GitHub workflow to publish both deployment branches, then manually deploy each App from its existing branch and repository-root source. Render’s existing Blueprint build automatically follows the updated builder. URLs, Git source branches, App startup commands, environment variables, OAuth credentials, grants and database schema remain unchanged. Verify `/healthz`, the exact 13-tool Research / 10-tool Wick catalogs, and an authenticated workspace read/write after deployment. Keep prior deployment commits available for rollback.
