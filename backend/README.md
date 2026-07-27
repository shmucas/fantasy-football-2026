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

## Acting on Sleeper (approval-gated)

The documented Sleeper API is read-only. `ffb/sleeper_private.py` talks to the
private GraphQL endpoint the Sleeper web app uses, which can set lineups,
submit waiver claims, add/drop and propose trades.

**Nothing is sent without a human approval.** The flow is:

1. Something proposes an action. It is stored with the exact GraphQL body that
   would be sent, plus a single-use unguessable token.
2. The proposal is posted to Discord with a link back to the app.
3. You approve or reject - in Discord (via the link) or in the app.
4. A worker sends only what was approved, and reports the outcome.

```
uv run python -m ffb.approvals.worker --list      # what is waiting
uv run python -m ffb.approvals.worker --dry-run   # what would be sent
uv run python -m ffb.approvals.worker             # send approved actions
```

### The two tiers, and why

| | Holds `SLEEPER_TOKEN` | Can record an approval | Can send to Sleeper |
| --- | --- | --- | --- |
| Web API (Vercel) | no | yes | **no** |
| Worker (your machine) | yes | no | yes |

The token is a full session credential for the whole Sleeper account, not a
scoped key. Keeping it off the web tier means compromising the site cannot move
a roster - the worst it can do is record an approval that a worker you control
later acts on.

| Variable | Notes |
| --- | --- |
| `SLEEPER_TOKEN` | The private-API session JWT. Worker only. Capture from sleeper.com → DevTools → Network → any `graphql` request → `authorization` header. It expires; the worker fails loudly rather than half-acting. |
| `FFB_APP_URL` | Public URL of the frontend, used to build approval links. |

Approvals expire after 12 hours: consent to change a lineup is consent to change
it now, not next week.

**Treat the approval link like a password.** The app has no login, so holding
the link is what authorises the action. Post it to a private channel.

## Tests

```
uv sync --group dev
uv run pytest
```
