# Keep-alive heartbeat

Last recorded: 2026-10

Written by `.github/workflows/keepalive.yml`. Its only purpose is to
be a commit: GitHub disables scheduled workflows in a public
repository after 60 days without one, and the keep-alive schedule is
what stops Lakebase's compute being disabled.

It records the month rather than the day, so it changes once a month.
Nothing reads it.
