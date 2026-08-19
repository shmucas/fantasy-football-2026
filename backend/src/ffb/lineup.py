"""Start/sit: the best legal lineup this roster can field, and how it differs
from the lineup currently set on Sleeper.

Read-only analysis. Nothing here sets a lineup on Sleeper.

The lineup itself is not new math: `select_starters` already fills starter and
flex slots by projection, so this module only decides who is eligible to be
filled in, and then diffs the result against the current starters.

Everything here is pure. Rosters arrive as lists of
`ffb.draft.strategy.Player`, availability signals arrive as plain dicts, so the
API endpoint and the CLI own all Sleeper calls. Run it with:

    uv run python -m ffb.lineup --league <league_id> --user <sleeper_user_id>

The same data caveats that shape `ffb.trades` apply, and for the same reasons:

  - The prebuilt pools carry no K rows at all, and their DEF rows use the ADP
    source's own ids ("ffc_1327"), which never match the team codes Sleeper
    puts on a roster ("IND"). Those slots cannot be reasoned about, so they are
    dropped by `trades.modelled_slots` and reported as unevaluated rather than
    silently left out of the answer.
  - A Sleeper roster holds players the pool has never heard of. They still
    occupy a starting slot, so a current starter outside the pool is reported
    as unvalued instead of being quietly treated as a zero.

An empty answer is the dangerous one here: "your lineup is already optimal" and
"we could not evaluate your roster at all" look identical if you only print
moves. So `advise` raises rather than returning an empty diff when the roster
cannot fill its modelled starting slots, the way `trades.find_trades` does.

Availability signals:

  - Bye weeks come from the committed nflverse schedule snapshot in data/nfl/,
    joined to a player through the committed sleeper_id -> team snapshot. No
    nflreadpy or polars, so this stays on the Vercel Function side of the line.
  - Injury status is not in the pools and not on a Sleeper roster. The only
    source is Sleeper's /players/nfl dump, which is roughly 5MB, so it is
    optional here: the CLI fetches it, the API endpoint does not and says so.
"""

import argparse
import json
from dataclasses import dataclass, field

from ffb.draft.sim import select_starters
from ffb.draft.strategy import FLEX_ELIGIBLE, Player
from ffb.trades import modelled_slots

# Sleeper injury_status values that mean the player will not play. Everything
# else, "Questionable" above all, is a note rather than a reason to bench.
OUT_STATUSES = {"Out", "IR", "PUP", "NA", "Sus", "Suspended", "COV", "DNR"}

# Starting slots the pools can never speak to: no K rows at all, and DEF rows
# carry the ADP source's ids rather than Sleeper's team codes.
POOL_BLIND_SLOTS = {"K", "DEF"}

# Below this many projected points a swap is rounding noise, not advice.
MIN_GAIN = 0.1


@dataclass(frozen=True)
class Move:
    """One player on one side of the start/sit diff."""

    player_id: str
    name: str
    position: str
    proj_points: float
    reason: str = ""


@dataclass(frozen=True)
class LineupAdvice:
    week: int | None
    start: list[Move]
    sit: list[Move]
    current_points: float
    optimal_points: float
    # Starting slots the pool cannot reason about (K, DEF), named so the caller
    # can say "we did not look at these" instead of omitting them.
    unevaluated_slots: list[str] = field(default_factory=list)
    # Current starters the pool has never heard of. They hold a slot but have
    # no projection, so they count as zero and are called out.
    unvalued_starters: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def points_gained(self) -> float:
        return self.optimal_points - self.current_points


def bye_week_by_team(schedule_rows: list[dict]) -> dict[str, int]:
    """team abbreviation -> its bye week, from regular season schedule rows.

    A bye is the regular season week in which a team plays nobody. Teams with
    no gap, or more than one, are left out rather than guessed at: a partial
    schedule snapshot would otherwise read as "everyone is on bye".
    """
    weeks: set[int] = set()
    played: dict[str, set[int]] = {}
    for row in schedule_rows:
        if row.get("game_type") not in (None, "", "REG"):
            continue
        week = int(row["week"])
        weeks.add(week)
        for key in ("home_team", "away_team"):
            team = row.get(key)
            if team:
                played.setdefault(team, set()).add(week)

    byes = {}
    for team, team_weeks in played.items():
        missing = sorted(weeks - team_weeks)
        if len(missing) == 1:
            byes[team] = missing[0]
    return byes


def unavailable_reason(
    player: Player,
    week: int | None,
    nfl_team: dict[str, str],
    byes: dict[str, int],
    injury: dict[str, str],
) -> str | None:
    """Why this player cannot be started this week, or None if they can.

    Both signals are optional: with empty dicts every player is available, and
    the lineup is chosen on projection alone.
    """
    status = (injury.get(player.player_id) or "").strip()
    if status in OUT_STATUSES:
        return f"listed {status}"
    team = nfl_team.get(player.player_id)
    if week is not None and team and byes.get(team) == week:
        return f"{team} bye in week {week}"
    return None


def eligible_players(
    players: list[Player],
    week: int | None,
    nfl_team: dict[str, str],
    byes: dict[str, int],
    injury: dict[str, str],
) -> tuple[list[Player], dict[str, str]]:
    """Split a roster into who can start and why the rest cannot."""
    ok: list[Player] = []
    blocked: dict[str, str] = {}
    for p in players:
        reason = unavailable_reason(p, week, nfl_team, byes, injury)
        if reason is None:
            ok.append(p)
        else:
            blocked[p.player_id] = reason
    return ok, blocked


def _modelled_positions(slots: list[str]) -> set[str]:
    positions = {s for s in slots if s not in ("FLEX", "BN")}
    if "FLEX" in slots:
        positions |= FLEX_ELIGIBLE
    return positions


def _move(p: Player, reason: str = "") -> Move:
    return Move(p.player_id, p.name, p.position, p.proj_points, reason)


def advise(
    players: list[Player],
    current_starter_ids: list[str],
    roster_positions: list[str],
    valued_positions: set[str],
    week: int | None = None,
    nfl_team: dict[str, str] | None = None,
    byes: dict[str, int] | None = None,
    injury: dict[str, str] | None = None,
) -> LineupAdvice:
    """The best lineup we can model, diffed against the one currently set.

    `players` is the roster reduced to the pool; `current_starter_ids` is
    Sleeper's `starters` list, which may contain "0" for an empty slot and ids
    the pool does not know.

    An unavailable current starter is scored at zero, not at their projection:
    that is what they will actually score, and it is the whole point of the
    comparison. Raises ValueError when the modelled starting slots cannot be
    filled, since an empty diff would read as "nothing to change".
    """
    nfl_team = nfl_team or {}
    byes = byes or {}
    injury = injury or {}

    if not valued_positions:
        # Not "nothing to change": the pool resolved nobody at all, which is
        # what an undrafted league looks like. Every slot would then read as
        # unmodellable for the wrong reason.
        raise ValueError(
            "The player pool resolved no players on any roster in this league, "
            "so no slot can be evaluated"
        )

    slots = modelled_slots(roster_positions, valued_positions)
    dropped = [s for s in roster_positions if s != "BN" and s not in slots]
    # K and DEF are structurally unmodellable: the pools carry no K rows and
    # their DEF ids never match Sleeper's. Any other dropped slot is this
    # league's own gap, so the two get said differently.
    blind = sorted({s for s in dropped if s in POOL_BLIND_SLOTS})
    gaps = sorted({s for s in dropped if s not in POOL_BLIND_SLOTS})
    needed = sum(1 for s in slots if s != "BN")

    startable, blocked = eligible_players(players, week, nfl_team, byes, injury)
    optimal = select_starters(startable, slots)
    if len(optimal) < needed:
        raise ValueError(
            f"Cannot fill the modelled slots {slots}: {len(players)} valued "
            f"players on the roster, {len(blocked)} of them unavailable, "
            f"{len(optimal)} of {needed} slots filled"
        )

    by_id = {p.player_id: p for p in players}
    positions = _modelled_positions(slots)
    current: list[Player] = []
    unvalued: list[str] = []
    for pid in current_starter_ids:
        if not pid or pid == "0":
            continue
        p = by_id.get(pid)
        if p is None:
            unvalued.append(pid)
        elif p.position in positions:
            current.append(p)

    current_points = sum(0.0 if p.player_id in blocked else p.proj_points for p in current)
    optimal_points = sum(p.proj_points for p in optimal)

    current_ids = {p.player_id for p in current}
    optimal_ids = {p.player_id for p in optimal}
    start = [
        _move(p, f"{p.proj_points:.0f} projected points")
        for p in optimal
        if p.player_id not in current_ids
    ]
    sit = [
        _move(p, blocked.get(p.player_id) or f"outscored, {p.proj_points:.0f} projected points")
        for p in current
        if p.player_id not in optimal_ids
    ]
    start.sort(key=lambda m: -m.proj_points)
    sit.sort(key=lambda m: m.proj_points)

    # A lineup has fixed slots, so telling someone to start a player without
    # naming who comes out is not advice. When fewer modelled slots are filled
    # than the league starts, the difference is being held by starters the pool
    # cannot value, so they are named as the ones to move.
    for pid in unvalued[: max(0, needed - len(current))]:
        sit.append(Move(pid, pid, "?", 0.0, "outside the pool, no projection"))

    notes = []
    if optimal_points - current_points < MIN_GAIN and not start:
        notes.append("Your lineup already gets every modelled slot right.")
    if blind:
        notes.append(
            f"Slots {', '.join(blind)} were not evaluated: the player pool has "
            f"no rows whose ids a Sleeper roster matches."
        )
    if gaps:
        notes.append(
            f"Slots {', '.join(gaps)} were not evaluated: the pool resolved no "
            f"player at that position on any roster in this league."
        )
    if unvalued:
        notes.append(
            f"{len(unvalued)} current starter(s) are outside the pool, so they "
            f"have no projection, were counted as zero, and the points gained "
            f"shown is therefore an upper bound."
        )
    if not injury:
        notes.append("Injury status was not consulted.")
    if week is None or not byes:
        notes.append("Bye weeks were not consulted.")

    return LineupAdvice(
        week=week,
        start=start,
        sit=sit,
        current_points=current_points,
        optimal_points=optimal_points,
        unevaluated_slots=sorted(set(dropped)),
        unvalued_starters=unvalued,
        notes=notes,
    )


def as_dict(advice: LineupAdvice) -> dict:
    """JSON-shaped advice, for the endpoint and for --json."""

    def side(moves: list[Move]) -> list[dict]:
        return [
            {
                "player_id": m.player_id,
                "name": m.name,
                "position": m.position,
                "proj_points": round(m.proj_points, 1),
                "reason": m.reason,
            }
            for m in moves
        ]

    return {
        "status": "ok",
        "week": advice.week,
        "start": side(advice.start),
        "sit": side(advice.sit),
        "current_points": round(advice.current_points, 1),
        "optimal_points": round(advice.optimal_points, 1),
        "points_gained": round(advice.points_gained, 1),
        "unevaluated_slots": advice.unevaluated_slots,
        "unvalued_starters": advice.unvalued_starters,
        "notes": advice.notes,
    }


# CLI. Sleeper calls live here, not in the functions above.


def season_byes(season: str) -> dict[str, int]:
    from ffb.nfldata.schedule import available_weeks, week_schedule

    rows = []
    for week in available_weeks(int(season)):
        rows.extend(week_schedule(int(season), week))
    return bye_week_by_team(rows)


def _injury_status(client) -> dict[str, str]:
    """sleeper_player_id -> injury status, from Sleeper's full player dump.

    Roughly 5MB, which is why this is CLI-only and never runs in a request.
    """
    return {
        pid: info.get("injury_status") or ""
        for pid, info in (client.get_players() or {}).items()
        if info.get("injury_status")
    }


def run(league_key: str, sleeper_user_id: str, week: int | None, skip_injuries: bool) -> dict:
    from ffb.api import _get_league, _pool_for
    from ffb.draft.run_sims import load_players
    from ffb.nfldata.ids import sleeper_team_lookup
    from ffb.sleeper_client import SleeperClient

    league = _get_league(league_key)
    pool_path = _pool_for(league.ppr, league.num_teams)[0]
    if pool_path is None:
        return {"status": "cannot_evaluate", "reason": "No player pool has been built yet"}

    with SleeperClient() as client:
        rosters = client.get_rosters(league.league_id)
        injury = {} if skip_injuries else _injury_status(client)

    mine = next((r for r in rosters if r.get("owner_id") == sleeper_user_id), None)
    if mine is None:
        return {
            "status": "cannot_evaluate",
            "league": league.name,
            "reason": f"No roster owned by {sleeper_user_id} in league {league_key}",
        }

    players = load_players(pool_path)
    by_id = {p.player_id: p for p in players}
    ids = mine.get("players") or []
    known = [by_id[pid] for pid in ids if pid in by_id]
    # Positions the pool actually resolves across the whole league, the same
    # test ffb.trades uses to decide which slots are worth modelling.
    valued_positions = {
        by_id[pid].position
        for r in rosters
        for pid in (r.get("players") or [])
        if pid in by_id
    }

    byes = season_byes(league.season)
    try:
        advice = advise(
            known,
            [str(s) for s in (mine.get("starters") or [])],
            league.roster_positions,
            valued_positions,
            week=week,
            nfl_team=sleeper_team_lookup(),
            byes=byes,
            injury=injury,
        )
    except ValueError as exc:
        return {
            "status": "cannot_evaluate",
            "reason": str(exc),
            "league": league.name,
            "roster_size": len(ids),
            "valued_players": len(known),
        }
    out = as_dict(advice)
    out["roster_id"] = mine.get("roster_id")
    out["league"] = league.name
    return out


def _print(result: dict) -> None:
    if result["status"] != "ok":
        print(f"Cannot evaluate this lineup: {result['reason']}")
        return
    print(f"{result['league']} roster {result['roster_id']}, week {result['week']}")
    print(
        f"Current modelled starters project {result['current_points']:.1f}; the "
        f"best legal lineup projects {result['optimal_points']:.1f} "
        f"(+{result['points_gained']:.1f})."
    )
    for move in result["sit"]:
        print(f"  BENCH  {move['name']:<24} {move['position']:<4} {move['reason']}")
    for move in result["start"]:
        print(f"  START  {move['name']:<24} {move['position']:<4} {move['reason']}")
    for note in result["notes"]:
        print(f"  note: {note}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Start/sit advice for one roster.")
    parser.add_argument("--league", required=True, help="Sleeper league id")
    parser.add_argument("--user", required=True, help="Sleeper user id who owns the roster")
    parser.add_argument("--week", type=int, help="NFL week, for bye week checks")
    parser.add_argument(
        "--no-injuries",
        action="store_true",
        help="skip Sleeper's 5MB player dump and ignore injury status",
    )
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args()

    result = run(args.league, args.user, args.week, args.no_injuries)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print(result)
    raise SystemExit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
