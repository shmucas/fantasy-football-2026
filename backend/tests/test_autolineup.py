"""Building the starters array Sleeper expects.

Order is the whole risk here. Sleeper takes a flat list and reads slot meaning
from position in that list, so an array that is right in content and wrong in
order silently starts a running back at tight end.
"""

import pytest

from ffb.autolineup import ordered_starters, starting_slots

POS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN"]


def pick(pid, position, pts, name=""):
    return {"player_id": pid, "position": position, "proj_points": pts,
            "name": name or f"{position}{pid}"}


# A full modelled lineup: one QB, two RB, two WR, one TE, one flex.
OPTIMAL = [
    pick("qb1", "QB", 300), pick("rb1", "RB", 280), pick("rb2", "RB", 260),
    pick("wr1", "WR", 250), pick("wr2", "WR", 240), pick("te1", "TE", 200),
    pick("rb3", "RB", 190),  # the flex
]
CURRENT = ["old_qb", "old_rb", "old_rb2", "old_wr", "old_wr2", "old_te",
           "old_flex", "k9", "SEA"]


def test_starting_slots_drops_bench_and_ir():
    assert starting_slots(POS) == ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
    assert starting_slots(["QB", "IR", "BN"]) == ["QB"]


def test_each_player_lands_in_a_slot_of_his_own_position():
    out = ordered_starters(POS, OPTIMAL, CURRENT)
    slots = starting_slots(POS)
    by_id = {p["player_id"]: p for p in OPTIMAL}
    for slot, pid in zip(slots, out):
        if slot in ("K", "DEF") or slot == "FLEX":
            continue
        assert by_id[pid]["position"] == slot, f"{pid} is in a {slot} slot"


def test_kicker_and_defense_keep_their_own_slots():
    # The pool cannot value them, so they must come back untouched and in place.
    out = ordered_starters(POS, OPTIMAL, CURRENT)
    assert out[7] == "k9"
    assert out[8] == "SEA"


def test_flex_takes_a_spare_and_not_a_player_a_fixed_slot_needs():
    out = ordered_starters(POS, OPTIMAL, CURRENT)
    assert out[6] == "rb3"           # the third RB goes to the flex
    assert out[1:3] == ["rb1", "rb2"]  # and the RB slots still have theirs


def test_every_player_appears_once():
    out = ordered_starters(POS, OPTIMAL, CURRENT)
    assert len(out) == len(set(out)) == 9


def test_refuses_when_sleeper_disagrees_about_how_many_slots_there_are():
    # Writing a lineup against a roster shape we do not understand would put
    # players in arbitrary slots, so this must raise rather than guess.
    with pytest.raises(ValueError, match="Refusing to guess"):
        ordered_starters(POS, OPTIMAL, CURRENT[:5])


def test_refuses_a_partial_lineup():
    # One receiver short: better to set nothing than to leave a slot empty.
    thin = [p for p in OPTIMAL if p["player_id"] != "wr2"]
    with pytest.raises(ValueError, match="Refusing to set a partial lineup"):
        ordered_starters(POS, thin, CURRENT)


def test_an_already_correct_lineup_comes_back_unchanged():
    current = ["qb1", "rb1", "rb2", "wr1", "wr2", "te1", "rb3", "k9", "SEA"]
    assert ordered_starters(POS, OPTIMAL, current) == current


def test_slot_order_is_followed_even_when_positions_are_interleaved():
    # Nothing guarantees a league lists its slots grouped by position, and the
    # optimal lineup arrives sorted by points rather than by slot.
    pos = ["RB", "WR", "RB", "WR", "QB", "TE", "FLEX", "K", "DEF"]
    current = ["a", "b", "c", "d", "e", "f", "g", "k9", "SEA"]
    out = ordered_starters(pos, OPTIMAL, current)
    assert [p[:2] for p in out[:6]] == ["rb", "wr", "rb", "wr", "qb", "te"]
    assert out[7:] == ["k9", "SEA"]


def test_a_player_already_starting_is_left_in_his_own_slot():
    # Both RB slots are interchangeable, so reshuffling the two running backs
    # between them scores exactly the same and only makes the log unreadable.
    current = ["qb1", "rb2", "rb1", "wr1", "wr2", "te1", "rb3", "k9", "SEA"]
    assert ordered_starters(POS, OPTIMAL, current) == current


def test_only_the_real_swap_moves():
    # One new tight end, everything else already right: exactly one slot changes.
    current = ["qb1", "rb1", "rb2", "wr1", "wr2", "old_te", "rb3", "k9", "SEA"]
    out = ordered_starters(POS, OPTIMAL, current)
    assert out[5] == "te1"
    assert [i for i, (a, b) in enumerate(zip(current, out)) if a != b] == [5]
