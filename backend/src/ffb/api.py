"""HTTP API for the frontend: league/roster data plus draft-sim triggering."""

import csv
import os
import random
import re
import statistics
from collections import Counter
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ffb.db import init_db
from ffb.draft.run_sims import DATA_DIR, RESULTS_DIR, load_players, run_scenario
from ffb.draft.sim import simulate_draft
from ffb.draft.strategy import replacement_levels, vorp
from ffb.leagues import LEAGUES, LeagueConfig
from ffb.lineup import advise, season_byes
from ffb.lineup import as_dict as lineup_as_dict
from ffb.nfldata.ids import sleeper_team_lookup
from ffb.nfldata.schedule import available_weeks, week_schedule
from ffb.sim.evaluate import run_scenarios, summarize
from ffb.sim.season import REG_SEASON_WEEKS
from ffb.sleeper_client import SleeperClient
from ffb.trades import TeamRoster, find_trades, rank_for_me


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="FFB API", lifespan=lifespan)

FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("FRONTEND_ORIGIN", "").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    # Falls back to matching any Vercel preview/prod URL so a forgotten or
    # stale FRONTEND_ORIGIN doesn't surface as a bare "Failed to fetch" in the
    # browser - FRONTEND_ORIGIN above still lets you allow a custom domain.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+|https://[\w-]+\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
DEFAULT_SEASON = os.getenv("FFB_SEASON", "2026")


@app.get("/api/leagues")
def list_leagues(sleeper_user_id: str, season: str = DEFAULT_SEASON) -> list[dict]:
    """Every league the Sleeper user actually plays in that season. Nothing here
    is configured on our side - Sleeper is the source of truth for the list."""
    try:
        with SleeperClient() as client:
            leagues = client.get_user_leagues(sleeper_user_id, season)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Couldn't reach Sleeper right now") from exc

    return [_league_from_sleeper(info).model_dump() for info in leagues or []]


@app.get("/api/sleeper/user/{username}")
def lookup_sleeper_user(username: str) -> dict:
    """Resolve a Sleeper username to its account. The frontend keeps the returned
    id for the browser session; there is no server-side login or stored user."""
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "That doesn't look like a Sleeper username")

    try:
        with SleeperClient() as client:
            info = client.get_user(username)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(404, "No Sleeper user with that username") from exc
        raise HTTPException(502, "Couldn't reach Sleeper right now") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Couldn't reach Sleeper right now") from exc

    if not info or not info.get("user_id"):
        raise HTTPException(404, "No Sleeper user with that username")

    return {
        "sleeper_user_id": info["user_id"],
        "sleeper_username": info.get("username") or username,
        "display_name": info.get("display_name"),
        "avatar": info.get("avatar"),
    }


@app.get("/api/leagues/{league_key}/roster")
def get_my_roster(league_key: str, sleeper_user_id: str) -> dict:
    league = _get_league(league_key)
    with SleeperClient() as client:
        info = client.get_league(league.league_id)
        rosters = client.get_rosters(league.league_id)
        users = client.get_users(league.league_id)

    user_by_id = {u["user_id"]: u for u in users}
    my_roster = next((r for r in rosters if r["owner_id"] == sleeper_user_id), None)
    if my_roster is None:
        raise HTTPException(404, "Your roster wasn't found in this league")

    return {
        "status": info["status"],
        "display_name": user_by_id[sleeper_user_id]["display_name"],
        "roster_id": my_roster["roster_id"],
        "player_ids": my_roster.get("players") or [],
    }


class SimRequest(BaseModel):
    league_key: str
    my_slot: int
    n_sims: int = 500
    rounds: int = 15
    forced_picks: dict[int, str] = {}
    already_picked: list[str] = []


class ScenarioStats(BaseModel):
    scenario: str
    mean: float
    stdev: float
    p10: float
    p50: float
    p90: float


class SamplePick(BaseModel):
    round: int
    player_id: str
    name: str
    position: str
    reason: str


class SimResponse(ScenarioStats):
    sample_roster: list[SamplePick] = []


@app.post("/api/sims/run", response_model=SimResponse)
def run_sim(req: SimRequest) -> SimResponse:
    league = _get_league(req.league_key)
    pool_path = _league_pool(league)

    if len(set(req.already_picked)) != len(req.already_picked):
        raise HTTPException(400, "already_picked contains the same player more than once")

    players = load_players(pool_path)
    # A pool smaller than num_teams * rounds would run the draft dry mid-simulation.
    rounds = min(req.rounds, len(players) // league.num_teams)
    if rounds < 1:
        raise HTTPException(400, "Player pool is too small for this league")
    try:
        scores = run_scenario(
            players, league.num_teams, rounds, league.roster_positions,
            req.my_slot, req.forced_picks, req.n_sims, already_picked=req.already_picked,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    out_dir = RESULTS_DIR / league.key
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = "baseline" if not req.forced_picks else "_".join(
        f"r{k}-{v}" for k, v in sorted(req.forced_picks.items())
    )
    if req.already_picked:
        tag = f"live{len(req.already_picked)}_{tag}"
    out_path = out_dir / f"{tag}.csv"
    with out_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["projected_points"])
        writer.writerows([[s] for s in scores])

    sample = _sample_roster_with_reasons(players, league, req, rounds)
    return SimResponse(scenario=tag, sample_roster=sample, **_summarize(scores))


def _sample_roster_with_reasons(players, league, req: SimRequest, rounds: int) -> list[SamplePick]:
    """Runs one representative draft and explains each of our picks."""
    result = simulate_draft(
        players, league.num_teams, rounds, league.roster_positions,
        req.my_slot, random.Random(0), req.forced_picks, req.already_picked,
    )
    replacement = replacement_levels(players, league.roster_positions, league.num_teams)

    picks: list[SamplePick] = []
    already_count = len(req.already_picked)
    for i, (slot, player) in enumerate(result.pick_order):
        if slot != req.my_slot:
            continue
        rnd = i // league.num_teams + 1
        if i < already_count:
            reason = "Already picked in the live draft"
        elif rnd in req.forced_picks:
            reason = "You forced this pick"
        else:
            player_vorp = vorp(player, replacement)
            reason = (
                f"Best value on the board: {player.proj_points:.0f} proj. pts, "
                f"{player_vorp:.0f} above the last startable {player.position} "
                f"(ADP {player.adp:.1f})"
            )
        picks.append(
            SamplePick(
                round=rnd, player_id=player.player_id, name=player.name,
                position=player.position, reason=reason,
            )
        )
    return picks


class SeasonSimRequest(BaseModel):
    my_slot: int
    n_samples: int = 300
    rounds: int = 15
    forced_picks: dict[int, str] = {}
    already_picked: list[str] = []


class WinBucket(BaseModel):
    wins: int
    pct: float


class SeasonScenario(BaseModel):
    scenario: str
    forced_picks: list[str] = []
    exp_wins: float
    win_stdev: float
    win_distribution: list[WinBucket]
    threshold_wins: int
    threshold_pct: float
    avg_points: float
    points_p10: float
    points_p50: float
    points_p90: float


class SeasonSimResponse(BaseModel):
    league_key: str
    my_slot: int
    n_samples: int
    rounds: int
    reg_season_weeks: int
    scenarios: list[SeasonScenario]


@app.post("/api/leagues/{league_key}/sims/season", response_model=SeasonSimResponse)
def run_season_sim(league_key: str, req: SeasonSimRequest) -> SeasonSimResponse:
    """Season simulator: turns projected points into expected wins for our team."""
    league = _get_league(league_key)
    pool_path = _league_pool(league)
    if not 1 <= req.my_slot <= league.num_teams:
        raise HTTPException(400, f"my_slot must be between 1 and {league.num_teams}")
    if req.n_samples < 50 or req.n_samples > 2000:
        raise HTTPException(400, "n_samples must be between 50 and 2000")

    players = load_players(pool_path)
    name_by_id = {p.player_id: p.name for p in players}
    # A pool smaller than num_teams * rounds would run the draft dry mid-simulation.
    rounds = min(req.rounds, len(players) // league.num_teams)
    if rounds < 1:
        raise HTTPException(400, "Player pool is too small for this league")
    out_of_range = sorted(r for r in req.forced_picks if not 1 <= r <= rounds)
    if out_of_range:
        raise HTTPException(
            400, f"Forced pick rounds {out_of_range} are outside the {rounds} simulated rounds"
        )

    scenarios: dict[str, dict[int, str]] = {}
    labels = ["baseline"]
    scenarios["baseline"] = {}
    if req.forced_picks:
        labels.append("forced")
        scenarios["forced"] = req.forced_picks

    try:
        runs = run_scenarios(
            players, league, req.my_slot, rounds, req.n_samples,
            scenarios, already_picked=req.already_picked,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    threshold = REG_SEASON_WEEKS // 2 + 1
    out = []
    for label in labels:
        wins, points_for = runs[label]
        stats = summarize(wins, points_for, threshold)
        counts = Counter(wins)
        out.append(
            SeasonScenario(
                scenario=label,
                forced_picks=[
                    f"R{r}: {name_by_id.get(pid, pid)}"
                    for r, pid in sorted(scenarios[label].items())
                ],
                exp_wins=stats["exp_wins"],
                win_stdev=stats["win_stdev"],
                win_distribution=[
                    WinBucket(wins=w, pct=100.0 * counts.get(w, 0) / len(wins))
                    for w in range(REG_SEASON_WEEKS + 1)
                ],
                threshold_wins=threshold,
                threshold_pct=stats["playoff_pct"],
                avg_points=stats["avg_pf"],
                **_points_percentiles(points_for),
            )
        )
    return SeasonSimResponse(
        league_key=league.key, my_slot=req.my_slot, n_samples=req.n_samples, rounds=rounds,
        reg_season_weeks=REG_SEASON_WEEKS, scenarios=out,
    )


def _points_percentiles(points_for: list[float]) -> dict[str, float]:
    ordered = sorted(points_for)
    n = len(ordered)
    return {
        "points_p10": ordered[int(n * 0.10)],
        "points_p50": ordered[int(n * 0.50)],
        "points_p90": ordered[min(n - 1, int(n * 0.90))],
    }


@app.get("/api/leagues/{league_key}/draft-order", response_model=dict[int, str])
def draft_order(league_key: str) -> dict[int, str]:
    """Real draft slot -> team display name, once Sleeper has assigned slots."""
    league = _get_league(league_key)
    with SleeperClient() as client:
        info = client.get_league(league.league_id)
        draft = client.get_draft(info["draft_id"])
        users = client.get_users(league.league_id)

    user_by_id = {u["user_id"]: u for u in users}
    order = draft.get("draft_order") or {}
    return {
        slot: user_by_id[uid]["display_name"]
        for uid, slot in order.items()
        if uid in user_by_id
    }


@app.get("/api/leagues/{league_key}/results", response_model=list[ScenarioStats])
def list_results(league_key: str) -> list[ScenarioStats]:
    _get_league(league_key)
    league_dir = RESULTS_DIR / league_key
    if not league_dir.exists():
        return []

    results = []
    for path in sorted(league_dir.glob("*.csv")):
        with path.open() as f:
            scores = [float(row["projected_points"]) for row in csv.DictReader(f)]
        results.append(ScenarioStats(scenario=path.stem, **_summarize(scores)))
    return results


@app.get("/api/leagues/{league_key}/players", response_model=list[dict])
def list_players(league_key: str) -> list[dict]:
    league = _get_league(league_key)
    pool_path = _pool_for(league.ppr, league.num_teams)[0]
    if pool_path is None:
        return []
    with pool_path.open() as f:
        return [_with_team(row) for row in csv.DictReader(f)]


@app.get("/api/leagues/{league_key}/recommend", response_model=list[dict])
def recommend_next_pick(league_key: str, exclude: str = "") -> list[dict]:
    """Best available players by VORP, given players already picked/forced elsewhere."""
    league = _get_league(league_key)
    pool_path = _pool_for(league.ppr, league.num_teams)[0]
    if pool_path is None:
        return []

    players = load_players(pool_path)
    excluded_ids = {pid for pid in exclude.split(",") if pid}
    available = [p for p in players if p.player_id not in excluded_ids]

    replacement = replacement_levels(available, league.roster_positions, league.num_teams)
    ranked = sorted(available, key=lambda p: -vorp(p, replacement))[:8]
    return [
        {
            "player_id": p.player_id,
            "name": p.name,
            "position": p.position,
            "proj_points": p.proj_points,
            "vorp": round(vorp(p, replacement), 1),
            "reason": (
                f"{p.proj_points:.0f} proj. pts, {vorp(p, replacement):.0f} above the last "
                f"startable {p.position} in this league - best value left on the board."
            ),
        }
        for p in ranked
    ]


@app.get("/api/leagues/{league_key}/waivers", response_model=list[dict])
def list_waivers(league_key: str) -> list[dict]:
    """Players in the pool not currently rostered by anyone in the league."""
    league = _get_league(league_key)
    pool_path = _pool_for(league.ppr, league.num_teams)[0]
    if pool_path is None:
        return []

    with SleeperClient() as client:
        rosters = client.get_rosters(league.league_id)
    rostered_ids = {pid for r in rosters for pid in (r.get("players") or [])}

    with pool_path.open() as f:
        pool = list(csv.DictReader(f))

    available = [_with_team(p) for p in pool if p["player_id"] not in rostered_ids]
    available.sort(key=lambda p: float(p["proj_points"] or 0), reverse=True)
    return available


@app.get("/api/leagues/{league_key}/waivers/recommend", response_model=list[dict])
def recommend_waivers(league_key: str, sleeper_user_id: str) -> list[dict]:
    """Best available waiver-wire pickups for your roster: ranked by VORP against the
    whole league's replacement level, flagged where they'd fill a starting need you have."""
    league = _get_league(league_key)
    pool_path = _pool_for(league.ppr, league.num_teams)[0]
    if pool_path is None:
        return []

    with SleeperClient() as client:
        rosters = client.get_rosters(league.league_id)
    rostered_ids = {pid for r in rosters for pid in (r.get("players") or [])}
    my_roster = next((r for r in rosters if r["owner_id"] == sleeper_user_id), None)
    my_player_ids = set(my_roster.get("players") or []) if my_roster else set()

    players = load_players(pool_path)
    by_id = {p.player_id: p for p in players}
    # Replacement level comes from the whole league (rostered + unrostered), since it
    # represents who's actually startable league-wide, not just who's left on waivers.
    replacement = replacement_levels(players, league.roster_positions, league.num_teams)

    starters_needed: Counter[str] = Counter()
    for slot in league.roster_positions:
        if slot in ("BN", "FLEX"):
            continue
        starters_needed[slot] += 1
    my_position_counts: Counter[str] = Counter()
    for pid in my_player_ids:
        p = by_id.get(pid)
        if p:
            my_position_counts[p.position] += 1

    available = [p for p in players if p.player_id not in rostered_ids]
    ranked = sorted(available, key=lambda p: -vorp(p, replacement))[:15]

    out = []
    for p in ranked:
        have = my_position_counts[p.position]
        need = starters_needed.get(p.position, 0)
        fills_need = have < need
        player_vorp = vorp(p, replacement)
        reason = (
            f"{p.proj_points:.0f} proj. pts, {player_vorp:.0f} above the last startable "
            f"{p.position} in this league."
        )
        if fills_need:
            reason += f" You're short at {p.position} ({have}/{need} starters)."
        if p.position == "DEF":
            team = DEF_CITY_TO_TEAM.get(p.name.removesuffix(" Defense"), "")
        else:
            team = _team_lookup().get(p.player_id, "")
        out.append(
            {
                "player_id": p.player_id,
                "name": p.name,
                "position": p.position,
                "nfl_team": team,
                "proj_points": p.proj_points,
                "vorp": round(player_vorp, 1),
                "fills_need": fills_need,
                "reason": reason,
            }
        )
    return out


@app.get("/api/leagues/{league_key}/trades", response_model=dict)
def recommend_trades(league_key: str, sleeper_user_id: str, limit: int = 10) -> dict:
    """Trade packages that improve your roster and the other manager's at once.

    Read-only: this finds and ranks trades, it never proposes one. Both
    orderings the finder produces are returned, since "best for the league" and
    "best for me" are different questions.
    """
    league = _get_league(league_key)
    pool_path = _pool_for(league.ppr, league.num_teams)[0]
    if pool_path is None:
        return {"by_joint_surplus": [], "by_my_surplus": []}

    with SleeperClient() as client:
        rosters = client.get_rosters(league.league_id)
        users = client.get_users(league.league_id)

    my_roster = next((r for r in rosters if r["owner_id"] == sleeper_user_id), None)
    if my_roster is None:
        raise HTTPException(404, "Your roster wasn't found in this league")

    players = load_players(pool_path)
    by_id = {p.player_id: p for p in players}
    replacement = replacement_levels(players, league.roster_positions, league.num_teams)
    name_by_owner = {u["user_id"]: u.get("display_name") or "Unknown" for u in users}

    def to_team(raw: dict) -> TeamRoster:
        ids = raw.get("players") or []
        known = [by_id[pid] for pid in ids if pid in by_id]
        return TeamRoster(
            roster_id=raw["roster_id"],
            owner_name=name_by_owner.get(raw.get("owner_id"), "Unknown"),
            players=known,
            unknown_count=len(ids) - len(known),
        )

    me = to_team(my_roster)
    others = [to_team(r) for r in rosters if r["roster_id"] != my_roster["roster_id"]]

    # Which positions the pool actually resolves on these rosters. K has no pool
    # rows and DEF ids never match Sleeper's team codes, so those slots cannot be
    # checked for fillability.
    valued_positions = {p.position for t in [me, *others] for p in t.players}
    ideas = find_trades(me, others, league.roster_positions, replacement, valued_positions)
    return {
        "by_joint_surplus": [_trade_out(t) for t in ideas[:limit]],
        "by_my_surplus": [_trade_out(t) for t in rank_for_me(ideas)[:limit]],
    }


def _trade_out(idea) -> dict:
    def side(players):
        return [
            {
                "player_id": p.player_id,
                "name": p.name,
                "position": p.position,
                "proj_points": p.proj_points,
            }
            for p in players
        ]

    return {
        "roster_id": idea.roster_id,
        "owner_name": idea.owner_name,
        "send": side(idea.send),
        "receive": side(idea.receive),
        "my_surplus": round(idea.my_surplus, 1),
        "their_surplus": round(idea.their_surplus, 1),
        "joint_surplus": round(idea.joint_surplus, 1),
        "reason": " ".join(
            [
                f"You gain {idea.my_surplus:.0f} projected points, they gain "
                f"{idea.their_surplus:.0f}.",
                *idea.notes,
            ]
        ),
    }


@lru_cache(maxsize=1)
def _team_lookup() -> dict[str, str]:
    return sleeper_team_lookup()


DEF_CITY_TO_TEAM = {
    "Arizona": "ARI", "Atlanta": "ATL", "Baltimore": "BAL", "Buffalo": "BUF",
    "Carolina": "CAR", "Chicago": "CHI", "Cincinnati": "CIN", "Cleveland": "CLE",
    "Dallas": "DAL", "Denver": "DEN", "Detroit": "DET", "Green Bay": "GB",
    "Houston": "HOU", "Indianapolis": "IND", "Jacksonville": "JAX",
    "Kansas City": "KC", "LA Chargers": "LAC", "LA Rams": "LAR", "Las Vegas": "LV",
    "Miami": "MIA", "Minnesota": "MIN", "New England": "NE", "New Orleans": "NO",
    "NY Giants": "NYG", "NY Jets": "NYJ", "Philadelphia": "PHI", "Pittsburgh": "PIT",
    "Seattle": "SEA", "San Francisco": "SF", "Tampa Bay": "TB", "Tennessee": "TEN",
    "Washington": "WAS",
}


def _with_team(player: dict) -> dict:
    """Attach the player's current NFL team abbreviation. Defense rows carry no
    Sleeper id we can trust (the ADP source has its own ids), so derive the team
    from the "<City> Defense" name instead."""
    if player["position"] == "DEF":
        city = player["name"].removesuffix(" Defense")
        team = DEF_CITY_TO_TEAM.get(city, "")
    else:
        team = _team_lookup().get(player["player_id"], "")
    return {**player, "nfl_team": team}


@app.get("/api/leagues/{league_key}/schedule/weeks", response_model=list[int])
def list_schedule_weeks(league_key: str) -> list[int]:
    league = _get_league(league_key)
    return available_weeks(int(league.season))


@app.get("/api/leagues/{league_key}/schedule/{week}", response_model=list[dict])
def get_schedule(league_key: str, week: int) -> list[dict]:
    league = _get_league(league_key)
    return week_schedule(int(league.season), week)


# Sleeper roster slots the simulator has no model for. The flex family all
# collapses onto our FLEX (RB/WR/TE); SUPER_FLEX really allows a QB too, so a
# superflex league is modelled as a plain flex and flagged. Bench-like and IDP
# slots are dropped: our player pool has no defensive players and nothing is
# ever started from taxi or IR.
FLEX_SLOTS = {"FLEX", "WRRB_FLEX", "REC_FLEX", "SUPER_FLEX", "IDP_FLEX"}
UNPLAYED_SLOTS = {"TAXI", "IR"}
IDP_SLOTS = {"DL", "LB", "DB", "IDP", "DE", "DT", "CB", "S"}


def _normalize_slots(roster_positions: list[str]) -> tuple[list[str], bool]:
    slots = []
    approx = False
    for slot in roster_positions:
        if slot in UNPLAYED_SLOTS or slot in IDP_SLOTS:
            continue
        if slot in FLEX_SLOTS:
            approx = approx or slot != "FLEX"
            slots.append("FLEX")
        else:
            slots.append(slot)
    return slots, approx


def _league_from_sleeper(info: dict) -> LeagueConfig:
    """Map a Sleeper league payload onto our config shape. `key` is the Sleeper
    league id, so every league the user is in addresses its own endpoints."""
    slots, flex_approx = _normalize_slots(info["roster_positions"])
    return LeagueConfig(
        key=info["league_id"],
        name=info["name"],
        league_id=info["league_id"],
        season=str(info["season"]),
        num_teams=info["total_rosters"],
        roster_positions=slots,
        flex_approx=flex_approx,
        # Sleeper's waiver_type 2 is FAAB bidding; 0/1 are rolling/reverse waivers.
        faab=info.get("settings", {}).get("waiver_type") == 2,
        ppr=float(info.get("scoring_settings", {}).get("rec", 0.0)),
        approx_pool=_pool_for(
            float(info.get("scoring_settings", {}).get("rec", 0.0)),
            info["total_rosters"],
        )[1],
    )


def _pool_for(ppr: float, num_teams: int) -> tuple[Path | None, bool]:
    """Pick the prebuilt draft pool that best fits a league shape.

    Pools are expensive to build (ADP fetch plus a multi-season history model),
    so they are generated offline per league in LEAGUES. A league we have no
    exact pool for reuses the closest one - same scoring format first, then the
    nearest team count - and gets flagged as approximate."""
    candidates = []
    for league in LEAGUES.values():
        path = DATA_DIR / "pools" / f"{league.key}.csv"
        if path.exists():
            candidates.append((league, path))
    if not candidates:
        return None, False

    exact = [
        (l, p) for l, p in candidates if l.ppr == ppr and l.num_teams == num_teams
    ]
    if exact:
        return exact[0][1], False

    same_format = [(l, p) for l, p in candidates if l.ppr == ppr] or candidates
    best = min(same_format, key=lambda lp: abs(lp[0].num_teams - num_teams))
    return best[1], True


def _league_pool(league: LeagueConfig) -> Path:
    path = _pool_for(league.ppr, league.num_teams)[0]
    if path is None:
        raise HTTPException(404, "No player pool has been built yet")
    return path


@lru_cache(maxsize=32)
def _get_league(league_key: str) -> LeagueConfig:
    """Look a league up on Sleeper by its id. Cached because every request in a
    league view needs it and Sleeper asks not to be polled hard."""
    try:
        with SleeperClient() as client:
            info = client.get_league(league_key)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(404, f"Unknown league {league_key!r}") from exc
        raise HTTPException(502, "Couldn't reach Sleeper right now") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Couldn't reach Sleeper right now") from exc

    if not info:
        raise HTTPException(404, f"Unknown league {league_key!r}")
    return _league_from_sleeper(info)


def _summarize(scores: list[float]) -> dict[str, float]:
    sorted_scores = sorted(scores)
    n = len(sorted_scores)
    return {
        "mean": statistics.mean(scores),
        "stdev": statistics.stdev(scores) if n > 1 else 0.0,
        "p10": sorted_scores[int(n * 0.10)],
        "p50": sorted_scores[int(n * 0.50)],
        "p90": sorted_scores[int(n * 0.90)],
    }


@app.get("/api/leagues/{league_key}/lineup", response_model=dict)
def recommend_lineup(league_key: str, sleeper_user_id: str, week: int | None = None) -> dict:
    """Start/sit: the best legal lineup you can field, diffed against the one set.

    Read-only. Bye weeks come from the committed schedule snapshot, so they cost
    nothing here. Injury status is not consulted: the only source is Sleeper's
    ~5MB player dump, which does not belong in a request, so the CLI
    (`python -m ffb.lineup`) fetches it and this endpoint says it did not.
    """
    league = _get_league(league_key)
    pool_path = _league_pool(league)

    with SleeperClient() as client:
        rosters = client.get_rosters(league.league_id)

    my_roster = next((r for r in rosters if r["owner_id"] == sleeper_user_id), None)
    if my_roster is None:
        raise HTTPException(404, "Your roster wasn't found in this league")

    players = load_players(pool_path)
    by_id = {p.player_id: p for p in players}
    known = [by_id[pid] for pid in (my_roster.get("players") or []) if pid in by_id]
    valued_positions = {
        by_id[pid].position
        for r in rosters
        for pid in (r.get("players") or [])
        if pid in by_id
    }

    try:
        advice = advise(
            known,
            [str(s) for s in (my_roster.get("starters") or [])],
            league.roster_positions,
            valued_positions,
            week=week,
            nfl_team=_team_lookup(),
            byes=season_byes(league.season),
        )
    except ValueError as exc:
        # An empty diff would read as "your lineup is fine". It isn't: we could
        # not model the roster at all, so say that instead.
        return {
            "status": "cannot_evaluate",
            "reason": str(exc),
            "roster_id": my_roster.get("roster_id"),
        }

    return {**lineup_as_dict(advice), "roster_id": my_roster["roster_id"], "league": league.name}
