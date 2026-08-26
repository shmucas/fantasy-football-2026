"""Trades other managers have sent me: what they are, and what to do about them.

The mirror of ffb.cli_trades. That one searches for offers worth making; this
one scores the offers already sitting in my Sleeper inbox and says accept,
reject or close call.

Read-only. It reads the inbox and values it. Accepting or rejecting goes
through ffb.sleeper_auth, which refuses to fire unless FFB_ALLOW_WRITES is set.

Needs SLEEPER_TOKEN: proposed trades are invisible to Sleeper's public API, so
without the token there is no inbox to read. That is reported as "could not
look", never as "no offers".
"""

import argparse
import json
import os
import sys

from ffb.cli_trades import assemble, resolve_league_id
from ffb.leagues import SLEEPER_USER_ID
from ffb.trades import VERDICT_UNCLEAR, evaluate_offer

# Statuses a machine reader can branch on, matching ffb.cli_trades' vocabulary.
STATUS_OK = "ok"
STATUS_EMPTY = "no_offers"
STATUS_NO_TOKEN = "no_token"
STATUS_FAILED = "cannot_evaluate"


def _sides(txn: dict, my_roster_id: int) -> tuple[list[str], list[str]]:
    """(player ids coming to me, player ids leaving me) for one transaction.

    Sleeper's shape: `adds` maps a player id to the roster receiving them, and
    `drops` maps a player id to the roster giving them up. This is the same
    reading ffb.trades.shopping_signal uses.
    """
    receive = [
        pid for pid, rid in (txn.get("adds") or {}).items() if rid is not None and int(rid) == my_roster_id
    ]
    send = [
        pid for pid, rid in (txn.get("drops") or {}).items() if rid is not None and int(rid) == my_roster_id
    ]
    return receive, send


def _other_roster_id(txn: dict, my_roster_id: int) -> int | None:
    others = [int(r) for r in (txn.get("roster_ids") or []) if int(r) != my_roster_id]
    # A three-way trade has no single counterparty; valuing "their" side then
    # means nothing, so we skip it rather than pick one arbitrarily.
    return others[0] if len(others) == 1 else None


def _offer_dict(data, txn: dict) -> dict:
    my_roster_id = data.me.roster_id
    receive_ids, send_ids = _sides(txn, my_roster_id)

    receive = [data.by_id[pid] for pid in receive_ids if pid in data.by_id]
    send = [data.by_id[pid] for pid in send_ids if pid in data.by_id]
    unresolved = (len(receive_ids) - len(receive)) + (len(send_ids) - len(send))

    other_id = _other_roster_id(txn, my_roster_id)
    other = next((t for t in data.others if t.roster_id == other_id), None)

    verdict = evaluate_offer(
        data.me,
        send,
        receive,
        data.league.roster_positions,
        data.replacement,
        data.valued_positions,
        unresolved=unresolved,
        other=other,
    )

    return {
        "transaction_id": txn.get("transaction_id") or txn.get("id"),
        "leg": txn.get("leg"),
        "from": other.owner_name if other else "unknown",
        "receive": [{"name": p.name, "position": p.position} for p in receive],
        "send": [{"name": p.name, "position": p.position} for p in send],
        "unresolved": unresolved,
        "my_surplus": round(verdict.my_surplus, 1),
        "their_surplus": None if verdict.their_surplus is None else round(verdict.their_surplus, 1),
        "verdict": verdict.verdict,
        "notes": list(verdict.notes),
    }


def report_for(league: str, user_id: str) -> dict:
    """Every pending offer waiting on me, valued. Data only, no printing."""
    league_id = resolve_league_id(league)

    if not os.getenv("SLEEPER_TOKEN", "").strip():
        return {
            "league_id": league_id,
            "status": STATUS_NO_TOKEN,
            "reason": "no SLEEPER_TOKEN set, so proposed trades are invisible",
            "offers": [],
        }

    data = assemble(league_id, user_id)
    base = {"league": data.league.name, "league_id": league_id}

    try:
        from ffb.sleeper_auth import SleeperAuthClient

        with SleeperAuthClient() as client:
            pending = client.get_inbox(league_id, data.me.roster_id)
    except Exception as exc:  # noqa: BLE001 - "could not look" must be sayable
        return {**base, "status": STATUS_FAILED, "reason": f"{type(exc).__name__}: {exc}", "offers": []}

    offers = [_offer_dict(data, txn) for txn in pending]
    return {**base, "status": STATUS_OK if offers else STATUS_EMPTY, "offers": offers}


def render_text(report: dict) -> str:
    if report["status"] == STATUS_NO_TOKEN:
        return f"Could not read the inbox: {report['reason']}"
    if report["status"] == STATUS_FAILED:
        return f"{report['league']} - could not read the inbox: {report['reason']}"
    if report["status"] == STATUS_EMPTY:
        return f"{report['league']} - no trades are waiting on you."

    lines = [f"{report['league']} - {len(report['offers'])} offer(s) waiting on you:"]
    for offer in report["offers"]:
        lines.extend(f"  {line}" for line in offer_lines(offer))
    return "\n".join(lines)


def offer_lines(offer: dict) -> list[str]:
    """One offer as human-readable lines. Shared with the Discord digest."""
    get = ", ".join(p["name"] for p in offer["receive"]) or "nothing"
    give = ", ".join(p["name"] for p in offer["send"]) or "nothing"
    verdict = offer["verdict"].replace("_", " ").upper()
    head = f"{verdict} - from {offer['from']}: you get {get}, you give {give}"
    if offer["verdict"] != VERDICT_UNCLEAR:
        theirs = "" if offer["their_surplus"] is None else f" / {offer['their_surplus']:+.1f} them"
        head += f" ({offer['my_surplus']:+.1f} me{theirs})"
    return [head] + [f"  note: {n}" for n in offer["notes"]]


def exit_code_for(report: dict) -> int:
    """0 when the answer is real, 2 when we could not look."""
    return 0 if report["status"] in (STATUS_OK, STATUS_EMPTY) else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("league", help="Sleeper league id, or a key from ffb.leagues")
    parser.add_argument("--user", default=SLEEPER_USER_ID, help="Sleeper user id")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    report = report_for(args.league, args.user)
    print(json.dumps(report, indent=2) if args.json else render_text(report))
    sys.exit(exit_code_for(report))


if __name__ == "__main__":
    main()
