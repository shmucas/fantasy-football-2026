"""Decide this week's lineup from the projections we already have.

Sleeper's `starters` is positional: index 0 is the first starting slot, index 1
the second, and so on, with BN/IR/TAXI removed. Nothing validates it - sending a
correctly-sized list in the wrong order silently starts the wrong players. That
alignment is the whole risk in this file, so it is done in one place and tested
hard.
"""

from dataclasses import dataclass

from ffb.draft.strategy import FLEX_ELIGIBLE, Player
from ffb.sleeper_private import starting_slots

# What Sleeper puts in a starting slot that is deliberately empty.
EMPTY_SLOT = "0"


@dataclass(frozen=True)
class LineupChange:
    slots: tuple[str, ...]           # slot names, in order
    proposed: tuple[str, ...]        # player ids, aligned to slots
    current: tuple[str, ...]         # what is set now, aligned where possible
    moves: tuple[tuple[str, str, str], ...]  # (slot, out_name, in_name)
    points_gained: float

    @property
    def is_change(self) -> bool:
        """Worth proposing only if it moves someone AND gains points.

        A change that projects to lose points is not worth a human's attention,
        and is usually a sign the optimiser cannot see a player it is replacing
        - so it fails closed and leaves the lineup alone.
        """
        return bool(self.moves) and self.points_gained > 0

    def summary(self) -> str:
        if not self.is_change:
            return "Lineup is already optimal"
        n = len(self.moves)
        return (
            f"Start {n} different player{'s' if n > 1 else ''} "
            f"(+{self.points_gained:.1f} projected points)"
        )

    def detail(self) -> str:
        return "\n".join(
            f"  {slot}: {out} -> {new}" for slot, out, new in self.moves
        )


def optimal_starters(roster: list[Player], roster_positions: list[str]) -> list[str]:
    """Best lineup as player ids, aligned to the league's starting slots.

    Fixed slots are filled first so a flex cannot steal a player that only a
    dedicated slot can use; flex slots then take the best of what is left.
    Slots with nobody eligible get EMPTY_SLOT rather than shifting everything
    after them, which would misalign the whole list.
    """
    slots = starting_slots(roster_positions)

    by_position: dict[str, list[Player]] = {}
    for player in roster:
        by_position.setdefault(player.position, []).append(player)
    for players in by_position.values():
        players.sort(key=lambda p: -p.proj_points)

    filled: dict[int, str] = {}
    used: set[str] = set()

    # Pass 1: dedicated slots, in slot order.
    for index, slot in enumerate(slots):
        if _is_flex(slot):
            continue
        for player in by_position.get(slot, []):
            if player.player_id not in used:
                filled[index] = player.player_id
                used.add(player.player_id)
                break

    # Pass 2: flex slots take the best remaining flex-eligible player.
    flex_pool = sorted(
        (p for p in roster if p.position in FLEX_ELIGIBLE and p.player_id not in used),
        key=lambda p: -p.proj_points,
    )
    for index, slot in enumerate(slots):
        if not _is_flex(slot):
            continue
        while flex_pool:
            candidate = flex_pool.pop(0)
            if candidate.player_id not in used:
                filled[index] = candidate.player_id
                used.add(candidate.player_id)
                break

    return [filled.get(i, EMPTY_SLOT) for i in range(len(slots))]


def plan(
    roster: list[Player],
    roster_positions: list[str],
    current_starters: list[str],
) -> LineupChange:
    """Compare the optimal lineup against what is set now."""
    slots = starting_slots(roster_positions)
    proposed = optimal_starters(roster, roster_positions)

    # Pad or trim what Sleeper reports so the two line up slot for slot.
    current = list(current_starters[: len(slots)])
    current += [EMPTY_SLOT] * (len(slots) - len(current))

    # Our pool does not cover every position - it has no kickers at all, and a
    # newly signed player may be missing too. The optimiser cannot see those
    # players, so left alone it would propose benching a perfectly good kicker
    # in favour of nobody. Never vacate a slot that currently holds someone.
    proposed = [
        now if new in (EMPTY_SLOT, "") and now not in (EMPTY_SLOT, "") else new
        for new, now in zip(proposed, current)
    ]
    proposed = _keep_players_put(slots, proposed, current, roster)

    by_id = {p.player_id: p for p in roster}

    def label(player_id: str) -> str:
        if player_id in (EMPTY_SLOT, ""):
            return "empty"
        player = by_id.get(player_id)
        return player.name if player else player_id

    def points(player_id: str) -> float:
        player = by_id.get(player_id)
        return player.proj_points if player else 0.0

    moves = tuple(
        (slot, label(old), label(new))
        for slot, old, new in zip(slots, current, proposed)
        if old != new
    )
    gained = sum(points(new) - points(old) for old, new in zip(current, proposed))

    return LineupChange(
        slots=tuple(slots),
        proposed=tuple(proposed),
        current=tuple(current),
        moves=moves,
        points_gained=round(gained, 2),
    )


def _keep_players_put(
    slots: list[str], proposed: list[str], current: list[str], roster: list[Player]
) -> list[str]:
    """Same set of starters, fewest moves.

    The optimiser fills slots left to right, so two flex-eligible players who
    are both already starting can come back swapped between the two flex slots
    - a change that gains nothing and reads as noise in an approval message.
    Anyone who is starting and stays starting keeps their existing slot.
    """
    by_id = {p.player_id: p for p in roster}
    wanted = [pid for pid in proposed if pid not in (EMPTY_SLOT, "")]

    settled: dict[int, str] = {}
    for index, slot in enumerate(slots):
        occupant = current[index]
        if occupant in (EMPTY_SLOT, "") or occupant not in wanted:
            continue
        if _eligible(by_id.get(occupant), slot, occupant in proposed):
            settled[index] = occupant
            wanted.remove(occupant)

    remaining = [pid for pid in proposed if pid in wanted]
    out: list[str] = []
    for index in range(len(slots)):
        if index in settled:
            out.append(settled[index])
        elif remaining:
            out.append(remaining.pop(0))
        else:
            out.append(EMPTY_SLOT)
    return out


def _eligible(player: Player | None, slot: str, already_proposed: bool) -> bool:
    """Can this player legally fill this slot?

    Unknown players (not in our pool) are only left where they already are,
    never moved somewhere we cannot verify they belong.
    """
    if player is None:
        return already_proposed
    if _is_flex(slot):
        return player.position in FLEX_ELIGIBLE
    return player.position == slot


def _is_flex(slot: str) -> bool:
    # Sleeper spells flex several ways; they all take RB/WR/TE here.
    return "FLEX" in slot
