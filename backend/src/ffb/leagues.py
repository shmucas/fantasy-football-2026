from pydantic import BaseModel


class LeagueConfig(BaseModel):
    key: str
    name: str
    league_id: str
    season: str
    num_teams: int
    friend_group: str
    roster_positions: list[str]
    faab: bool


LEAGUES: dict[str, LeagueConfig] = {
    "miller_league_hs": LeagueConfig(
        key="miller_league_hs",
        name="Miller League",
        league_id="1315433135034355712",
        season="2026",
        num_teams=14,
        friend_group="high_school",
        roster_positions=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF",
                           "BN", "BN", "BN", "BN", "BN"],
        faab=True,
    ),
    "maxxing_college": LeagueConfig(
        key="maxxing_college",
        name="FANTASYFOOTBALLMAXXING",
        league_id="1386491914559197184",
        season="2026",
        num_teams=10,
        friend_group="college",
        roster_positions=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF",
                           "BN", "BN", "BN", "BN", "BN"],
        faab=False,
    ),
}

SLEEPER_USER_ID = "1125887731814576128"
SLEEPER_USERNAME = "lucaspedroferreira"
