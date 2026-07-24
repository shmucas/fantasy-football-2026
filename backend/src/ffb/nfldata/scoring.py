"""Turn nflverse weekly stat rows into fantasy points under a league's exact
Sleeper scoring settings.

Only offensive skill scoring (QB/RB/WR/TE) is mapped here - kicker distance
scoring and team-defense are not in nflverse player_stats and are handled with
baselines elsewhere. Sleeper keys with no nflverse equivalent (IDP, yardage
bonuses that are 0 in these leagues) are ignored.
"""

import polars as pl

# Sleeper scoring key -> nflverse player_stats column.
SLEEPER_TO_NFLVERSE: dict[str, str] = {
    "pass_yd": "passing_yards",
    "pass_td": "passing_tds",
    "pass_int": "passing_interceptions",
    "pass_2pt": "passing_2pt_conversions",
    "rush_yd": "rushing_yards",
    "rush_td": "rushing_tds",
    "rush_2pt": "rushing_2pt_conversions",
    "rec": "receptions",
    "rec_yd": "receiving_yards",
    "rec_td": "receiving_tds",
    "rec_2pt": "receiving_2pt_conversions",
    "fum_lost": "fumbles_lost_total",
    "fum_rec_td": "fumble_recovery_tds",
}


def weekly_fantasy_points(df: pl.DataFrame, scoring_settings: dict[str, float]) -> pl.DataFrame:
    """Add an `fp` column: fantasy points per row under the given scoring.

    Unmapped or zero-weight Sleeper keys are skipped. Missing stat columns are
    treated as 0 so a stat this league scores but nflverse lacks doesn't crash.
    """
    terms = []
    for sleeper_key, weight in scoring_settings.items():
        if not weight:
            continue
        col = SLEEPER_TO_NFLVERSE.get(sleeper_key)
        if col is None:
            continue
        if col in df.columns:
            terms.append(pl.col(col).fill_null(0) * weight)

    fp = terms[0] if terms else pl.lit(0.0)
    for t in terms[1:]:
        fp = fp + t
    return df.with_columns(fp.alias("fp"))
