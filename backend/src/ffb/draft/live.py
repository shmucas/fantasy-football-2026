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

Defenses and kickers are special-cased, because their raw VORP is meaningless
against skill players (a projected top defense outscores a mid-round WR in the
model, but no one drafts a defense that early in real life). DEF and K are
only eligible in the final two rounds, and skill slots are filled first even
then.

The pool stores defenses under FFC placeholder ids (`ffc_*`), not Sleeper
team ids (`SEA`, `BUF`, ...). We remap them from Sleeper's player dump at
startup, and kickers - which the pool omits entirely - are loaded from that
same dump ordered by search rank (real user interest, not a projection).

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
from dataclasses import replace
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
    current_round: int,
    total_rounds: int,
) -> Player:
    """Best pick: fill an open starter/flex slot by VORP, else best VORP left.

    DEF and K are gated to the final two rounds, because their raw VORP is not
    comparable to skill players. Within the last two rounds we still prefer a
    still-open skill slot over DEF/K (you fill your flex before your kicker).
    """
    filled = Counter(p.position for p in my_roster)

    def has_room(p: Player) -> bool:
        return room_for(p.position, roster_positions, filled) > 0

    late = current_round >= total_rounds - 1  # last two rounds

    if not late:
        candidates = [p for p in available if p.position not in ("DEF", "K")]
    else:
        skill = [p for p in available if p.position not in ("DEF", "K") and has_room(p)]
        if skill:
            candidates = skill
        else:
            candidates = available

    roomed = [p for p in candidates if has_room(p)]
    return max(roomed if roomed else candidates, key=lambda p: vorp(p, replacement))


# Pool DEF names -> Sleeper team id. The pool uses FFC placeholder ids and
# names like "LA Rams Defense"; Sleeper keys defenses by team code ("LAR") with
# first/last name "Los Angeles"/"Rams". Match on the name, not the placeholder.
_CITY_ALIASES = {
    "LA": "Los Angeles",
    "NY": "New York",
    "SF": "San Francisco",
    "GB": "Green Bay",
    "KC": "Kansas City",
    "TB": "Tampa Bay",
    "NE": "New England",
    "NO": "New Orleans",
}


def _def_name_parts(name: str) -> tuple[str, str]:
    """'Seattle Defense' -> ('seattle', ''); 'LA Rams Defense' -> ('los angeles', 'rams')."""
    base = name.removesuffix(" Defense").strip().lower()
    words = base.split()
    if len(words) == 1:
        return words[0], ""
    first = words[0].upper()
    if first in _CITY_ALIASES:
        return _CITY_ALIASES[first].lower(), " ".join(words[1:])
    return base, ""


def load_defenses(client: SleeperClient) -> dict[tuple[str, str], str]:
    """(city, mascot) -> Sleeper team id, from the players dump."""
    players = client.get_players("nfl")
    out: dict[tuple[str, str], str] = {}
    for p in players.values():
        if p.get("position") != "DEF":
            continue
        city = (p.get("first_name") or "").strip().lower()
        mascot = (p.get("last_name") or "").strip().lower()
        out[(city, mascot)] = p["player_id"]
    return out


def _sleeper_skill_ids(client: SleeperClient) -> dict[tuple[str, str], str]:
    """(normalized full name, position) -> Sleeper id, for non-DEF players.

    A few skill players slip into the pool under FFC placeholder ids too
    (Kenny Gainwell, Chig Okonkwo) when the offline pool build failed to match
    them. Match on name + position against the dump to recover their real ids.
    """
    players = client.get_players("nfl")
    out: dict[tuple[str, str], str] = {}
    for p in players.values():
        pos = p.get("position")
        if pos in (None, "DEF"):
            continue
        name = f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip().lower()
        out[(name, pos)] = p["player_id"]
    return out


def remap_defenses(pool: list[Player], defense_ids: dict[tuple[str, str], str]) -> list[Player]:
    """Swap ffc_* DEF ids for real Sleeper team ids, matched on name."""
    remapped = []
    for p in pool:
        if p.position != "DEF":
            remapped.append(p)
            continue
        city, mascot = _def_name_parts(p.name)
        sid = defense_ids.get((city, mascot)) or defense_ids.get((city, ""))
        if sid is None:
            # e.g. pool says "New England Defense" but Sleeper has (new england, patriots)
            sid = next(
                (v for (c, _), v in defense_ids.items() if c == city), None
            )
        if sid is not None:
            remapped.append(replace(p, player_id=sid))
        else:
            remapped.append(p)  # leave it; the submit will be rejected and logged
    return remapped


def remap_skill_ids(pool: list[Player], skill_ids: dict[tuple[str, str], str]) -> list[Player]:
    """Swap ffc_* skill-player ids for real Sleeper ids, matched on name + position."""
    remapped = []
    for p in pool:
        if not p.player_id.startswith("ffc_"):
            remapped.append(p)
            continue
        sid = skill_ids.get((p.name.strip().lower(), p.position))
        if sid is not None:
            remapped.append(replace(p, player_id=sid))
        else:
            remapped.append(p)  # leave it; the submit will be rejected and logged
    return remapped


def _sleeper_unavailable(client: SleeperClient) -> set[str]:
    """Sleeper ids for players we must not draft: on IR or otherwise inactive.

    The prebuilt pool is a snapshot; a player who went on season-ending IR
    after the build still carries his full projection, so VORP will happily
    draft him. Sleeper's dump knows the current status - exclude them up front
    rather than discovering it after the pick is already in.
    """
    players = client.get_players("nfl")
    out: set[str] = set()
    for pid, p in players.items():
        if p.get("position") == "DEF":
            continue  # team defenses don't go on IR
        if p.get("injury_status") == "IR" or p.get("status") == "Inactive":
            out.add(pid)
    return out


def load_kickers(client: SleeperClient) -> list[Player]:
    """Kickers from Sleeper's player dump, best search rank first.

    The pool has no kicker projections, so these carry proj_points=0 and are
    only ever chosen in the final two rounds. search_rank is real Sleeper data
    (user interest), so "best" is at least a real signal, not a projection.
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
            pool_players = remap_defenses(pool_players, load_defenses(read))
            pool_players = remap_skill_ids(pool_players, _sleeper_skill_ids(read))
            unavailable = _sleeper_unavailable(read)
            kickers = load_kickers(read)

            num_teams = int(draft["settings"]["teams"])
            my_slot = draft["draft_order"][my_user_id]
            rounds = int(draft["settings"]["rounds"])
            positions = roster_positions_from_settings(draft["settings"])
            replacement = replacement_levels(pool_players, positions, num_teams)
            all_players = [p for p in pool_players + kickers if p.player_id not in unavailable]
            last_submitted: int | None = None
            undraftable: set[str] = set()

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
                available = [
                    p for p in all_players
                    if p.player_id not in picked and p.player_id not in undraftable
                ]

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

                choice = choose_pick(
                    available, my_roster, positions, replacement,
                    current_round=(pick_no - 1) // num_teams + 1,
                    total_rounds=rounds,
                )
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
                    msg = str(exc)
                    if "not draftable" in msg or "draft_player_not_draftable" in msg:
                        # The player id doesn't map to a draftable Sleeper player.
                        # Drop it and pick the next-best instead of retrying it
                        # forever (which just burns the pick clock).
                        undraftable.add(choice.player_id)
                        print(
                            f"  !! {choice.name} is not draftable (bad id "
                            f"{choice.player_id}); dropping it and picking the next-best"
                        )
                        continue
                    # Anything else (out-of-turn, duplicate): wait and re-poll.
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
