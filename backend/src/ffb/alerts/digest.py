"""Daily digest: trade ideas, offers waiting on me, and start/sit, posted to Discord.

This is the scheduled job an autonomous agent (or a launchd timer) drives. It
reads only. Nothing here touches Sleeper's write endpoints, and it deliberately
holds no code that could: proposing a trade or setting a lineup goes through
ffb.sleeper_auth, which refuses to fire unless FFB_ALLOW_WRITES is set.

The reporting rule that matters: a tool failing to evaluate is not the same as
finding nothing, and both look like silence if you only print results. Every
league reports which of the two happened, and a failure keeps the exit code
non-zero so a cron job cannot look healthy while telling you nothing.
"""

import argparse
import os

from ffb.alerts import discord

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


def build(leagues: list[str], user_id: str, limit: int, week: int | None) -> tuple[str, bool]:
    """Returns the message and whether anything failed to evaluate."""
    from ffb import cli_trades, inbox, lineup

    has_token = bool(os.getenv("SLEEPER_TOKEN", "").strip())

    trade_blocks: list[str] = []
    inbox_blocks: list[str] = []
    lineup_blocks: list[str] = []
    failed = False

    for league_id in leagues:
        try:
            report = cli_trades.report_for(league_id, user_id, limit)
        except Exception as exc:
            # A league that blows up should not take the other league's report
            # down with it, but it must not be silently dropped either.
            trade_blocks.append(f"**{league_id}** - trade finder errored: {exc}")
            failed = True
        else:
            if report.get("status") in TRADE_FAILURE_STATUSES:
                failed = True
            trade_blocks.extend(_trade_lines(report, limit))

        # Offers sent to me. Skipped entirely without a token: the inbox lives
        # behind Sleeper's private GraphQL, and a digest that cannot see it is
        # still a healthy digest, so this never sets `failed`.
        if has_token:
            try:
                incoming = inbox.report_for(league_id, user_id)
            except Exception as exc:
                inbox_blocks.append(f"**{league_id}** - inbox errored: {exc}")
                failed = True
            else:
                if incoming.get("status") == inbox.STATUS_FAILED:
                    failed = True
                inbox_blocks.extend(_inbox_lines(incoming))

        try:
            result = lineup.run(league_id, user_id, week, skip_injuries=False)
        except Exception as exc:
            lineup_blocks.append(f"**{league_id}** - lineup errored: {exc}")
            failed = True
        else:
            if result.get("status") == "cannot_evaluate":
                failed = True
            lineup_blocks.extend(_lineup_lines(result))

    parts = ["__**Trades**__", *trade_blocks]
    if inbox_blocks:
        parts += ["", "__**Offers waiting on you**__", *inbox_blocks]
    parts += ["", "__**Start / sit**__", *lineup_blocks]
    if failed:
        parts.append("")
        parts.append("Something above could not be evaluated. That is not the same as nothing to do.")
    return "\n".join(parts), failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default=os.getenv(USER_ENV, DEFAULT_USER))
    parser.add_argument("--limit", type=int, default=3, help="trade ideas per league")
    parser.add_argument("--week", type=int, default=None)
    parser.add_argument(
        "--dry-run", action="store_true", help="print the digest instead of posting"
    )
    args = parser.parse_args()

    message, failed = build(league_ids(), args.user, args.limit, args.week)

    # Always print. Under launchd this is what lands in the log, and a job whose
    # log says only "finished" is useless when you are trying to work out what
    # it told you three days ago.
    print(message)

    if args.dry_run:
        pass
    elif not discord.webhook_url():
        print("\n(not posted: no DISCORD_WEBHOOK_URL set)")
    else:
        discord.post(message)
        print("\n(posted to Discord)")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
