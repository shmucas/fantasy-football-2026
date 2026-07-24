"""Build a per-league draft pool CSV that the simulator consumes.

For each player with an ADP:
  - skill players (QB/RB/WR/TE): mean points from the positional points curve
    at their within-position market rank; weekly stdev = position CV x per-game
    mean.
  - K/DEF: baseline points by within-position ADP rank (nflverse has no kicker
    distance scoring or team defense), low weekly stdev.

Output columns match run_sims.load_players: player_id, name, position,
proj_points, proj_stdev, adp, adp_stdev.
"""

import argparse
import csv
from pathlib import Path

from ffb.leagues import LEAGUES
from ffb.nfldata.adp import fetch_adp, scoring_format
from ffb.nfldata.history import build_history_model
from ffb.nfldata.ids import normalize_name, sleeper_id_lookup
from ffb.sleeper_client import SleeperClient

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
HISTORY_SEASONS = [2022, 2023, 2024]
GAMES_PER_SEASON = 17

# K/DEF baseline: best starter ~ these points, declining ~4% per rank.
KDEF_TOP_POINTS = {"K": 150.0, "DEF": 140.0}
KDEF_WEEKLY_STDEV = {"K": 3.0, "DEF": 4.0}


def _league_scoring(sleeper_scoring: dict) -> dict[str, float]:
    return {k: float(v) for k, v in sleeper_scoring.items()}


def build_pool(league_key: str, year: int = 2026) -> Path:
    league = LEAGUES[league_key]
    with SleeperClient() as client:
        sleeper_league = client.get_league(league.league_id)
    scoring = _league_scoring(sleeper_league["scoring_settings"])

    fmt = scoring_format(scoring.get("rec", 0.0))
    adp_rows = fetch_adp(fmt, league.num_teams, year)
    history = build_history_model(scoring, HISTORY_SEASONS)
    sid_lookup = sleeper_id_lookup()

    # Assign within-position rank by ADP order.
    adp_rows = sorted(adp_rows, key=lambda r: r["adp"])
    pos_counter: dict[str, int] = {}
    out_rows = []
    joined = 0
    for r in adp_rows:
        pos = r["position"]
        pos_counter[pos] = pos_counter.get(pos, 0) + 1
        rank = pos_counter[pos]

        if pos in ("K", "DEF"):
            proj = KDEF_TOP_POINTS.get(pos, 120.0) * (0.96 ** (rank - 1))
            stdev = KDEF_WEEKLY_STDEV.get(pos, 4.0)
        else:
            season_pts = history.curve_points(pos, rank)
            if season_pts is None:
                continue  # unknown position, skip
            proj = season_pts
            cv = history.cv.get(pos, 0.8)
            stdev = round(cv * (proj / GAMES_PER_SEASON), 2)

        sid = sid_lookup.get((normalize_name(r["name"]), pos))
        if sid:
            joined += 1
        out_rows.append(
            {
                "player_id": sid or f"ffc_{r['player_id']}",
                "name": r["name"],
                "position": pos,
                "proj_points": round(proj, 1),
                "proj_stdev": stdev,
                "adp": r["adp"],
                "adp_stdev": r.get("stdev", 0.0),
            }
        )

    out_dir = DATA_DIR / "pools"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{league_key}.csv"
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        writer.writeheader()
        writer.writerows(out_rows)

    # Coverage report.
    top100 = out_rows[:100]
    top100_joined = sum(1 for r in top100 if not r["player_id"].startswith("ffc_"))
    print(f"[{league_key}] format={fmt} teams={league.num_teams} -> {out_path}")
    print(f"  players: {len(out_rows)} | sleeper_id joined: {joined}/{len(out_rows)}"
          f" | top-100 ADP joined: {top100_joined}/100")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True, choices=list(LEAGUES.keys()))
    parser.add_argument("--year", type=int, default=2026)
    args = parser.parse_args()
    build_pool(args.league, args.year)


if __name__ == "__main__":
    main()
