"""Draft value + opponent pick modeling.

VORP (value over replacement) ranks players by how much better they are than
the last starter-worthy player at their position, given this league's roster
rules - not just raw projected points.

Opponent model (gaussian draft slot): each opponent draws an effective draft
slot per available player = ADP + Normal(0, sigma), and takes the player with
the best (lowest) noisy slot, after a positional-need penalty. This mirrors how
real drafts run down an ADP board with human noise, instead of compressing the
whole board into a softmax where even a far-off player has some chance.

sigma grows by round: small early (chalk, ~a few spots) and larger in later
rounds where managers reach for sleepers. A player's own ADP stdev (from FFC)
sets a floor, so genuinely volatile players stay volatile.
"""

import random
from collections import Counter
from dataclasses import dataclass

FLEX_ELIGIBLE = {"RB", "WR", "TE"}

# Round-scaled draft-slot noise, in overall-pick units.
SIGMA_BASE = 3.0        # round 1 noise floor (~+/- 3 spots)
SIGMA_PER_ROUND = 1.3   # added std per round beyond the first
NEED_PENALTY_SLOTS = 25.0  # slots added once a position's starting need is met


@dataclass(frozen=True)
class Player:
    player_id: str
    name: str
    position: str
    proj_points: float
    proj_stdev: float
    adp: float
    adp_stdev: float = 0.0
    game_cv: float = 0.8    # weekly (game-to-game) coefficient of variation
    season_cv: float = 0.3  # season-to-season coefficient of variation


def replacement_levels(players: list[Player], roster_positions: list[str], num_teams: int) -> dict[str, float]:
    """Points of the last startable player at each position, league-wide."""
    starters_needed: Counter[str] = Counter()
    flex_slots = 0
    for slot in roster_positions:
        if slot == "BN":
            continue
        if slot == "FLEX":
            flex_slots += 1
        else:
            starters_needed[slot] += 1

    by_pos: dict[str, list[Player]] = {}
    for p in players:
        by_pos.setdefault(p.position, []).append(p)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda p: -p.proj_points)

    # Distribute flex slots to the position with the best marginal player at that depth.
    flex_pool = sorted(
        (p for p in players if p.position in FLEX_ELIGIBLE),
        key=lambda p: -p.proj_points,
    )
    flex_taken: Counter[str] = Counter()
    base_depth = {pos: starters_needed[pos] * num_teams for pos in starters_needed}
    idx = 0
    for p in flex_pool:
        rank_in_pos = sum(1 for q in by_pos[p.position] if q.proj_points >= p.proj_points)
        if rank_in_pos <= base_depth.get(p.position, 0):
            continue  # already counted as a starter at their own position
        if idx >= flex_slots * num_teams:
            break
        flex_taken[p.position] += 1
        idx += 1

    levels = {}
    for pos, plist in by_pos.items():
        depth = starters_needed.get(pos, 0) * num_teams + flex_taken.get(pos, 0)
        depth = min(depth, len(plist))
        levels[pos] = plist[depth - 1].proj_points if depth > 0 else plist[-1].proj_points * 0.5
    return levels


def vorp(player: Player, replacement: dict[str, float]) -> float:
    return player.proj_points - replacement.get(player.position, 0)


def round_sigma(current_round: int) -> float:
    """Draft-slot noise (std, in pick units) for a given round."""
    return SIGMA_BASE + SIGMA_PER_ROUND * (current_round - 1)


def opponent_pick(
    available: list[Player],
    roster_so_far: list[Player],
    roster_positions: list[str],
    rng: random.Random,
    current_round: int,
) -> Player:
    """Simulated opponent pick via noisy draft slot.

    effective_slot = ADP + Normal(0, sigma) + need_penalty; the opponent takes
    the smallest effective_slot. sigma is the larger of the round noise and the
    player's own ADP stdev, so both round-level reaching and per-player
    volatility show up. The need penalty pushes back positions whose starting
    requirement is already filled, keeping rosters realistic.
    """
    need_slots = Counter(slot for slot in roster_positions if slot != "BN")
    filled = Counter(p.position for p in roster_so_far)

    def need_penalty(pos: str) -> float:
        starter_need = need_slots.get(pos, 0)
        flex_capacity = need_slots.get("FLEX", 0) if pos in FLEX_ELIGIBLE else 0
        room = starter_need + flex_capacity - filled.get(pos, 0)
        return 0.0 if room > 0 else NEED_PENALTY_SLOTS

    base_sigma = round_sigma(current_round)
    best_player = None
    best_slot = float("inf")
    for p in available:
        sigma = max(base_sigma, p.adp_stdev)
        effective_slot = p.adp + rng.gauss(0, sigma) + need_penalty(p.position)
        if effective_slot < best_slot:
            best_slot = effective_slot
            best_player = p
    return best_player
