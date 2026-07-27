"""Decide which waiver claim, if any, is worth making.

Reuses the same replacement-level and VORP maths the Waivers tab shows, so the
claim the bot proposes is the one the app already recommends - no second,
divergent opinion about who is good.
"""

from collections import Counter
from dataclasses import dataclass

from ffb.draft.strategy import Player, replacement_levels, vorp

# Don't propose a claim for a marginal upgrade; a roster spot has value and
# every claim spends either FAAB or waiver priority.
MIN_VORP_GAIN = 10.0


@dataclass(frozen=True)
class WaiverClaim:
    add: Player
    drop: Player | None
    vorp_gain: float
    fills_need: bool
    faab_bid: int

    def summary(self) -> str:
        bid = f" for ${self.faab_bid}" if self.faab_bid else ""
        dropping = f", dropping {self.drop.name}" if self.drop else ""
        return f"Claim {self.add.name} ({self.add.position}){bid}{dropping}"

    def detail(self) -> str:
        why = (
            f"  {self.add.name}: {self.add.proj_points:.0f} projected points, "
            f"{self.vorp_gain:.0f} better than "
            + (f"{self.drop.name}" if self.drop else "replacement level")
        )
        if self.fills_need:
            why += f"\n  Fills an unfilled starting {self.add.position} slot."
        return why


def best_claim(
    my_players: list[Player],
    available: list[Player],
    all_players: list[Player],
    roster_positions: list[str],
    num_teams: int,
    *,
    roster_is_full: bool = True,
    faab_budget_left: int | None = None,
    min_gain: float = MIN_VORP_GAIN,
) -> WaiverClaim | None:
    """The single claim most worth making, or None if nothing clears the bar.

    Deliberately returns one claim rather than a ranked list: this feeds an
    approval message, and a list of fifteen names is not a decision.
    """
    if not available:
        return None

    replacement = replacement_levels(all_players, roster_positions, num_teams)
    target = max(available, key=lambda p: vorp(p, replacement))
    target_vorp = vorp(target, replacement)

    drop = _drop_candidate(my_players, replacement, roster_positions) if roster_is_full else None
    baseline = vorp(drop, replacement) if drop else 0.0
    gain = target_vorp - baseline
    if gain < min_gain:
        return None

    return WaiverClaim(
        add=target,
        drop=drop,
        vorp_gain=round(gain, 1),
        fills_need=_fills_need(target, my_players, roster_positions),
        faab_bid=_bid(gain, faab_budget_left),
    )


def _drop_candidate(
    my_players: list[Player], replacement: dict[str, float], roster_positions: list[str]
) -> Player | None:
    """Who to cut: the least valuable player we are not relying on to start.

    Anyone holding down a starting slot is protected, so a claim can never
    quietly drop the only tight end on the roster.
    """
    if not my_players:
        return None

    needed: Counter[str] = Counter()
    for slot in roster_positions:
        if slot in ("BN", "IR", "TAXI"):
            continue
        needed[slot] += 1

    by_position: dict[str, list[Player]] = {}
    for player in my_players:
        by_position.setdefault(player.position, []).append(player)
    for players in by_position.values():
        players.sort(key=lambda p: -p.proj_points)

    protected: set[str] = set()
    for position, count in needed.items():
        for player in by_position.get(position, [])[:count]:
            protected.add(player.player_id)

    droppable = [p for p in my_players if p.player_id not in protected]
    if not droppable:
        return None
    return min(droppable, key=lambda p: vorp(p, replacement))


def _fills_need(target: Player, my_players: list[Player], roster_positions: list[str]) -> bool:
    needed = sum(1 for slot in roster_positions if slot == target.position)
    have = sum(1 for p in my_players if p.position == target.position)
    return have < needed


def _bid(gain: float, budget_left: int | None) -> int:
    """A starting FAAB number, scaled by how big the upgrade is.

    Intentionally simple and explainable rather than clever: this is a starting
    point for a human who is about to see it and can say no.
    """
    if not budget_left or budget_left <= 0:
        return 0
    share = min(0.35, max(0.02, gain / 200.0))
    return max(1, int(round(budget_left * share)))
