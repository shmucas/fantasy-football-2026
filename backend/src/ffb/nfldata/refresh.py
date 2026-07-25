"""Refresh the committed nflverse snapshots the API serves at runtime.

The deployed API reads data/nfl/ instead of calling nflverse, so polars and
nflreadpy stay out of the serverless bundle. Run this locally and commit the
result whenever the schedule or player-team assignments move:

    uv run python -m ffb.nfldata.refresh --season 2026
"""

import argparse
import csv

from ffb.nfldata.ids import PLAYER_TEAMS_PATH
from ffb.nfldata.schedule import COLUMNS, SCHEDULE_DIR, schedule_path


def refresh_schedule(season: int) -> int:
    import nflreadpy as nfl
    import polars as pl

    df = (
        nfl.load_schedules(seasons=[season])
        .filter(pl.col("season") == season)
        .select(COLUMNS)
        .sort("gameday", "gametime")
    )
    path = schedule_path(season)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row in df.iter_rows(named=True):
            writer.writerow({k: "" if row[k] is None else row[k] for k in COLUMNS})
    return df.height


def refresh_player_teams() -> int:
    import nflreadpy as nfl
    import polars as pl

    ids = nfl.load_ff_playerids().filter(
        pl.col("sleeper_id").is_not_null() & pl.col("team").is_not_null()
    )
    PLAYER_TEAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PLAYER_TEAMS_PATH.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["sleeper_id", "team"])
        for row in ids.iter_rows(named=True):
            writer.writerow([str(row["sleeper_id"]), row["team"]])
    return ids.height


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=2026)
    args = parser.parse_args()

    games = refresh_schedule(args.season)
    print(f"wrote {games} games -> {schedule_path(args.season)}")
    players = refresh_player_teams()
    print(f"wrote {players} players -> {PLAYER_TEAMS_PATH}")
    print(f"(snapshots live in {SCHEDULE_DIR} - commit them)")


if __name__ == "__main__":
    main()
