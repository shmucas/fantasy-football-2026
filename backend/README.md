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

## Sleeper data (read-only API)

```bash
uv run python -m ffb.verify        # confirm both leagues + our roster are reachable
```

## Draft simulator

The simulator runs a full snake draft where opponents are modeled by ADP value
(softmax) plus a positional-need penalty, so simulated rosters stay realistic.
You can force your own round-1/round-2 picks to compare outcome distributions.

Player projections/ADP currently come from a **synthetic sample** file
(`data/players_sample.csv`, regenerate with `python data/generate_sample_players.py`).
Swap in a real FantasyPros/nflverse export later - same columns.

```bash
# Baseline: no forced picks
uv run python -m ffb.draft.run_sims --league maxxing_college --my-slot 4 --n-sims 2000

# Force a specific round-1 pick (repeat --force for more rounds)
uv run python -m ffb.draft.run_sims --league maxxing_college --my-slot 4 \
    --n-sims 2000 --force 1:SAMPLE1 --force 2:SAMPLE60

# Compare all saved scenarios for a league
uv run python -m ffb.draft.analyze --league maxxing_college
```

Results are saved per-league to `data/results/<league_key>/<scenario>.csv`.

## Module layout

| File | Purpose |
|------|---------|
| `ffb/leagues.py` | Both league configs (IDs, roster rules, FAAB flag) |
| `ffb/sleeper_client.py` | Read-only Sleeper API client |
| `ffb/draft/strategy.py` | VORP + ADP/need-based opponent model |
| `ffb/draft/sim.py` | Single snake-draft simulation |
| `ffb/draft/run_sims.py` | Parallel batch runner with forced-pick scenarios |
| `ffb/draft/analyze.py` | Outcome-distribution comparison |
