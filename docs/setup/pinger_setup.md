# Readiness pinger setup

This guide belongs in the repository so deployment validation does not depend on
an untracked document in a developer's parent directory.

## 1. Render health check

Keep the Blueprint health check on `/healthz`. It reports process liveness
without querying Lakebase; database downtime must not trigger process restart
loops. `/readyz` additionally checks the database and can report degraded status.

## 2. While the application is running

The configured database keep-warm worker can keep Lakebase active while the app
process is running. It cannot run while Render has suspended that process.
Review the existing `DB_KEEPWARM_SECONDS` setting before changing the interval.

## 3. Lakebase, overnight

An external scheduler must target `/readyz`, not `/healthz`, if the objective is
to exercise the database during periods when Render would otherwise be asleep.
The documented overnight schedule is `0 */4 * * *` (every four hours). Confirm
scheduler timezone, endpoint URL, current platform limits and observed wake-up
behaviour when configuring it. Allow enough HTTP timeout for a cold application
and database to wake, and alert on repeated non-success responses.

This schedule is operational configuration: keeping this guide in Git does not
create a scheduler or guarantee that the deployed endpoint will remain active.
