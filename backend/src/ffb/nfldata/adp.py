"""Average draft position from Fantasy Football Calculator's public API.

FFC's `stdev` is the spread of a player's *draft slot* (how much their pick
position varies) - this feeds the opponent draft model, and is distinct from
fantasy-point variance.
"""

import httpx

FFC_URL = "https://fantasyfootballcalculator.com/api/v1/adp/{fmt}"


def scoring_format(rec_points: float) -> str:
    """Map a league's per-reception points to an FFC scoring format."""
    if rec_points >= 1.0:
        return "ppr"
    if rec_points >= 0.5:
        return "half-ppr"
    return "standard"


def fetch_adp(fmt: str, teams: int, year: int) -> list[dict]:
    """Return FFC ADP rows: name, position, team, adp, stdev, bye, ..."""
    resp = httpx.get(FFC_URL.format(fmt=fmt), params={"teams": teams, "year": year}, timeout=15.0)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "Success":
        raise RuntimeError(f"FFC ADP error for {fmt} teams={teams} year={year}: {data.get('status')}")
    return data["players"]
