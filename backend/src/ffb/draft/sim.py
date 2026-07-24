"""Single-draft simulation: snake draft, all opponents ADP+need-modeled,
except optional forced picks for our own slot to test 'what if I take X here'."""

import random
from dataclasses import dataclass, field

from ffb.draft.strategy import Player, opponent_pick


@dataclass
class DraftResult:
    rosters: dict[int, list[Player]] = field(default_factory=dict)  # team_slot -> players drafted
    pick_order: list[tuple[int, Player]] = field(default_factory=list)  # (team_slot, player)


def snake_order(num_teams: int, rounds: int) -> list[int]:
    order: list[int] = []
    for rnd in range(rounds):
        team_range = range(1, num_teams + 1)
        order.extend(reversed(team_range) if rnd % 2 == 1 else team_range)
    return order


def simulate_draft(
    players: list[Player],
    num_teams: int,
    rounds: int,
    roster_positions: list[str],
    my_team_slot: int,
    rng: random.Random,
    forced_picks: dict[int, str] | None = None,  # round -> player_id, forces OUR pick that round
) -> DraftResult:
    forced_picks = forced_picks or {}

    # Reserve forced players for our own pick: opponents never see them, so the
    # scenario "I commit to player X in round R" is always realized and can't be
    # sniped before our slot. Missing IDs fail loudly here, not silently.
    reserved: dict[str, Player] = {}
    for rnd, pid in forced_picks.items():
        match = next((p for p in players if p.player_id == pid), None)
        if match is None:
            raise ValueError(f"Forced pick round {rnd}: player_id {pid!r} not in pool")
        reserved[pid] = match

    available = [p for p in players if p.player_id not in reserved]
    rosters: dict[int, list[Player]] = {slot: [] for slot in range(1, num_teams + 1)}
    pick_order: list[tuple[int, Player]] = []

    order = snake_order(num_teams, rounds)
    for i, team_slot in enumerate(order):
        current_round = i // num_teams + 1

        if team_slot == my_team_slot and current_round in forced_picks:
            pick = reserved[forced_picks[current_round]]
        else:
            pick = opponent_pick(available, rosters[team_slot], roster_positions, rng)
            available.remove(pick)
        rosters[team_slot].append(pick)
        pick_order.append((team_slot, pick))

    return DraftResult(rosters=rosters, pick_order=pick_order)


def roster_projected_points(roster: list[Player], roster_positions: list[str]) -> float:
    """Sum of projected points from the best lineup this roster can field
    (ignores bench, respects starter + flex slot counts)."""
    from collections import Counter

    from ffb.draft.strategy import FLEX_ELIGIBLE

    starter_slots = Counter(s for s in roster_positions if s not in ("BN",))
    flex_slots = starter_slots.pop("FLEX", 0)

    by_pos: dict[str, list[Player]] = {}
    for p in roster:
        by_pos.setdefault(p.position, []).append(p)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda p: -p.proj_points)

    total = 0.0
    used: set[str] = set()
    for pos, count in starter_slots.items():
        for p in by_pos.get(pos, [])[:count]:
            total += p.proj_points
            used.add(p.player_id)

    flex_pool = sorted(
        (p for p in roster if p.position in FLEX_ELIGIBLE and p.player_id not in used),
        key=lambda p: -p.proj_points,
    )
    for p in flex_pool[:flex_slots]:
        total += p.proj_points

    return total
