"""The rules about when an action may be sent to Sleeper.

Deliberately pure - no database, no HTTP, no clock of its own. The one question
that matters ("may this be executed?") is a function of the row and the current
time, so it can be tested exhaustively and cannot be accidentally bypassed by a
caller that forgets a check.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# proposed -> approved -> executed
#          -> rejected
#          -> expired (by time, never by transition)
PROPOSED = "proposed"
APPROVED = "approved"
REJECTED = "rejected"
EXECUTED = "executed"
FAILED = "failed"

# An approval is consent to do something *now*. Stale consent is not consent:
# a lineup change approved last week should not fire after kickoff.
DEFAULT_TTL = timedelta(hours=12)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


def is_expired(expires_at: datetime, now: datetime) -> bool:
    return _aware(now) >= _aware(expires_at)


def can_decide(status: str, expires_at: datetime, now: datetime) -> Decision:
    """May a human still approve or reject this?"""
    if status != PROPOSED:
        return Decision(False, f"already {status}")
    if is_expired(expires_at, now):
        return Decision(False, "expired")
    return Decision(True)


def can_execute(status: str, expires_at: datetime, now: datetime) -> Decision:
    """May this action be sent to Sleeper?

    The only path to True is an approval that has not expired and has not
    already been used. Everything else - unapproved, rejected, expired, already
    executed, previously failed - is refused.
    """
    if status == PROPOSED:
        return Decision(False, "not approved")
    if status == REJECTED:
        return Decision(False, "rejected")
    if status == EXECUTED:
        return Decision(False, "already executed")
    if status == FAILED:
        return Decision(False, "already attempted and failed")
    if status != APPROVED:
        return Decision(False, f"unknown status {status!r}")
    if is_expired(expires_at, now):
        return Decision(False, "approval expired before it was executed")
    return Decision(True)


def default_expiry(now: datetime, ttl: timedelta = DEFAULT_TTL) -> datetime:
    return _aware(now) + ttl


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat those as UTC rather than
    letting a comparison raise and take a job down."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
