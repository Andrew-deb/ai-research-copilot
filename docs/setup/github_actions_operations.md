# GitHub Actions — what runs, what it costs, and how to switch it off

Two workflows run in this repository. They do very different jobs, and only one
of them is safe to disable casually.

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `mcp-deployment.yml` | push and PR to `main` | Runs the test suite, builds deployment artifacts, **and pushes the branches Databricks deploys from** |
| `keepalive.yml` | schedule, six times a day | Touches Lakebase directly, wakes Render before the HTTP ping window |

---

## Do not schedule anything time-sensitive here

**A GitHub cron is not an alarm clock. It is a request to join a queue.**

`cron: '43 4 * * *'` does not mean "run at 04:43". It means "from 04:43 this run
is eligible". GitHub starts it when it gets to it, and on this repository that
is about **five hours later**.

Measured, two consecutive days, same crons:

| Due (UTC) | 21 Oct arrived | 22 Oct arrived |
| --- | --- | --- |
| 22:13 | 00:37 | 00:41 |
| 01:37 | 06:30 | 06:32 |
| 04:07 | 09:17 | 09:20 |
| 04:43 | 09:35 | 09:36 |
| 04:53 | 09:41 | 09:40 |

Matching to within four minutes across days: this is a stable offset, not
congestion. Moving the minutes off the hour changed nothing, because contention
was never the cause.

**Event triggers on the same repository are fast.** Six push and pull_request
runs started **3 to 46 seconds** after their commit. Same repo, same runners,
same free tier — the difference is the trigger type, not the capacity.

Why the scheduled queue runs five hours behind here is not documented by GitHub
and is not worth guessing at. The measurement is what matters: **anything that
must happen at a particular time needs an event, not a cron.**

What this cost before it was understood: cron-job.org began pinging at 06:00
Lagos while Render was still asleep, the wake that should have run at 05:43
arrived at 10:36, and every ping in between failed — roughly 55 failures each
morning, against a provider that disables a job after 25.

---

## Waking Render on time: the dispatch trigger

Two tools, each with half of what is needed:

| | Punctual | Patient |
| --- | --- | --- |
| cron-job.org | yes, fires to the second | **no** — abandons a request at 30 s, and a cold start takes ~42 s |
| GitHub Actions via `schedule` | **no** — ~5 h late | yes, waits as long as asked |

Neither works alone. So the punctual one raises an event and the patient one
does the waiting:

```text
05:50 Lagos  cron-job.org POSTs to the dispatches endpoint   (<1 s, succeeds)
   +seconds  keepalive.yml starts, like a push
   +42 s     Render is awake
06:00 Lagos  the normal */5 pings begin, and now they succeed
```

### Step 1 — work out the URL

```text
https://api.github.com/repos/<owner>/<repo>/dispatches
```

`<owner>` and `<repo>` are the two path segments of the repository's GitHub
address. For this project the remote is
`https://github.com/Andrew-deb/ai-research-copilot.git`, so:

```text
owner = Andrew-deb
repo  = ai-research-copilot

https://api.github.com/repos/Andrew-deb/ai-research-copilot/dispatches
```

If you are ever unsure, `git remote -v` prints it.

### Step 2 — create the token on GitHub

A **fine-grained** personal access token, not a classic one. Classic tokens
cannot be scoped to a single repository, and this token only ever needs one.

1. GitHub → click your avatar (top right) → **Settings**
   *(your account settings, not the repository's)*
2. Left sidebar, scroll to the bottom → **Developer settings**
3. **Personal access tokens** → **Fine-grained tokens**
4. **Generate new token**

Then fill in:

| Field | Value |
| --- | --- |
| **Token name** | `alfred-keepalive-dispatch` — name it for its job, so a future you can tell what breaks if it is revoked |
| **Expiration** | 90 days. Not "no expiration" — see the warning below |
| **Resource owner** | your own account |
| **Repository access** | **Only select repositories** → `ai-research-copilot` |

Then **Repository permissions** — expand it and set exactly one:

| Permission | Set to | Why |
| --- | --- | --- |
| **Contents** | **Read and write** | what the dispatches endpoint requires |
| *Metadata* | *Read* (automatic) | GitHub adds this itself; leave it |

Leave every other permission at *No access*. Leave **Account permissions**
untouched.

5. **Generate token**
6. **Copy it now.** GitHub shows the value once. If you lose it, delete the
   token and make another — you cannot read it back.

### Step 3 — prove the token works before wiring it up

One command, and it is worth doing: it separates "the token is wrong" from
"the cron job is wrong", which otherwise look identical.

```bash
curl -i -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{"event_type":"wake-render"}' \
  https://api.github.com/repos/Andrew-deb/ai-research-copilot/dispatches
```

| Response | Meaning |
| --- | --- |
| **204 No Content** | Correct. A run should appear in the Actions tab within seconds |
| **404 Not Found** | The token cannot see the repository — wrong repository selected, or Contents permission not set. **It is not a typo in the URL**; GitHub deliberately returns 404 rather than 403 so that a token cannot be used to discover which private repositories exist |
| **401 Unauthorized** | The token is wrong, expired, or has a stray space |
| **422** | The body is malformed — check it is exactly `{"event_type":"wake-render"}` |

### Step 4 — create the cron-job.org job

**Cronjobs → Create cronjob.**

On the **COMMON** tab:

| Field | Value |
| --- | --- |
| Title | `Alfred — wake Render for the ping window` |
| URL | `https://api.github.com/repos/Andrew-deb/ai-research-copilot/dispatches` |
| Enable job | on |
| Save responses in job history | **off** — a 204 has no body worth keeping, and the request carries a credential |
| Execution schedule | **Custom** → `50 5 * * *` |

That is **05:50 in the job's own timezone**, which the ADVANCED tab shows as
Africa/Lagos — ten minutes before the `*/5 6-22` ping window opens, leaving room
for the ~42 s cold start plus a retry.

On the **ADVANCED** tab:

| Field | Value |
| --- | --- |
| Request method | **POST** |
| Request body | `{"event_type":"wake-render"}` |
| Timeout | 30 seconds (the default is fine — this call takes under a second) |
| Treat redirects as success | off |

Then **Headers** → **+ ADD**, three of them:

```text
Accept          application/vnd.github+json
Authorization   Bearer ghp_yourtokenhere
Content-Type    application/json
```

The word `Bearer`, then a single space, then the token. A missing `Bearer` or a
trailing space is the usual cause of a 401.

On the **NOTIFICATIONS** tab, turn on failure notifications. This job is the
only thing waking Render on time, and it failing silently is how the previous
arrangement went unnoticed for weeks.

**Save**, then use **TEST RUN** to fire it once immediately and confirm a
`repository_dispatch` run appears in the Actions tab.

### What you are handing over

The token lives in cron-job.org's header field. Anyone with access to that
account can start workflows in this repository and write to its contents.
Scoped to one repository the blast radius is small, but it is not zero.

Give it an expiry rather than "no expiration". A dispatch that stops working is
a loud, easily diagnosed failure that you will notice the next morning, which is
the right way for a credential to lapse — whereas a token that never expires is
one you will never think about again.

To rotate it: generate a new token, edit the header in cron-job.org, TEST RUN,
then delete the old token on GitHub. In that order, so there is no gap.

### Checking it worked

The run appears in the Actions tab with event **`repository_dispatch`** rather
than `schedule`, within seconds of 05:50. The `keepalive` job takes ~45 s when
it performs a real cold start and ~6 s when the app was already awake — the
duration is the quickest way to tell which happened.

The morning after, the `*/5 6-22` ping job should show **no failures at 06:00**.
That is the result worth checking; everything above is only the means.

GitHub's own schedule stays enabled and unchanged. It still performs the
overnight database touches, where arriving five hours late is harmless: Lakebase
only needs touching a few times a day, and that half has run without a single
failure.

---

## Repository secrets

`keepalive.yml` needs one: **`DATABASE_URL`**, the same connection string the
application uses. `mcp-deployment.yml` needs none — it authenticates with the
`GITHUB_TOKEN` that Actions provides automatically.

### Setting it from VS Code, or from the browser

Both work, and both end up in the same place. The GitHub Actions extension for
VS Code can create repository secrets (*Settings → Secrets → Repository
Secrets*, then the `+` that appears on hover), and it uses the same API the web
UI does: the value is encrypted with the repository's public key before it
leaves the machine, so it is not sent in the clear either way.

Two small reasons to prefer the browser for this particular secret:

- The extension needs an OAuth session with sufficient scope, and when that
  scope is missing the failure is usually a silent no-op rather than an error.
- A connection string is long and easy to truncate when pasting into a
  single-line input. The web form shows the whole value while you paste it, and
  you can immediately confirm the secret appears in the list afterwards.

Neither tool can show you a secret's value again once saved. If in doubt,
overwrite it.

**Never put the connection string in a workflow file, a commit message, or a
log line.** GitHub masks known secret values in logs, but only ones registered
as secrets.

---

## Disabling a workflow

### How

Three equivalent routes. Nothing is deleted, and re-enabling is the same action
in reverse.

**VS Code** — the GitHub Actions extension, under *Workflows*: right-click the
workflow, *Disable workflow*.

**Browser** — *Actions* tab → select the workflow in the left sidebar → the
`···` menu at the top right → *Disable workflow*.

**Command line:**

```bash
gh workflow disable mcp-deployment.yml
gh workflow enable  mcp-deployment.yml
gh workflow list --all          # shows which are active
```

A disabled workflow keeps its file, its history and its secrets. Scheduled runs
stop; manual runs are refused until it is enabled again.

### What you lose by disabling `mcp-deployment.yml`

This is the part worth reading before switching it off, because the name
undersells it. It is not only a test runner — **it is the deployment
mechanism**:

1. Runs the full test suite on every push and pull request
2. Builds the deployment artifacts
3. **Pushes the deployment branches that Databricks deploys from**

Disable it and (3) stops with the rest. A change to shared code would pass
review, merge, and never reach the MCP server — and nothing would report an
error, because nothing ran. The symptom would be Databricks quietly serving
yesterday's code.

So: safe to disable while the project is genuinely dormant, and **a trap if you
forget to re-enable it before the next change**.

### What you lose by disabling `keepalive.yml`

Lakebase stops being touched overnight, and Render stops being woken before the
HTTP ping window. That is the exact state that disabled the database compute
twice and required recreating the branch. Do not disable this one to save
minutes; it is the cheaper of the two by a wide margin.

---

## The minutes question

Public repositories get unlimited standard-runner minutes. **Private
repositories on GitHub Free get 2,000 minutes a month.** The question only
arises if this repository becomes private.

### Where the minutes actually go

`keepalive.yml` is small: six short runs a day, most of them a database query
and nothing else. Budget a few hundred minutes a month at most.

`mcp-deployment.yml` is the expensive one, because it runs the **entire test
suite** — around 1,500 tests — and it runs that suite **twice per change**:

```text
open a PR        -> validate  (full suite)
push to the PR   -> validate  (full suite, previous run cancelled)
merge the PR     -> validate  (full suite AGAIN) + publish
```

The merge re-runs everything on code that was validated minutes earlier. That
duplication is where a private repository's minutes would go.

### What does NOT fix it

**Removing the `push: branches: [main]` trigger breaks deployment.** It is
tempting, and it is wrong. The `publish` job is declared as:

```yaml
publish:
  needs: validate
  if: github.ref == 'refs/heads/main' && (github.event_name == 'push' || github.event_name == 'workflow_dispatch')
```

It depends on `validate` **and** requires a `push` event. Remove the push
trigger and `publish` stops running on merge, so deployment branches are never
updated — the same silent staleness as disabling the workflow outright, but
harder to notice because the PR checks still go green.

### What does

In rough order of benefit per unit of risk:

**1. Keep the heartbeat out of CI.** `keepalive.yml` commits a monthly file to
the default branch, and `mcp-deployment.yml` has no path filter — so that commit
would otherwise run the full suite and republish the deployment branches, once a
month, for a file nothing reads. Its commit message therefore ends in
`[skip ci]`. If you add other automated commits, do the same.

**2. Cache the dependency install.** Nothing is cached today, so every run
resolves and downloads the same packages. `actions/setup-python` supports it
directly:

```yaml
- uses: actions/setup-python@...
  with:
    python-version: '3.12'
    cache: 'pip'
```

The dependency set here is deliberately light — `sentence-transformers` and
`torch` are excluded on purpose — so expect tens of seconds per run rather than
minutes. Worth doing; not a transformation.

**3. Filter by path.** A change to markdown does not need the MCP suite:

```yaml
on:
  push:
    branches: [main]
    paths-ignore: ['**.md', 'docs/**', 'context/**']
```

This is the largest safe saving if documentation changes are a meaningful share
of commits.

**4. Accept the double run.** It is not pure waste. The merge commit is a
different commit from the PR head, and on a repository where `main` can also be
pushed to directly, the merge-time run is the only thing that validates what is
actually on `main`. Buying back those minutes costs real safety.

### Recommendation

Leave both workflows enabled. If minutes become tight after going private, apply
(1), (2) and (3) in that order. Do not disable `mcp-deployment.yml` to save
minutes — the thing it would stop doing is deploying, and that failure is
silent.

If the project does go properly dormant, disabling it is one click and one click
back. Set yourself a reminder to re-enable it before the next change to shared
code.

---

## Related

| Document | Contents |
| --- | --- |
| `docs/setup/pinger_setup.md` | Why overnight cover is a workflow and not an HTTP ping |
| `context/setup/pinger_setup.md` | The measurements behind that decision |
| `.github/workflows/keepalive.yml` | The schedule, and why overnight runs leave Render asleep |
