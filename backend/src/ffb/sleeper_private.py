"""Client for Sleeper's private GraphQL API - the one its own web app uses.

The documented REST API at api.sleeper.app is read-only, so it can advise but
never act. This endpoint is what makes setting a lineup or submitting a waiver
claim possible. It is undocumented and can change without notice, so it is kept
in this one file: when it breaks, this is the only place to look.

The token is a full session credential for the whole Sleeper account, not a
scoped API key. It lives in SLEEPER_TOKEN, is never logged and never leaves the
machine running the worker. Nothing here should ever be imported by the web API.

Every mutation is a pure payload builder plus a separate send step, so callers
can construct exactly what would be sent without sending it. See ffb.approvals
for the gate that decides whether a send is allowed at all.
"""

import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

GRAPHQL_URL = "https://sleeper.com/graphql"
TOKEN_ENV = "SLEEPER_TOKEN"


class SleeperAuthError(Exception):
    """Auth, transport or GraphQL-level failure talking to the private API."""


@dataclass(frozen=True)
class TokenInfo:
    user_id: str
    display_name: str
    expires_at: int

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def seconds_remaining(self) -> int:
        return max(0, int(self.expires_at - time.time()))


def inspect_token(jwt: str) -> TokenInfo:
    """Read (without verifying) a Sleeper JWT: who it is for and when it dies.

    Verification is Sleeper's job. This exists so a job can fail loudly on an
    expired token instead of spending a request to find out.
    """
    parts = jwt.split(".")
    if len(parts) != 3:
        raise SleeperAuthError("Token is not a JWT (expected three dot-separated parts)")
    body = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(body.encode()))
    except Exception as exc:
        raise SleeperAuthError(f"Could not decode token payload: {exc}") from exc
    return TokenInfo(
        user_id=str(payload.get("user_id", "")),
        display_name=str(payload.get("display_name", "")),
        expires_at=int(payload.get("exp", 0)),
    )


def token_from_env() -> str | None:
    return (os.getenv(TOKEN_ENV) or "").strip() or None


# --- payload builders -------------------------------------------------------
#
# Pure: they never touch the network. An action can be built, stored, shown to a
# human and approved long before anything is sent.


def _op(name: str, query: str, variables: dict) -> dict:
    return {"operationName": name, "query": query, "variables": variables}


# Slots that are not part of the starting lineup and so never appear in
# `starters`. Sleeper spells the reserve slot "IR".
BENCH_SLOTS = {"BN", "IR", "TAXI"}


def starting_slots(roster_positions: list[str]) -> list[str]:
    """The starting slots, in order, for a league's roster_positions.

    `starters` is positional, so this ordering is the contract: index 0 is
    whoever fills the first slot. Getting it wrong starts the wrong players
    without any error, which is why it is a named function with its own tests
    rather than an inline filter.
    """
    return [slot for slot in roster_positions if slot not in BENCH_SLOTS]


def set_starters_payload(
    league_id: str, roster_id: int, starters: list[str], leg: int | None = None
) -> dict:
    """Set the lineup. `starters` is positional: one entry per starting slot in
    the league's roster_positions order, with BN/IR/TAXI removed. Order is the
    meaning here, so a wrong order silently starts the wrong players."""
    query = """
    mutation update_roster_starters(
      $league_id: Snowflake!, $roster_id: Int!, $starters: [String]!, $leg: Int
    ) {
      update_roster_starters(
        league_id: $league_id, roster_id: $roster_id, starters: $starters, leg: $leg
      ) { roster_id starters players reserve taxi }
    }
    """
    return _op(
        "update_roster_starters",
        query,
        {
            "league_id": league_id,
            "roster_id": roster_id,
            "starters": list(starters),
            "leg": leg,
        },
    )


def waiver_claim_payload(
    league_id: str,
    roster_id: int,
    add_player_id: str,
    drop_player_id: str | None = None,
    faab_bid: int = 0,
) -> dict:
    """Submit a waiver claim. `faab_bid` is dollars, 0 in priority leagues."""
    query = """
    mutation create_waiver_claim(
      $league_id: Snowflake!, $roster_id: Int!,
      $adds: JSON, $drops: JSON, $waiver_budget: Int
    ) {
      create_waiver_claim(
        league_id: $league_id, roster_id: $roster_id,
        adds: $adds, drops: $drops, waiver_budget: $waiver_budget
      ) { transaction_id status type created adds drops settings }
    }
    """
    return _op(
        "create_waiver_claim",
        query,
        {
            "league_id": league_id,
            "roster_id": roster_id,
            "adds": {add_player_id: roster_id},
            "drops": {drop_player_id: roster_id} if drop_player_id else {},
            "waiver_budget": int(faab_bid),
        },
    )


def add_drop_payload(
    league_id: str, roster_id: int, add_player_id: str, drop_player_id: str | None = None
) -> dict:
    """Free-agent add, optionally dropping someone to make room.

    The most dangerous of these: a drop is immediate and the player can be
    claimed by someone else before you notice.
    """
    query = """
    mutation create_free_agent(
      $league_id: Snowflake!, $roster_id: Int!, $adds: JSON, $drops: JSON
    ) {
      create_free_agent(
        league_id: $league_id, roster_id: $roster_id, adds: $adds, drops: $drops
      ) { transaction_id status type created adds drops }
    }
    """
    return _op(
        "create_free_agent",
        query,
        {
            "league_id": league_id,
            "roster_id": roster_id,
            "adds": {add_player_id: roster_id},
            "drops": {drop_player_id: roster_id} if drop_player_id else {},
        },
    )


def propose_trade_payload(
    league_id: str,
    roster_id: int,
    target_roster_id: int,
    send_player_ids: list[str],
    receive_player_ids: list[str],
) -> dict:
    """Offer a trade. Unlike the others this is visible to another person the
    moment it lands, which is why it stays behind the same approval gate."""
    query = """
    mutation propose_trade(
      $league_id: Snowflake!, $roster_id: Int!, $adds: JSON, $drops: JSON,
      $consenter_ids: [Int]
    ) {
      propose_trade(
        league_id: $league_id, roster_id: $roster_id,
        adds: $adds, drops: $drops, consenter_ids: $consenter_ids
      ) { transaction_id status type created adds drops consenter_ids }
    }
    """
    return _op(
        "propose_trade",
        query,
        {
            "league_id": league_id,
            "roster_id": roster_id,
            # adds are what comes to us, drops are what leaves us.
            "adds": {pid: roster_id for pid in receive_player_ids},
            "drops": {pid: target_roster_id for pid in send_player_ids},
            "consenter_ids": [roster_id, target_roster_id],
        },
    )


def league_transactions_payload(league_id: str, limit: int = 50) -> dict:
    """Read-only: league transaction history, including trades."""
    query = """
    query league_transactions_filtered($league_id: Snowflake!, $limit: Int) {
      league_transactions_filtered(league_id: $league_id, limit: $limit) {
        transaction_id status type created adds drops settings roster_ids
      }
    }
    """
    return _op("league_transactions_filtered", query, {"league_id": league_id, "limit": limit})


# --- transport --------------------------------------------------------------


class SleeperPrivateClient:
    """Sends built payloads. Holds the token; keep it off the web tier."""

    def __init__(self, token: str | None = None, url: str = GRAPHQL_URL, timeout: float = 30.0):
        self.token = token or token_from_env()
        if not self.token:
            raise SleeperAuthError(
                f"No Sleeper token - set {TOKEN_ENV}. Capture it from sleeper.com "
                "DevTools > Network > any graphql request > authorization header."
            )
        self.url = url
        self._client = httpx.Client(timeout=timeout)

    @property
    def token_info(self) -> TokenInfo:
        return inspect_token(self.token)

    def _headers(self, op_name: str) -> dict[str, str]:
        # Sleeper takes the raw JWT, with no "Bearer " prefix.
        return {
            "authorization": self.token,
            "content-type": "application/json",
            "accept": "application/json",
            "origin": "https://sleeper.com",
            "referer": "https://sleeper.com/",
            "x-sleeper-graphql-op": op_name,
        }

    def send(self, payload: dict) -> Any:
        """Post a built payload and return its `data` field.

        Sleeper answers failures with HTTP 200 and an `errors` array, so status
        codes are not a reliable signal and raise_for_status would miss them.
        """
        if self.token_info.is_expired:
            raise SleeperAuthError(
                f"Token expired. Re-capture it from the web app and update {TOKEN_ENV}."
            )
        op_name = payload.get("operationName", "")
        response = self._client.post(self.url, headers=self._headers(op_name), json=payload)
        try:
            body = response.json()
        except ValueError as exc:
            raise SleeperAuthError(
                f"Non-JSON response (HTTP {response.status_code}) from {op_name}"
            ) from exc

        errors = body.get("errors")
        if errors:
            raise SleeperAuthError(f"{op_name} failed: {_error_text(errors)}")
        return (body.get("data") or {}).get(op_name)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SleeperPrivateClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _error_text(errors: Any) -> str:
    """Sleeper returns errors as a list of dicts, a bare dict, or a list of
    strings depending on the failure. Flatten all three to one message."""
    if isinstance(errors, dict):
        errors = [errors]
    if not isinstance(errors, list):
        return str(errors)
    messages = []
    for err in errors:
        if isinstance(err, dict):
            messages.append(str(err.get("message") or err.get("code") or err))
        else:
            messages.append(str(err))
    return "; ".join(messages)
