"""The alerters' memory: small JSON files kept in the repo.

This used to be two tables in hosted Postgres. That database was there to serve
the website, and once the website went away the only thing still reading it was
this: about forty rows of "what did I say last time". Supabase's direct host is
IPv6-only and GitHub Actions runners have no IPv6, so every scheduled run died
on connect and the only sign was a red X on a page nobody watches.

Files instead. The job writes them and pushes them back, so the state survives
between runs without a service to keep alive, and a diff on state/digest.json
reads as a log of what the bot decided to tell you.

Set FFB_STATE_DIR to point somewhere else, which is what a scratch run wants so
it does not leave the real state dirty.
"""

import json
import os
from pathlib import Path

STATE_DIR_ENV = "FFB_STATE_DIR"

# backend/src/ffb/alerts/store.py -> the repo root, so the files sit at the top
# level where they are easy to find rather than buried under the package.
DEFAULT_DIR = Path(__file__).resolve().parents[4] / "state"


def state_dir() -> Path:
    override = os.getenv(STATE_DIR_ENV)
    return Path(override) if override else DEFAULT_DIR


def path(name: str) -> Path:
    return state_dir() / f"{name}.json"


def read(name: str) -> dict:
    """The stored state, or {} when there is none.

    A missing file means we have never written this state, which every caller
    already treats as "no history": the digest posts, the injury watch seeds.
    """
    try:
        with path(name).open() as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def write(name: str, data: dict) -> None:
    """Replace the stored state.

    Sorted keys and a trailing newline keep the diff small and stable: without
    them a run that changed nothing would still churn the file and produce a
    commit saying nothing happened.
    """
    target = path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(target)
