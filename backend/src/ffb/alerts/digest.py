"""Digest: trade ideas, offers waiting on me, and start/sit, posted to Discord.

This is the scheduled job an autonomous agent (or a launchd timer) drives. It
reads only. Nothing here touches Sleeper's write endpoints, and it deliberately
holds no code that could: proposing a trade or setting a lineup goes through
ffb.sleeper_auth, which refuses to fire unless FFB_ALLOW_WRITES is set.

The reporting rule that matters: a tool failing to evaluate is not the same as
finding nothing, and both look like silence if you only print results. Every
league reports which of the two happened, and a failure keeps the exit code
non-zero so a cron job cannot look healthy while telling you nothing.

Two rules keep it from becoming noise, which is what it was when every run
posted everything:

  - Sections are chosen per run with --sections, because they move on
    different clocks. Trade ideas change when rosters do, roughly twice a
    week; a lineup matters in the hours before kickoff.
  - A section that would say the same thing it said last time says nothing.
    ffb.alerts.state holds the fingerprints. When every section is unchanged
    the job posts nothing at all rather than posting an empty digest.

That makes silence ambiguous, which this project otherwise refuses to allow.
The answer is --force: one scheduled run a week posts regardless, so two quiet
weeks means something is broken rather than calm.
"""

import argparse
import os

from ffb.alerts import discord

# The sections a run can include, in the order they appear in the message.
ALL_SECTIONS = ("trades", "inbox", "lineup")

# Leagues to report on, as Sleeper ids. Overridable so a second season or a
# borrowed league does not need a code change.
LEAGUES_ENV = "FFB_DIGEST_LEAGUES"
DEFAULT_LEAGUES = [
    "1391439647548129280",  # FANTASY FUCKBOYZ
    "1315433135034355712",  # Miller League
]

USER_ENV = "FFB_SLEEPER_USER_ID"
DEFAULT_USER = "1125887731814576128"

# Statuses from ffb.cli_trades that mean "we could not look", not "nothing found".
TRADE_FAILURE_STATUSES = {"nothing_evaluated", "my_roster_infeasible"}


def league_ids() -> list[str]:
    raw = os.getenv(LEAGUES_ENV, "").strip()
    if not raw:
        return DEFAULT_LEAGUES
    return [part.strip() for part in raw.split(",") if part.strip()]


def _trade_lines(report: dict, limit: int) -> list[str]:
    status = report.get("status")
    league = report.get("league") or report.get("league_id") or "league"

    if status in TRADE_FAILURE_STATUSES:
        return [f"**{league}** - could not evaluate trades: {report.get('reason', status)}"]
    if status == "rosters_empty":
        return [f"**{league}** - no rosters yet, the league has not drafted."]

    ideas = report.get("by_my_surplus") or []
    if not ideas:
        evaluated = report.get("rosters_evaluated", "?")
        return [
            f"**{league}** - no trade helps both sides "
            f"({evaluated} rosters checked, so this is a real answer)."
        ]

    lines = [f"**{league}** - top {min(limit, len(ideas))} of {len(ideas)}:"]
    for idea in ideas[:limit]:
        send = ", ".join(p["name"] for p in idea.get("send", [])) or "nothing"
        recv = ", ".join(p["name"] for p in idea.get("receive", [])) or "nothing"
        lines.append(
            f"  +{idea['my_surplus']:.1f} me / +{idea['their_surplus']:.1f} them "
            f"with {idea.get('owner_name', '?')}: send {send}, get {recv}"
        )
    return lines


def _lineup_lines(result: dict) -> list[str]:
    league = result.get("league") or result.get("league_id") or "league"
    if result.get("status") == "cannot_evaluate":
        return [f"**{league}** - could not evaluate lineup: {result.get('reason', 'unknown')}"]

    start = result.get("start") or []
    sit = result.get("sit") or []
    if not start and not sit:
        return [f"**{league}** - lineup is already optimal."]

    gained = result.get("points_gained", 0)
    lines = [f"**{league}** - {gained:+.1f} projected points available:"]
    for move in start:
        lines.append(f"  START {move['name']} ({move['position']}) - {move['reason']}")
    for move in sit:
        lines.append(f"  BENCH {move['name']} ({move['position']}) - {move['reason']}")
    for note in result.get("notes") or []:
        lines.append(f"  note: {note}")
    return lines


def _inbox_lines(report: dict) -> list[str]:
    from ffb import inbox

    league = report.get("league") or report.get("league_id") or "league"
    if report.get("status") == inbox.STATUS_FAILED:
        return [f"**{league}** - could not read the inbox: {report.get('reason', 'unknown')}"]
    if report.get("status") == inbox.STATUS_EMPTY:
        return [f"**{league}** - no trades are waiting on you."]

    offers = report.get("offers") or []
    lines = [f"**{league}** - {len(offers)} offer(s) waiting on you:"]
    for offer in offers:
        lines.extend(f"  {line}" for line in inbox.offer_lines(offer))
    return lines


def build(
    leagues: list[str],
    user_id: str,
    limit: int,
    week: int | None,
    sections: tuple[str, ...] = ALL_SECTIONS,
) -> tuple[dict[str, list[str]], dict[tuple[str, str], str], bool]:
    """Gather the requested sections.

    Returns the message blocks by section, the fingerprint of each
    (league, section) answer, and whether anything failed to evaluate.
    Deciding what to post is `select` below: this only looks.
    """
    from ffb import cli_trades, inbox, lineup
    from ffb.alerts import state

    has_token = bool(os.getenv("SLEEPER_TOKEN", "").strip())

    blocks: dict[str, list[str]] = {name: [] for name in ALL_SECTIONS}
    prints: dict[tuple[str, str], str] = {}
    failures: list[str] = []

    for league_id in leagues:
        if "trades" in sections:
            try:
                report = cli_trades.report_for(league_id, user_id, limit)
            except Exception as exc:
                # A league that blows up should not take the other league's
                # report down with it, but it must not be silently dropped.
                blocks["trades"].append(f"**{league_id}** - trade finder errored: {exc}")
                failures.append(f"{league_id}:trades:{exc}")
            else:
                if report.get("status") in TRADE_FAILURE_STATUSES:
                    failures.append(f"{league_id}:trades:{report.get('status')}")
                blocks["trades"].extend(_trade_lines(report, limit))
                prints[(league_id, state.TRADES)] = state.fingerprint(
                    state.trades_identity(report)
                )

        # Offers sent to me. Skipped entirely without a token: the inbox lives
        # behind Sleeper's private GraphQL, and a digest that cannot see it is
        # still a healthy digest, so this never counts as a failure.
        if "inbox" in sections and has_token:
            try:
                incoming = inbox.report_for(league_id, user_id)
            except Exception as exc:
                blocks["inbox"].append(f"**{league_id}** - inbox errored: {exc}")
                failures.append(f"{league_id}:inbox:{exc}")
            else:
                if incoming.get("status") == inbox.STATUS_FAILED:
                    failures.append(f"{league_id}:inbox:{incoming.get('reason')}")
                blocks["inbox"].extend(_inbox_lines(incoming))
                prints[(league_id, state.INBOX)] = state.fingerprint(
                    state.inbox_identity(incoming)
                )

        if "lineup" in sections:
            try:
                result = lineup.run(league_id, user_id, week, skip_injuries=False)
            except Exception as exc:
                blocks["lineup"].append(f"**{league_id}** - lineup errored: {exc}")
                failures.append(f"{league_id}:lineup:{exc}")
            else:
                if result.get("status") == "cannot_evaluate":
                    failures.append(f"{league_id}:lineup:{result.get('reason')}")
                blocks["lineup"].extend(_lineup_lines(result))
                prints[(league_id, state.LINEUP)] = state.fingerprint(
                    state.lineup_identity(result)
                )

    if failures:
        prints[("", state.FAILURES)] = state.fingerprint(
            state.failures_identity(failures)
        )
    return blocks, prints, bool(failures)


HEADINGS = {
    "trades": "__**Trades**__",
    "inbox": "__**Offers waiting on you**__",
    "lineup": "__**Start / sit**__",
}


def render(blocks: dict[str, list[str]], failed: bool) -> str:
    parts: list[str] = []
    for name in ALL_SECTIONS:
        lines = blocks.get(name) or []
        if not lines:
            continue
        if parts:
            parts.append("")
        parts += [HEADINGS[name], *lines]
    if failed:
        parts += [
            "",
            "Something above could not be evaluated. That is not the same as nothing to do.",
        ]
    return "\n".join(parts)


def changed(prints: dict, stored: dict[str, dict[str, str]]) -> set[str]:
    """Which sections are saying something they did not say last time.

    A section with no stored fingerprint counts as changed, so the first run
    after a deploy always speaks rather than starting out silent.
    """
    out = set()
    for (league_id, section), value in prints.items():
        if stored.get(league_id, {}).get(section) != value:
            out.add(section)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default=os.getenv(USER_ENV, DEFAULT_USER))
    parser.add_argument("--limit", type=int, default=3, help="trade ideas per league")
    parser.add_argument("--week", type=int, default=None)
    parser.add_argument(
        "--sections",
        default=",".join(ALL_SECTIONS),
        help=f"comma-separated subset of {','.join(ALL_SECTIONS)}",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="post even when nothing changed - the weekly heartbeat",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the digest instead of posting"
    )
    args = parser.parse_args()

    sections = tuple(
        name.strip() for name in args.sections.split(",") if name.strip() in ALL_SECTIONS
    )
    if not sections:
        parser.error(f"--sections must name at least one of {', '.join(ALL_SECTIONS)}")

    leagues = league_ids()
    blocks, prints, failed = build(leagues, args.user, args.limit, args.week, sections)

    from ffb.alerts import state
    from ffb.db import Session, init_db

    init_db()
    with Session() as session:
        stored = {league_id: state.load(session, league_id) for league_id in leagues}
        stored[""] = state.load(session, "")
        moved = changed(prints, stored)

        # A section that has not moved is dropped from the message rather than
        # repeated. --force keeps everything, which is what makes the weekly
        # run a real heartbeat instead of a louder no-op.
        shown = blocks if args.force else {k: v for k, v in blocks.items() if k in moved}
        message = render(shown, failed)

        # Always print, even when posting nothing. Under Actions this is the
        # log, and a job whose log says only "finished" is useless when you are
        # working out what it told you three days ago.
        print(message or "(nothing new)")

        if not message:
            print("\n(not posted: nothing changed since the last run)")
            return 1 if failed else 0

        if args.dry_run:
            pass
        elif not discord.webhook_url():
            print("\n(not posted: no DISCORD_WEBHOOK_URL set)")
        else:
            discord.post(message)
            print("\n(posted to Discord)")

        # Only remember what we actually said. A dry run or a missing webhook
        # must not convince the next run that you have already been told.
        if not args.dry_run and discord.webhook_url():
            for (league_id, section), value in prints.items():
                state.save(session, league_id, section, value)
            session.commit()

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
