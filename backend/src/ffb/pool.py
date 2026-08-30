"""League lookup and draft-pool selection.

This is the part of the old HTTP layer that the CLI and the scheduled jobs
actually needed. It lived in ffb.api until the website was retired; importing
it from there meant a cron job paid for FastAPI to resolve a league id.

Nothing here raises HTTP errors. A league that cannot be resolved raises
LeagueLookupError, which the callers already translate into their own exits.
"""

from functools import lru_cache
from pathlib import Path

import httpx

from ffb.draft.run_sims import DATA_DIR
from ffb.leagues import LEAGUES, LeagueConfig
from ffb.sleeper_client import SleeperClient

# Slots the pool cannot model. IDP and taxi/IR spots are dropped rather than
# treated as startable, and every flex variant collapses to plain FLEX.
FLEX_SLOTS = {"FLEX", "WRRB_FLEX", "REC_FLEX", "SUPER_FLEX", "IDP_FLEX"}
UNPLAYED_SLOTS = {"TAXI", "IR"}
IDP_SLOTS = {"DL", "LB", "DB", "IDP", "DE", "DT", "CB", "S"}


class LeagueLookupError(RuntimeError):
    """A league id that Sleeper does not know, or could not be fetched."""


def normalize_slots(roster_positions: list[str]) -> tuple[list[str], bool]:
    slots = []
    approx = False
    for slot in roster_positions:
        if slot in UNPLAYED_SLOTS or slot in IDP_SLOTS:
            continue
        if slot in FLEX_SLOTS:
            approx = approx or slot != "FLEX"
            slots.append("FLEX")
        else:
            slots.append(slot)
    return slots, approx


def pool_for(ppr: float, num_teams: int) -> tuple[Path | None, bool]:
    """Pick the prebuilt draft pool that best fits a league shape.

    Pools are expensive to build (ADP fetch plus a multi-season history model),
    so they are generated offline per league in LEAGUES. A league we have no
    exact pool for reuses the closest one - same scoring format first, then the
    nearest team count - and gets flagged as approximate."""
    candidates = []
    for league in LEAGUES.values():
        path = DATA_DIR / "pools" / f"{league.key}.csv"
        if path.exists():
            candidates.append((league, path))
    if not candidates:
        return None, False

    exact = [(l, p) for l, p in candidates if l.ppr == ppr and l.num_teams == num_teams]
    if exact:
        return exact[0][1], False

    same_format = [(l, p) for l, p in candidates if l.ppr == ppr] or candidates
    best = min(same_format, key=lambda lp: abs(lp[0].num_teams - num_teams))
    return best[1], True


def league_from_sleeper(info: dict) -> LeagueConfig:
    """Map a Sleeper league payload onto our config shape. `key` is the Sleeper
    league id, so every league the user is in addresses its own config."""
    slots, flex_approx = normalize_slots(info["roster_positions"])
    ppr = float(info.get("scoring_settings", {}).get("rec", 0.0))
    return LeagueConfig(
        key=info["league_id"],
        name=info["name"],
        league_id=info["league_id"],
        season=str(info["season"]),
        num_teams=info["total_rosters"],
        roster_positions=slots,
        flex_approx=flex_approx,
        # Sleeper's waiver_type 2 is FAAB bidding; 0/1 are rolling/reverse waivers.
        faab=info.get("settings", {}).get("waiver_type") == 2,
        ppr=ppr,
        approx_pool=pool_for(ppr, info["total_rosters"])[1],
    )


@lru_cache(maxsize=32)
def get_league(league_key: str) -> LeagueConfig:
    """Look a league up on Sleeper by its id. Cached because a single run asks
    for the same league repeatedly and Sleeper asks not to be polled hard."""
    try:
        with SleeperClient() as client:
            info = client.get_league(league_key)
    except httpx.HTTPError as exc:
        raise LeagueLookupError(f"Couldn't reach Sleeper: {exc}") from exc

    if not info:
        raise LeagueLookupError(f"Unknown league {league_key!r}")
    return league_from_sleeper(info)
