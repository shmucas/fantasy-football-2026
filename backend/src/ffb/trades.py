"""Trade finder: packages that make both rosters better by the existing model.

Read-only analysis. Nothing here proposes or sends anything to Sleeper.

Valuation reuses the draft machinery rather than inventing new math:
`select_starters` / `roster_projected_points` for the lineup a roster can
actually field, and `replacement_levels` / `vorp` for what the bench is worth.
The season simulator in ffb.sim is deliberately not used: it simulates a draft
and opens a process pool, which is both the wrong question for a trade and too
slow to run per candidate package inside a request.

Everything in this module is pure: rosters come in as lists of
`ffb.draft.strategy.Player`, so the API endpoint owns all Sleeper calls.

Two data caveats that shape the feasibility rules, both verified against a live
league:

  - The prebuilt pools carry no K rows at all, and their DEF rows use the ADP
    source's own ids ("ffc_1327"), which never match the team codes Sleeper
    puts on a roster ("IND"). So K and DEF starting slots cannot be checked for
    fillability and are ignored by `modelled_slots`.
  - A Sleeper roster holds players the pool has never heard of (deep bench,
    rookies). They are untradeable and worth zero here, but they still occupy a
    roster spot, so they are carried as `unknown_count`.
"""

from dataclasses import dataclass, field
from itertools import combinations

from ffb.draft.sim import select_starters
from ffb.draft.strategy import Player, vorp

# A bench player is insurance, not a starter, so their value over replacement
# counts at a discount against the starting lineup they are not in.
BENCH_WEIGHT = 0.25

# Both sides must gain more than this many projected points for a package to be
# worth showing. A bare "> 0" would emit floating point noise as trades.
MIN_SURPLUS = 0.5

# Search bounds. Packages are 1-for-1, 2-for-1 and 1-for-2; the candidate pool
# on each side is capped so an opponent sweep stays cheap enough for a request.
MAX_CANDIDATES = 10
MAX_PACKAGE = 2


@dataclass(frozen=True)
class TeamRoster:
    """One league roster, reduced to what the pool can value."""

    roster_id: int
    owner_name: str
    players: list[Player]
    # Roster spots held by players outside the pool: they count against roster
    # size but have no projection, so they are never traded.
    unknown_count: int = 0


@dataclass(frozen=True)
class TradeIdea:
    roster_id: int
    owner_name: str
    send: list[Player]
    receive: list[Player]
    my_surplus: float
    their_surplus: float
    shopping_hits: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def joint_surplus(self) -> float:
        return self.my_surplus + self.their_surplus


def active_player_ids(raw_roster: dict) -> list[str]:
    """The players on a roster that occupy a normal roster spot.

    Sleeper's `players` includes anyone stashed on IR or the taxi squad, but a
    league can allow those beyond its `roster_positions`. One IR stash was
    therefore enough to push a roster over its own slot count and get it
    dropped as unfileable, which silently shrank the trade search: 8 of 13
    rosters in one real league, purely on this.
    """
    ids = raw_roster.get("players") or []
    parked = set(raw_roster.get("reserve") or []) | set(raw_roster.get("taxi") or [])
    return [pid for pid in ids if pid not in parked]


def modelled_slots(roster_positions: list[str], valued_positions: set[str]) -> list[str]:
    """Starting slots we can actually reason about.

    `valued_positions` is the set of positions the pool resolves on the real
    rosters, so it excludes K (no pool rows) and DEF (pool rows carry the ADP
    source's ids, which never match Sleeper's team codes). Those slots would
    read as permanently unfillable and would reject every trade, so they are
    dropped instead. FLEX and BN always survive.
    """
    keep = {"FLEX", "BN"}
    return [s for s in roster_positions if s in keep or s in valued_positions]


def roster_value(
    players: list[Player], slots: list[str], replacement: dict[str, float]
) -> float:
    """Projected points of the best lineup, plus discounted bench surplus."""
    starters = select_starters(players, slots)
    starter_ids = {p.player_id for p in starters}
    total = sum(p.proj_points for p in starters)
    for p in players:
        if p.player_id not in starter_ids:
            total += BENCH_WEIGHT * max(0.0, vorp(p, replacement))
    return total


def is_feasible(
    players: list[Player],
    unknown_count: int,
    slots: list[str],
    full_roster_positions: list[str],
) -> bool:
    """A roster that cannot be filed is not a trade.

    Two rules: the roster must still fit, and every modelled starting slot must
    still have a body in it.
    """
    if len(players) + unknown_count > len(full_roster_positions):
        return False
    needed = sum(1 for s in slots if s != "BN")
    return len(select_starters(players, slots)) >= needed


def _swap(players: list[Player], out: tuple[Player, ...], incoming: tuple[Player, ...]):
    out_ids = {p.player_id for p in out}
    return [p for p in players if p.player_id not in out_ids] + list(incoming)


def _packages(players: list[Player], max_size: int):
    for size in range(1, max_size + 1):
        yield from combinations(players, size)


def _candidates(
    players: list[Player], replacement: dict[str, float], limit: int
) -> list[Player]:
    """The players most worth talking about: highest value over replacement.

    Used for both sides, so the search is symmetric.
    """
    return sorted(players, key=lambda p: -vorp(p, replacement))[:limit]


def find_trades(
    me: TeamRoster,
    others: list[TeamRoster],
    roster_positions: list[str],
    replacement: dict[str, float],
    valued_positions: set[str],
    shopping: dict[tuple[int, str], int] | None = None,
    per_team_limit: int = 3,
    max_package: int = MAX_PACKAGE,
) -> list[TradeIdea]:
    """Every package that improves both rosters, best joint surplus first.

    `replacement` must be computed once over the whole pool and passed in, so
    the two sides' surpluses are on the same scale.

    `shopping` is optional enrichment: (roster_id, player_id) -> how many times
    that manager has offered that player in a trade. It only breaks ties and
    annotates; the search works identically without it.
    """
    shopping = shopping or {}
    slots = modelled_slots(roster_positions, valued_positions)
    if not is_feasible(me.players, me.unknown_count, slots, roster_positions):
        # Not "no trades exist": our own roster cannot even be filed, which
        # means the pool failed to resolve it. Fail loudly rather than
        # returning an empty list that reads like a clean negative answer.
        raise ValueError(
            f"Your roster cannot fill the modelled slots {slots} "
            f"({len(me.players)} valued players, {me.unknown_count} unvalued)"
        )
    my_before = roster_value(me.players, slots, replacement)
    my_send_pool = _candidates(me.players, replacement, MAX_CANDIDATES)

    ideas: list[TradeIdea] = []
    for other in others:
        if not is_feasible(other.players, other.unknown_count, slots, roster_positions):
            continue  # We cannot value this roster well enough to trade with it.
        their_before = roster_value(other.players, slots, replacement)
        their_send_pool = _candidates(other.players, replacement, MAX_CANDIDATES)
        found: list[TradeIdea] = []

        for send in _packages(my_send_pool, max_package):
            for receive in _packages(their_send_pool, max_package):
                if len(send) > 1 and len(receive) > 1:
                    continue  # 2-for-2 blows up the search for little extra
                mine = _swap(me.players, send, receive)
                theirs = _swap(other.players, receive, send)
                my_unknown = me.unknown_count
                their_unknown = other.unknown_count
                if not is_feasible(mine, my_unknown, slots, roster_positions):
                    continue
                if not is_feasible(theirs, their_unknown, slots, roster_positions):
                    continue

                my_surplus = roster_value(mine, slots, replacement) - my_before
                their_surplus = roster_value(theirs, slots, replacement) - their_before
                if my_surplus <= MIN_SURPLUS or their_surplus <= MIN_SURPLUS:
                    continue

                hits = sum(shopping.get((other.roster_id, p.player_id), 0) for p in receive)
                found.append(
                    TradeIdea(
                        roster_id=other.roster_id,
                        owner_name=other.owner_name,
                        send=list(send),
                        receive=list(receive),
                        my_surplus=my_surplus,
                        their_surplus=their_surplus,
                        shopping_hits=hits,
                        notes=_notes(other, slots, receive, hits),
                    )
                )

        ideas.extend(_undominated(found)[:per_team_limit])

    ideas.sort(key=lambda t: (-t.joint_surplus, -t.shopping_hits))
    return ideas


def _notes(other: TeamRoster, slots: list[str], receive, hits: int) -> list[str]:
    notes = []
    bench_ids = {p.player_id for p in other.players} - {
        p.player_id for p in select_starters(other.players, slots)
    }
    spare = [p.name for p in receive if p.player_id in bench_ids]
    if spare:
        notes.append(f"{', '.join(spare)} sits on their bench, so it is depth they can spare.")
    if hits:
        notes.append(f"They have offered a player in this package {hits} time(s) before.")
    return notes


def _undominated(found: list[TradeIdea]) -> list[TradeIdea]:
    """Drop packages another package beats outright, best joint surplus first.

    A package is dominated when a smaller one is at least as good for both
    sides: a 2-for-1 that moves the needle exactly as far as the 1-for-1 inside
    it is just extra bodies. Ties on both surpluses keep the smaller package.
    """
    ordered = sorted(
        found,
        key=lambda t: (len(t.send) + len(t.receive), -t.joint_surplus, -t.shopping_hits),
    )
    kept: list[TradeIdea] = []
    for idea in ordered:
        size = len(idea.send) + len(idea.receive)
        if any(
            len(k.send) + len(k.receive) <= size
            and k.my_surplus >= idea.my_surplus - 1e-9
            and k.their_surplus >= idea.their_surplus - 1e-9
            for k in kept
        ):
            continue
        kept.append(idea)
    kept.sort(key=lambda t: (-t.joint_surplus, -t.shopping_hits))
    return kept


def rank_for_me(ideas: list[TradeIdea]) -> list[TradeIdea]:
    """Same ideas, ordered by what they do for us rather than jointly."""
    return sorted(ideas, key=lambda t: (-t.my_surplus, -t.shopping_hits))


# Trade block / shopping signal.
#
# Sleeper's public REST API has no trade block endpoint. The published docs at
# https://docs.sleeper.com/ never mention one and enumerate the whole endpoint
# set (user, league, rosters, users, matchups, playoff bracket, transactions,
# traded picks, NFL state, drafts); /league/<id>/trade_block and three other
# plausible spellings all 404 against a live league. The app's trade block
# presumably rides Sleeper's unpublished GraphQL, which is out of scope here.
#
# The closer substitute is trade history including the offers that never
# landed. Sleeper's private GraphQL `league_transactions_filtered` returns
# proposed / rejected / cancelled / vetoed trades, not just the completed ones
# the REST API exposes. That needs an auth token this project does not have, so
# the function below takes the raw transactions as data and stays optional:
# with no token you pass nothing and the finder behaves the same.


def shopping_signal(transactions: list[dict]) -> dict[tuple[int, str], int]:
    """(roster_id, player_id) -> how often that manager offered that player.

    Reads Sleeper's transaction shape: `drops` maps a player id to the roster
    id giving them up. Any trade counts, whatever its status: a rejected or
    cancelled offer still says the manager was willing to move the player.
    """
    counts: dict[tuple[int, str], int] = {}
    for txn in transactions:
        if txn.get("type") != "trade":
            continue
        for player_id, roster_id in (txn.get("drops") or {}).items():
            if roster_id is None:
                continue
            key = (int(roster_id), str(player_id))
            counts[key] = counts.get(key, 0) + 1
    return counts
