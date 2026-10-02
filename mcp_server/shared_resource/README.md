# Shared Alfred layers

The shared package is organized by architectural layer. `services/` contains
collection, paper, note, progress, goal, and reading-plan modules;
`exceptions.py` contains their common errors. Notes are separate from progress,
and goals are separate from reading-plan ordering.

Reusable `repositories/`, `brokers/`, or `middleware/` layers will be added when
their implementations are extracted for both servers. Server-specific tool
catalogs, prompts, startup/configuration, and policies stay with their servers.
Business services continue to receive their dependencies explicitly; moving an
infrastructure module here does not make it a dependency of every service.

Research's top-level service modules remain compatibility adapters. They supply the Lakebase
repository to this package; tool registration and acting-user resolution remain
in the MCP interface. Future Wick adapters can use these operations without
importing Research's tool catalog, prompt, config, or identity middleware.

Internal imports are relative, so this package cannot accidentally import the
dashboard's flat `services`, `repositories`, or `exceptions` modules. Domain
exceptions are re-exported by the existing MCP `exceptions.py` module.

This first increment lives inside the existing `mcp_server/` deployment root.
Databricks can continue deploying that directory's contents unchanged. A second
app will need a self-contained artifact containing this same authoritative
package. The dashboard remains independent until that packaging and its richer
note/curated-read contracts are reconciled; importing its Flask services into
the MCP process is not the sharing strategy.

Collection mutations require an attributable owner and reject curated examples.
Ordering is validated before persistence and written atomically by the
repository. Paper lookup stays local; external discovery remains Research's
responsibility. Existing tool signatures, response fields, and demo read behavior
are retained. The legacy paper-note API remains paper-only. Rich note creation
and bounded workspace reads now exist as service contracts; registering Wick's
tools and action approval remain subsequent increments.

No new environment variables, dependencies, schema migrations, or deployment
root changes are required for this increment.

## Workspace contracts

`services/workspace_service.py` provides finite resource discovery and owner-scoped
retrieval. `repositories/workspace_repository.py` holds the parameterized SQL and
receives the runtime's database executor. It imports neither configuration nor a
database driver. The runtime adapter in the top-level `services/workspace_service.py`
supplies Lakebase; it does not register tools or determine the acting user.

Resource kinds are paper, collection, note, goal and a finite page allowlist.
Anonymous discovery excludes private kinds and only reads explicitly curated
collections. Private reads require identity before querying. Results have typed
references and provenance; the trusted dashboard will map references to routes.
This layer never visits arbitrary URLs or executes page actions.

Search and list requests are capped at 50 items with numeric offset cursors up
to 10,000. Note content uses 4,000-character chunks, up to the 10,000-character
note contract. Paper abstracts and collection/goal descriptions have explicit
truncation indicators. Goal reads do not run semantic matching. Offset cursors
are reauthorized on every request and may shift if items change between pages.

`note_service.create_note` accepts standalone or paper-linked notes, titles and
tags. It uses the dashboard's limits and normalization for valid text inputs,
and rejects non-text tag elements. Research's `save_note` retains its signature,
paper requirement and response fields, and now uses the same 10,000-character
body limit as the dashboard. Rich metadata needs the existing sql/16–18 note
schema; curated reads need the existing `is_curated` column. No new migration is
introduced. Note reads also use the existing sql/19 pin column. The dashboard continues using its current services until shared
runtime packaging is integrated; it is not importing MCP's flat modules.
