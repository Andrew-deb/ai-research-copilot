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
are retained. The legacy paper-note API remains paper-only; standalone notes,
title/tag validation, bounded workspace reads, and action approval are later
increments, not new tools enabled by this extraction.

No new environment variables, dependencies, schema migrations, or deployment
root changes are required for this increment.
