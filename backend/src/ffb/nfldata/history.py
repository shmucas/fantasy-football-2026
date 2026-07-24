"""Derive from nflverse historical weekly scoring, under a given league's scoring:

- positional points curve: what the Nth-best player at a position scores in a
  full season (the shape is stable year to year). Maps a player's *current
  market rank* to projected points, so rookies/team-changers are priced by the
  market while point values come from history.
- position variance (coefficient of variation): weekly std / points-per-game,
  so any player - including one with no history - gets a sane weekly stdev
  from their projected scoring.
"""

from dataclasses import dataclass

import nflreadpy as nfl
import polars as pl

from ffb.nfldata.scoring import weekly_fantasy_points

SKILL_POSITIONS = ("QB", "RB", "WR", "TE")
MIN_GAMES_FOR_VARIANCE = 8


@dataclass
class HistoryModel:
    # position -> list of season points indexed by (rank-1); rank 1 = best.
    curve: dict[str, list[float]]
    # position -> weekly coefficient of variation (weekly std / weekly mean).
    cv: dict[str, float]
    # position -> within-season game-to-game CV (the game-level draw).
    game_cv: dict[str, float]
    # position -> between-season CV of a player's season mean (the season draw).
    season_cv: dict[str, float]

    def curve_points(self, position: str, positional_rank: int) -> float | None:
        c = self.curve.get(position)
        if not c:
            return None
        idx = min(positional_rank, len(c)) - 1  # clamp beyond last known rank
        return c[idx]


def _scored_weekly(scoring: dict[str, float], seasons: list[int]) -> pl.DataFrame:
    df = nfl.load_player_stats(seasons=seasons).filter(
        (pl.col("season_type") == "REG") & (pl.col("position").is_in(SKILL_POSITIONS))
    )
    return weekly_fantasy_points(df, scoring)


def build_history_model(scoring: dict[str, float], seasons: list[int]) -> HistoryModel:
    weekly = _scored_weekly(scoring, seasons)

    # Per player-season totals + weekly variance.
    per_season = weekly.group_by(["player_id", "season", "position"]).agg(
        total_fp=pl.col("fp").sum(),
        games=pl.col("fp").len(),
        ppg=pl.col("fp").mean(),
        wk_std=pl.col("fp").std(),
    )

    # --- positional points curve: rank within each position-season by total, ---
    # then average the season total at each rank across seasons.
    ranked = per_season.with_columns(
        pl.col("total_fp").rank("ordinal", descending=True).over(["season", "position"]).alias("pos_rank")
    )
    curve_df = ranked.group_by(["position", "pos_rank"]).agg(pl.col("total_fp").mean().alias("pts"))

    curve: dict[str, list[float]] = {}
    for pos in SKILL_POSITIONS:
        rows = curve_df.filter(pl.col("position") == pos).sort("pos_rank")
        curve[pos] = rows["pts"].to_list()

    # --- variance decomposition from established player-seasons ---
    # game-level CV: within-season week-to-week std relative to that season's ppg.
    established = per_season.filter(pl.col("games") >= MIN_GAMES_FOR_VARIANCE).with_columns(
        (pl.col("wk_std") / pl.col("ppg")).alias("gcv")
    )
    gcv_df = established.group_by("position").agg(pl.col("gcv").median().alias("v"))
    game_cv = {r["position"]: r["v"] for r in gcv_df.iter_rows(named=True)}
    cv = dict(game_cv)  # weekly CV == game-level CV

    # season-level CV: spread of a player's season ppg across seasons, relative
    # to their own multi-season mean. Needs >= 2 qualifying seasons per player.
    multi = (
        established.group_by(["player_id", "position"])
        .agg(
            career_ppg=pl.col("ppg").mean(),
            season_ppg_std=pl.col("ppg").std(),
            n_seasons=pl.col("ppg").len(),
        )
        .filter(pl.col("n_seasons") >= 2)
        .with_columns((pl.col("season_ppg_std") / pl.col("career_ppg")).alias("scv"))
    )
    scv_df = multi.group_by("position").agg(pl.col("scv").median().alias("v"))
    season_cv = {r["position"]: r["v"] for r in scv_df.iter_rows(named=True)}

    return HistoryModel(curve=curve, cv=cv, game_cv=game_cv, season_cv=season_cv)
