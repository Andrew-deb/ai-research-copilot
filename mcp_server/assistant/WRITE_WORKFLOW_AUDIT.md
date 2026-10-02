# Wick write-workflow audit

Audited against the note, goal and collection dashboard workflows on 2 October 2026, after the shared-infrastructure migration. Shared module availability and tool exposure are separate decisions; this is not an instruction to expose every database operation.

| Workflow | Shared operation | Wick exposure after this increment | Next work |
| --- | --- | --- | --- |
| Create standalone/paper-linked note with title/tags | `note_service.create_note` | Existing `create_note` | Preserve bounded content and returned saved metadata |
| Read/find notes | Workspace discovery/read services | Existing workspace tools | No new read tool needed |
| Edit note body/title/tags | Shared owner-scoped note operation; dashboard delegates to it | Not exposed | Shared operation extracted; define overwrite approval before tool exposure |
| Pin/unpin note | Shared owner-scoped note operation; dashboard delegates to it | Not exposed | Shared operation extracted; decide focused tool exposure with permissions |
| Delete note | Shared owner-scoped note operation; dashboard delegates to it | Not exposed | Shared persistence/service contract extracted; structured destructive-action approval before tool exposure |
| Create learning goal | `goal_service.create_learning_goal` | New tool of the same name | Shared title/description validation also used by dashboard |
| Change goal status | `goal_service.update_goal_status` | New tool of the same name | Require explicit instruction; resolve exact owned target; active/completed/archived only |
| Find/read goals | Workspace discovery/read services | Existing workspace tools | No duplicate goal-list/detail tool needed; no automatic semantic matching |
| Create collection | Shared collection service | Existing `create_collection` | Existing behavior preserved |
| Find/read collections | Workspace discovery/read services | Existing workspace tools | Resolve ambiguity before mutation |
| Add/remove existing corpus papers | Shared collection service | Existing membership tools | Removal retains conversational confirmation; not structured approval |
| Reorder collection | Shared planning reorder exists; dashboard uses an ordered-ID adapter | Not exposed | Reconcile complete order vs legacy partial-position contract and owner/curated checks before exposing a narrow reorder tool |
| Rename/delete collection or rewrite goal content | No current matching exposed workflow/tool contract | Not exposed | Scope product behavior and shared validation deliberately |

## Current increment

Wick grows from 10 to 12 clearly defined tools by adding goal creation and status updates. Research remains at 13 tools and gains no tools from this change. The tool count is a design budget, not a protocol restriction. No generic operation dispatcher is introduced.

Goal services require an actor; title length is bounded at 300 characters and text is normalized. Status updates use owner-filtered persistence and treat missing/foreign goals alike. Both Flask and MCP call the same shared business operations. The dashboard translates domain exceptions into its existing error types. Goal creation does not run embeddings/discovery or spend model quota on matching papers.

Render enforces the existing `goals:write` tier capability, Wick mode and write gate; MCP requires attributable identity. The prompt requires explicit intent, target resolution and successful tool results before claiming completion. These controls do not implement Allow once/Always allow; that remains separate planned work.

## Deployment

Publish the updated assistant artifact and deploy Wick, and rebuild/restart Render with the matching 12-tool registry. Strict catalog checking will reject mixed 10/12 versions, so a short rollout interval is possible until both are current. No migration, new dependency or environment setting is required. Research need not redeploy for this goal increment; the prior shared-infrastructure migration had its own both-server rollout.

## Next increment

Reconcile the shared note mutation contracts first, then integrate structured permissions before destructive/overwrite tool exposure. Collection reorder should preserve a validated complete order, use a single atomic write, and reject unknown/duplicate paper IDs. Do not import Flask services into MCP or duplicate business logic in tool registration.

## Shared note-mutation foundation

Editing, explicit pin state and deletion now exist in `shared_resource/services/note_service.py`. Both database runtimes reuse owner-scoped SQL in `shared_resource/repositories/note_mutation_repository.py` via their own write executors. Dashboard adapters preserve note presentation, error mapping and delete return behavior. Note editing is full replacement: omitted title/tags clear that metadata, while paper linkage and pin state remain unchanged. Creation/editing reuse one bounded text/title/tag normalizer. Invalid non-text content/tags are rejected rather than coerced.

No MCP mutation tools are added. Wick remains at 12 tools and Research at 13; structured approval and operation-specific exposure remain the next increment. Existing UI routes keep their capability and CSRF checks. No migration or environment change is required. Deploying Render makes the dashboard consume the shared implementation; new MCP artifacts include it but no server redeployment is needed solely for new behavior because there is no new registered tool.

## Structured approvals (next increment)

Existing Wick writes now pass a dashboard action policy before MCP I/O. Exact-call
one-time approvals and revocable operation/target/endpoint grants are durable and
owner scoped. Read access, context selection and model text never create grants.
Run checkpoints preserve remaining tool calls across a pause without repeating
completed calls. New note edit/pin/delete registrations remain a later increment;
shared note mutation services from PR #29 are available but not exposed as tools.
Research's existing write operations retain their current compatibility policy.
See `deploy/DEPLOYMENT.md` for migration and live verification requirements.

## Note tool exposure

Wick now exposes `edit_note`, `set_note_pinned` and `delete_note` as separate thin
adapters over the shared note services and existing owner-filtered SQL. All three
require a bound actor and Notes Write capability; Research gains no registrations.
Each has an explicit note-target approval policy, including scoped revocation.

`edit_note` fully replaces body/title/tags; omitted metadata clears it. Pin state
and paper association remain untouched. Read the exact note and preserve metadata
unless asked to change it; never overwrite from a truncated preview. Pin/unpin
uses an explicit boolean and deletion returns a receipt only after persistence
confirms removal. Permanent deletion is labelled clearly in the approval UI.

Wick grows from 12 to 15 tools. These distinct operations justify exceeding the
approximate dozen-tool budget without ambiguous dispatching. Research stays at
13. Complete collection reorder remains the next shared workflow reconciliation.

## Complete collection ordering and run permission selection

Shared `reorder_collection` and `collection_order_repository` now reconcile the
manual dashboard workflow and Wick's complete-order tool. The transaction checks
current membership and owner/curated state before bulk persistence. Research's
legacy explicit-position reading-plan API retains compatibility.

The composer exposes explicit per-run Ask/Autonomous selection. Ask respects
existing scoped grants; Autonomous does not save grants and retains resource and
capability checks. It is unavailable to anonymous users and Research. Defaults
reset on reload. Wick now has 16 focused tools; further expansion should revisit
frequency and overlap before adding more tools.
