"""What the digest said last time, so it can stay quiet when nothing moved.

Same idea as the injury watcher's InjuryState: post changes, not standing
state. The digest ran three times a day and re-sent identical trade ideas until
a roster changed, which is what made it noise rather than signal.

A fingerprint deliberately covers *identity*, not the numbers. Projected points
drift by fractions between runs, so folding them in would break the fingerprint
every time and defeat the whole thing. Two runs that name the same trade are
the same answer.
"""

import hashlib
import json
from datetime import datetime, timezone

from ffb.models import DigestState

# Section names, used as part of the state key. Changing one resets that
# section's history, which costs a single extra post.
TRADES = "trades"
INBOX = "inbox"
LINEUP = "lineup"
FAILURES = "failures"


def fingerprint(identity) -> str:
    """A stable hash of whatever identifies this answer.

    `identity` must be JSON-serializable and ordered deterministically by the
    caller: a set that iterates in a different order each run would look like
    a change every time.
    """
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def load(session, league_id: str) -> dict[str, str]:
    """section -> last posted fingerprint, for one league."""
    rows = session.query(DigestState).filter(DigestState.league_id == league_id).all()
    return {row.section: row.fingerprint for row in rows}


def save(session, league_id: str, section: str, value: str) -> None:
    row = session.get(DigestState, {"league_id": league_id, "section": section})
    if row is None:
        row = DigestState(league_id=league_id, section=section)
        session.add(row)
    row.fingerprint = value
    row.updated_at = datetime.now(timezone.utc)


# -- what identifies each section's answer ---------------------------------
#
# Each returns something JSON-serializable and deterministically ordered.


def trades_identity(report: dict) -> list:
    """The set of trade ideas, by who and which players move.

    Surplus values are excluded on purpose: they wobble slightly run to run
    without the advice changing.
    """
    ideas = report.get("by_my_surplus") or []
    return sorted(
        [
            idea.get("roster_id"),
            sorted(p["player_id"] for p in idea.get("send", [])),
            sorted(p["player_id"] for p in idea.get("receive", [])),
        ]
        for idea in ideas
    )


def inbox_identity(report: dict) -> list:
    """Which offers are pending. A new offer speaks; an unanswered one does not
    keep speaking, since the reminder is what made this noisy."""
    return sorted(str(o.get("transaction_id")) for o in report.get("offers") or [])


def lineup_identity(result: dict) -> list:
    """The moves being recommended, not the points they would gain."""
    return [
        sorted(m["name"] for m in result.get("start") or []),
        sorted(m["name"] for m in result.get("sit") or []),
    ]


def failures_identity(messages: list[str]) -> list:
    return sorted(messages)
