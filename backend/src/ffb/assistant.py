"""Propose a roster move, then execute it only after an explicit confirmation.

The chat bot drives this. It exists because two rules have to hold at once:

  - The scheduled digest must never write. It reads, it reports, that is all.
  - A move asked for in conversation should actually happen, not be described
    forever.

Both hold if writes are only ever reachable through a second, separate command
carrying a code that the first command printed. `propose` computes a move and
parks it; `confirm` is the only place in this package that turns the
FFB_ALLOW_WRITES kill switch on, and it does so in its own process memory for
one mutation. Nothing is written to a .env file, so no scheduled job can
inherit the ability to write by accident.

A plan expires. An approval you gave ten minutes ago was for a roster state
that may no longer exist, and a stale confirmation is exactly how you drop the
wrong player.
"""

import argparse
import json
import os
import secrets
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from ffb.draft.strategy import FLEX_ELIGIBLE
from ffb.sleeper_auth import SleeperAuthClient, WRITES_ENV

# Where parked plans live. Deliberately outside the repo: the repo sits under
# ~/Desktop, which macOS privacy protection puts out of reach of background
# jobs (the same block that kept the digest from ever posting).
PENDING_ENV = "FFB_PENDING_FILE"
DEFAULT_PENDING = Path.home() / ".local" / "ffb" / "pending.json"

# How long an approval code stays good.
PLAN_TTL_SECONDS = 600

# Operations the bot may run at all. A plan naming anything else is refused at
# confirm time, so widening this list is a deliberate edit rather than
# something a prompt can talk the agent into.
ALLOWED_OPERATIONS = {
    "free_agent_move",
    "set_starters",
    "submit_waiver_claim",
    "propose_trade",
}


class PlanError(RuntimeError):
    pass


@dataclass
class Plan:
    code: str
    created: float
    operation: str
    league_id: str
    league_name: str
    roster_id: int
    kwargs: dict
    summary: list[str]

    @property
    def expired(self) -> bool:
        return time.time() - self.created > PLAN_TTL_SECONDS

    def describe(self) -> list[str]:
        age = int(time.time() - self.created)
        left = max(0, PLAN_TTL_SECONDS - age)
        return [
            *self.summary,
            "",
            f"PLAN: {self.operation} in {self.league_name}",
            f"Reply `confirm {self.code}` within {left // 60}m {left % 60}s.",
        ]


def _pending_path() -> Path:
    raw = os.getenv(PENDING_ENV, "").strip()
    return Path(raw) if raw else DEFAULT_PENDING


def save_plan(plan: Plan) -> None:
    path = _pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # One plan at a time on purpose. A queue of pending roster moves is a way
    # to confirm the wrong one.
    path.write_text(json.dumps(asdict(plan), indent=2))
    path.chmod(0o600)


def load_plan(code: str) -> Plan:
    path = _pending_path()
    if not path.exists():
        raise PlanError("No move is waiting for approval.")
    plan = Plan(**json.loads(path.read_text()))
    if not secrets.compare_digest(plan.code, code.strip().lower()):
        raise PlanError(f"Code {code!r} does not match the waiting plan.")
    if plan.expired:
        path.unlink(missing_ok=True)
        raise PlanError(
            f"That plan expired after {PLAN_TTL_SECONDS // 60} minutes. "
            "Ask again to get a fresh one."
        )
    if plan.operation not in ALLOWED_OPERATIONS:
        raise PlanError(f"Operation {plan.operation!r} is not allowed.")
    return plan


def clear_plan() -> None:
    _pending_path().unlink(missing_ok=True)


# -- building plans -------------------------------------------------------


def _context(league_key: str, sleeper_user_id: str):
    """Roster, league and the scored player pool, the way lineup.run gets them."""
    from ffb.api import _get_league, _pool_for
    from ffb.draft.run_sims import load_players
    from ffb.sleeper_client import SleeperClient

    league = _get_league(league_key)
    pool_path = _pool_for(league.ppr, league.num_teams)[0]
    if pool_path is None:
        raise PlanError("No player pool has been built yet.")

    with SleeperClient() as client:
        rosters = client.get_rosters(league.league_id)

    mine = next((r for r in rosters if r.get("owner_id") == sleeper_user_id), None)
    if mine is None:
        raise PlanError(f"No roster owned by {sleeper_user_id} in {league.name}.")
    return league, rosters, mine, load_players(pool_path)


def propose_fill_bench(league_key: str, sleeper_user_id: str) -> Plan:
    """Fill an open roster spot with the best free agent the pool knows about."""
    league, rosters, mine, players = _context(league_key, sleeper_user_id)

    held = len(mine.get("players") or [])
    capacity = len(league.roster_positions)
    if held >= capacity:
        raise PlanError(
            f"No open spot: {held} players on a {capacity}-man roster. "
            "Something has to be dropped first."
        )

    taken = {pid for r in rosters for pid in (r.get("players") or [])}
    free = [p for p in players if p.player_id not in taken and p.proj_points > 0]
    if not free:
        raise PlanError("The pool has no unrostered players with a projection.")
    best = max(free, key=lambda p: p.proj_points)

    return Plan(
        code=secrets.token_hex(2),
        created=time.time(),
        operation="free_agent_move",
        league_id=league.league_id,
        league_name=league.name,
        roster_id=int(mine["roster_id"]),
        kwargs={"adds": [best.player_id], "drops": []},
        summary=[
            f"Best free agent: {best.name} ({best.position}, "
            f"{best.proj_points:.0f} projected points)",
            f"  add  {best.player_id}  {best.name}",
            f"  drop none - {capacity - held} spot(s) open",
        ],
    )


def _starter_slots(roster_positions: list[str]) -> list[str]:
    return [s for s in roster_positions if s != "BN"]


def _fits(position: str, slot: str) -> bool:
    return slot == position or (slot == "FLEX" and position in FLEX_ELIGIBLE)


def propose_lineup(league_key: str, sleeper_user_id: str, week: int | None) -> Plan:
    """Bench anyone ruled out and start the best legal replacement.

    Only slots the advisor actually modelled are touched. K and DEF are left
    exactly as they are: the pool carries no rows whose ids match them, so any
    change there would be a guess.
    """
    from ffb.lineup import run as lineup_run

    league, _rosters, mine, _players = _context(league_key, sleeper_user_id)
    advice = lineup_run(league_key, sleeper_user_id, week, skip_injuries=False)
    if advice.get("status") != "ok":
        raise PlanError(advice.get("reason", "the lineup could not be evaluated"))

    starters = [str(s) for s in (mine.get("starters") or [])]
    slots = _starter_slots(league.roster_positions)
    if len(starters) != len(slots):
        raise PlanError(
            f"Sleeper reports {len(starters)} starters for {len(slots)} slots; "
            "refusing to guess the mapping."
        )

    sits = list(advice.get("sit") or [])
    starts = list(advice.get("start") or [])
    if not starts:
        raise PlanError("Your lineup already gets every modelled slot right.")

    new = list(starters)
    lines: list[str] = []
    for out_move, in_move in zip(sits, starts):
        out_id = str(out_move["player_id"])
        if out_id not in new:
            raise PlanError(f"{out_move['name']} is not in the current lineup.")
        i = new.index(out_id)
        # A swap is only safe if the incoming player is legal in the exact slot
        # the outgoing one vacates. Anything else would need the whole lineup
        # rebuilt, and a wrong guess sets an illegal lineup.
        if not _fits(in_move["position"], slots[i]):
            raise PlanError(
                f"{in_move['name']} ({in_move['position']}) cannot fill the "
                f"{slots[i]} slot that {out_move['name']} leaves. "
                "Set this one by hand."
            )
        new[i] = str(in_move["player_id"])
        lines.append(
            f"  {slots[i]:5} OUT {out_move['name']} ({out_move['reason']})"
        )
        lines.append(f"  {slots[i]:5} IN  {in_move['name']} ({in_move['reason']})")

    return Plan(
        code=secrets.token_hex(2),
        created=time.time(),
        operation="set_starters",
        league_id=league.league_id,
        league_name=league.name,
        roster_id=int(mine["roster_id"]),
        kwargs={"starters": new, "leg": week},
        summary=[
            f"Lineup change, +{advice['points_gained']:.1f} projected points:",
            *lines,
        ],
    )


# -- executing ------------------------------------------------------------


def execute(plan: Plan) -> dict:
    """Fire the parked mutation. The only writing path in this package."""
    # sleeper_auth gates on both the dry_run flag and the environment kill
    # switch. Setting it here, in this process, after a code matched, is what
    # keeps every other entry point read-only.
    os.environ[WRITES_ENV] = "1"
    try:
        with SleeperAuthClient(dry_run=False) as client:
            method = getattr(client, plan.operation)
            return method(plan.league_id, plan.roster_id, **plan.kwargs)
    finally:
        os.environ.pop(WRITES_ENV, None)


# -- CLI ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("fill-bench", "propose adding the best free agent to an open spot"),
        ("lineup", "propose benching anyone who will not play"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--league", required=True)
        p.add_argument("--user", default=os.getenv("FFB_SLEEPER_USER_ID", ""))
        if name == "lineup":
            p.add_argument("--week", type=int, default=None)

    c = sub.add_parser("confirm", help="execute the waiting plan")
    c.add_argument("code")

    sub.add_parser("show", help="print the waiting plan, if any")
    sub.add_parser("cancel", help="discard the waiting plan")

    args = parser.parse_args(argv)

    try:
        if args.command == "confirm":
            plan = load_plan(args.code)
            result = execute(plan)
            clear_plan()
            print(f"Done: {plan.operation} in {plan.league_name}")
            print(json.dumps(result, indent=2, default=str))
            return 0

        if args.command == "cancel":
            clear_plan()
            print("Discarded.")
            return 0

        if args.command == "show":
            path = _pending_path()
            if not path.exists():
                print("Nothing waiting for approval.")
                return 0
            plan = Plan(**json.loads(path.read_text()))
            print("\n".join(plan.describe()) if not plan.expired else "The waiting plan expired.")
            return 0

        if not args.user:
            raise PlanError("No Sleeper user id: pass --user or set FFB_SLEEPER_USER_ID.")

        if args.command == "fill-bench":
            plan = propose_fill_bench(args.league, args.user)
        else:
            plan = propose_lineup(args.league, args.user, args.week)

        save_plan(plan)
        print("\n".join(plan.describe()))
        return 0

    except PlanError as exc:
        print(f"Cannot do that: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
