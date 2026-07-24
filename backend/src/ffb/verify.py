"""Sanity check: pull each configured league and confirm we can find our own roster."""

from ffb.leagues import LEAGUES, SLEEPER_USER_ID
from ffb.sleeper_client import SleeperClient


def main() -> None:
    with SleeperClient() as client:
        for league in LEAGUES.values():
            info = client.get_league(league.league_id)
            rosters = client.get_rosters(league.league_id)
            users = client.get_users(league.league_id)

            user_by_id = {u["user_id"]: u for u in users}
            my_roster = next(r for r in rosters if r["owner_id"] == SLEEPER_USER_ID)

            print(f"--- {league.name} ({league.friend_group}) ---")
            print(f"  status: {info['status']}, teams: {info['total_rosters']}")
            print(f"  my roster_id: {my_roster['roster_id']}")
            print(f"  my display name: {user_by_id[SLEEPER_USER_ID]['display_name']}")
            print(f"  draft_id: {info['draft_id']}")
            print()


if __name__ == "__main__":
    main()
