"""HTTP API for the frontend: league/roster data plus draft-sim triggering."""

import csv
import random
import statistics
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ffb.draft.run_sims import DATA_DIR, RESULTS_DIR, load_players, run_scenario
from ffb.draft.sim import simulate_draft
from ffb.draft.strategy import replacement_levels, vorp
from ffb.leagues import LEAGUES, SLEEPER_USER_ID
from ffb.nfldata.ids import sleeper_team_lookup
from ffb.nfldata.schedule import available_weeks, week_schedule
from ffb.sleeper_client import SleeperClient

app = FastAPI(title="FFB API")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/leagues")
def list_leagues() -> list[dict]:
    return [league.model_dump() for league in LEAGUES.values()]


@app.get("/api/leagues/{league_key}/roster")
def get_my_roster(league_key: str) -> dict:
    league = _get_league(league_key)
    with SleeperClient() as client:
        info = client.get_league(league.league_id)
        rosters = client.get_rosters(league.league_id)
        users = client.get_users(league.league_id)

    user_by_id = {u["user_id"]: u for u in users}
    my_roster = next((r for r in rosters if r["owner_id"] == SLEEPER_USER_ID), None)
    if my_roster is None:
        raise HTTPException(404, "Your roster wasn't found in this league")

    return {
        "status": info["status"],
        "display_name": user_by_id[SLEEPER_USER_ID]["display_name"],
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
    pool_path = DATA_DIR / "pools" / f"{league.key}.csv"
    if not pool_path.exists():
        raise HTTPException(404, f"No player pool found at {pool_path}")

    if len(set(req.already_picked)) != len(req.already_picked):
        raise HTTPException(400, "already_picked contains the same player more than once")

    players = load_players(pool_path)
    try:
        scores = run_scenario(
            players, league.num_teams, req.rounds, league.roster_positions,
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

    sample = _sample_roster_with_reasons(players, league, req)
    return SimResponse(scenario=tag, sample_roster=sample, **_summarize(scores))


def _sample_roster_with_reasons(players, league, req: SimRequest) -> list[SamplePick]:
    """Runs one representative draft and explains each of our picks."""
    result = simulate_draft(
        players, league.num_teams, req.rounds, league.roster_positions,
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
    pool_path = DATA_DIR / "pools" / f"{league.key}.csv"
    if not pool_path.exists():
        return []
    with pool_path.open() as f:
        return [_with_team(row) for row in csv.DictReader(f)]


@app.get("/api/leagues/{league_key}/recommend", response_model=list[dict])
def recommend_next_pick(league_key: str, exclude: str = "") -> list[dict]:
    """Best available players by VORP, given players already picked/forced elsewhere."""
    league = _get_league(league_key)
    pool_path = DATA_DIR / "pools" / f"{league.key}.csv"
    if not pool_path.exists():
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
    pool_path = DATA_DIR / "pools" / f"{league.key}.csv"
    if not pool_path.exists():
        return []

    with SleeperClient() as client:
        rosters = client.get_rosters(league.league_id)
    rostered_ids = {pid for r in rosters for pid in (r.get("players") or [])}

    with pool_path.open() as f:
        pool = list(csv.DictReader(f))

    available = [_with_team(p) for p in pool if p["player_id"] not in rostered_ids]
    available.sort(key=lambda p: float(p["proj_points"] or 0), reverse=True)
    return available


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


def _get_league(league_key: str):
    league = LEAGUES.get(league_key)
    if league is None:
        raise HTTPException(404, f"Unknown league {league_key!r}")
    return league


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
