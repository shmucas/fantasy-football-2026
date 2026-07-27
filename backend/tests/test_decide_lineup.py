"""Lineup decisions, and the slot alignment that makes them safe.

Sleeper validates nothing about `starters` beyond its length, so a
correctly-sized list in the wrong order starts the wrong players and looks
fine. These tests are about that ordering more than anything else.
"""

from ffb.decide import lineup
from ffb.draft.strategy import Player

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN", "IR"]


def p(pid: str, position: str, points: float, name: str | None = None) -> Player:
    return Player(
        player_id=pid, name=name or f"{position}{pid}", position=position,
        proj_points=points, proj_stdev=1.0, adp=1.0,
    )


def test_starters_are_aligned_to_slots_in_order():
    roster = [p("qb", "QB", 300), p("r1", "RB", 250), p("r2", "RB", 200),
              p("w1", "WR", 240), p("w2", "WR", 210), p("te", "TE", 150),
              p("r3", "RB", 120)]
    starters = lineup.optimal_starters(roster, SLOTS)
    # QB, RB, RB, WR, WR, TE, FLEX
    assert starters == ["qb", "r1", "r2", "w1", "w2", "te", "r3"]


def test_best_player_at_a_position_starts():
    roster = [p("bench", "QB", 100), p("stud", "QB", 400)]
    assert lineup.optimal_starters(roster, ["QB", "BN"]) == ["stud"]


def test_flex_takes_the_best_remaining_not_one_a_fixed_slot_needs():
    """The RB2 slot must not be robbed by the flex."""
    roster = [p("r1", "RB", 250), p("r2", "RB", 240), p("w1", "WR", 245)]
    starters = lineup.optimal_starters(roster, ["RB", "RB", "FLEX"])
    assert starters[0] == "r1"
    assert starters[1] == "r2"
    assert starters[2] == "w1"


def test_a_slot_with_nobody_eligible_is_empty_not_shifted():
    """Shifting would misalign every slot after it - the dangerous failure."""
    roster = [p("qb", "QB", 300), p("te", "TE", 150)]
    starters = lineup.optimal_starters(roster, ["QB", "RB", "TE"])
    assert starters == ["qb", lineup.EMPTY_SLOT, "te"]


def test_bench_ir_and_taxi_slots_are_never_part_of_starters():
    roster = [p("qb", "QB", 300)]
    starters = lineup.optimal_starters(roster, ["QB", "BN", "BN", "IR", "TAXI"])
    assert len(starters) == 1


def test_no_player_is_started_twice():
    roster = [p("only", "RB", 200)]
    starters = lineup.optimal_starters(roster, ["RB", "RB", "FLEX"])
    assert starters.count("only") == 1
    assert starters[1] == lineup.EMPTY_SLOT


def test_an_empty_roster_produces_all_empty_slots():
    assert lineup.optimal_starters([], ["QB", "RB"]) == [lineup.EMPTY_SLOT] * 2


# --- plan(): comparing against what is set now ------------------------------


def test_optimal_lineup_proposes_no_change():
    roster = [p("qb", "QB", 300), p("r1", "RB", 250)]
    change = lineup.plan(roster, ["QB", "RB"], ["qb", "r1"])
    assert change.is_change is False
    assert change.moves == ()
    assert "already optimal" in change.summary()


def test_a_benched_stud_is_detected_with_the_points_it_gains():
    roster = [p("stud", "QB", 400, "Stud"), p("scrub", "QB", 100, "Scrub")]
    change = lineup.plan(roster, ["QB", "BN"], ["scrub"])
    assert change.is_change is True
    assert change.moves == (("QB", "Scrub", "Stud"),)
    assert change.points_gained == 300.0
    assert "+300.0 projected points" in change.summary()


def test_a_short_current_lineup_is_padded_not_misaligned():
    """Sleeper can report fewer starters than there are slots. Padding must go
    on the end, so the slots that were reported stay where they are."""
    roster = [p("qb", "QB", 300), p("r1", "RB", 250, "Bijan")]
    change = lineup.plan(roster, ["QB", "RB"], ["qb"])
    assert change.current == ("qb", lineup.EMPTY_SLOT)
    assert change.proposed == ("qb", "r1")
    assert change.moves == (("RB", "empty", "Bijan"),)


def test_an_overlong_current_lineup_is_trimmed():
    roster = [p("qb", "QB", 300)]
    change = lineup.plan(roster, ["QB"], ["qb", "stale", "stale2"])
    assert change.current == ("qb",)
    assert change.is_change is False


def test_empty_slots_are_described_as_empty_not_as_an_id():
    roster = [p("qb", "QB", 300)]
    change = lineup.plan(roster, ["QB", "RB"], ["qb", "0"])
    assert all("0" not in move[1] for move in change.moves)


def test_moves_name_players_not_ids():
    roster = [p("a", "RB", 200, "Bijan Robinson"), p("b", "RB", 100, "Backup Guy")]
    change = lineup.plan(roster, ["RB", "BN"], ["b"])
    assert change.moves == (("RB", "Backup Guy", "Bijan Robinson"),)


def test_proposed_length_always_matches_slot_count():
    """The wire format depends on this; a mismatch is rejected or misapplied."""
    for positions in (
        ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN"],
        ["QB", "BN"],
        ["QB", "RB", "WR", "FLEX", "FLEX", "IR"],
    ):
        roster = [p("x", "QB", 10)]
        change = lineup.plan(roster, positions, [])
        expected = len([s for s in positions if s not in ("BN", "IR", "TAXI")])
        assert len(change.proposed) == expected
        assert len(change.slots) == expected


# --- refusing to make things worse ------------------------------------------


def test_a_slot_we_cannot_fill_keeps_whoever_is_there():
    """Our pool has no kickers, so the optimiser cannot see one. It must not
    propose benching a real kicker in favour of an empty slot."""
    roster = [p("qb", "QB", 300)]  # no kicker in our projections
    change = lineup.plan(roster, ["QB", "K"], ["qb", "my-kicker"])
    assert change.proposed == ("qb", "my-kicker")
    assert change.is_change is False


def test_an_unknown_player_in_a_slot_is_not_dropped_for_nobody():
    roster = [p("qb", "QB", 300)]
    change = lineup.plan(roster, ["QB", "DEF"], ["qb", "some-defense"])
    assert "some-defense" in change.proposed


def test_a_change_that_loses_points_is_not_proposed():
    """Fails closed: if the maths says it is worse, leave the lineup alone."""
    roster = [p("known", "RB", 50, "Known")]
    # The current starter is not in our pool, so it looks like 0 points to us.
    change = lineup.plan(roster, ["RB", "BN"], ["unknown-stud"])
    # We would swap in our known player, gaining 50 by our reckoning.
    assert change.points_gained == 50.0
    assert change.is_change is True

    # But if our own player is worth nothing, there is nothing to gain.
    roster_zero = [p("known", "RB", 0, "Known")]
    flat = lineup.plan(roster_zero, ["RB", "BN"], ["unknown-stud"])
    assert flat.points_gained == 0.0
    assert flat.is_change is False


def test_an_empty_slot_still_gets_filled_when_we_have_someone():
    roster = [p("r1", "RB", 200, "Bijan")]
    change = lineup.plan(roster, ["RB"], ["0"])
    assert change.proposed == ("r1",)
    assert change.is_change is True


def test_players_already_starting_are_not_shuffled_between_equivalent_slots():
    """Two flex-eligible starters must not come back swapped for no gain."""
    cmc = p("cmc", "RB", 280, "CMC")
    jsn = p("jsn", "WR", 260, "JSN")
    roster = [p("qb", "QB", 300), cmc, jsn]
    slots = ["QB", "FLEX", "FLEX"]
    change = lineup.plan(roster, slots, ["qb", "cmc", "jsn"])
    assert change.proposed == ("qb", "cmc", "jsn")
    assert change.moves == ()
    assert change.is_change is False


def test_a_real_upgrade_still_happens_without_churn():
    """Only the genuinely improved slot should move."""
    stud = p("stud", "RB", 300, "Stud")
    ok = p("ok", "WR", 200, "OK")
    bench = p("bench", "RB", 50, "Bench")
    roster = [stud, ok, bench]
    change = lineup.plan(roster, ["FLEX", "FLEX"], ["bench", "ok"])
    assert set(change.proposed) == {"stud", "ok"}
    assert change.is_change is True
    # "ok" was already starting, so it should not be listed as a move.
    assert len(change.moves) == 1
    assert change.moves[0][2] == "Stud"
