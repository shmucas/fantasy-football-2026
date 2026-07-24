"""HTTP API for the frontend: league/roster data plus draft-sim triggering."""

import csv
import statistics
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ffb.draft.run_sims import DATA_DIR, RESULTS_DIR, load_players, run_scenario
from ffb.leagues import LEAGUES, SLEEPER_USER_ID
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


class ScenarioStats(BaseModel):
    scenario: str
    mean: float
    stdev: float
    p10: float
    p50: float
    p90: float


@app.post("/api/sims/run", response_model=ScenarioStats)
def run_sim(req: SimRequest) -> ScenarioStats:
    league = _get_league(req.league_key)
    pool_path = DATA_DIR / "pools" / f"{league.key}.csv"
    if not pool_path.exists():
        raise HTTPException(404, f"No player pool found at {pool_path}")

    players = load_players(pool_path)
    scores = run_scenario(
        players, league.num_teams, req.rounds, league.roster_positions,
        req.my_slot, req.forced_picks, req.n_sims,
    )

    out_dir = RESULTS_DIR / league.key
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = "baseline" if not req.forced_picks else "_".join(
        f"r{k}-{v}" for k, v in sorted(req.forced_picks.items())
    )
    out_path = out_dir / f"{tag}.csv"
    with out_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["projected_points"])
        writer.writerows([[s] for s in scores])

    return ScenarioStats(scenario=tag, **_summarize(scores))


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
        return list(csv.DictReader(f))


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
