"""Set the optimal starting lineup on Sleeper, instead of only advising it.

The digest has always worked out the best lineup it can model. It could not act
on it, so a starter ruled out on a Sunday morning stayed in the lineup until
someone opened the app. This closes that gap.

    uv run python -m ffb.autolineup --dry-run

What it will not touch:

  - K and DEF. The pool carries no kicker rows and its defense ids never match
    Sleeper's, so `lineup.advise` refuses to reason about those slots. Whoever
    is in them stays in them, at the same index.
  - Anything at all unless FFB_ALLOW_WRITES is set. Without it this prints the
    lineup it would have set and changes nothing.

Lineups and not waivers or trades: writing a lineup is reversible and costs
nothing, while a waiver claim spends FAAB and a trade involves other people.
Neither of those can be taken back by rerunning the job.
"""

import argparse
import os

from ffb.alerts import discord
from ffb.draft.strategy import FLEX_ELIGIBLE
from ffb.lineup import POOL_BLIND_SLOTS
from ffb.lineup import run as advise_lineup
from ffb.sleeper_auth import SleeperAuthClient, writes_enabled

USER_ENV = "FFB_SLEEPER_USER_ID"
DEFAULT_USER = "1125887731814576128"
LEAGUES_ENV = "FFB_LINEUP_LEAGUES"

BENCH_SLOTS = {"BN", "IR"}


def starting_slots(roster_positions: list[str]) -> list[str]:
    """The slots Sleeper's `starters` array lines up with, in order.

    Confirmed against a live roster: `starters` holds one entry per non-bench
    slot in `roster_positions` order, so index 7 of the array is whoever is in
    slot 7. Everything below depends on that, which is why it is stated once.
    """
    return [s for s in roster_positions if s not in BENCH_SLOTS]


def ordered_starters(
    roster_positions: list[str],
    optimal: list[dict],
    current: list[str],
) -> list[str]:
    """The full starters array in slot order, ready to send to Sleeper.

    `optimal` is the modelled best lineup, which has no opinion about K or DEF.
    Those slots keep whoever is in them now, at the same index, so a lineup we
    can only partly reason about is still written back whole.

    Fixed slots are filled before FLEX, or the flex would take a receiver a WR
    slot still needed and leave that slot empty.
    """
    slots = starting_slots(roster_positions)
    if len(current) != len(slots):
        raise ValueError(
            f"Sleeper returned {len(current)} starters for {len(slots)} slots "
            f"({slots}). Refusing to guess which player belongs in which slot."
        )

    pool = sorted(optimal, key=lambda p: -p.get("proj_points", 0.0))
    by_id = {str(p["player_id"]): p for p in pool}
    used: set[str] = set()
    out: list[str | None] = [None] * len(slots)

    def fits(player: dict, slot: str) -> bool:
        if slot == "FLEX":
            return player.get("position") in FLEX_ELIGIBLE
        return player.get("position") == slot

    # Blind slots first: keep what is there, and make sure that player cannot
    # also be handed to a modelled slot.
    for i, slot in enumerate(slots):
        if slot in POOL_BLIND_SLOTS:
            out[i] = str(current[i])
            used.add(str(current[i]))

    # Then leave alone anyone who is already starting in a slot they still fit.
    # Everyone in `optimal` is going to start somewhere, so incumbency only
    # decides which slot they sit in and costs nothing. Reassigning them anyway
    # produced diffs like "RB: Taylor -> Henderson, RB: Henderson -> Taylor":
    # a write that changes the array, scores exactly the same, and makes the
    # log unreadable.
    for i, slot in enumerate(slots):
        pid = str(current[i])
        if out[i] is None and pid in by_id and pid not in used and fits(by_id[pid], slot):
            out[i] = pid
            used.add(pid)

    def take(slot: str) -> str | None:
        for p in pool:
            pid = str(p["player_id"])
            if pid not in used and fits(p, slot):
                used.add(pid)
                return pid
        return None

    for i, slot in enumerate(slots):
        if out[i] is None and slot != "FLEX":
            out[i] = take(slot)

    for i, slot in enumerate(slots):
        if out[i] is None and slot == "FLEX":
            out[i] = take("FLEX")

    missing = [slots[i] for i, v in enumerate(out) if v is None]
    if missing:
        raise ValueError(
            f"No player available for slots {missing}. Refusing to set a partial lineup."
        )
    return [str(v) for v in out]


def league_ids() -> list[str]:
    """The same leagues the digest reports on.

    Not ffb.leagues.LEAGUES: the id it holds for the college league is stale and
    Sleeper 404s on it. The digest's list is the one that is kept current.
    """
    raw = os.getenv(LEAGUES_ENV, "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    from ffb.alerts.digest import DEFAULT_LEAGUES

    return list(DEFAULT_LEAGUES)


def current_week() -> int | None:
    """The week Sleeper thinks it is.

    This matters more than it looks: ffb.lineup only checks bye weeks when it
    is given a week, so running without one will happily start a player whose
    team is not playing. Returning None on failure keeps the job alive, and the
    caller says so rather than pretending byes were considered.
    """
    from ffb.sleeper_client import SleeperClient

    try:
        with SleeperClient() as client:
            return int(client.get_state()["week"])
    except Exception:
        return None


def plan_for(league_id: str, user_id: str, week: int | None) -> dict:
    """What this league's lineup should be, and whether that differs from now."""
    from ffb.pool import get_league
    from ffb.sleeper_client import SleeperClient

    advice = advise_lineup(league_id, user_id, week, skip_injuries=False)
    if advice.get("status") != "ok":
        return {"league_id": league_id, "status": advice.get("status", "error"),
                "reason": advice.get("reason", "unknown"),
                "league": advice.get("league", league_id)}

    league = get_league(league_id)
    with SleeperClient() as client:
        rosters = client.get_rosters(league_id)
    mine = next((r for r in rosters if r.get("owner_id") == user_id), None)
    if mine is None:
        return {"league_id": league_id, "status": "cannot_evaluate",
                "reason": f"no roster owned by {user_id}", "league": league.name}

    current = [str(s) for s in (mine.get("starters") or [])]
    wanted = ordered_starters(league.roster_positions, advice["optimal"], current)
    return {
        "league_id": league_id,
        "league": league.name,
        "status": "ok",
        "roster_id": mine.get("roster_id"),
        "slots": starting_slots(league.roster_positions),
        "current": current,
        "wanted": wanted,
        "changed": wanted != current,
        "points_gained": advice.get("points_gained", 0.0),
        # Names for both sides of every swap. Mapping only the incoming players
        # left the outgoing ones showing as raw Sleeper ids in the log.
        "names": {
            str(m["player_id"]): m["name"]
            for key in ("optimal", "start", "sit")
            for m in advice.get(key, [])
        },
    }


def describe(plan: dict) -> list[str]:
    """What happened, named rather than numbered: a diff of ids is unreadable
    three days later when you are working out what the job did."""
    if plan["status"] != "ok":
        return [f"**{plan['league']}** - could not set lineup: {plan['reason']}"]
    if not plan["changed"]:
        return [f"**{plan['league']}** - lineup already correct, left alone."]

    name = lambda pid: plan["names"].get(pid, pid)
    lines = [f"**{plan['league']}** - lineup set (+{plan['points_gained']:.0f} projected)"]
    for slot, was, now in zip(plan["slots"], plan["current"], plan["wanted"]):
        if was != now:
            lines.append(f"  {slot}: {name(was)} -> {name(now)}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default=os.getenv(USER_ENV, DEFAULT_USER))
    parser.add_argument("--week", type=int, default=None)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="work out the lineup and print it, changing nothing",
    )
    args = parser.parse_args()

    week = args.week if args.week is not None else current_week()
    if week is None:
        # Without a week, a player on bye looks startable. Say so loudly rather
        # than quietly setting a lineup that benches nobody for their bye.
        print("::warning::Could not read the current NFL week, so bye weeks "
              "were not consulted.")
    else:
        print(f"week {week}")

    blocks: list[str] = []
    failed = False
    for league_id in league_ids():
        try:
            plan = plan_for(league_id, args.user, week)
        except Exception as exc:
            # A league we cannot evaluate must not stop the others, and must not
            # pass for success either.
            blocks.append(f"**{league_id}** - lineup errored: {exc}")
            failed = True
            continue

        if plan["status"] != "ok":
            failed = True
            blocks.extend(describe(plan))
            continue

        # Always log both sides, every run. If this ever sets the wrong lineup
        # the log is the only way to see what it did and put it back.
        print(f"{plan['league']}: slots   {plan['slots']}")
        print(f"{plan['league']}: current {plan['current']}")
        print(f"{plan['league']}: wanted  {plan['wanted']}")

        if not plan["changed"]:
            blocks.extend(describe(plan))
            continue

        if args.dry_run or not writes_enabled():
            why = "dry run" if args.dry_run else "writes are off"
            print(f"{plan['league']}: not sending ({why})")
            # Name the moves even when not sending. "Would set a lineup" tells
            # you nothing you can check; the swaps are the thing to look at.
            lines = describe(plan)
            lines[0] = lines[0].replace("lineup set", f"would set lineup ({why})")
            blocks.extend(lines)
            continue

        with SleeperAuthClient() as auth:
            auth.set_starters(plan["league_id"], plan["roster_id"], plan["wanted"])
        print(f"{plan['league']}: sent")
        blocks.extend(describe(plan))

    message = "\n".join(blocks)
    print("\n" + (message or "(nothing to report)"))
    if message and not args.dry_run and discord.configured():
        discord.post(f"__**Lineup**__\n{message}")
        print("\n(posted to Discord)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
