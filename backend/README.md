# ffb backend

FastAPI + SQLAlchemy service for the draft planner.

Run locally:

```
uv run uvicorn ffb.api:app --port 8010 --reload
```

## Environment variables

All optional locally - the defaults keep local dev working with no setup.

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | local SQLite file at `backend/data/ffb.db` | Supabase Postgres connection string. `postgres://` and `postgresql://` prefixes are normalized to `postgresql+psycopg://`. |
| `SESSION_SECRET` | `dev-only-insecure-secret` | Signs the session cookie. Must be set to a long random value in production. |
| `COOKIE_SECURE` | `false` | Set to `true` in production (HTTPS only). |
| `COOKIE_SAMESITE` | `lax` | Set to `none` in production, where the frontend and backend are on different sites. `none` requires `COOKIE_SECURE=true`. |

Supabase is used only as hosted Postgres: no Supabase Auth, no RLS, no client SDK.
