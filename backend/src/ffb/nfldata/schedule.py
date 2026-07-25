"""NFL game schedule, by week, from nflverse."""

import nflreadpy as nfl
import polars as pl


def week_schedule(season: int, week: int) -> list[dict]:
    df = nfl.load_schedules(seasons=[season]).filter(
        (pl.col("season") == season) & (pl.col("week") == week)
    )
    games = df.select(
        "game_id", "game_type", "week", "gameday", "weekday", "gametime",
        "away_team", "away_score", "home_team", "home_score",
    ).sort("gameday", "gametime")
    return games.to_dicts()


def available_weeks(season: int) -> list[int]:
    df = nfl.load_schedules(seasons=[season]).filter(pl.col("season") == season)
    return sorted(df["week"].unique().to_list())
