"""Run the maths, decide, and put the decision in front of a human.

    uv run python -m ffb.decide.run lineup   --dry-run
    uv run python -m ffb.decide.run waivers  --dry-run

Nothing here talks to the private API. It reads with the documented REST API,
decides using the projections we already build, and files a proposal. Sending
is the worker's job, and only after you approve.
"""

import argparse
import os

from ffb.approvals import notify, store
from ffb.decide import lineup as lineup_decider
from ffb.decide import waivers as waiver_decider
from ffb.draft.run_sims import load_players
from ffb.leagues import SLEEPER_USERNAME
from ffb.sleeper_client import SleeperClient
from ffb.sleeper_private import set_starters_payload, waiver_claim_payload

DEFAULT_SEASON = os.getenv("FFB_SEASON", "2026")
WATCH_USERNAME = os.getenv("FFB_SLEEPER_USERNAME", SLEEPER_USERNAME)


def _pool_path(ppr: float, num_teams: int):
    """Reuse the API's pool picker so the bot and the UI agree on projections."""
    from ffb.api import _pool_for

    return _pool_for(ppr, num_teams)[0]


def _my_context(client: SleeperClient, username: str, season: str):
    """Every league for this user, with their roster in it."""
    user = client.get_user(username)
    if not user or not user.get("user_id"):
        raise SystemExit(f"No Sleeper user called {username!r}")
    user_id = user["user_id"]

    for info in client.get_user_leagues(user_id, season) or []:
        rosters = client.get_rosters(info["league_id"]) or []
        mine = next((r for r in rosters if r.get("owner_id") == user_id), None)
        if mine is not None:
            yield info, rosters, mine


def propose_lineups(season: str, username: str, dry_run: bool) -> int:
    from ffb.api import _league_from_sleeper

    proposed = 0
    with SleeperClient() as client:
        for info, _rosters, mine in _my_context(client, username, season):
            league = _league_from_sleeper(info)
            pool = _pool_path(league.ppr, league.num_teams)
            if pool is None:
                print(f"{league.name}: no player pool, skipping")
                continue

            players = {p.player_id: p for p in load_players(pool)}
            roster = [players[pid] for pid in (mine.get("players") or []) if pid in players]
            change = lineup_decider.plan(
                roster, league.roster_positions, list(mine.get("starters") or [])
            )

            if not change.is_change:
                print(f"{league.name}: {change.summary()}")
                continue

            print(f"{league.name}: {change.summary()}")
            print(change.detail())
            if dry_run:
                proposed += 1
                continue

            action = store.propose(
                kind="set_starters",
                league_id=league.league_id,
                league_name=league.name,
                roster_id=int(mine["roster_id"]),
                summary=change.summary(),
                detail=change.detail(),
                payload=set_starters_payload(
                    league.league_id, int(mine["roster_id"]), list(change.proposed)
                ),
            )
            _announce(action)
            proposed += 1
    return proposed


def propose_waivers(season: str, username: str, dry_run: bool) -> int:
    from ffb.api import _league_from_sleeper

    proposed = 0
    with SleeperClient() as client:
        for info, rosters, mine in _my_context(client, username, season):
            league = _league_from_sleeper(info)
            pool = _pool_path(league.ppr, league.num_teams)
            if pool is None:
                print(f"{league.name}: no player pool, skipping")
                continue

            all_players = load_players(pool)
            by_id = {p.player_id: p for p in all_players}
            rostered = {pid for r in rosters for pid in (r.get("players") or [])}
            my_ids = list(mine.get("players") or [])

            claim = waiver_decider.best_claim(
                my_players=[by_id[p] for p in my_ids if p in by_id],
                available=[p for p in all_players if p.player_id not in rostered],
                all_players=all_players,
                roster_positions=league.roster_positions,
                num_teams=league.num_teams,
                roster_is_full=len(my_ids) >= len(league.roster_positions),
                faab_budget_left=_faab_left(league, mine),
            )
            if claim is None:
                print(f"{league.name}: nothing on waivers worth a claim")
                continue

            print(f"{league.name}: {claim.summary()}")
            print(claim.detail())
            if dry_run:
                proposed += 1
                continue

            action = store.propose(
                kind="waiver_claim",
                league_id=league.league_id,
                league_name=league.name,
                roster_id=int(mine["roster_id"]),
                summary=claim.summary(),
                detail=claim.detail(),
                payload=waiver_claim_payload(
                    league.league_id,
                    int(mine["roster_id"]),
                    add_player_id=claim.add.player_id,
                    drop_player_id=claim.drop.player_id if claim.drop else None,
                    faab_bid=claim.faab_bid,
                ),
            )
            _announce(action)
            proposed += 1
    return proposed


def _faab_left(league, roster) -> int | None:
    if not league.faab:
        return None
    settings = roster.get("settings") or {}
    spent = settings.get("waiver_budget_used")
    return None if spent is None else max(0, 100 - int(spent))


def _announce(action) -> None:
    try:
        notify.announce(action)
        print(f"  proposed - approve at {notify.approval_link(action)}")
    except Exception as exc:
        # Still proposed; it is in the app's queue even if Discord is down.
        print(f"  proposed, but could not post to Discord: {exc}")
        print(f"  approve at {notify.approval_link(action)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("what", choices=["lineup", "waivers"])
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--username", default=WATCH_USERNAME)
    parser.add_argument(
        "--dry-run", action="store_true", help="decide and print, propose nothing"
    )
    args = parser.parse_args()

    runner = propose_lineups if args.what == "lineup" else propose_waivers
    count = runner(args.season, args.username, args.dry_run)
    verb = "would propose" if args.dry_run else "proposed"
    print(f"\n{verb} {count} action(s).")


if __name__ == "__main__":
    main()
