"""Crosswalk player names/ids across sources so FFC ADP and nflverse history
join to Sleeper roster player_ids.

Matching is by normalized name + position (FFC gives no stable id shared with
Sleeper). Normalization strips case, punctuation, and generational suffixes.
"""

import re

import nflreadpy as nfl
import polars as pl

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    n = name.lower()
    n = re.sub(r"[^a-z\s]", "", n)  # drop periods, apostrophes, hyphens
    parts = [p for p in n.split() if p not in _SUFFIXES]
    return "".join(parts)


def sleeper_id_lookup() -> dict[tuple[str, str], str]:
    """(normalized_name, position) -> sleeper_id, from nflverse's ff_playerids."""
    ids = nfl.load_ff_playerids().filter(pl.col("sleeper_id").is_not_null())
    lookup: dict[tuple[str, str], str] = {}
    for row in ids.iter_rows(named=True):
        key = (normalize_name(row["name"]), row["position"])
        lookup[key] = str(row["sleeper_id"])
    return lookup


def sleeper_team_lookup() -> dict[str, str]:
    """sleeper_id -> current NFL team abbreviation, from nflverse's ff_playerids."""
    ids = nfl.load_ff_playerids().filter(
        pl.col("sleeper_id").is_not_null() & pl.col("team").is_not_null()
    )
    return {str(row["sleeper_id"]): row["team"] for row in ids.iter_rows(named=True)}
