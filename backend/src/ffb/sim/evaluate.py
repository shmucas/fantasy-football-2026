"""Compare draft-pick scenarios by expected regular-season wins.

For each scenario (an optional set of forced picks) we run N samples. Each
sample simulates the whole draft (our forced picks + noisy opponents), then
plays one sampled season and records our wins and points-for. Scenarios share
the same sample seeds, so opponent draft noise and season/game noise are common
across them and the comparison is low-variance (common random numbers).
"""

import argparse
import random
import statistics
from multiprocessing import Pool
from pathlib import Path

from ffb.draft.run_sims import load_players
from ffb.draft.sim import simulate_draft
from ffb.leagues import LEAGUES
from ffb.sim.season import REG_SEASON_WEEKS, SeasonModel, round_robin

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Worker globals, built once per process by _init_worker.
_G: dict = {}


def _init_worker(players, league, my_slot, rounds, n_samples, schedule):
    _G["players"] = players
    _G["league"] = league
    _G["my_slot"] = my_slot
    _G["rounds"] = rounds
    _G["schedule"] = schedule
    _G["model"] = SeasonModel(players, n_samples, seed=0)


def _run_sample(task):
    forced_picks, sample_i = task
    g = _G
    rng = random.Random(sample_i)  # CRN: same seed -> same opponent noise across scenarios
    result = simulate_draft(
        g["players"], g["league"].num_teams, g["rounds"],
        g["league"].roster_positions, g["my_slot"], rng, forced_picks,
    )
    return g["model"].evaluate(
        result.rosters, g["league"].roster_positions, g["my_slot"], g["schedule"], sample_i
    )


def run_scenario(pool, forced_picks, n_samples):
    results = pool.map(_run_sample, [(forced_picks, i) for i in range(n_samples)])
    wins = [w for w, _ in results]
    points_for = [pf for _, pf in results]
    return wins, points_for


def summarize(wins, points_for, playoff_threshold):
    return {
        "exp_wins": statistics.mean(wins),
        "win_stdev": statistics.pstdev(wins),
        "playoff_pct": 100.0 * sum(1 for w in wins if w >= playoff_threshold) / len(wins),
        "avg_pf": statistics.mean(points_for),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True, choices=list(LEAGUES.keys()))
    parser.add_argument("--my-slot", type=int, required=True)
    parser.add_argument("--rounds", type=int, default=15)
    parser.add_argument("--n-samples", type=int, default=1500)
    parser.add_argument(
        "--force",
        action="append",
        default=[],
        metavar="LABEL=ROUND:PLAYER_ID",
        help="A scenario: label plus one forced pick, e.g. chase=1:7564. Repeatable.",
    )
    args = parser.parse_args()

    league = LEAGUES[args.league]
    players = load_players(DATA_DIR / "pools" / f"{league.key}.csv")
    schedule = round_robin(league.num_teams, REG_SEASON_WEEKS)

    scenarios: dict[str, dict] = {"baseline": {}}
    for item in args.force:
        label, pick = item.split("=")
        rnd, pid = pick.split(":")
        scenarios[label] = {int(rnd): pid}

    name = {p.player_id: p.name for p in players}
    playoff_threshold = REG_SEASON_WEEKS // 2 + 1
    print(f"{league.name} | slot {args.my_slot} | {args.n_samples} samples | "
          f"{league.num_teams} teams, {REG_SEASON_WEEKS} reg weeks\n")
    print(f"{'scenario':<34}{'exp wins':>10}{'win sd':>9}{'>= ' + str(playoff_threshold) + 'W%':>9}{'avg PF':>9}")

    with Pool(
        initializer=_init_worker,
        initargs=(players, league, args.my_slot, args.rounds, args.n_samples, schedule),
    ) as pool:
        for label, forced in scenarios.items():
            wins, pf = run_scenario(pool, forced, args.n_samples)
            s = summarize(wins, pf, playoff_threshold)
            forced_desc = ", ".join(f"R{r}:{name.get(p, p)}" for r, p in forced.items())
            tag = f"{label} ({forced_desc})" if forced_desc else label
            print(f"{tag:<34}{s['exp_wins']:>10.2f}{s['win_stdev']:>9.2f}"
                  f"{s['playoff_pct']:>8.1f}%{s['avg_pf']:>9.0f}")


if __name__ == "__main__":
    main()
