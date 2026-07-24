"""Draft value + opponent pick modeling.

VORP (value over replacement) ranks players by how much better they are than
the last starter-worthy player at their position, given this league's roster
rules - not just raw projected points.

Opponents are modeled as: softmax over ADP-implied value, with a penalty for
positions they've already filled. This keeps simulated rosters realistic
(no team drafting 5 RBs) without needing a full per-manager behavior model.
"""

import math
import random
from collections import Counter
from dataclasses import dataclass

FLEX_ELIGIBLE = {"RB", "WR", "TE"}


@dataclass(frozen=True)
class Player:
    player_id: str
    name: str
    position: str
    proj_points: float
    proj_stdev: float
    adp: float


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


def opponent_pick(
    available: list[Player],
    roster_so_far: list[Player],
    roster_positions: list[str],
    rng: random.Random,
    temperature: float = 6.0,
) -> Player:
    """Pick one player for a simulated opponent: ADP-value softmax, penalized
    for positions already over-filled relative to starting need."""
    need_slots = Counter(slot for slot in roster_positions if slot != "BN")
    filled = Counter(p.position for p in roster_so_far)

    def need_penalty(pos: str) -> float:
        starter_need = need_slots.get(pos, 0)
        flex_capacity = need_slots.get("FLEX", 0) if pos in FLEX_ELIGIBLE else 0
        room = starter_need + flex_capacity - filled.get(pos, 0)
        return 0.0 if room > 0 else -8.0  # soft penalty once starting need is met

    scores = []
    for p in available:
        adp_value = -p.adp  # lower ADP (drafted earlier) = higher value
        scores.append(adp_value / temperature + need_penalty(p.position) + rng.gauss(0, 1.5))

    max_score = max(scores)
    weights = [math.exp(s - max_score) for s in scores]
    return rng.choices(available, weights=weights, k=1)[0]
