"""Crosswalk player names/ids across sources so FFC ADP and nflverse history
join to Sleeper roster player_ids.

Matching is by normalized name + position (FFC gives no stable id shared with
Sleeper). Normalization strips case, punctuation, and generational suffixes.

sleeper_id_lookup() runs in the offline pool build and pulls from nflverse.
sleeper_team_lookup() serves an API request, so it reads the committed snapshot
in data/nfl/ instead - see ffb.nfldata.schedule for why.
"""

import csv
import re
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
PLAYER_TEAMS_PATH = DATA_DIR / "nfl" / "player_teams.csv"

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    n = name.lower()
    n = re.sub(r"[^a-z\s]", "", n)  # drop periods, apostrophes, hyphens
    parts = [p for p in n.split() if p not in _SUFFIXES]
    return "".join(parts)


def sleeper_id_lookup() -> dict[tuple[str, str], str]:
    """(normalized_name, position) -> sleeper_id, from nflverse's ff_playerids.

    Build-time only: imports nflreadpy/polars lazily so importing this module
    from the API doesn't drag them into the serverless bundle.
    """
    import nflreadpy as nfl
    import polars as pl

    ids = nfl.load_ff_playerids().filter(pl.col("sleeper_id").is_not_null())
    lookup: dict[tuple[str, str], str] = {}
    for row in ids.iter_rows(named=True):
        key = (normalize_name(row["name"]), row["position"])
        lookup[key] = str(row["sleeper_id"])
    return lookup


@lru_cache(maxsize=1)
def sleeper_team_lookup() -> dict[str, str]:
    """sleeper_id -> current NFL team abbreviation, from the committed snapshot."""
    if not PLAYER_TEAMS_PATH.exists():
        return {}
    with PLAYER_TEAMS_PATH.open() as f:
        return {row["sleeper_id"]: row["team"] for row in csv.DictReader(f)}
