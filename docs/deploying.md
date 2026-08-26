# Deploying

The frontend is a static Vite build, so Vercel fits it. The backend is a
long-lived FastAPI process, which Vercel does not run, so it needs its own host.
Render is the recommendation here: it deploys straight from the GitHub repo,
lets you point a service at the `backend/` subdirectory, and installs with
`uv sync` from the committed `uv.lock`. Railway or Fly.io work the same way -
the `backend/Procfile` declares the start command for hosts that read one.

Deploy the backend first, because the frontend build needs its URL.

### 1. Backend (Render)

Create a Web Service from this repo with:

- **Root directory**: `backend`
- **Build command**: `uv sync --frozen`
- **Start command**: `uv run uvicorn ffb.api:app --host 0.0.0.0 --port $PORT`

Environment variables:

| Variable | Value |
|----------|-------|
| `FRONTEND_ORIGIN` | Your Vercel URL, e.g. `https://ffb26.vercel.app`. Comma-separate to allow more than one (handy for Vercel preview URLs). |

Local dev origins keep working without this variable: `localhost` and
`127.0.0.1` on any port are always allowed.

Environment variables:

| Variable | Default | Notes |
| --- | --- | --- |
| `FRONTEND_ORIGIN` | none | Your Vercel URL(s), comma-separated. |
| `DATABASE_URL` | local SQLite file | Supabase Postgres connection string. `postgres://`/`postgresql://` prefixes are normalized to `postgresql+psycopg://`. |

See `backend/README.md` for the full picture on these.

Three things to know:

- The player pools in `backend/data/pools/` are committed, so the API has data
  on first boot. Rebuilding a pool means committing the new CSV, not running
  the build on the host.
- The nflverse snapshots in `backend/data/nfl/` are committed for the same
  reason, and also to keep `polars`/`nflreadpy` out of the deployed bundle -
  see "Serving vs building" below. Refresh them with
  `uv run python -m ffb.nfldata.refresh --season 2026` and commit the result.
- Simulation results are written to `backend/data/results/`, which is wiped on
  every redeploy. Fine for now - the season simulator returns results directly
  in its API response instead of writing to disk, so this only affects the
  older points-based draft simulator.

### Serving vs building

A Vercel Function has a hard 500MB bundle limit, and the data-science stack
blows straight through it: `polars` alone unpacks to ~215MB and `scipy` to
~110MB. So the two paths have deliberately different dependency lists:

| | Deps | Used for |
| --- | --- | --- |
| Serving (`requirements.txt`, repo root) | fastapi, httpx, itsdangerous, numpy, pydantic, psycopg, sqlalchemy - ~100MB | What `api/index.py` needs to answer a request |
| Building (`backend/pyproject.toml`) | the above plus nflreadpy, polars, pyyaml - ~340MB | Pool builds and snapshot refreshes, run locally |

Anything the API imports at request time has to be in **both** files. The
practical rule: if an endpoint needs nflverse data, snapshot it into
`backend/data/` with a refresh script and read the snapshot at request time,
rather than reaching for polars in the serving path.

### 2. Frontend (Vercel)

In the Vercel project settings:

- **Root directory**: `frontend`. The framework preset and output directory
  come from `frontend/vercel.json`, so leave those alone.
- **Environment variable**: `VITE_API_URL`, set to the backend URL from step 1
  **including the `/api` suffix**, e.g. `https://ffb26-api.onrender.com/api`.

Vite bakes env vars in at build time, so changing `VITE_API_URL` needs a
redeploy, not just a restart. If it is unset the build falls back to
`http://localhost:8010/api`, which is what local dev uses. Copy
`frontend/.env.example` to `frontend/.env.local` if you want to point local dev
at a deployed backend.

### 3. Deploy

Deploy the frontend, open it, and confirm the league list loads. An empty page
with CORS errors in the browser console means `FRONTEND_ORIGIN` on the backend
does not match the Vercel URL exactly, scheme included.
