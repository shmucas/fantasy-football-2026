"""Waiver wire: who is worth claiming, and whether they fill a hole I have.

The one decision in this project with a hard deadline. Claims process early
Wednesday morning, so advice that arrives Wednesday afternoon is worthless.
That is why the digest runs this on Tuesday evening.

Read-only. Submitting a claim goes through ffb.sleeper_auth, which refuses to
fire unless FFB_ALLOW_WRITES is set.

Ranking reuses the draft machinery rather than inventing new math: `vorp`
against `replacement_levels` computed over the whole pool, so a pickup is
measured against the last startable player at their position league-wide
rather than against whoever happens to be left on the wire.

One data caveat, the same one that shapes ffb.trades: the pools carry no K
rows at all, and their DEF rows use the ADP source's ids ("ffc_1327"), which
never match the team codes Sleeper puts on a roster ("SEA"). A defense would
therefore never look rostered and every run would recommend all 22 of them.
Both positions are excluded, and the report says so rather than quietly
returning a list that is wrong at the top.
"""

import argparse
import json
import sys
from collections import Counter

from ffb.draft.run_sims import load_players
from ffb.draft.strategy import replacement_levels, vorp
from ffb.leagues import SLEEPER_USER_ID
from ffb.pool import LeagueLookupError, get_league, pool_for
from ffb.sleeper_client import SleeperClient

# Positions the pool cannot reconcile against a Sleeper roster. See the module
# docstring: including them would put wrong answers at the top of the list.
UNRECONCILED = {"DEF", "K"}

# How many pickups to rank. More than this is not a shortlist any more.
DEFAULT_LIMIT = 8

# A pickup has to clear this much value over replacement to be worth a claim.
# Below it you are churning the bottom of your bench for noise.
MIN_VORP = 1.0

STATUS_OK = "ok"
STATUS_NONE = "no_pickups"
STATUS_NO_POOL = "no_pool"
STATUS_FAILED = "cannot_evaluate"


def starting_needs(roster_positions: list[str]) -> Counter:
    """How many bodies each named starting slot wants.

    FLEX and BN are skipped: they take several positions, so being "short" at
    one of them is not a fact about any single position.
    """
    needs: Counter = Counter()
    for slot in roster_positions:
        if slot in ("BN", "FLEX") or slot in UNRECONCILED:
            continue
        needs[slot] += 1
    return needs


def rank_pickups(
    available: list,
    my_players: list,
    replacement: dict[str, float],
    roster_positions: list[str],
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Best available players, best first, annotated with why.

    Pure: rosters and pool come in as `ffb.draft.strategy.Player` lists, so
    the caller owns every Sleeper call.
    """
    needs = starting_needs(roster_positions)
    have: Counter = Counter(p.position for p in my_players)

    worth_it = [
        p
        for p in available
        if p.position not in UNRECONCILED and vorp(p, replacement) > MIN_VORP
    ]
    ranked = sorted(worth_it, key=lambda p: -vorp(p, replacement))[:limit]

    out = []
    for p in ranked:
        value = vorp(p, replacement)
        short = have[p.position] < needs.get(p.position, 0)
        reason = (
            f"{p.proj_points:.0f} proj pts, {value:.0f} above the last startable "
            f"{p.position} in this league"
        )
        if short:
            reason += (
                f". You are short at {p.position} "
                f"({have[p.position]}/{needs[p.position]} starters)"
            )
        out.append(
            {
                "player_id": p.player_id,
                "name": p.name,
                "position": p.position,
                "proj_points": round(p.proj_points, 1),
                "vorp": round(value, 1),
                "fills_need": short,
                "reason": reason,
            }
        )
    return out


def report_for(league: str, user_id: str, limit: int = DEFAULT_LIMIT) -> dict:
    """The full report as data, for callers that are not the command line.

    Mirrors ffb.cli_trades.report_for: one builder, so the digest can never
    disagree with the CLI about whether the wire was empty or unreadable.
    """
    try:
        config = get_league(league)
    except LeagueLookupError as exc:
        return {"league_id": league, "status": STATUS_FAILED, "reason": str(exc), "pickups": []}

    base = {"league": config.name, "league_id": config.league_id}
    pool_path = pool_for(config.ppr, config.num_teams)[0]
    if pool_path is None:
        return {**base, "status": STATUS_NO_POOL, "reason": "no player pool built", "pickups": []}

    try:
        with SleeperClient() as client:
            rosters = client.get_rosters(config.league_id)
    except Exception as exc:  # noqa: BLE001 - "could not look" must be sayable
        return {**base, "status": STATUS_FAILED, "reason": f"{type(exc).__name__}: {exc}", "pickups": []}

    if not any(r.get("players") for r in rosters):
        return {
            **base,
            "status": STATUS_FAILED,
            "reason": "no rosters yet, the league has not drafted",
            "pickups": [],
        }

    # Owned means owned, IR and taxi included: a stashed player is not on the
    # wire even though he does not count against the active roster.
    owned = {pid for r in rosters for pid in (r.get("players") or [])}
    mine = next((r for r in rosters if r.get("owner_id") == user_id), None)
    if mine is None:
        return {
            **base,
            "status": STATUS_FAILED,
            "reason": f"no roster owned by {user_id} in this league",
            "pickups": [],
        }

    players = load_players(pool_path)
    by_id = {p.player_id: p for p in players}
    # Replacement level comes from the whole pool, rostered or not: it stands
    # for who is startable league-wide, not who happens to be left over.
    replacement = replacement_levels(players, config.roster_positions, config.num_teams)

    my_players = [by_id[pid] for pid in (mine.get("players") or []) if pid in by_id]
    available = [p for p in players if p.player_id not in owned]
    considered = [p for p in available if p.position not in UNRECONCILED]
    pickups = rank_pickups(
        available, my_players, replacement, config.roster_positions, limit
    )

    return {
        **base,
        "status": STATUS_OK if pickups else STATUS_NONE,
        "pickups": pickups,
        # How many the wire actually offered. Without this, "nothing worth
        # claiming" and "we could not see the wire" read identically.
        "considered": len(considered),
        "faab": config.faab,
        "excluded_positions": sorted(UNRECONCILED),
    }


def pickup_lines(report: dict) -> list[str]:
    """One report as human-readable lines. Shared with the Discord digest."""
    league = report.get("league") or report.get("league_id") or "league"
    if report["status"] == STATUS_FAILED:
        return [f"**{league}** - could not check waivers: {report.get('reason', 'unknown')}"]
    if report["status"] == STATUS_NO_POOL:
        return [f"**{league}** - no player pool built, so the wire cannot be ranked."]
    if report["status"] == STATUS_NONE:
        return [
            f"**{league}** - none of the {report.get('considered', '?')} players "
            "on the wire beat what you already have, so this is a real answer."
        ]

    pickups = report["pickups"]
    lines = [f"**{league}** - {len(pickups)} worth a claim:"]
    for p in pickups:
        flag = " (fills a need)" if p["fills_need"] else ""
        lines.append(f"  {p['name']} ({p['position']}) +{p['vorp']:.0f}{flag} - {p['reason']}")
    return lines


def render_text(report: dict) -> str:
    return "\n".join(pickup_lines(report))


def exit_code_for(report: dict) -> int:
    """0 when the answer is real, 2 when we could not look."""
    return 0 if report["status"] in (STATUS_OK, STATUS_NONE) else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("league", help="Sleeper league id, or a key from ffb.leagues")
    parser.add_argument("--user", default=SLEEPER_USER_ID, help="Sleeper user id")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    report = report_for(args.league, args.user, args.limit)
    print(json.dumps(report, indent=2) if args.json else render_text(report))
    sys.exit(exit_code_for(report))


if __name__ == "__main__":
    main()
