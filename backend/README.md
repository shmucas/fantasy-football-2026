# FFB Backend

Fantasy football draft simulator + Sleeper data pipeline for two leagues:

- **Miller League** (14-team, high school, FAAB waivers) - key `miller_league_hs`
- **FANTASYFOOTBALLMAXXING** (10-team, college, rolling waivers) - key `maxxing_college`

Both leagues share one codebase and one local SQLite DB (`data/ffb.db`), scoped by
`league_key`. There is no separate database per league.

## Setup

```bash
uv sync
```

## Web UI

A small FastAPI layer exposes league data and lets you trigger simulations from
the React frontend in `../frontend`.

```bash
# backend API (from backend/)
uv run uvicorn ffb.api:app --reload --port 8010

# frontend (from frontend/)
npm install
npm run dev
```

## Sleeper data (read-only API)

```bash
uv run python -m ffb.verify        # confirm both leagues + our roster are reachable
```

## Draft data pipeline

Builds a per-league draft pool from real 2026 data:

- **ADP** from Fantasy Football Calculator (per league scoring: PPR for Maxxing,
  half-PPR for Miller), which also supplies each player's draft-slot stdev.
- **Mean projections** from a positional points curve derived from nflverse
  historical weekly scoring under each league's exact Sleeper scoring, mapped by
  the player's current market rank - so rookies and team-changers are priced by
  the market, not by (missing/stale) history.
- **Weekly variance** from each position's historical coefficient of variation.
- **K/DEF** use ADP-rank baselines (nflverse has no kicker distance scoring or
  team defense).

```bash
uv run python -m ffb.nfldata.build --league maxxing_college
uv run python -m ffb.nfldata.build --league miller_league_hs
```

Output: `data/pools/<league_key>.csv`. A coverage report prints how many players
joined to a Sleeper id.

## Draft simulator

Runs a full snake draft. Opponents pick by **noisy draft slot**:
`effective_slot = ADP + Normal(0, sigma)`, taking the best available slot after a
positional-need penalty. sigma grows by round (chalk early, reaches late), so a
far-off player has ~0% chance early but sleepers get reached for late. You can
force your own picks to compare outcome distributions.

```bash
# Baseline (defaults to data/pools/<league>.csv)
uv run python -m ffb.draft.run_sims --league maxxing_college --my-slot 4 --n-sims 2000

# Force round-1 pick by Sleeper player_id (repeat --force for more rounds)
uv run python -m ffb.draft.run_sims --league maxxing_college --my-slot 4 \
    --n-sims 2000 --force 1:7564

# Compare all saved scenarios for a league
uv run python -m ffb.draft.analyze --league maxxing_college
```

Results are saved per-league to `data/results/<league_key>/<scenario>.csv`.

## Module layout

| File | Purpose |
|------|---------|
| `ffb/leagues.py` | Both league configs (IDs, roster rules, FAAB flag) |
| `ffb/sleeper_client.py` | Read-only Sleeper API client |
| `ffb/nfldata/scoring.py` | Sleeper scoring settings -> fantasy points from nflverse |
| `ffb/nfldata/history.py` | Positional points curve + variance from history |
| `ffb/nfldata/adp.py` | Fantasy Football Calculator ADP fetch |
| `ffb/nfldata/ids.py` | Name/id crosswalk to Sleeper |
| `ffb/nfldata/build.py` | Build per-league draft pool CSV |
| `ffb/draft/strategy.py` | VORP + gaussian-slot opponent model |
| `ffb/draft/sim.py` | Single snake-draft simulation |
| `ffb/draft/run_sims.py` | Parallel batch runner with forced-pick scenarios |
| `ffb/draft/analyze.py` | Outcome-distribution comparison |
