# ffb backend

FastAPI + SQLAlchemy service for the draft planner.

See the [root README](../README.md) for setup, usage, and deploy instructions.

Run locally:

```
uv run uvicorn ffb.api:app --port 8010 --reload
```

## Environment variables

All optional locally - the defaults keep local dev working with no setup.

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | local SQLite file at `backend/data/ffb.db` | Supabase Postgres connection string. `postgres://` and `postgresql://` prefixes are normalized to `postgresql+psycopg://`. |
| `FRONTEND_ORIGIN` | none | Deployed frontend origin(s) for CORS, comma-separated. Local `localhost`/`127.0.0.1` origins always work. |
| `DISCORD_WEBHOOK_URL` | none | Incoming webhook the injury watcher posts to. Required only for that job. |
| `FFB_SLEEPER_USERNAME` | `SLEEPER_USERNAME` in `ffb/leagues.py` | Whose rosters the injury watcher follows. The web app has no login, so the job has to be told. |
| `FFB_SEASON` | `2026` | Season used by the API's league lookup and the injury watcher. |

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
deployed API bundle, so it runs from a machine with the full `uv sync`
environment - a cron box, a GitHub Actions schedule, or a Render cron job - not
from the Vercel function.

## Tests

```
uv sync --group dev
uv run pytest
```
