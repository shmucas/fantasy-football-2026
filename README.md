# Fantasy Football 2026

Tools to help draft and manage two fantasy football teams on Sleeper.

- **Miller League** - 14 teams, high school friends, FAAB waivers - key `miller_league_hs`
- **FANTASYFOOTBALLMAXXING** - 10 teams, college friends, rolling waivers - key `maxxing_college`

Both leagues use the same code. Data is tagged with a `league_key`, so there is
one database, not one per league.

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

Two things to know:

- The player pools in `backend/data/pools/` are committed, so the API has data
  on first boot. Rebuilding a pool means committing the new CSV, not running
  the build on the host.
- Simulation results are written to `backend/data/results/`, which is wiped on
  every redeploy. Fine for now - the season simulator returns results directly
  in its API response instead of writing to disk, so this only affects the
  older points-based draft simulator.

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
| `ffb/leagues.py` | The two league configs |
| `ffb/sleeper_client.py` | Read-only Sleeper API client |
| `ffb/db.py` | SQLAlchemy engine - Supabase Postgres via `DATABASE_URL`, local SQLite otherwise |
| `ffb/nfldata/scoring.py` | Turn NFL stats into fantasy points for a league |
| `ffb/nfldata/history.py` | Points curve + week/season variance from past data |
| `ffb/nfldata/adp.py` | Fetch ADP from Fantasy Football Calculator |
| `ffb/nfldata/ids.py` | Match player names to Sleeper ids, current NFL team lookup |
| `ffb/nfldata/schedule.py` | NFL game schedule by week, from nflverse |
| `ffb/nfldata/build.py` | Build the player pool CSV |
| `ffb/draft/strategy.py` | Player value + the opponent pick model |
| `ffb/draft/sim.py` | One mock draft |
| `ffb/draft/run_sims.py` | Many mock drafts, compare points |
| `ffb/draft/analyze.py` | Compare saved point results |
| `ffb/sim/season.py` | One simulated season -> your wins |
| `ffb/sim/evaluate.py` | Compare picks by expected wins (CLI and the Simulations tab's API) |
