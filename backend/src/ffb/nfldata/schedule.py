"""NFL game schedule, by week.

Reads a committed CSV snapshot of the nflverse schedule rather than fetching
from nflverse at request time. The snapshot keeps the deployed API free of
polars/nflreadpy (~215MB of wheels, which blows the serverless bundle limit)
and means a cold start doesn't depend on nflverse being reachable.

Refresh the snapshot with `python -m ffb.nfldata.refresh` and commit the result,
the same way the player pools in data/pools/ are handled.
"""

import csv
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
SCHEDULE_DIR = DATA_DIR / "nfl"

COLUMNS = [
    "game_id", "game_type", "week", "gameday", "weekday", "gametime",
    "away_team", "away_score", "home_team", "home_score",
]


def schedule_path(season: int) -> Path:
    return SCHEDULE_DIR / f"schedule_{season}.csv"


def _int_or_none(value: str) -> int | None:
    return int(value) if value not in ("", "None") else None


@lru_cache(maxsize=4)
def _load(season: int) -> tuple[dict, ...]:
    path = schedule_path(season)
    if not path.exists():
        return ()
    with path.open() as f:
        rows = [
            {
                **row,
                "week": int(row["week"]),
                "gametime": row["gametime"] or None,
                "away_score": _int_or_none(row["away_score"]),
                "home_score": _int_or_none(row["home_score"]),
            }
            for row in csv.DictReader(f)
        ]
    # Sort key mirrors the old polars .sort("gameday", "gametime"); empty
    # kickoff times sort last within a day rather than raising on None.
    rows.sort(key=lambda r: (r["gameday"], r["gametime"] or "99:99"))
    return tuple(rows)


def week_schedule(season: int, week: int) -> list[dict]:
    return [dict(row) for row in _load(season) if row["week"] == week]


def available_weeks(season: int) -> list[int]:
    return sorted({row["week"] for row in _load(season)})
