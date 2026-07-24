# Fantasy Football 2026

Tools to help draft and manage two fantasy football teams on Sleeper.

- **Miller League** - 14 teams, high school friends, FAAB waivers - key `miller_league_hs`
- **FANTASYFOOTBALLMAXXING** - 10 teams, college friends, rolling waivers - key `maxxing_college`

Both leagues use the same code. Data is tagged with a `league_key`, so there is
one database, not one per league.

- `backend/` - Python. Pulls Sleeper data, builds player projections, and runs
  the draft and season simulations.
- `frontend/` - React app to view teams, picks, and simulation results.

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

## Backend files

| File | What it does |
|------|--------------|
| `ffb/leagues.py` | The two league configs |
| `ffb/sleeper_client.py` | Read-only Sleeper API client |
| `ffb/nfldata/scoring.py` | Turn NFL stats into fantasy points for a league |
| `ffb/nfldata/history.py` | Points curve + week/season variance from past data |
| `ffb/nfldata/adp.py` | Fetch ADP from Fantasy Football Calculator |
| `ffb/nfldata/ids.py` | Match player names to Sleeper ids |
| `ffb/nfldata/build.py` | Build the player pool CSV |
| `ffb/draft/strategy.py` | Player value + the opponent pick model |
| `ffb/draft/sim.py` | One mock draft |
| `ffb/draft/run_sims.py` | Many mock drafts, compare points |
| `ffb/draft/analyze.py` | Compare saved point results |
| `ffb/sim/season.py` | One simulated season -> your wins |
| `ffb/sim/evaluate.py` | Compare picks by expected wins |
