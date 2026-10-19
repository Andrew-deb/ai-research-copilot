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

An HTTP scheduler cannot cover this period, and the reason is measurable rather
than a matter of configuration:

```text
Render cold start        ~42 s    measured 18-19 October 2026
cron-job.org's timeout    30 s    hard limit on a free account
```

An HTTP pinger reaches the database only through the application, so it needs
Render awake — and it cannot wake it, because Render discards a half-started
container shortly after the requester disconnects. An earlier revision of this
guide prescribed `0 */4 * * *` against `/readyz` and advised allowing "enough
HTTP timeout for a cold application to wake". No such timeout was available, the
job never succeeded, and Lakebase's compute was disabled twice.

Overnight coverage therefore belongs to `.github/workflows/keepalive.yml`, which
connects to Postgres directly and does not involve Render at all. Review that
workflow before changing any schedule here.

## 4. Division of responsibility

| When | Who | What |
| --- | --- | --- |
| Before the HTTP window opens | GitHub Actions | Wake the app, waiting past a cold start |
| During the window | external HTTP scheduler | Keep an already-awake app awake |
| During the window | the app | `DB_KEEPWARM_SECONDS` touches Lakebase |
| Twice during the window | GitHub Actions | Fail-safe if the HTTP scheduler has stopped |
| Overnight | GitHub Actions | `SELECT 1` direct to Lakebase, app left asleep |

Overnight runs must not wake Render. A service that never sleeps consumes
substantially all of the monthly free instance-hour quota, and exceeding that
quota suspends every free service until the next month.

This schedule is operational configuration: keeping this guide in Git does not
create a scheduler or guarantee that the deployed endpoint will remain active.
