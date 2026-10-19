# GitHub Actions — what runs, what it costs, and how to switch it off

Two workflows run in this repository. They do very different jobs, and only one
of them is safe to disable casually.

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `mcp-deployment.yml` | push and PR to `main` | Runs the test suite, builds deployment artifacts, **and pushes the branches Databricks deploys from** |
| `keepalive.yml` | schedule, six times a day | Touches Lakebase directly, wakes Render before the HTTP ping window |

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
