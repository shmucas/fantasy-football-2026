"""Authenticated Sleeper client for the private GraphQL endpoint.

The public REST API in `sleeper_client.py` is read-only by design. Anything
that changes a team - proposing a trade, setting a lineup, claiming a waiver -
goes through https://sleeper.com/graphql instead, which needs the same JWT the
Sleeper web app sends.

Endpoint shape and operation names were worked out from
https://github.com/cameron-eth/sleeper-sdk and confirmed against a live league.
That repo carries no licence, so this is written from the observed protocol
rather than copied.

Two things about this endpoint break normal HTTP habits:

  - The token goes in `authorization` raw, with no `Bearer` prefix.
  - Errors come back as HTTP 200 with an `errors` array in the body, so
    `raise_for_status` would happily wave a failure through.

Writes are gated. See `WRITES_ENV` below: every mutation is a dry run unless
writes are explicitly switched on, because this drives a real team in a league
with real people in it.
"""

import base64
import json
import os
import time
from dataclasses import dataclass

import httpx

GRAPHQL_URL = "https://sleeper.com/graphql"

TOKEN_ENV = "SLEEPER_TOKEN"

# The kill switch. Mutations refuse to fire unless this is set to "1".
# Default-off means a misconfigured cron job describes what it would have done
# instead of doing it, which is the failure we want on a money league.
WRITES_ENV = "FFB_ALLOW_WRITES"

# Warn when the token has less than this left, so it gets replaced on purpose
# rather than in the middle of a Sunday.
EXPIRY_WARNING_DAYS = 14


class SleeperAuthError(RuntimeError):
    pass


class WritesDisabled(RuntimeError):
    """Raised when a mutation is attempted with writes switched off."""


@dataclass(frozen=True)
class TokenInfo:
    user_id: str
    display_name: str
    issued_at: int
    expires_at: int

    @property
    def seconds_remaining(self) -> int:
        return max(0, int(self.expires_at - time.time()))

    @property
    def days_remaining(self) -> int:
        return self.seconds_remaining // 86400

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def expiring_soon(self) -> bool:
        return not self.is_expired and self.days_remaining <= EXPIRY_WARNING_DAYS


def inspect_token(token: str) -> TokenInfo:
    """Read the JWT payload. This does not verify the signature - only Sleeper
    can do that. It is here to answer "who is this and when does it die"."""
    parts = token.split(".")
    if len(parts) != 3:
        raise SleeperAuthError("Token is not a JWT (expected three dot-separated parts)")
    body = parts[1]
    body += "=" * (-len(body) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(body.encode()))
    except Exception as exc:
        raise SleeperAuthError(f"Could not decode the token payload: {exc}") from exc
    return TokenInfo(
        user_id=str(payload.get("user_id", "")),
        display_name=payload.get("display_name", ""),
        issued_at=int(payload.get("iat", 0)),
        expires_at=int(payload.get("exp", 0)),
    )


def writes_enabled() -> bool:
    return os.getenv(WRITES_ENV, "").strip() == "1"


@dataclass(frozen=True)
class PlannedWrite:
    """What a mutation would have sent. Returned instead of firing when the
    client is in dry-run mode, so a caller can log or post it for review."""

    operation: str
    variables: dict

    def describe(self) -> str:
        return f"[dry run] {self.operation} {json.dumps(self.variables, sort_keys=True)}"


class SleeperAuthClient:
    """Talks to Sleeper's private GraphQL endpoint on behalf of one user.

    `dry_run` defaults to the inverse of the FFB_ALLOW_WRITES kill switch, so
    the safe mode is what you get by leaving things alone.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        dry_run: bool | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.token = token or os.getenv(TOKEN_ENV, "").strip()
        if not self.token:
            raise SleeperAuthError(
                f"No token. Set {TOKEN_ENV} in backend/.env - capture it from "
                "sleeper.com DevTools, Network, any graphql request, the "
                "authorization header."
            )
        self.token_info = inspect_token(self.token)
        if self.token_info.is_expired:
            raise SleeperAuthError(
                "Token expired. Re-capture it from the Sleeper web app."
            )
        self.dry_run = (not writes_enabled()) if dry_run is None else dry_run
        self._client = httpx.Client(timeout=timeout)

    def __enter__(self) -> "SleeperAuthClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- transport --------------------------------------------------------

    def _headers(self, operation: str) -> dict:
        # Mirrors what the web app sends. The token is raw here, no Bearer.
        return {
            "authorization": self.token,
            "content-type": "application/json",
            "accept": "application/json",
            "origin": "https://sleeper.com",
            "referer": "https://sleeper.com/",
            "x-sleeper-graphql-op": operation,
        }

    def _gql(self, operation: str, query: str, variables: dict) -> dict:
        response = self._client.post(
            GRAPHQL_URL,
            headers=self._headers(operation),
            json={"operationName": operation, "query": query, "variables": variables},
        )
        # Deliberately no raise_for_status: this endpoint reports failures as
        # 200 with an errors array, so status alone tells us very little.
        try:
            body = response.json()
        except ValueError as exc:
            raise SleeperAuthError(
                f"Non-JSON response (HTTP {response.status_code}): {response.text[:200]}"
            ) from exc

        errors = body.get("errors")
        if errors:
            if isinstance(errors, dict):
                errors = [errors]
            codes = {e.get("code") for e in errors if isinstance(e, dict)}
            if "unauthorized" in codes:
                raise SleeperAuthError(
                    "Sleeper rejected the token. It may have been invalidated by a "
                    "password change or a sign-out."
                )
            raise SleeperAuthError(f"{operation} failed: {errors}")
        return body.get("data") or {}

    def _mutate(self, operation: str, query: str, variables: dict) -> dict | PlannedWrite:
        """Run a mutation, or describe it if writes are off."""
        if self.dry_run:
            return PlannedWrite(operation=operation, variables=variables)
        if not writes_enabled():
            raise WritesDisabled(
                f"Refusing to run {operation}: set {WRITES_ENV}=1 to allow writes."
            )
        return self._gql(operation, query, variables)

    # -- reads ------------------------------------------------------------

    _TRANSACTIONS_QUERY = """
    query league_transactions_filtered(
      $league_id: Snowflake!, $type_filters: [String],
      $status_filters: [String], $limit: Int
    ) {
      league_transactions_filtered(
        league_id: $league_id, type_filters: $type_filters,
        status_filters: $status_filters, limit: $limit
      ) {
        transaction_id status type creator consenter_ids roster_ids
        created leg adds drops metadata settings draft_picks waiver_budget
      }
    }
    """

    def get_trades(
        self,
        league_id: str,
        statuses: list[str] | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Trades at any status.

        This is the reason the token is worth having for reads too: the public
        API only ever returns completed trades, so proposed, rejected and
        cancelled offers are invisible to it. Those are the ones that say what
        a manager actually wants. Note Sleeper spells it "cancelled".
        """
        data = self._gql(
            "league_transactions_filtered",
            self._TRANSACTIONS_QUERY,
            {
                "league_id": league_id,
                "type_filters": ["trade"],
                "status_filters": statuses,
                "limit": limit,
            },
        )
        return data.get("league_transactions_filtered") or []

    def get_inbox(self, league_id: str, my_roster_id: int) -> list[dict]:
        """Proposed trades waiting on my consent."""
        return [
            t
            for t in self.get_trades(league_id, statuses=["proposed"])
            if my_roster_id in (t.get("roster_ids") or [])
            and my_roster_id not in (t.get("consenter_ids") or [])
        ]

    def get_outbox(self, league_id: str, my_roster_id: int) -> list[dict]:
        """Trades I proposed that someone else has not answered yet."""
        out = []
        for t in self.get_trades(league_id, statuses=["proposed"]):
            rosters = t.get("roster_ids") or []
            consenters = t.get("consenter_ids") or []
            if my_roster_id in consenters and any(r not in consenters for r in rosters):
                out.append(t)
        return out

    # -- writes -----------------------------------------------------------

    def propose_trade(
        self,
        league_id: str,
        adds: list[tuple[str, int]],
        drops: list[tuple[str, int]],
        expires_at: int | None = None,
    ) -> dict | PlannedWrite:
        """Offer a trade.

        `adds` is (player_id, roster that receives them), `drops` is
        (player_id, roster that gives them up). Sleeper wants these as two
        parallel lists rather than pairs.
        """
        query = """
        mutation propose_trade(
          $league_id: Snowflake!, $k_adds: [String], $v_adds: [Int],
          $k_drops: [String], $v_drops: [Int], $expires_at: Int
        ) {
          propose_trade(
            league_id: $league_id, k_adds: $k_adds, v_adds: $v_adds,
            k_drops: $k_drops, v_drops: $v_drops, expires_at: $expires_at
          ) { transaction_id status type created leg }
        }
        """
        return self._mutate("propose_trade", query, {
            "league_id": league_id,
            "k_adds": [p for p, _ in adds],
            "v_adds": [r for _, r in adds],
            "k_drops": [p for p, _ in drops],
            "v_drops": [r for _, r in drops],
            "expires_at": expires_at,
        })

    def _trade_action(self, operation: str, league_id: str, transaction_id: str, leg: int):
        query = """
        mutation %s($league_id: Snowflake!, $transaction_id: Snowflake!, $leg: Int!) {
          %s(league_id: $league_id, transaction_id: $transaction_id, leg: $leg) {
            transaction_id status type created
          }
        }
        """ % (operation, operation)
        return self._mutate(operation, query, {
            "league_id": league_id,
            "transaction_id": transaction_id,
            "leg": leg,
        })

    def accept_trade(self, league_id: str, transaction_id: str, leg: int):
        return self._trade_action("accept_trade", league_id, transaction_id, leg)

    def reject_trade(self, league_id: str, transaction_id: str, leg: int):
        return self._trade_action("reject_trade", league_id, transaction_id, leg)

    def cancel_trade(self, league_id: str, transaction_id: str, leg: int):
        return self._trade_action("cancel_trade", league_id, transaction_id, leg)

    def set_starters(
        self, league_id: str, roster_id: int, starters: list[str], leg: int | None = None
    ) -> dict | PlannedWrite:
        """Set the starting lineup. Order has to line up with the league's
        roster_positions, minus the bench and IR slots."""
        query = """
        mutation update_roster_starters(
          $league_id: Snowflake!, $roster_id: Int!, $starters: [String]!, $leg: Int
        ) {
          update_roster_starters(
            league_id: $league_id, roster_id: $roster_id,
            starters: $starters, leg: $leg
          ) { roster_id starters players reserve taxi }
        }
        """
        return self._mutate("update_roster_starters", query, {
            "league_id": league_id,
            "roster_id": roster_id,
            "starters": starters,
            "leg": leg,
        })

    def submit_waiver_claim(
        self,
        league_id: str,
        roster_id: int,
        add_player_id: str,
        drop_player_id: str | None = None,
        faab_bid: int = 0,
    ) -> dict | PlannedWrite:
        """Claim a player off waivers. `faab_bid` is in dollars, 0 in a league
        that runs on priority instead of a budget."""
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
        return self._mutate("create_waiver_claim", query, {
            "league_id": league_id,
            "roster_id": roster_id,
            "adds": {add_player_id: roster_id},
            "drops": {drop_player_id: roster_id} if drop_player_id else {},
            "waiver_budget": int(faab_bid),
        })

    def make_draft_pick(
        self, draft_id: str, player_id: str, pick_no: int, sport: str = "nfl"
    ) -> dict | PlannedWrite:
        """Submit a pick in a live draft.

        `pick_no` is the overall pick number (1-based) this draft is on, the
        way the live draft board counts them. Sleeper uses it to reject a pick
        that arrives out of turn, so a stale poll can't double-pick.
        """
        query = """
        mutation draft_pick_player(
          $sport: String, $player_id: String, $draft_id: Snowflake!, $pick_no: Int
        ) {
          draft_pick_player(
            sport: $sport, player_id: $player_id, draft_id: $draft_id, pick_no: $pick_no
          ) { player_id pick_no picked_by draft_id is_keeper metadata }
        }
        """
        return self._mutate("draft_pick_player", query, {
            "sport": sport,
            "player_id": player_id,
            "draft_id": draft_id,
            "pick_no": pick_no,
        })

    def set_autopick(self, draft_id: str) -> dict | PlannedWrite:
        """Toggle Sleeper's own autopick ON for the token's user in this draft.

        This makes the platform pick for us on every turn, so it is *not* what
        the bot wants while it is alive (the two would race). It is a deliberate
        "take over, Sleeper" switch if we decide to hand the draft back.
        """
        query = """
        mutation put_user_on_autopick($draft_id: Snowflake!) {
          put_user_on_autopick(draft_id: $draft_id)
        }
        """
        return self._mutate("put_user_on_autopick", query, {"draft_id": draft_id})

    def free_agent_move(
        self,
        league_id: str,
        roster_id: int,
        adds: list[str] | None = None,
        drops: list[str] | None = None,
    ) -> dict | PlannedWrite:
        """Add and/or drop free agents for one roster in one transaction.

        `adds` and `drops` are Sleeper player ids. Sleeper wants them as two
        parallel lists (`k_*` player ids, `v_*` the roster they move for),
        the same shape as `propose_trade`. A drop without an add is a plain
        roster cut.
        """
        query = """
        mutation league_create_transaction(
          $type: String!, $league_id: Snowflake!,
          $k_adds: [String], $v_adds: [Int], $k_drops: [String], $v_drops: [Int]
        ) {
          league_create_transaction(
            type: $type, league_id: $league_id,
            k_adds: $k_adds, v_adds: $v_adds, k_drops: $k_drops, v_drops: $v_drops
          ) { transaction_id status type adds drops roster_ids }
        }
        """
        return self._mutate("league_create_transaction", query, {
            "type": "free_agent",
            "league_id": league_id,
            "k_adds": adds or [],
            "v_adds": [roster_id] * len(adds or []),
            "k_drops": drops or [],
            "v_drops": [roster_id] * len(drops or []),
        })


