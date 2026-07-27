"""Round-trip the approval flow against a real database.

The state tests prove the rules; these prove the store actually applies them,
using a sender that records every call so "nothing was sent" is checkable
rather than assumed.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ffb.approvals import state, store
from ffb.db import Base
from ffb.models import PendingAction  # noqa: F401  (registers the table)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)  # in-memory, per test
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as s:
        yield s


class RecordingSender:
    """Stands in for the Sleeper client and remembers what it was asked to do."""

    def __init__(self, response=None, boom: Exception | None = None):
        self.calls: list[dict] = []
        self.response = response or {"transaction_id": "txn-1"}
        self.boom = boom

    def __call__(self, payload: dict):
        self.calls.append(payload)
        if self.boom:
            raise self.boom
        return self.response


def make(session, **kw) -> PendingAction:
    return store.propose(
        kind=kw.get("kind", "waiver_claim"),
        league_id="L1",
        roster_id=3,
        summary=kw.get("summary", "Claim someone for $12"),
        payload={"operationName": "create_waiver_claim", "variables": {"x": 1}},
        session=session,
    )


def test_proposing_does_not_send_anything(session):
    sender = RecordingSender()
    make(session)
    assert sender.calls == []


def test_unapproved_action_is_refused_and_sender_never_called(session):
    action = make(session)
    sender = RecordingSender()
    ok, reason = store.execute(action.id, sender, session=session)
    assert ok is False
    assert reason == "not approved"
    assert sender.calls == [], "an unapproved action must never reach Sleeper"


def test_approve_then_execute_sends_exactly_once(session):
    action = make(session)
    changed, _ = store.decide(action.approval_token, approve=True, session=session)
    assert changed is True

    sender = RecordingSender()
    ok, result = store.execute(action.id, sender, session=session)
    assert ok is True
    assert "txn-1" in result
    assert len(sender.calls) == 1
    # What was approved is what was sent.
    assert sender.calls[0]["operationName"] == "create_waiver_claim"


def test_rejected_action_never_sends(session):
    action = make(session)
    store.decide(action.approval_token, approve=False, session=session)
    sender = RecordingSender()
    ok, reason = store.execute(action.id, sender, session=session)
    assert ok is False
    assert reason == "rejected"
    assert sender.calls == []


def test_second_execute_is_refused(session):
    """A retry must not submit the same waiver claim twice."""
    action = make(session)
    store.decide(action.approval_token, approve=True, session=session)
    sender = RecordingSender()
    store.execute(action.id, sender, session=session)
    ok, reason = store.execute(action.id, sender, session=session)
    assert ok is False
    assert reason == "already executed"
    assert len(sender.calls) == 1


def test_a_failed_send_does_not_stay_approved(session):
    """Otherwise a crashed send would be retried and could double-submit."""
    action = make(session)
    store.decide(action.approval_token, approve=True, session=session)
    sender = RecordingSender(boom=RuntimeError("sleeper said no"))

    ok, reason = store.execute(action.id, sender, session=session)
    assert ok is False
    assert "sleeper said no" in reason
    assert session.get(PendingAction, action.id).status == state.FAILED

    retry = RecordingSender()
    ok2, reason2 = store.execute(action.id, retry, session=session)
    assert ok2 is False
    assert retry.calls == []


def test_approval_token_is_single_use(session):
    action = make(session)
    first, _ = store.decide(action.approval_token, approve=True, session=session)
    second, msg = store.decide(action.approval_token, approve=False, session=session)
    assert first is True
    assert second is False, "a replayed link must not flip the decision"
    assert session.get(PendingAction, action.id).status == state.APPROVED


def test_tokens_are_unguessable_and_unique(session):
    tokens = {make(session).approval_token for _ in range(25)}
    assert len(tokens) == 25
    assert all(len(t) > 30 for t in tokens)


def test_unknown_token_decides_nothing(session):
    changed, msg = store.decide("not-a-real-token", approve=True, session=session)
    assert changed is False
    assert "No such action" in msg


def test_pending_lists_only_undecided(session):
    a = make(session)
    b = make(session)
    store.decide(a.approval_token, approve=True, session=session)
    still = [p.id for p in store.pending(session=session)]
    assert still == [b.id]


def test_approved_ready_lists_only_executable(session):
    a = make(session)
    make(session)  # left proposed
    store.decide(a.approval_token, approve=True, session=session)
    ready = [p.id for p in store.approved_ready(session=session)]
    assert ready == [a.id]


def test_expired_approval_does_not_execute(session):
    action = make(session)
    store.decide(action.approval_token, approve=True, session=session)
    # Backdate the expiry to simulate the approval going stale.
    session.get(PendingAction, action.id).expires_at = state.default_expiry(
        state._aware(action.created_at)
    ).replace(year=2000)
    sender = RecordingSender()
    ok, reason = store.execute(action.id, sender, session=session)
    assert ok is False
    assert "expired" in reason
    assert sender.calls == []
