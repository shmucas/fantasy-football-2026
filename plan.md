# Plan: Sleeper's private API

Notes from reading [cameron-eth/sleeper-sdk](https://github.com/cameron-eth/sleeper-sdk),
and what we should do with it.

## The headline: it is not what our roadmap card assumed

Our roadmap card says:

> Sleeper's documented read-only endpoints stop short of the good stuff. Map
> what the app itself calls so projections, matchups and transactions can be
> pulled properly.

That premise is wrong on the most important word. The SDK's private-API surface
contains **no projections and no stats**. Every operation it found is either
trade history or a *write*:

| Operation | Kind | What it does |
| --- | --- | --- |
| `league_transactions_filtered` | query | League trade history, filterable |
| `propose_trade` | mutation | Send a trade offer |
| `accept_trade` / `reject_trade` / `cancel_trade` | mutation | Respond to an offer |
| `update_roster_starters` | mutation | Set your lineup |
| `create_free_agent` | mutation | Add/drop |
| `create_waiver_claim` / `cancel_waiver_claim` | mutation | Waiver claims, with FAAB bid |
| `move_to_taxi` / `move_to_ir` / `activate_from_ir` | mutation | Roster slot moves |

So this is not a data-enrichment unlock. **It is the missing arm for Autopilot** —
the thing that lets the bot *act* rather than only advise. That is genuinely
valuable to us, but it is a different item on the board than the card describes.

Where does the SDK get its numbers? Not from Sleeper. It scrapes KeepTradeCut
for dynasty values and uses `nflreadpy` for points-per-game — and we already
depend on `nflreadpy`. There is nothing to harvest there.

## The surface, concretely

- **Endpoint:** `https://sleeper.com/graphql` — note the host, not
  `api.sleeper.app` where the documented REST API lives.
- **Auth:** a JWT in a raw `authorization` header, with **no `Bearer` prefix**.
- **Getting the token:** sleeper.com → DevTools → Network → any `graphql`
  request → copy the `authorization` header. It decodes to `user_id`,
  `display_name`, `iat`, `exp`, so expiry is checkable locally before spending
  a request.
- **Headers that appear to matter:** `origin` and `referer` set to
  `https://sleeper.com`, plus `x-sleeper-graphql-op` naming the operation.
- **Error handling:** failures come back as **HTTP 200 with an `errors` array**,
  so `raise_for_status()` is useless here. Error shapes are inconsistent — a
  list of dicts, a bare dict, or a list of strings — and the SDK normalizes all
  three. Worth copying rather than rediscovering.
- **Rate limit:** Sleeper's documented ceiling is 1000 requests/minute.

Example, the one that matters most for us:

```graphql
mutation create_waiver_claim(
  $league_id: Snowflake!, $roster_id: Int!,
  $adds: JSON, $drops: JSON, $waiver_budget: Int
) {
  create_waiver_claim(
    league_id: $league_id, roster_id: $roster_id,
    adds: $adds, drops: $drops, waiver_budget: $waiver_budget
  ) { transaction_id status type created adds drops settings }
}
```

`adds`/`drops` are `{player_id: roster_id}` maps; `waiver_budget` is the FAAB
bid in dollars.

## Things to decide before writing code

**The token is a credential, not a config value.** It is a live session for the
whole Sleeper account — not a scoped API key, and it can do anything the web app
can. Consequences: env var only, never committed, never logged, never sent to
the frontend, and never handled by the Vercel function. It expires, so any job
using it needs a clear failure mode when it does.

**Writes are irreversible in the ways that hurt.** A dropped player can be
claimed by someone else before you notice; a sent trade is visible to the other
manager immediately. Anything that mutates gets a dry-run default and an
explicit confirmation, the way the SDK's `send-trade` does.

**This is an undocumented API and can change without notice.** Isolate it behind
one module so a breakage is one file, not a scattered hunt. Never let it become
a dependency of the web app's request path.

**Worth you deciding, not me:** automating an account through a private API is
the kind of thing platforms' terms typically don't sanction, even when it is
your own account and your own data. The risk is account-level, not legal, and
it lands on you rather than on the code. Read-only use is meaningfully lower
risk than writes. I'd suggest going as far as Phase 2 below and then making a
deliberate call about Phase 3, rather than drifting into writes by momentum.

## Plan

### Phase 1 — read-only, no writes at all

Add `ffb/sleeper_private.py` as a sibling to `sleeper_client.py`, holding the
GraphQL transport only: token inspection, the `origin`/`referer` headers, the
200-with-errors handling, and the single `league_transactions_filtered` query.

Then wire it to the roadmap's **Waiver claim scraper**, which is read-only and
already queued: pull league transactions on a schedule and store them, so we
learn what each manager chases and what they pay. This reuses the `ffb/alerts/`
job pattern and the database that injury watch just started using.

Deliverable: `uv run python -m ffb.alerts.transactions --dry-run` prints the
league's recent adds, drops and trades with FAAB amounts.

Note the documented REST API already exposes `/league/{id}/transactions/{week}`,
and our `SleeperClient.get_transactions` already wraps it. **Start there.** Only
reach for GraphQL if the REST version turns out to be missing something we
need — that comparison is the first task, not an assumption.

### Phase 2 — build the writes, send nothing

Implement `set_starters`, `create_waiver_claim` and `create_free_agent` as
payload builders with a hard `dry_run=True` default that returns the exact
GraphQL body instead of posting it. Unit-test the payload shape — particularly
that `starters` is ordered to match `roster_positions` with BN/IR/TAXI removed,
which is the easiest thing to get silently wrong.

This lets the start/sit and waiver recommendations we already compute render as
"here is the exact claim I would submit" without any account risk.

### Phase 3 — guarded writes, one at a time

Only with an explicit decision from you. Order them by how recoverable a mistake
is: `set_starters` first (harmless, overwrite-able), then waiver claims (costs
FAAB), then add/drop (can lose a player outright). Each behind `--yes`, each
logging what it sent, and each with the resulting `transaction_id` recorded to
the database so we can reconcile afterwards.

Trades stay out of scope: `propose_trade` involves another person, and a bug
there is a social problem, not just a technical one.

### Phase 4 — Autopilot

The roadmap's end state, and only reachable after Phase 3 has run supervised for
a while. Needs the long-lived host we keep deferring — the Vercel function
cannot do this, which is the same constraint that shaped the injury watcher into
a webhook job.

## Still open

- **Projections.** This repo does not solve it. Sleeper is widely believed to
  serve undocumented projection and stat endpoints under `api.sleeper.app`, but
  I could not verify that — the sandbox blocks outbound calls to that host, and
  the SDK does not use them. Treat as unverified until someone opens DevTools on
  a projections screen and looks. If they turn out not to exist, our current
  positional-curve approach stays, and the honest fix for the "approximate
  projections" warning in the UI is a per-league pool build, not scraping.
- **Which token, whose account.** Everything here is scoped to one Sleeper
  account, matching how the backend already works. Multi-user would need real
  credential storage, which we do not have and should not build casually.
- **Does REST cover the transaction data we want?** Phase 1's first task.
