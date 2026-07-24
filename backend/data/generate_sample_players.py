"""Generates a synthetic player pool (projections + ADP) so the draft simulator
is runnable before real projection/ADP data is wired in. Replace with a real
loader (FantasyPros/nflverse export -> data/players.csv) later."""

import csv
import random
from pathlib import Path

random.seed(26)

POSITIONS = {
    "QB": (24, 260, 30),
    "RB": (60, 220, 55),
    "WR": (70, 210, 50),
    "TE": (24, 160, 35),
    "K": (20, 130, 15),
    "DEF": (20, 130, 20),
}

OUT_PATH = Path(__file__).parent / "players_sample.csv"


def main() -> None:
    rows = []
    pid = 1
    for pos, (count, top_points, stdev_base) in POSITIONS.items():
        for rank in range(1, count + 1):
            decay = top_points * (0.965 ** rank)
            proj_points = round(max(decay, 20) + random.uniform(-4, 4), 1)
            proj_stdev = round(stdev_base * (0.6 + rank / count), 1)
            rows.append(
                {
                    "player_id": f"SAMPLE{pid}",
                    "name": f"{pos} Player {rank}",
                    "position": pos,
                    "nfl_team": "FA",
                    "bye_week": random.randint(5, 14),
                    "proj_points": proj_points,
                    "proj_stdev": proj_stdev,
                }
            )
            pid += 1

    rows.sort(key=lambda r: -r["proj_points"])
    for i, r in enumerate(rows, start=1):
        r["adp"] = round(i + random.gauss(0, i * 0.08), 1)

    with OUT_PATH.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} synthetic players to {OUT_PATH}")


if __name__ == "__main__":
    main()
