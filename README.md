# Fantasy Football 2026

Tools to help draft and manage fantasy football teams on Sleeper.

Enter a Sleeper username and the app loads every league that user is in for the
season straight from Sleeper. Leagues are addressed by their Sleeper league id,
so nothing about a league is configured here.

Two leagues are still named in `ffb/leagues.py`, but only as the build-time
registry for generating draft pools offline:

- **Miller League** - 14 teams, half PPR, FAAB waivers - key `miller_league_hs`
- **FANTASYFOOTBALLMAXXING** - 10 teams, full PPR, rolling waivers - key `maxxing_college`

A league with no exact pool reuses the closest one by scoring format and team
count, and the app labels those projections as approximate.

- `backend/` - Python. Pulls Sleeper data, builds player projections, and runs
  the draft and season simulations.
- `frontend/` - React app to view teams, picks, and simulation results. There is
  no login: enter and confirm your Sleeper username in the top right corner and
  it is kept for that browser session. Four tabs per league: Draft, Waivers,
  Schedule, and Simulations.

## Backend setup

```bash
cd backend
uv sync
```

## Web UI

A small FastAPI service serves league data and starts simulations for the React
app.

```bash
# API (from backend/)
uv run uvicorn ffb.api:app --reload --port 8010

# Frontend (from frontend/)
npm install
npm run dev
```

## Deploying

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

## Check Sleeper data

Sleeper's API is read only. This checks we can reach both leagues and find your
roster.

```bash
cd backend
uv run python -m ffb.verify
```

## Build the player pool

Makes the list of draftable players with a projection for each. It combines:

- **ADP** (who gets drafted where) from Fantasy Football Calculator, using each
  league's scoring (PPR for Maxxing, half-PPR for Miller).
- **Projected points** from a positional curve. We learn what the best QB, the
  12th-best QB, and so on score in a season from past NFL data, then place each
  player on that curve by their current draft rank. Rookies and players who
  changed teams get priced by the market, not by old stats.
- **How much a player swings**: week-to-week (`game_cv`) and season-to-season
  (`season_cv`). Kickers and defenses use simple baselines.

```bash
uv run python -m ffb.nfldata.build --league maxxing_college
uv run python -m ffb.nfldata.build --league miller_league_hs
```

Output: `backend/data/pools/<league_key>.csv`.

## Draft simulator (points)

Runs a full mock draft many times. Each opponent's pick slot is `ADP + a random
nudge`. The nudge is small early (people take the obvious pick) and bigger late
(people reach for sleepers). You can force your own picks and see how your team's
total projected points change.

```bash
uv run python -m ffb.draft.run_sims --league maxxing_college --my-slot 4 --n-sims 2000
uv run python -m ffb.draft.run_sims --league maxxing_college --my-slot 4 \
    --n-sims 2000 --force 1:7564
uv run python -m ffb.draft.analyze --league maxxing_college
```

## Season simulator (wins)

Total points is a rough score. What you really want is how many games you win.
This plays a full season for each drafted team and counts wins.

Each run:

1. Give every player one season-long luck factor (a good year or a bad year).
2. Give each week its own luck on top of the season factor.
3. Every team starts its best lineup (chosen on projections, not on the results
   being rolled) and plays a head-to-head schedule.
4. Count your wins.

Two tricks keep it fast and fair:

- **Common random numbers**: when comparing two of your picks, every other team
  and every weekly roll stays the same. Only your pick changes, so small real
  differences show up instead of drowning in noise.
- **Latin hypercube sampling** on the season luck: spread the draws evenly
  instead of pure random, so fewer runs give a steadier answer.

```bash
uv run python -m ffb.sim.evaluate --league maxxing_college --my-slot 4 --n-samples 1500 \
    --force chase=1:7564 --force cmc=1:4034
```

Prints expected wins, how often you hit the playoff win total, and average points.

The same logic is exposed at `POST /api/leagues/{league_key}/sims/season` for
the frontend's Simulations tab, which charts expected wins, the win
distribution, and a P10/P50/P90 points range - carrying over whatever's
already logged on the Live Draft Board or planned as a forced pick.

## How the math works

### Player value: VORP

A player's raw projection doesn't say much on its own - what matters is how
much better they are than the player you'd get for free at the same
position. We compute **replacement level** per position: the projection of
the last starter-worthy player at that position, league-wide, given the
roster rules and number of teams (flex slots are allocated to whichever
position has the best marginal player at that depth). Then:

```
VORP(player) = player.proj_points - replacement_level[player.position]
```

This is what ranks the "Suggested next pick" list and picks the sample
roster's picks in the UI.

### Opponent draft model

Each simulated opponent doesn't just take the top of the ADP board - real
drafts have noise (reaches, sleepers) and are shaped by roster need. For each
available player, we compute a noisy effective draft slot:

```
effective_slot = ADP + Normal(0, sigma) + need_penalty
```

The opponent takes the player with the lowest effective slot.

- `sigma = max(round_sigma(round), player.adp_stdev)` - noise grows by round
  (`SIGMA_BASE + SIGMA_PER_ROUND * (round - 1)`), so round 1 is close to
  chalk and late rounds are noisy reaches. A player's own ADP volatility (from
  FFC) sets a floor, so genuinely unpredictable players stay unpredictable.
- `need_penalty` adds `NEED_PENALTY_SLOTS` (25 spots) once a position's
  starting requirement (including flex capacity) is already filled on that
  team's roster, pushing the opponent off positions they don't need.

### Draft simulation

One simulated draft plays the snake order pick by pick. Any pick already
logged on the Live Draft Board is replayed exactly as it happened (not
simulated). Your own forced picks are reserved ahead of time so they can't be
sniped by an opponent. Everything else runs through the opponent model above.
Running `--n-sims` (or the UI's simulation count) of these and looking at the
distribution of your team's projected points (mean, stdev, P10/P50/P90) is
how "Scenario Results" gets built.

### Season simulation: points to wins

Total projected points is a rough score - what you actually want is win
probability. The season simulator sim samples a full season per draft outcome:

1. **Season factor**, once per player: `season_level = ppg * (1 + z_season * season_cv)`,
   where `ppg = proj_points / 17`.
2. **Weekly score**, per player per week, conditioned on that season level:
   `week_score = season_level * (1 + z_game * game_cv)` (clamped at 0).
3. Each team starts its best lineup **by projection** (not by the score
   about to be rolled - you don't get to see the future when setting a
   lineup), and plays a fixed round-robin schedule against the other slots.
4. Wins are counted from head-to-head weekly totals.

Two variance-reduction tricks make this cheap and fair to compare scenarios
with:

- **Common random numbers**: every player's random draws are keyed by
  `(seed, player index)` only - identical across different forced-pick
  scenarios. So when you compare "what if I take Chase in round 1" vs "what
  if I take CMC," every other team and every weekly roll is held fixed, and
  only your pick differs. Real (small) differences don't get drowned out by
  independent noise.
- **Latin hypercube sampling** on the season factor: instead of drawing
  `n_samples` season z-values independently at random, we take evenly spaced
  quantiles and randomly permute them per player. This spreads samples evenly
  across the distribution, so you need fewer samples for a stable estimate.
  Weekly (game-level) draws are left as plain i.i.d. draws since they average
  out fast across 14 weeks anyway.

## Backend files

| File | What it does |
|------|--------------|
| `ffb/leagues.py` | Build-time league configs - the registry of prebuilt draft pools |
| `ffb/sleeper_client.py` | Read-only Sleeper API client |
| `ffb/db.py` | SQLAlchemy engine - Supabase Postgres via `DATABASE_URL`, local SQLite otherwise |
| `ffb/nfldata/scoring.py` | Turn NFL stats into fantasy points for a league |
| `ffb/nfldata/history.py` | Points curve + week/season variance from past data |
| `ffb/nfldata/adp.py` | Fetch ADP from Fantasy Football Calculator |
| `ffb/nfldata/ids.py` | Match player names to Sleeper ids, current NFL team lookup |
| `ffb/nfldata/schedule.py` | NFL game schedule by week, from the committed snapshot |
| `ffb/nfldata/refresh.py` | Rebuild the `data/nfl/` snapshots from nflverse |
| `ffb/nfldata/build.py` | Build the player pool CSV |
| `ffb/draft/strategy.py` | Player value + the opponent pick model |
| `ffb/draft/sim.py` | One mock draft |
| `ffb/draft/run_sims.py` | Many mock drafts, compare points |
| `ffb/draft/analyze.py` | Compare saved point results |
| `ffb/sim/season.py` | One simulated season -> your wins |
| `ffb/sim/evaluate.py` | Compare picks by expected wins (CLI and the Simulations tab's API) |
| `ffb/alerts/injuries.py` | Injury watch: diff the NFL report against your rosters, post changes |
| `ffb/alerts/diff.py` | What counts as an injury change (pure, unit-tested) |
| `ffb/alerts/discord.py` | Post to a Discord incoming webhook |

## Injury watch

Posts to Discord when someone on one of your rosters changes injury status.

```bash
cd backend
uv run python -m ffb.alerts.injuries --dry-run   # print instead of posting
uv run python -m ffb.alerts.injuries             # post to DISCORD_WEBHOOK_URL
```

It stores the last status it announced per player, so it only speaks up when
something actually moved - safe to put on a schedule. The first run of a season
seeds that state and stays quiet rather than posting every already-injured
player; `--force` overrides.

Set `DISCORD_WEBHOOK_URL` to a Discord **incoming webhook** (Server Settings ->
Integrations -> Webhooks). A webhook is all this needs - it is a plain HTTPS
POST, so the job runs anywhere on a timer. A bot that answers slash commands
would need a long-lived process, which the current Vercel hosting cannot
provide; that is what the Render option in the deploy section is for.

Like the pool build, this job uses `nflreadpy`/`polars` and so runs from a full
`uv sync` environment, not from the deployed function. See "Serving vs
building" above.

### On a schedule (GitHub Actions)

`.github/workflows/injury-watch.yml` runs the watcher for you. It fires on the
days that matter in-season (Wednesday to Friday practice reports, and inactives
before the Thursday, Sunday and Monday games) and can also be started by hand
from the Actions tab, with optional `force` and `dry_run` toggles.

Two repository secrets are required (Settings -> Secrets and variables ->
Actions):

- `DISCORD_WEBHOOK_URL` - the incoming webhook described above.
- `DATABASE_URL` - the same hosted Postgres the app uses.

An optional `FFB_SEASON` repository variable overrides the season the job asks
for. Useful while nflverse has no injury data for the coming season yet.

`DATABASE_URL` is the state persistence. The watcher keeps what it last
announced in the `injury_state` table, and a GitHub runner has no disk that
survives the run, so without this the job would write to a throwaway SQLite
file, look like a first run every time and never post. The workflow checks both
secrets are present before doing any work and fails if either is missing.

Any other scheduled job that needs to remember something between runs should do
the same: point `DATABASE_URL` at that Postgres and keep its state in a table,
wherever the job happens to run from.

Two GitHub caveats worth knowing: scheduled workflows only run from the default
branch, and GitHub disables the schedule on a repository with 60 days of no
activity. Cron times are UTC with no daylight saving, which is why the schedule
uses wide windows rather than exact kickoff times.
