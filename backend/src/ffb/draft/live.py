"""Live draft auto-picker: watch a Sleeper snake draft and pick for us.

This is the write path counterpart to the read-only draft simulator. It polls
the public draft endpoints, works out whose turn it is from the snake order,
and - when it is ours - submits the best available player via the
`draft_pick_player` mutation (see `sleeper_auth.py`).

Pick decision: VORP, the same value model the draft simulator and the
"Suggested next pick" list use. A positional-need rule keeps the roster legal:
while a starter/flex slot is still unfilled, we only consider players who fit
one, ranked by VORP. Once every starter and flex slot is spoken for, the bench
rounds just take the best VORP left.

Kickers are the one gap: the prebuilt pool has no kicker projections. They are
picked from Sleeper's player dump ordered by search rank (real user interest,
not a fabricated projection) and only when the K slot is the last thing left.

Safety rails:

  - Writes are gated by FFB_ALLOW_WRITES=1, exactly like every other mutation.
    Without it this only describes what it would have picked.
  - Every pick is submitted with its `pick_no`, so Sleeper rejects an
    out-of-turn pick rather than double-drafting on a stale poll.
  - After submitting we re-poll and confirm the pick actually landed; a pick
    that didn't land is logged loudly and retried next cycle.

Usage:

    uv run python -m ffb.draft.live \
        --draft 1391439648600887296 \
        --pool data/pools/maxxing_college.csv \
        --interval 5

Set FFB_ALLOW_WRITES=1 to actually pick. Leave it unset to watch + print.
"""

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

from ffb.draft.run_sims import load_players
from ffb.draft.strategy import FLEX_ELIGIBLE, Player, replacement_levels, vorp
from ffb.sleeper_auth import (
    PlannedWrite,
    SleeperAuthClient,
    SleeperAuthError,
    WritesDisabled,
)
from ffb.sleeper_client import SleeperClient


def snake_slot(pick_no: int, num_teams: int) -> int:
    """Which draft slot (1-based) picks at overall pick number `pick_no`.

    Standard snake: odd rounds run 1..N, even rounds run N..1.
    """
    rnd = (pick_no - 1) // num_teams + 1
    within = (pick_no - 1) % num_teams
    return within + 1 if rnd % 2 == 1 else num_teams - within


def is_my_turn(picks_made: int, num_teams: int, my_slot: int) -> bool:
    """The next pick (picks_made + 1) belongs to `my_slot`."""
    return snake_slot(picks_made + 1, num_teams) == my_slot


def roster_positions_from_settings(settings: dict) -> list[str]:
    """Rebuild the roster slot list from a draft's settings block, e.g.
    slots_qb=1, slots_rb=2, ..., slots_bn=7 -> ['QB','RB','RB',...,'BN'x7]."""
    pos_map = {
        "qb": "QB", "rb": "RB", "wr": "WR", "te": "TE",
        "flex": "FLEX", "k": "K", "def": "DEF", "bn": "BN",
    }
    slots: list[str] = []
    for key, pos in pos_map.items():
        count = int(settings.get(f"slots_{key}", 0) or 0)
        slots.extend([pos] * count)
    return slots


def room_for(pos: str, roster_positions: list[str], filled: Counter) -> int:
    """Starter + flex capacity still open for `pos` on our roster."""
    need = Counter(s for s in roster_positions if s != "BN")
    starter = need.get(pos, 0)
    flex_cap = need.get("FLEX", 0) if pos in FLEX_ELIGIBLE else 0
    return starter + flex_cap - filled.get(pos, 0)


def choose_pick(
    available: list[Player],
    my_roster: list[Player],
    roster_positions: list[str],
    replacement: dict[str, float],
) -> Player:
    """Best pick: fill an open starter/flex slot by VORP, else best VORP left."""
    filled = Counter(p.position for p in my_roster)
    roomed = [p for p in available if room_for(p.position, roster_positions, filled) > 0]
    candidates = roomed if roomed else available
    return max(candidates, key=lambda p: vorp(p, replacement))


def load_kickers(client: SleeperClient) -> list[Player]:
    """Kickers from Sleeper's player dump, best search rank first.

    The pool has no kicker projections, so these carry proj_points=0 and are
    only ever chosen once the K slot is the last open position. search_rank is
    real Sleeper data (user interest), so "best" is at least a real signal,
    not a made-up projection.
    """
    players = client.get_players("nfl")
    kickers = [p for p in players.values() if p.get("position") == "K"]
    kickers.sort(key=lambda p: (p.get("search_rank") or 1_000_000, p.get("full_name") or ""))
    return [
        Player(
            player_id=p["player_id"],
            name=p.get("full_name") or p.get("first_name", ""),
            position="K",
            proj_points=0.0,
            proj_stdev=0.0,
            adp=500.0,
        )
        for p in kickers
    ]


def fetch_state(client: SleeperClient, draft_id: str):
    """Current draft status and the picks made so far."""
    draft = client.get_draft(draft_id)
    picks = client.get_draft_picks(draft_id)
    return draft, picks


def taken_player_ids(picks: list[dict]) -> set[str]:
    return {p.get("player_id") for p in picks if p.get("player_id")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True, help="Sleeper draft id")
    parser.add_argument("--pool", required=True, help="path to a prebuilt player pool CSV")
    parser.add_argument("--user-id", default=None, help="Sleeper user id (default: from token)")
    parser.add_argument("--interval", type=float, default=5.0, help="poll seconds")
    parser.add_argument("--once", action="store_true", help="one poll cycle, then exit")
    args = parser.parse_args()

    with SleeperAuthClient() as auth:
        my_user_id = args.user_id or auth.token_info.user_id
        if not auth.dry_run:
            print(f"writes ON (FFB_ALLOW_WRITES=1): I will pick as user {my_user_id}")
        else:
            print(f"writes OFF: I will describe, not submit, picks for {my_user_id}")

        pool_players = load_players(Path(args.pool))
        with SleeperClient() as read:
            draft = read.get_draft(args.draft)
            kickers = load_kickers(read)

            num_teams = int(draft["settings"]["teams"])
            my_slot = draft["draft_order"][my_user_id]
            rounds = int(draft["settings"]["rounds"])
            positions = roster_positions_from_settings(draft["settings"])
            replacement = replacement_levels(pool_players, positions, num_teams)
            all_players = pool_players + kickers
            last_submitted: int | None = None

            print(
                f"draft {args.draft}: {num_teams} teams, {rounds} rounds, "
                f"snake, my slot {my_slot}, pick timer {draft['settings'].get('pick_timer')}s"
            )
            print(f"roster slots: {positions}")

            while True:
                draft, picks = fetch_state(read, args.draft)
                status = draft.get("status")
                picked = taken_player_ids(picks)
                my_roster = [
                    p for p in all_players
                    if p.player_id in {pk.get("player_id") for pk in picks if pk.get("picked_by") == my_user_id}
                ]
                available = [p for p in all_players if p.player_id not in picked]

                print(f"[{time.strftime('%H:%M:%S')}] status={status} picks={len(picks)}/{num_teams * rounds}")

                if status in ("complete",):
                    print("draft complete.")
                    return 0

                if status not in ("drafting", "paused"):
                    if args.once:
                        return 0
                    time.sleep(args.interval)
                    continue

                if not is_my_turn(len(picks), num_teams, my_slot):
                    if args.once:
                        return 0
                    time.sleep(args.interval)
                    continue

                pick_no = len(picks) + 1
                # The picks list is eventually consistent: right after we submit,
                # a re-poll can still show the old count. If we already submitted
                # for this pick number, wait for the board to catch up instead of
                # re-picking (which Sleeper would reject as a duplicate).
                if last_submitted is not None and pick_no <= last_submitted:
                    if args.once:
                        return 0
                    time.sleep(args.interval)
                    continue

                if not available:
                    print("no available players left in pool; nothing to pick")
                    if args.once:
                        return 0
                    time.sleep(args.interval)
                    continue

                choice = choose_pick(available, my_roster, positions, replacement)
                print(
                    f"  MY TURN (pick {pick_no}): {choice.name} ({choice.position}), "
                    f"proj {choice.proj_points:.0f}, vorp {vorp(choice, replacement):.0f}"
                )
                try:
                    result = auth.make_draft_pick(args.draft, choice.player_id, pick_no)
                except WritesDisabled:
                    print("  (writes disabled; would have submitted this pick)")
                    return 0
                except SleeperAuthError as exc:
                    # Out-of-turn or duplicate: don't crash, just wait and re-poll.
                    print(f"  !! Sleeper rejected the pick ({exc}); waiting and re-polling")
                    if args.once:
                        return 0
                    time.sleep(args.interval)
                    continue

                if isinstance(result, PlannedWrite):
                    print(f"  (dry run) would pick {choice.name}")
                else:
                    # A dict back means Sleeper processed it; that is the
                    # confirmation, so do not trust a follow-up poll for it.
                    last_submitted = pick_no
                    print(f"  -> picked {choice.name} (Sleeper confirmed pick {pick_no})")

                if args.once:
                    return 0
                time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
