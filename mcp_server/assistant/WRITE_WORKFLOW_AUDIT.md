# Wick write-workflow audit

Audited against the note, goal and collection dashboard workflows on 2 October 2026, after the shared-infrastructure migration. Shared module availability and tool exposure are separate decisions; this is not an instruction to expose every database operation.

| Workflow | Shared operation | Wick exposure after this increment | Next work |
| --- | --- | --- | --- |
| Create standalone/paper-linked note with title/tags | `note_service.create_note` | Existing `create_note` | Preserve bounded content and returned saved metadata |
| Read/find notes | Workspace discovery/read services | Existing workspace tools | No new read tool needed |
| Edit note body/title/tags | Rich dashboard operation; absent from shared note service | Not exposed | Extract validated owner-scoped operation, reconcile result/error contracts, and define overwrite approval |
| Pin/unpin note | Dashboard operation; absent from shared note service | Not exposed | Extract owner-scoped operation before adding a focused tool |
| Delete note | Dashboard operation; absent from shared note service | Not exposed | Extract persistence/service contract; structured destructive-action approval first |
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
