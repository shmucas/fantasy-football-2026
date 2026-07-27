"""Propose, approve, reject and execute actions.

The gate lives in execute(): it re-reads the row and re-checks the rules at send
time, rather than trusting whatever the caller believed. An approval handed out
twelve hours ago is not a licence to fire now.
"""

import secrets
from datetime import datetime, timezone

from sqlalchemy import select

from ffb.approvals import state
from ffb.db import Session
from ffb.models import PendingAction

TOKEN_BYTES = 32


def _now() -> datetime:
    return datetime.now(timezone.utc)


def propose(
    kind: str,
    league_id: str,
    roster_id: int,
    summary: str,
    payload: dict,
    *,
    league_name: str = "",
    detail: str = "",
    session=None,
) -> PendingAction:
    """Record an intended action and mint its single-use approval token."""
    now = _now()
    action = PendingAction(
        kind=kind,
        league_id=league_id,
        league_name=league_name,
        roster_id=roster_id,
        summary=summary,
        detail=detail,
        payload=payload,
        status=state.PROPOSED,
        approval_token=secrets.token_urlsafe(TOKEN_BYTES),
        created_at=now,
        expires_at=state.default_expiry(now),
    )
    if session is not None:
        session.add(action)
        session.flush()
        return action
    with Session() as s:
        s.add(action)
        s.commit()
        s.refresh(action)
        s.expunge(action)
        return action


def by_token(token: str, session=None) -> PendingAction | None:
    stmt = select(PendingAction).where(PendingAction.approval_token == token)
    if session is not None:
        return session.scalar(stmt)
    with Session() as s:
        action = s.scalar(stmt)
        if action is not None:
            s.expunge(action)
        return action


def decide(token: str, approve: bool, session=None) -> tuple[bool, str]:
    """Approve or reject by token. Returns (changed, message).

    Idempotent by construction: the first call moves the row out of `proposed`,
    so a replayed link finds it already decided and changes nothing.
    """

    def _apply(s) -> tuple[bool, str]:
        action = s.scalar(
            select(PendingAction).where(PendingAction.approval_token == token)
        )
        if action is None:
            return False, "No such action"
        allowed = state.can_decide(action.status, action.expires_at, _now())
        if not allowed.allowed:
            return False, f"Cannot decide: {allowed.reason}"
        action.status = state.APPROVED if approve else state.REJECTED
        action.decided_at = _now()
        return True, "Approved" if approve else "Rejected"

    if session is not None:
        return _apply(session)
    with Session() as s:
        result = _apply(s)
        s.commit()
        return result


def pending(session=None) -> list[PendingAction]:
    """Actions still awaiting a decision and not yet stale."""
    now = _now()

    def _read(s):
        rows = s.scalars(
            select(PendingAction).where(PendingAction.status == state.PROPOSED)
        ).all()
        return [r for r in rows if not state.is_expired(r.expires_at, now)]

    if session is not None:
        return _read(session)
    with Session() as s:
        rows = _read(s)
        for r in rows:
            s.expunge(r)
        return rows


def approved_ready(session=None) -> list[PendingAction]:
    """Approved actions that are still executable right now."""
    now = _now()

    def _read(s):
        rows = s.scalars(
            select(PendingAction).where(PendingAction.status == state.APPROVED)
        ).all()
        return [r for r in rows if state.can_execute(r.status, r.expires_at, now).allowed]

    if session is not None:
        return _read(session)
    with Session() as s:
        rows = _read(s)
        for r in rows:
            s.expunge(r)
        return rows


def execute(action_id: int, sender, *, session=None) -> tuple[bool, str]:
    """Send one action, if and only if it is still allowed to be sent.

    `sender` takes the stored payload and returns Sleeper's response. Injected
    rather than imported so the gate can be tested without a token, and so this
    module never depends on the network.
    """

    def _run(s) -> tuple[bool, str]:
        action = s.get(PendingAction, action_id)
        if action is None:
            return False, "No such action"

        verdict = state.can_execute(action.status, action.expires_at, _now())
        if not verdict.allowed:
            return False, verdict.reason

        # Claim it before sending. If the send raises, the row is left FAILED
        # rather than APPROVED, so a retry cannot double-submit a waiver claim.
        action.status = state.EXECUTED
        action.executed_at = _now()
        try:
            response = sender(action.payload)
        except Exception as exc:
            action.status = state.FAILED
            action.result = str(exc)[:500]
            return False, action.result

        action.result = _describe(response)
        return True, action.result

    if session is not None:
        return _run(session)
    with Session() as s:
        result = _run(s)
        s.commit()
        return result


def _describe(response) -> str:
    if isinstance(response, dict):
        txn = response.get("transaction_id")
        if txn:
            return f"transaction_id={txn}"
        return str(response)[:500]
    return str(response)[:500]
