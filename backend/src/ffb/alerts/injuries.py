"""Injury watch: tell me when someone on my roster shows up on the report.

Runs as a scheduled job, not inside the API - it needs nflreadpy/polars, which
are deliberately excluded from the serverless bundle (see the repo README).

    uv run python -m ffb.alerts.injuries --season 2026

The first run for a season records the current report and stays quiet, so you
don't get one message per already-injured player. Use --force to post anyway.
"""

import argparse
import os
from datetime import datetime, timezone

from sqlalchemy import delete, select

from ffb.alerts import discord
from ffb.alerts.diff import RosteredPlayer, diff_statuses, format_message
from ffb.db import Session, init_db
from ffb.leagues import SLEEPER_USERNAME
from ffb.models import InjuryState
from ffb.nfldata.ids import normalize_name
from ffb.sleeper_client import SleeperClient

DEFAULT_SEASON = os.getenv("FFB_SEASON", "2026")
# Sleeper username to watch. The web app has no login, so the job needs telling.
WATCH_USERNAME = os.getenv("FFB_SLEEPER_USERNAME", SLEEPER_USERNAME)

# "Full Participation in Practice" is the all-clear, so it is not news.
_HEALTHY_PRACTICE = "full participation"


def report_status(row: dict) -> str | None:
    """The one line worth repeating for an injury row, or None if it's noise."""
    report = (row.get("report_status") or "").strip()
    if report:
        return report
    practice = (row.get("practice_status") or "").strip()
    if practice and _HEALTHY_PRACTICE not in practice.lower():
        return f"Practice: {practice}"
    return None


def rostered_players(season: str, username: str) -> dict[str, list[str]]:
    """sleeper_player_id -> the league names it is rostered in, for one user."""
    with SleeperClient() as client:
        user = client.get_user(username)
        if not user or not user.get("user_id"):
            raise SystemExit(f"No Sleeper user called {username!r}")
        user_id = user["user_id"]

        owned: dict[str, list[str]] = {}
        for league in client.get_user_leagues(user_id, season) or []:
            rosters = client.get_rosters(league["league_id"]) or []
            mine = next((r for r in rosters if r.get("owner_id") == user_id), None)
            if mine is None:
                continue
            for player_id in mine.get("players") or []:
                owned.setdefault(str(player_id), []).append(league["name"])
    return owned


def latest_injury_rows(season: int) -> tuple[list[dict], int]:
    """Injury rows for the most recent week that has any, plus that week."""
    import nflreadpy as nfl
    import polars as pl

    injuries = nfl.load_injuries(seasons=[season])
    if injuries.height == 0:
        return [], 0
    week = int(injuries.select(pl.col("week").max()).item())
    rows = injuries.filter(pl.col("week") == week).to_dicts()
    return rows, week


def load_state(session, season: str) -> dict[str, InjuryState]:
    rows = session.scalars(select(InjuryState).where(InjuryState.season == season))
    return {row.sleeper_player_id: row for row in rows}


def collect(season: str, username: str) -> tuple[dict, dict, int]:
    """Gather what the diff needs: rostered players, their statuses, the week."""
    owned = rostered_players(season, username)
    rows, week = latest_injury_rows(int(season))

    # Injury rows carry names, not Sleeper ids, so go through the same
    # name+position crosswalk the draft pool build uses.
    from ffb.nfldata.ids import sleeper_id_lookup

    lookup = sleeper_id_lookup()

    current: dict[str, str] = {}
    seen: dict[str, RosteredPlayer] = {}
    for row in rows:
        name = row.get("full_name") or ""
        position = row.get("position") or ""
        player_id = lookup.get((normalize_name(name), position))
        if player_id is None or player_id not in owned:
            continue
        status = report_status(row)
        if status is None:
            continue
        current[player_id] = status
        seen[player_id] = RosteredPlayer(
            sleeper_player_id=player_id,
            name=name,
            position=position,
            nfl_team=row.get("team") or "",
            leagues=tuple(owned[player_id]),
        )
    return seen, current, week


def run(season: str, username: str, force: bool, dry_run: bool) -> int:
    init_db()
    with Session() as session:
        stored = load_state(session, season)
        seen, current, week = collect(season, username)

        # Anyone we flagged before still needs considering, even if they have
        # dropped off this week's report - that absence is the good news.
        owned_leagues = rostered_players(season, username)
        rostered = dict(seen)
        for player_id, row in stored.items():
            if player_id not in rostered and player_id in owned_leagues:
                rostered[player_id] = RosteredPlayer(
                    sleeper_player_id=player_id,
                    name=row.name or player_id,
                    position=row.position,
                    nfl_team=row.nfl_team,
                    leagues=tuple(owned_leagues[player_id]),
                )

        previous = {pid: row.status for pid, row in stored.items()}
        result = diff_statuses(rostered, current, previous, week)

        first_run = not stored
        if first_run and not force:
            _persist(session, season, week, result.next_state, rostered)
            session.commit()
            print(
                f"Seeded {len(result.next_state)} injury statuses for {season} "
                f"week {week}. Nothing posted on a first run - rerun with "
                f"--force to announce the current report."
            )
            return 0

        message = format_message(result.alerts, week)
        if not message:
            print(f"No injury changes for week {week}.")
            return 0

        if dry_run:
            print(message)
        else:
            sent = discord.post(message)
            print(f"Posted {len(result.alerts)} change(s) in {sent} message(s).")

        _persist(session, season, week, result.next_state, rostered)
        session.commit()
        return len(result.alerts)


def _persist(session, season: str, week: int, next_state, rostered) -> None:
    now = datetime.now(timezone.utc)
    for player_id, status in next_state.items():
        if status is None:
            session.execute(
                delete(InjuryState).where(
                    InjuryState.season == season,
                    InjuryState.sleeper_player_id == player_id,
                )
            )
            continue
        player = rostered.get(player_id)
        row = session.get(InjuryState, {"season": season, "sleeper_player_id": player_id})
        if row is None:
            row = InjuryState(season=season, sleeper_player_id=player_id)
            session.add(row)
        row.status = status
        row.week = week
        row.updated_at = now
        if player is not None:
            row.name = player.name
            row.position = player.position
            row.nfl_team = player.nfl_team


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--username", default=WATCH_USERNAME)
    parser.add_argument(
        "--force", action="store_true", help="post even on the first run for a season"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the message instead of posting"
    )
    args = parser.parse_args()
    run(args.season, args.username, args.force, args.dry_run)


if __name__ == "__main__":
    main()
