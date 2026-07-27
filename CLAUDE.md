# Working on this repo

## The Vercel function has a hard 500MB limit

This has already broken a deploy once, at 843MB. The data-science stack is what
does it: `polars` (pulled in by `nflreadpy`) unpacks to ~215MB and `scipy` to
~110MB. Installed serving deps are currently ~103MB, so there is headroom - but
it is one careless dependency away from gone.

**The rule: serving and building have separate dependency lists, and they must
stay separate.**

| | File | Contains |
| --- | --- | --- |
| Serving | `requirements.txt` (repo root) | Only what answering an HTTP request needs |
| Building | `backend/pyproject.toml` | The above plus `nflreadpy`, `polars`, dev tools |

Before adding anything to `requirements.txt`, check what it drags in. Before
importing a new package in `ffb/api.py` or anything it imports, check it is in
`requirements.txt` - the two must agree or the function 500s at runtime.

If an endpoint needs nflverse data, **snapshot it** into `backend/data/` with a
refresh script and read the snapshot at request time. That is why
`data/nfl/schedule_2026.csv` and `data/nfl/player_teams.csv` exist. Do not reach
for polars in the serving path.

Heavy imports that are genuinely needed offline go **inside the function that
uses them**, not at module scope, so importing the module does not pull them in.
`ffb/nfldata/ids.py` and `ffb/alerts/injuries.py` both do this.

Check before shipping:

```bash
cd backend && uv run python -c "
import sys; from ffb.api import app
print([m for m in ('polars','nflreadpy','scipy') if m in sys.modules] or 'clean')"
```

`vercel.json` ships `backend/**` into the function, so `.vercelignore` is what
keeps non-serving code out. Note that `ffb/api.py` imports `ffb.approvals.store`
**lazily inside endpoint functions** - a module being absent from `sys.modules`
after `import ffb.api` does not prove it is unused. Exercise the endpoints
before excluding anything.

## The Sleeper token never touches Vercel

`SLEEPER_TOKEN` is a full session credential for the whole Sleeper account, not
a scoped API key. Two tiers, deliberately:

- **Web API (Vercel)** records approvals. No token, cannot call Sleeper.
- **Worker (a machine you control)** holds the token and sends approved actions.

So `ffb/sleeper_private.py` and `ffb/approvals/worker.py` are excluded from the
function bundle. Keep it that way: nothing in the serving path should import
them, and the token should never appear in a Vercel env var.

Anything that writes to Sleeper goes through `ffb/approvals` and requires a
human approval. `can_execute()` fails closed; do not add a bypass.

## Conventions

- Committed data files (`backend/data/pools/`, `backend/data/nfl/`) are
  regenerated locally and committed, never built on the host.
- Tests: `cd backend && uv sync --group dev && uv run pytest`.
- Frontend: `cd frontend && npm run build && npm run lint`.
- The roadmap board in `frontend/src/App.tsx` is meant to reflect reality. If
  you ship something on it, move the card.
