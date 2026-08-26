"""Trade finder on the command line, for agents and cron jobs.

Same analysis the /api/leagues/{key}/trades endpoint runs, without the HTTP
layer:

    uv run python -m ffb.cli_trades 1391439647548129280
    uv run python -m ffb.cli_trades miller_league_hs --json --limit 5

Read-only. Nothing here proposes or sends a trade.

The league assembly (fetch rosters, resolve them against the prebuilt pool,
compute replacement levels once over the whole pool) is imported from
ffb.api rather than copied, so the CLI and the web app cannot drift apart on
slot normalization - which is exactly where a roster wrongly reads as
infeasible.

An empty result is a legitimate answer, so this reports which kind of empty it
is: how many opposing rosters were actually evaluated and how many were
skipped because the pool could not fill their modelled slots. "Evaluated 11,
found nothing" is a real negative. "Evaluated 0" is a data problem.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

from ffb.draft.run_sims import load_players
from ffb.draft.strategy import replacement_levels
from ffb.leagues import LEAGUES, SLEEPER_USER_ID, LeagueConfig
from ffb.sleeper_client import SleeperClient
from ffb.trades import (
    TeamRoster,
    active_player_ids,
    find_trades,
    is_feasible,
    modelled_slots,
    rank_for_me,
    shopping_signal,
)

# Statuses a machine reader can branch on without parsing prose.
STATUS_OK = "ok"
STATUS_NO_TRADES = "no_trades_found"
STATUS_NOTHING_EVALUATED = "nothing_evaluated"
STATUS_MY_ROSTER_INFEASIBLE = "my_roster_infeasible"
STATUS_ROSTERS_EMPTY = "rosters_empty"


@dataclass
class Assembly:
    """Everything find_trades needs, plus what the report has to explain."""

    league: LeagueConfig
    me: TeamRoster
    others: list[TeamRoster]
    replacement: dict[str, float]
    valued_positions: set[str]
    slots: list[str]
    approx_pool: bool
    # Pool players by Sleeper id, so a caller holding raw ids from a
    # transaction can resolve them without loading the pool a second time.
    by_id: dict[str, object] = field(default_factory=dict)

    @property
    def feasible_others(self) -> list[TeamRoster]:
        return [
            t
            for t in self.others
            if is_feasible(
                t.players, t.unknown_count, self.slots, self.league.roster_positions
            )
        ]


def resolve_league_id(league: str) -> str:
    """Accept either a configured league key or a raw Sleeper league id."""
    config = LEAGUES.get(league)
    return config.league_id if config else league


def assemble(league_id: str, sleeper_user_id: str) -> Assembly:
    """Fetch and value one league's rosters. Raises SystemExit on user error."""
    # Imported lazily: ffb.api pulls in FastAPI and the sim stack, which a
    # cron job should not pay for until it is actually going to run.
    from ffb.api import _get_league, _pool_for

    league = _get_league(league_id)
    pool_path, approx = _pool_for(league.ppr, league.num_teams)
    if pool_path is None:
        raise SystemExit("No player pool has been built yet.")

    with SleeperClient() as client:
        rosters = client.get_rosters(league.league_id)
        users = client.get_users(league.league_id)

    my_roster = next((r for r in rosters if r["owner_id"] == sleeper_user_id), None)
    if my_roster is None:
        raise SystemExit(
            f"No roster owned by {sleeper_user_id} in {league.name}. "
            "Pass --user with the Sleeper user id that owns the team."
        )

    players = load_players(pool_path)
    by_id = {p.player_id: p for p in players}
    replacement = replacement_levels(players, league.roster_positions, league.num_teams)
    name_by_owner = {u["user_id"]: u.get("display_name") or "Unknown" for u in users}

    def to_team(raw: dict) -> TeamRoster:
        ids = active_player_ids(raw)
        known = [by_id[pid] for pid in ids if pid in by_id]
        return TeamRoster(
            roster_id=raw["roster_id"],
            owner_name=name_by_owner.get(raw.get("owner_id"), "Unknown"),
            players=known,
            unknown_count=len(ids) - len(known),
        )

    me = to_team(my_roster)
    others = [to_team(r) for r in rosters if r["roster_id"] != my_roster["roster_id"]]
    valued_positions = {p.position for t in [me, *others] for p in t.players}
    return Assembly(
        league=league,
        me=me,
        others=others,
        replacement=replacement,
        valued_positions=valued_positions,
        slots=modelled_slots(league.roster_positions, valued_positions),
        approx_pool=approx,
        by_id=by_id,
    )


def load_shopping(league_id: str) -> tuple[dict, str]:
    """Optional enrichment: how often each manager has offered each player.

    Needs SLEEPER_TOKEN, since only Sleeper's private GraphQL returns the
    proposed / rejected / cancelled offers. Every failure is soft: the finder
    works identically on public data, so we return an empty signal and a line
    saying why.
    """
    if not os.getenv("SLEEPER_TOKEN", "").strip():
        return {}, "no SLEEPER_TOKEN set"
    try:
        from ffb.sleeper_auth import SleeperAuthClient

        with SleeperAuthClient() as client:
            trades = client.get_trades(
                league_id,
                statuses=["complete", "proposed", "rejected", "cancelled", "vetoed"],
            )
    except Exception as exc:  # noqa: BLE001 - enrichment must never be fatal
        return {}, f"{type(exc).__name__}: {exc}"

    # The GraphQL rows carry `type` already, but the server-side type filter
    # means anything returned is a trade; default it so a missing field cannot
    # silently zero the whole signal.
    rows = [{**t, "type": t.get("type") or "trade"} for t in trades]
    return shopping_signal(rows), f"{len(rows)} trade(s) seen"


def _side(players) -> list[dict]:
    return [
        {
            "player_id": p.player_id,
            "name": p.name,
            "position": p.position,
            "proj_points": round(p.proj_points, 1),
        }
        for p in players
    ]


def idea_dict(idea) -> dict:
    return {
        "roster_id": idea.roster_id,
        "owner_name": idea.owner_name,
        "send": _side(idea.send),
        "receive": _side(idea.receive),
        "my_surplus": round(idea.my_surplus, 1),
        "their_surplus": round(idea.their_surplus, 1),
        "joint_surplus": round(idea.joint_surplus, 1),
        "shopping_hits": idea.shopping_hits,
        "notes": list(idea.notes),
    }


def status_for(evaluated: int, ideas: list) -> str:
    """Which kind of answer this is, so an empty list is never ambiguous."""
    if evaluated == 0:
        return STATUS_NOTHING_EVALUATED
    return STATUS_OK if ideas else STATUS_NO_TRADES


def build_report(
    league_name: str,
    league_id: str,
    me: TeamRoster,
    total_others: int,
    evaluated: int,
    ideas: list,
    limit: int,
    shopping_note: str,
    shopping_hits_total: int,
    approx_pool: bool,
) -> dict:
    status = status_for(evaluated, ideas)
    return {
        "status": status,
        "league": league_name,
        "league_id": league_id,
        "my_roster_id": me.roster_id,
        "my_valued_players": len(me.players),
        "my_unvalued_players": me.unknown_count,
        "rosters_total": total_others,
        "rosters_evaluated": evaluated,
        "rosters_skipped_infeasible": total_others - evaluated,
        "approx_pool": approx_pool,
        "shopping_signal": shopping_note,
        "shopping_hits": shopping_hits_total,
        "ideas_found": len(ideas),
        "by_joint_surplus": [idea_dict(t) for t in ideas[:limit]],
        "by_my_surplus": [idea_dict(t) for t in rank_for_me(ideas)[:limit]],
        "message": explain(status, evaluated, total_others, len(ideas)),
    }


def explain(status: str, evaluated: int, total: int, found: int) -> str:
    if status == STATUS_ROSTERS_EMPTY:
        return (
            "Every roster in the league is empty, so there is nothing to "
            "trade. The league has not drafted yet."
        )
    if status == STATUS_MY_ROSTER_INFEASIBLE:
        return "Your own roster could not be filed against the modelled slots."
    if status == STATUS_NOTHING_EVALUATED:
        return (
            f"No trades, but none of the {total} other rosters could be "
            "evaluated: every one was skipped as infeasible. That is a data "
            "problem, not a clean negative answer."
        )
    if status == STATUS_NO_TRADES:
        return (
            f"No package helps both sides. {evaluated} of {total} rosters were "
            "evaluated, so this is a real negative answer."
        )
    return f"{found} idea(s) across {evaluated} of {total} rosters."


def _fmt_side(players) -> str:
    return ", ".join(f"{p['name']} ({p['position']})" for p in players)


def render_text(report: dict, limit: int) -> str:
    lines = [
        f"{report['league']} [{report['league_id']}] roster {report['my_roster_id']}",
        f"{report['ideas_found']} idea(s) | "
        f"{report['rosters_evaluated']}/{report['rosters_total']} rosters evaluated | "
        f"{report['rosters_skipped_infeasible']} skipped infeasible | "
        f"status {report['status']}",
        f"pool: {'approximate' if report['approx_pool'] else 'exact'} | "
        f"shopping signal: {report['shopping_signal']} "
        f"({report['shopping_hits']} hit(s))",
    ]
    if not report["by_joint_surplus"]:
        lines.append(report["message"])
        return "\n".join(lines)

    for title, key in (
        ("Best for the league", "by_joint_surplus"),
        ("Best for me", "by_my_surplus"),
    ):
        lines.append("")
        lines.append(f"{title} (top {limit}):")
        for i, idea in enumerate(report[key], start=1):
            lines.append(
                f"  {i}. +{idea['my_surplus']:.1f} me / "
                f"+{idea['their_surplus']:.1f} them "
                f"(joint +{idea['joint_surplus']:.1f}) with {idea['owner_name']}"
            )
            lines.append(f"     send    {_fmt_side(idea['send'])}")
            lines.append(f"     receive {_fmt_side(idea['receive'])}")
            for note in idea["notes"]:
                lines.append(f"     note: {note}")
    return "\n".join(lines)


def report_for(league: str, user_id: str, limit: int, no_shopping: bool = False) -> dict:
    """The full report as data, for callers that are not the command line.

    run() renders this; the daily digest consumes it directly. Keeping one
    builder means the digest can never disagree with the CLI about whether a
    league had no trades or could not be evaluated.
    """
    league_id = resolve_league_id(league)
    data = assemble(league_id, user_id)

    shopping, note = ({}, "disabled") if no_shopping else load_shopping(league_id)
    evaluated = len(data.feasible_others)

    if not any(t.players or t.unknown_count for t in [data.me, *data.others]):
        report = build_report(
            data.league.name, league_id, data.me, len(data.others), 0,
            [], limit, note, 0, data.approx_pool,
        )
        report["status"] = STATUS_ROSTERS_EMPTY
        report["rosters_skipped_infeasible"] = 0
        report["message"] = explain(STATUS_ROSTERS_EMPTY, 0, len(data.others), 0)
        return report

    try:
        ideas = find_trades(
            data.me,
            data.others,
            data.league.roster_positions,
            data.replacement,
            data.valued_positions,
            shopping=shopping,
        )
    except ValueError as exc:
        report = build_report(
            data.league.name, league_id, data.me, len(data.others), evaluated,
            [], limit, note, 0, data.approx_pool,
        )
        report["status"] = STATUS_MY_ROSTER_INFEASIBLE
        report["message"] = f"{explain(STATUS_MY_ROSTER_INFEASIBLE, 0, 0, 0)} {exc}"
        return report

    return build_report(
        data.league.name,
        league_id,
        data.me,
        len(data.others),
        evaluated,
        ideas,
        limit,
        note,
        sum(t.shopping_hits for t in ideas),
        data.approx_pool,
    )


def exit_code_for(report: dict) -> int:
    """0 when the answer is real, 2 when we could not look."""
    return 0 if report["status"] in (STATUS_OK, STATUS_NO_TRADES, STATUS_ROSTERS_EMPTY) else 2


def run(league: str, user_id: str, limit: int, as_json: bool, no_shopping: bool) -> int:
    report = report_for(league, user_id, limit, no_shopping)
    print(json.dumps(report, indent=2) if as_json else render_text(report, limit))
    return exit_code_for(report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("league", help="Sleeper league id, or a key from ffb.leagues")
    parser.add_argument("--user", default=SLEEPER_USER_ID, help="Sleeper user id")
    parser.add_argument("--limit", type=int, default=5, help="ideas per ordering")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--no-shopping",
        action="store_true",
        help="skip the SLEEPER_TOKEN trade-history enrichment",
    )
    args = parser.parse_args()
    sys.exit(run(args.league, args.user, args.limit, args.json, args.no_shopping))


if __name__ == "__main__":
    main()
