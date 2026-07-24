"""Compare outcome distributions across forced-pick scenarios (e.g. round-1
RB vs round-1 WR) using saved run_sims.py results."""

import argparse
import csv
import statistics
from pathlib import Path

from ffb.leagues import LEAGUES

RESULTS_DIR = Path(__file__).resolve().parents[3] / "data" / "results"


def load_scores(path: Path) -> list[float]:
    with path.open() as f:
        return [float(row["projected_points"]) for row in csv.DictReader(f)]


def summarize(scores: list[float]) -> dict[str, float]:
    sorted_scores = sorted(scores)
    n = len(sorted_scores)
    return {
        "mean": statistics.mean(scores),
        "stdev": statistics.stdev(scores) if n > 1 else 0.0,
        "p10": sorted_scores[int(n * 0.10)],
        "p50": sorted_scores[int(n * 0.50)],
        "p90": sorted_scores[int(n * 0.90)],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True, choices=list(LEAGUES.keys()))
    args = parser.parse_args()

    league_dir = RESULTS_DIR / args.league
    if not league_dir.exists():
        print(f"No results yet for {args.league}. Run run_sims.py first.")
        return

    print(f"{'scenario':<30}{'mean':>10}{'stdev':>10}{'p10':>10}{'p50':>10}{'p90':>10}")
    for path in sorted(league_dir.glob("*.csv")):
        stats = summarize(load_scores(path))
        print(
            f"{path.stem:<30}{stats['mean']:>10.1f}{stats['stdev']:>10.1f}"
            f"{stats['p10']:>10.1f}{stats['p50']:>10.1f}{stats['p90']:>10.1f}"
        )


if __name__ == "__main__":
    main()
