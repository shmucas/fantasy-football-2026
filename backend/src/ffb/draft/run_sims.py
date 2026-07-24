"""Run N draft simulations in parallel, optionally forcing our round-1/round-2
pick, and save the resulting outcome distributions to data/results/."""

import argparse
import csv
import random
from multiprocessing import Pool
from pathlib import Path

from ffb.draft.sim import roster_projected_points, simulate_draft
from ffb.draft.strategy import Player
from ffb.leagues import LEAGUES

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
RESULTS_DIR = DATA_DIR / "results"


def load_players(csv_path: Path) -> list[Player]:
    players = []
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            players.append(
                Player(
                    player_id=row["player_id"],
                    name=row["name"],
                    position=row["position"],
                    proj_points=float(row["proj_points"]),
                    proj_stdev=float(row["proj_stdev"]),
                    adp=float(row["adp"]),
                    adp_stdev=float(row.get("adp_stdev", 0.0) or 0.0),
                )
            )
    return players


def _run_one(args: tuple) -> float:
    players, num_teams, rounds, roster_positions, my_slot, forced_picks, seed = args
    rng = random.Random(seed)
    result = simulate_draft(players, num_teams, rounds, roster_positions, my_slot, rng, forced_picks)
    return roster_projected_points(result.rosters[my_slot], roster_positions)


def run_scenario(
    players: list[Player],
    num_teams: int,
    rounds: int,
    roster_positions: list[str],
    my_slot: int,
    forced_picks: dict[int, str] | None,
    n_sims: int,
    base_seed: int = 0,
) -> list[float]:
    tasks = [
        (players, num_teams, rounds, roster_positions, my_slot, forced_picks, base_seed + i)
        for i in range(n_sims)
    ]
    with Pool() as pool:
        return pool.map(_run_one, tasks)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True, choices=list(LEAGUES.keys()))
    parser.add_argument("--my-slot", type=int, required=True, help="Your draft slot (1-indexed)")
    parser.add_argument("--rounds", type=int, default=15)
    parser.add_argument("--n-sims", type=int, default=2000)
    parser.add_argument(
        "--players-csv",
        default=None,
        help="Defaults to data/pools/<league>.csv (built by ffb.nfldata.build).",
    )
    parser.add_argument(
        "--force",
        action="append",
        default=[],
        metavar="ROUND:PLAYER_ID",
        help="Force your pick in a given round, e.g. --force 1:SAMPLE3. Repeatable.",
    )
    args = parser.parse_args()

    league = LEAGUES[args.league]
    csv_path = Path(args.players_csv) if args.players_csv else DATA_DIR / "pools" / f"{league.key}.csv"
    players = load_players(csv_path)
    forced = dict(item.split(":") for item in args.force)
    forced = {int(k): v for k, v in forced.items()}

    scores = run_scenario(
        players, league.num_teams, args.rounds, league.roster_positions,
        args.my_slot, forced, args.n_sims,
    )

    out_dir = RESULTS_DIR / league.key
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = "baseline" if not forced else "_".join(f"r{k}-{v}" for k, v in sorted(forced.items()))
    out_path = out_dir / f"{tag}.csv"
    with out_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["projected_points"])
        writer.writerows([[s] for s in scores])

    print(f"Ran {args.n_sims} sims for {league.name}, scenario '{tag}' -> {out_path}")


if __name__ == "__main__":
    main()
