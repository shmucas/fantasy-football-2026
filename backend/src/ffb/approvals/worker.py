"""Send the actions a human approved, and nothing else.

This is the only component that holds SLEEPER_TOKEN. It runs where the injury
watcher runs - a machine you control - not on Vercel. The web app can record an
approval but cannot act on it, so compromising the site does not move a roster.

    uv run python -m ffb.approvals.worker --list      # what is waiting
    uv run python -m ffb.approvals.worker --dry-run   # what would be sent
    uv run python -m ffb.approvals.worker             # send approved actions
"""

import argparse

from ffb.approvals import notify, store
from ffb.db import Session, init_db
from ffb.models import PendingAction
from ffb.sleeper_private import SleeperPrivateClient


def show_pending() -> int:
    waiting = store.pending()
    if not waiting:
        print("Nothing awaiting approval.")
        return 0
    print(f"{len(waiting)} awaiting approval:")
    for action in waiting:
        print(f"  [{action.kind}] {action.summary}")
        print(f"      {notify.approval_link(action)}")
    return len(waiting)


def run(dry_run: bool, announce: bool, sender=None) -> int:
    """Execute every approved action. Returns how many were sent.

    `sender` takes a payload and returns Sleeper's response. It defaults to a
    real client built from SLEEPER_TOKEN; passing one in lets the loop be
    exercised without a token, and keeps the token out of anything but the
    real run.
    """
    init_db()
    ready = store.approved_ready()
    if not ready:
        print("No approved actions to send.")
        return 0

    if dry_run:
        for action in ready:
            print(f"WOULD SEND [{action.kind}] {action.summary}")
            print(f"    {action.payload['operationName']} {action.payload['variables']}")
        print(f"\n{len(ready)} action(s) ready. Nothing was sent (--dry-run).")
        return 0

    client = None
    if sender is None:
        client = SleeperPrivateClient()
        sender = client.send
    try:
        sent = 0
        with Session() as session:
            for approved in ready:
                ok, detail = store.execute(approved.id, sender, session=session)
                session.commit()
                action = session.get(PendingAction, approved.id)
                print(f"{'sent' if ok else 'failed'}: {action.summary} - {detail}")
                if announce:
                    try:
                        notify.announce_outcome(action, ok, detail)
                    except Exception as exc:
                        # A failed notification must not hide the real result.
                        print(f"  (could not post outcome to Discord: {exc})")
                sent += 1 if ok else 0
        return sent
    finally:
        if client is not None:
            client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="show what is awaiting approval")
    parser.add_argument("--dry-run", action="store_true", help="print instead of sending")
    parser.add_argument(
        "--quiet", action="store_true", help="do not post outcomes back to Discord"
    )
    args = parser.parse_args()

    init_db()
    if args.list:
        show_pending()
        return
    run(args.dry_run, announce=not args.quiet)


if __name__ == "__main__":
    main()
