"""Ask for approval in Discord, and report back what happened.

Discord webhooks are one-way, so a message cannot carry a button. Instead the
proposal carries a link back to the web app holding the action's single-use
token: read it in Discord, approve it in one tap. That keeps the whole loop
working without a Discord bot, which would need a long-lived host we do not
have.

Possession of the link is the authorisation. The web app has no login, so treat
the link like a password: it belongs in a private channel, not a public one.
"""

import os

from ffb.alerts import discord
from ffb.models import PendingAction

# Where the approval page lives. Falls back to local dev.
APP_URL_ENV = "FFB_APP_URL"
DEFAULT_APP_URL = "http://localhost:5173"


def app_url() -> str:
    return (os.getenv(APP_URL_ENV) or DEFAULT_APP_URL).rstrip("/")


def approval_link(action: PendingAction) -> str:
    # A query parameter rather than a path: the frontend is a single-page app
    # with no router, so /approve/<token> would 404 on a cold load.
    return f"{app_url()}/?approve={action.approval_token}"


def proposal_message(action: PendingAction) -> str:
    where = f" - {action.league_name}" if action.league_name else ""
    lines = [
        f"**Approval needed: {_label(action.kind)}{where}**",
        action.summary,
    ]
    if action.detail:
        lines.append(action.detail)
    lines.append(f"Approve or reject: {approval_link(action)}")
    lines.append(
        f"_Expires {action.expires_at:%a %d %b %H:%M} UTC. Nothing happens unless you approve._"
    )
    return "\n".join(lines)


def outcome_message(action: PendingAction, ok: bool, detail: str) -> str:
    head = "✅ Done" if ok else "⚠️ Failed"
    return f"{head}: {action.summary}\n{detail}"


def announce(action: PendingAction) -> int:
    """Post a proposal. Returns the number of Discord messages sent."""
    return discord.post(proposal_message(action))


def announce_outcome(action: PendingAction, ok: bool, detail: str) -> int:
    return discord.post(outcome_message(action, ok, detail))


def _label(kind: str) -> str:
    return {
        "set_starters": "Lineup change",
        "waiver_claim": "Waiver claim",
        "add_drop": "Add / drop",
        "trade": "Trade offer",
        "draft_pick": "Draft pick",
    }.get(kind, kind)
