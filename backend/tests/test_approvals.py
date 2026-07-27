"""The safety property: nothing reaches Sleeper without a live human approval.

These tests exist to make that hard to break by accident. If one fails, assume
the gate is open, not that the test is wrong.
"""

from datetime import datetime, timedelta, timezone

import pytest

from ffb.approvals import state


NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)
SOON = NOW + timedelta(hours=6)


# --- the gate ---------------------------------------------------------------


def test_proposed_action_cannot_execute():
    """The whole point: merely wanting to do something is not permission."""
    verdict = state.can_execute(state.PROPOSED, SOON, NOW)
    assert verdict.allowed is False
    assert verdict.reason == "not approved"


def test_approved_action_can_execute():
    assert state.can_execute(state.APPROVED, SOON, NOW).allowed is True


def test_rejected_action_cannot_execute():
    assert state.can_execute(state.REJECTED, SOON, NOW).allowed is False


def test_executed_action_cannot_execute_again():
    """Guards against double-submitting a waiver claim or a trade."""
    assert state.can_execute(state.EXECUTED, SOON, NOW).allowed is False


def test_failed_action_does_not_silently_retry():
    assert state.can_execute(state.FAILED, SOON, NOW).allowed is False


def test_approval_goes_stale():
    """Consent to change a lineup is consent to change it now, not next week."""
    expired = NOW - timedelta(minutes=1)
    verdict = state.can_execute(state.APPROVED, expired, NOW)
    assert verdict.allowed is False
    assert "expired" in verdict.reason


def test_unknown_status_is_refused_not_allowed():
    """Anything unrecognised must fail closed."""
    assert state.can_execute("something-new", SOON, NOW).allowed is False


@pytest.mark.parametrize(
    "status",
    [state.PROPOSED, state.REJECTED, state.EXECUTED, state.FAILED, "", "APPROVED"],
)
def test_only_exactly_approved_passes(status):
    """Case matters; near-misses must not open the gate."""
    assert state.can_execute(status, SOON, NOW).allowed is False


# --- deciding ---------------------------------------------------------------


def test_a_proposed_action_can_be_decided():
    assert state.can_decide(state.PROPOSED, SOON, NOW).allowed is True


def test_an_expired_proposal_cannot_be_approved():
    assert state.can_decide(state.PROPOSED, NOW - timedelta(seconds=1), NOW).allowed is False


def test_an_already_decided_action_cannot_be_redecided():
    """A replayed approval link must not flip a rejection into an approval."""
    assert state.can_decide(state.REJECTED, SOON, NOW).allowed is False
    assert state.can_decide(state.APPROVED, SOON, NOW).allowed is False


def test_naive_timestamps_are_treated_as_utc():
    """SQLite returns naive datetimes; comparing them must not blow up."""
    naive_future = datetime(2026, 9, 10, 18, 0)
    assert state.can_execute(state.APPROVED, naive_future, NOW).allowed is True


def test_default_expiry_is_in_the_future():
    assert state.default_expiry(NOW) > NOW
