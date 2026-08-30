# Fantasy Football 2026

Tools to draft and manage my fantasy football teams on Sleeper.

It drafts for me, tells me who to start, finds trades, values the offers other
managers send me, watches for injuries, and posts what it finds to Discord.

Everything runs from the CLI or from a scheduled GitHub Actions job. There is
no website: a React frontend and a Vercel Function used to serve one, and both
were removed once it was clear nobody used them.

Two leagues are named in `ffb/leagues.py`, but only to build draft pools
offline: **Miller League** (14 teams, half PPR) and **FANTASYFOOTBALLMAXXING**
(10 teams, full PPR). A league with no exact pool reuses the closest one and
those projections are labelled approximate.

## Setup

```bash
cd backend && uv sync --group dev
```

Secrets live in `~/.local/ffb/.env`, not in the repo. Scheduled jobs read that
path. `backend/.env` is the local fallback.

Keep this repo out of `~/Desktop`. macOS privacy protection blocks background
jobs from reading anything there, which killed the digest for six days without
making a sound. If you move the repo, delete `backend/.venv` and re-run
`uv sync --group dev` - the shebangs keep the old path.

## What it does

### Build the player pool

Makes the list of draftable players with a projection for each. Combines ADP
from Fantasy Football Calculator, projected points from a positional curve
learned off past NFL data, and how much each player swings week to week.

```bash
uv run python -m ffb.nfldata.build --league maxxing_college
```

Output: `backend/data/pools/<league_key>.csv`. Committed, so a fresh checkout has data
on first boot. Rebuilding means committing the new CSV.

### Simulate the draft

`run_sims` plays a full mock draft many times and compares total points.
`sim.evaluate` goes further and counts wins, which is what actually matters.

```bash
uv run python -m ffb.draft.run_sims --league maxxing_college --my-slot 4 --n-sims 2000
uv run python -m ffb.sim.evaluate --league maxxing_college --my-slot 4 \
    --n-samples 1500 --force chase=1:7564
```

See [docs/how-it-works.md](docs/how-it-works.md) for the math.

### Draft for me, live

`ffb/draft/live.py` auto-picks in a real Sleeper draft. It polls the public
endpoints, works out whose turn it is, and submits through the private GraphQL.

```bash
set -a && . ./.env && set +a
FFB_ALLOW_WRITES=1 uv run python -m ffb.draft.live \
    --draft <draft_id> --pool data/pools/<key>.csv --interval 4
```

Two guardrails a real draft forces on you: DEF and K are held back to the last
two rounds, because their raw value is not comparable to skill players and
nothing drafts a defense in round 3 faster than a naive max-value picker.
Defenses are also remapped to real Sleeper ids, since the pool stores them
under placeholder ids that Sleeper rejects.

### Digest

Trade ideas, waiver pickups, offers waiting on me, and start/sit, posted to
Discord.

```bash
uv run python -m ffb.alerts.digest --limit 3
uv run python -m ffb.alerts.digest --sections lineup --dry-run
```

It reads only. A tool failing to evaluate is not the same as finding nothing,
and both look like silence if you only print results, so every league says
which of the two happened and a failure keeps the exit code non-zero. Expect a
non-zero exit while a league is undrafted. That is the mechanism working.

**It only speaks when something changed.** Each section is fingerprinted by
what it names - which trade, which player - and a run that would repeat itself
posts nothing at all. Fingerprints exclude projected points, since those drift
a fraction between runs without the advice changing.

Sections run on separate schedules because they move at different speeds:

| Section | When | Why |
| --- | --- | --- |
| `waivers` | Tue evening | the last useful moment before claims process |
| `trades` | Wed morning | waivers have processed, so rosters just moved |
| `lineup` | Thu evening, Sun late morning | before TNF locks, and before the 1pm ET window |
| `inbox` | with either | someone else's offer is on their clock, not yours |

Sunday also passes `--force`, so it posts even when nothing changed. That is
deliberate: dedupe makes silence ambiguous, and one guaranteed post a week
means two quiet weeks is a broken job rather than a calm one.

### Offers waiting on me

Trades other managers have sent me, valued by the same model, with an accept /
reject / close call verdict.

```bash
uv run python -m ffb.inbox 1391439647548129280
```

Needs `SLEEPER_TOKEN`: proposed trades are invisible to Sleeper's public API.
Without it there is no inbox to read, which is reported as "could not look" and
the digest skips the section entirely rather than claiming there are no offers.
An offer holding a K or a DEF gets no confident verdict either, since the pool
cannot value those and zero would read as a free win.

### Waiver wire

Who is worth claiming, and whether they fill a starting hole.

```bash
uv run python -m ffb.waivers 1391439647548129280
```

Ranked by value over replacement computed across the whole pool, so a pickup
is measured against the last startable player at that position league-wide,
not against whoever is left on the wire. K and DEF are excluded: the pools
carry no K rows, and their DEF ids never match Sleeper's, so every defense
would look permanently unrostered.

An empty answer says how many players it looked at, so "nothing worth
claiming" cannot be confused with "could not see the wire".

### Injury watch

Posts when someone on my roster changes injury status.

```bash
uv run python -m ffb.alerts.injuries --dry-run   # print instead of posting
```

It remembers what it last announced, so it only speaks up when something moved.
The first run of a season stays quiet instead of dumping every injured player.
`--force` overrides.

### Roster moves from chat

`ffb/assistant.py` lets the Discord bot make roster moves without ever making
one unsupervised. The scheduled jobs must never write, but a move I ask for in
chat should actually happen. Both hold because writes are only reachable
through a second command carrying a code the first one printed.

```bash
uv run python -m ffb.assistant fill-bench --league <league_id>
# prints the plan and `Reply confirm a1b2`
uv run python -m ffb.assistant lineup --league <league_id>
uv run python -m ffb.assistant confirm a1b2
uv run python -m ffb.assistant show     # what is waiting
uv run python -m ffb.assistant cancel   # discard it
```

`confirm` is the only place that sets `FFB_ALLOW_WRITES`, in its own process
memory, for one mutation. It never touches a `.env` file, so no scheduled job
can inherit the ability to write.

Plans expire after ten minutes. An approval given ten minutes ago was for a
roster that may not exist any more, and a stale confirmation is how you drop
the wrong player.

Two deliberate refusals: the lineup path will not swap in a player who is not
legal in the exact slot being vacated, it tells you to do that one by hand
rather than set an illegal lineup. K and DEF are never touched, because the
pool has no rows whose ids match them, so any change would be a guess.

## Schedules

Both jobs run on GitHub Actions, not on my laptop. A laptop schedule only fires
when the lid is open.

| Workflow | When |
| --- | --- |
| `.github/workflows/digest.yml` | Twice a day, plus Sunday late morning for inactives |
| `.github/workflows/injury-watch.yml` | Practice reports Wed to Fri, and before Thu/Sun/Mon games |

Both need two repository secrets under Settings > Secrets and variables >
Actions:

- `DISCORD_WEBHOOK_URL` - a Discord incoming webhook.
- `DATABASE_URL` - the same hosted Postgres the app uses.

`DATABASE_URL` is state. A runner has no disk that survives the run, so without
it the injury watcher looks like a first run every time and never posts. Any
scheduled job that needs to remember something should keep it in that Postgres.

`SLEEPER_TOKEN` is deliberately not a secret here. This repo is public, and a
token that can write my roster has no business on public runners. The scheduled
jobs only use Sleeper's public API.

Two GitHub gotchas: scheduled workflows only run from the default branch, and
GitHub disables the schedule after 60 days of no activity. Cron is UTC with no
daylight saving, which is why each slot is listed twice.

## Check Sleeper data

```bash
uv run python -m ffb.verify
```

## Backend files

| File | What it does |
|------|--------------|
| `ffb/leagues.py` | Build-time league configs |
| `ffb/sleeper_client.py` | Read-only Sleeper API client |
| `ffb/sleeper_auth.py` | Private GraphQL write client. Dry-run unless `FFB_ALLOW_WRITES=1` |
| `ffb/assistant.py` | Propose a roster move, execute only after a typed confirmation |
| `ffb/db.py` | SQLAlchemy engine. Postgres via `DATABASE_URL`, SQLite otherwise |
| `ffb/lineup.py` | Start/sit: who cannot play, and the best legal lineup |
| `ffb/trades.py` | Trade finder: what helps both sides |
| `ffb/nfldata/build.py` | Build the player pool CSV |
| `ffb/nfldata/refresh.py` | Rebuild the `data/nfl/` snapshots from nflverse |
| `ffb/nfldata/scoring.py` | Turn NFL stats into fantasy points |
| `ffb/nfldata/history.py` | Points curve and variance from past data |
| `ffb/nfldata/adp.py` | Fetch ADP from Fantasy Football Calculator |
| `ffb/nfldata/ids.py` | Match player names to Sleeper ids |
| `ffb/nfldata/schedule.py` | NFL schedule by week |
| `ffb/draft/strategy.py` | Player value and the opponent pick model |
| `ffb/draft/sim.py` | One mock draft |
| `ffb/draft/run_sims.py` | Many mock drafts, compare points |
| `ffb/draft/live.py` | Live draft bot |
| `ffb/draft/analyze.py` | Compare saved point results |
| `ffb/sim/season.py` | One simulated season to wins |
| `ffb/sim/evaluate.py` | Compare picks by expected wins |
| `ffb/waivers.py` | Rank the wire, flag what fills a need |
| `ffb/inbox.py` | Value the trades sent to me |
| `ffb/pool.py` | League lookup and draft-pool selection |
| `ffb/alerts/digest.py` | The digest, and which sections to run |
| `ffb/alerts/state.py` | What it said last time, so it can stay quiet |
| `ffb/alerts/injuries.py` | Injury watch |
| `ffb/alerts/diff.py` | What counts as an injury change |
| `ffb/alerts/discord.py` | Post to a Discord webhook |

## More

- [docs/how-it-works.md](docs/how-it-works.md) - VORP, the opponent model, and
  the season simulator.

