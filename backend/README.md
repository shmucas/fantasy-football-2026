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

Supabase is used only as hosted Postgres: no Supabase Auth, no RLS, no client SDK.
