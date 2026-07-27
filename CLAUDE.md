# ffb26

## Deployment shape

The app ships to Vercel as a Vite frontend (`frontend/`, built by
`vercel.json`'s `buildCommand`) plus a single Python Function at
`api/index.py`, which pulls in `backend/**` via `includeFiles`.

The Function bundle deliberately excludes the heavy data dependencies
(nflreadpy, polars) to stay under the size limit. `.vercelignore` keeps
virtualenvs, caches, and local data out of it for the same reason.

## What this means for new work

Anything that needs nflreadpy or polars cannot run as a Vercel Function. Plan
it as a scheduled job outside the deployment (CI or a local/cron run), the way
the Discord injury watcher (`ffb.alerts.injuries`) already works.

Env vars for those jobs, such as `DISCORD_WEBHOOK_URL`, belong wherever the job
runs, not only in the Vercel project settings. `backend/.env` covers local runs
and is gitignored.
