# ffb backend

SQLAlchemy-backed CLI and scheduled jobs for the draft planner.

See the [root README](../README.md) for setup, usage, and deploy instructions.

Run locally:

```
```

## Environment variables

All optional locally - the defaults keep local dev working with no setup.

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | local SQLite file at `backend/data/ffb.db` | Supabase Postgres connection string. `postgres://` and `postgresql://` prefixes are normalized to `postgresql+psycopg://`. |
| `DISCORD_WEBHOOK_URL` | none | Incoming webhook the injury watcher posts to. Required only for that job. |
| `FFB_SLEEPER_USERNAME` | `SLEEPER_USERNAME` in `ffb/leagues.py` | Whose rosters the injury watcher follows. The web app has no login, so the job has to be told. |
| `FFB_SEASON` | `2026` | Season used by league lookup and the injury watcher. |

Supabase is used only as hosted Postgres: no Supabase Auth, no RLS, no client SDK.

## Injury watch

Posts to Discord when a player on any of your rosters changes injury status.
It diffs the current NFL report against what it last announced, so running it
repeatedly is safe - an unchanged report produces no message.

```
uv run python -m ffb.alerts.injuries --dry-run    # print, don't post
uv run python -m ffb.alerts.injuries              # post to DISCORD_WEBHOOK_URL
```

The first run of a season records the current report and stays quiet, so you
don't get one message per already-injured player. `--force` posts anyway.

This job needs `nflreadpy`/`polars`, which are deliberately kept out of the
full dependency set, so it runs from a machine with the full `uv sync`
environment - a cron box, a GitHub Actions schedule, or a Render cron job - not
rather than from a trimmed-down bundle.

## Tests

```
uv sync --group dev
uv run pytest
```
