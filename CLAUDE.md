# ffb26

## Shape

There is no deployment and no website. This is a Python package in `backend/`
plus two scheduled GitHub Actions workflows that post to Discord:

- `.github/workflows/digest.yml` - trade ideas, waivers, offers waiting on me,
  start/sit. Sections run on separate crons and only post when they change.
- `.github/workflows/injury-watch.yml` - injury status changes

Everything else is run by hand from the CLI (`python -m ffb.cli_trades`,
`ffb.waivers`, `ffb.inbox`, `ffb.alerts.digest`).

A Vite frontend and a Vercel Python Function used to serve this. Both were
removed: the site was a demo, not something anyone used. `ffb.pool` holds the
league lookup and pool selection the CLI still needs from that layer.

## What this means for new work

New capabilities belong as a CLI module plus, if it needs to reach you, a
section in the digest. Do not add an HTTP layer back without a reason to.

Jobs run on GitHub Actions runners, so their env vars are repository secrets,
not a hosting provider's settings. `backend/.env` covers local runs and is
gitignored.

Writes to Sleeper stay gated behind `FFB_ALLOW_WRITES`, which is deliberately
never set in CI.
