"""Start/sit only helps if it never mistakes "we could not look" for "no change"."""

import pytest

from ffb.draft.strategy import Player
from ffb.lineup import (
    LineupAdvice,
    advise,
    as_dict,
    bye_week_by_team,
    eligible_players,
    unavailable_reason,
)

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN"]
VALUED_POSITIONS = {"QB", "RB", "WR", "TE"}


def player(pid: str, pos: str, points: float, name: str | None = None) -> Player:
    return Player(
        player_id=pid,
        name=name or f"{pos}{pid}",
        position=pos,
        proj_points=points,
        proj_stdev=20.0,
        adp=50.0,
    )


def roster() -> list[Player]:
    """Eight valued players: exactly the seven modelled starters plus a bench RB
    who is better than the worst starter, so a wrong lineup is fixable."""
    return [
        player("q", "QB", 300.0),
        player("r1", "RB", 220.0),
        player("r2", "RB", 200.0),
        player("r3", "RB", 190.0),
        player("w1", "WR", 210.0),
        player("w2", "WR", 180.0),
        player("w3", "WR", 100.0),
        player("t", "TE", 150.0),
    ]


def optimal_ids() -> list[str]:
    return ["q", "r1", "r2", "w1", "w2", "t", "r3", "0", "0"]


# Bye weeks, from the committed schedule snapshot's shape.


def _game(week: int, home: str, away: str) -> dict:
    return {"week": week, "game_type": "REG", "home_team": home, "away_team": away}


def test_a_team_missing_exactly_one_week_is_on_bye_that_week():
    rows = [_game(1, "KC", "BUF"), _game(1, "NYJ", "DEN"), _game(2, "KC", "DEN")]
    byes = bye_week_by_team(rows)
    assert byes["BUF"] == 2
    assert "KC" not in byes and "DEN" not in byes


def test_a_team_with_no_gap_has_no_bye():
    rows = [_game(1, "KC", "BUF"), _game(2, "KC", "BUF")]
    assert bye_week_by_team(rows) == {}


def test_a_partial_snapshot_does_not_invent_byes_for_everyone():
    """Two missing weeks is a gappy snapshot, not two byes. Guessing would bench
    half the roster."""
    rows = [_game(1, "KC", "BUF"), _game(2, "DEN", "LV"), _game(3, "DEN", "LV")]
    assert "KC" not in bye_week_by_team(rows)


# Availability.


def test_a_player_listed_out_cannot_start():
    p = player("r1", "RB", 220.0)
    assert unavailable_reason(p, 5, {}, {}, {"r1": "Out"}) == "listed Out"


def test_questionable_is_a_note_not_a_benching():
    p = player("r1", "RB", 220.0)
    assert unavailable_reason(p, 5, {}, {}, {"r1": "Questionable"}) is None


def test_a_player_on_bye_cannot_start():
    p = player("r1", "RB", 220.0)
    reason = unavailable_reason(p, 7, {"r1": "BUF"}, {"BUF": 7}, {})
    assert reason == "BUF bye in week 7"


def test_a_player_playing_that_week_is_available():
    p = player("r1", "RB", 220.0)
    assert unavailable_reason(p, 6, {"r1": "BUF"}, {"BUF": 7}, {}) is None


def test_with_no_signals_at_all_everyone_is_available():
    ok, blocked = eligible_players(roster(), None, {}, {}, {})
    assert len(ok) == len(roster())
    assert blocked == {}


# The diff.


def test_a_correct_lineup_gains_nothing_and_says_so():
    advice = advise(roster(), optimal_ids(), SLOTS, VALUED_POSITIONS)
    assert advice.start == []
    assert advice.sit == []
    assert advice.points_gained == 0
    assert any("already gets every modelled slot right" in n for n in advice.notes)


def test_the_better_bench_player_is_started_over_the_worse_starter():
    current = ["q", "r1", "r2", "w1", "w3", "t", "r3", "0", "0"]
    advice = advise(roster(), current, SLOTS, VALUED_POSITIONS)
    assert [m.player_id for m in advice.start] == ["w2"]
    assert [m.player_id for m in advice.sit] == ["w3"]
    assert advice.points_gained == pytest.approx(80.0)


def test_a_starter_on_bye_is_benched_and_scores_zero():
    advice = advise(
        roster(),
        optimal_ids(),
        SLOTS,
        VALUED_POSITIONS,
        week=7,
        nfl_team={"r3": "BUF"},
        byes={"BUF": 7},
    )
    assert [m.player_id for m in advice.sit] == ["r3"]
    assert [m.player_id for m in advice.start] == ["w3"]
    assert "bye" in advice.sit[0].reason
    # r3 (190) was scored at zero, and w3 (100) replaces him.
    assert advice.points_gained == pytest.approx(100.0)


def test_a_starter_ruled_out_is_benched():
    advice = advise(
        roster(), optimal_ids(), SLOTS, VALUED_POSITIONS, injury={"r3": "Out"}
    )
    assert [m.player_id for m in advice.sit] == ["r3"]
    assert advice.sit[0].reason == "listed Out"


def test_slots_the_pool_cannot_value_are_reported_not_omitted():
    advice = advise(roster(), optimal_ids(), SLOTS, VALUED_POSITIONS)
    assert advice.unevaluated_slots == ["DEF", "K"]
    assert any("DEF" in n and "not evaluated" in n for n in advice.notes)


def test_a_starter_outside_the_pool_is_called_out_not_ignored():
    current = ["q", "r1", "r2", "w1", "w2", "t", "r3", "9999", "IND"]
    advice = advise(roster(), current, SLOTS, VALUED_POSITIONS)
    assert advice.unvalued_starters == ["9999", "IND"]
    assert any("outside the pool" in n for n in advice.notes)


def test_empty_slots_in_the_sleeper_starters_list_are_skipped():
    advice = advise(roster(), ["0"] * 9, SLOTS, VALUED_POSITIONS)
    assert advice.current_points == 0
    assert len(advice.start) == 7


def test_an_empty_roster_is_an_error_not_a_clean_lineup():
    """An undrafted roster returning "no moves" is the bug this guards against."""
    with pytest.raises(ValueError, match="Cannot fill"):
        advise([], ["0"] * 9, SLOTS, VALUED_POSITIONS)


def test_an_undrafted_league_says_so_rather_than_blaming_the_slots():
    """With nothing resolved anywhere, every slot would look unmodellable for
    the wrong reason. The real cause is that there are no players."""
    with pytest.raises(ValueError, match="no players on any roster"):
        advise([], ["0"] * 9, SLOTS, set())


def test_a_position_the_league_resolved_nowhere_is_reported_as_its_own_gap():
    """A missing TE is this league's pool gap, not the K/DEF id mismatch."""
    no_te = [p for p in roster() if p.position != "TE"]
    advice = advise(no_te, ["q", "r1", "r2", "w1", "w2", "r3"], SLOTS, {"QB", "RB", "WR"})
    assert advice.unevaluated_slots == ["DEF", "K", "TE"]
    assert any("TE" in n and "no player at that position" in n for n in advice.notes)


def test_a_slot_held_by_an_unvalued_starter_names_who_comes_out():
    """Two starters the pool cannot see means two bodies have to move, or the
    advice reads as "start these players and bench nobody"."""
    current = ["q", "r1", "r2", "w1", "w2", "9999", "8888", "0", "IND"]
    advice = advise(roster(), current, SLOTS, VALUED_POSITIONS)
    assert len(advice.start) == 2
    assert [m.player_id for m in advice.sit] == ["9999", "8888"]
    assert all(m.reason == "outside the pool, no projection" for m in advice.sit)
    assert any("upper bound" in n for n in advice.notes)


def test_a_roster_with_nobody_left_at_a_position_is_an_error():
    healthy = [p for p in roster() if p.position != "QB"]
    with pytest.raises(ValueError, match="Cannot fill"):
        advise(healthy, optimal_ids(), SLOTS, VALUED_POSITIONS)


def test_injuries_that_empty_a_starting_slot_are_an_error_not_silence():
    with pytest.raises(ValueError, match="Cannot fill"):
        advise(roster(), optimal_ids(), SLOTS, VALUED_POSITIONS, injury={"q": "Out"})


def test_missing_signals_are_stated_rather_than_assumed_clean():
    advice = advise(roster(), optimal_ids(), SLOTS, VALUED_POSITIONS)
    assert any("Injury status was not consulted" in n for n in advice.notes)
    assert any("Bye weeks were not consulted" in n for n in advice.notes)


# Output shape, which an agent parses.


def test_json_output_carries_the_caveats_alongside_the_moves():
    current = ["q", "r1", "r2", "w1", "w3", "t", "r3", "0", "0"]
    out = as_dict(advise(roster(), current, SLOTS, VALUED_POSITIONS))
    assert out["status"] == "ok"
    assert out["points_gained"] == pytest.approx(80.0)
    assert [m["name"] for m in out["start"]] == ["WRw2"]
    assert out["unevaluated_slots"] == ["DEF", "K"]
    assert isinstance(out["notes"], list) and out["notes"]


def test_advice_is_a_frozen_result_object():
    advice = advise(roster(), optimal_ids(), SLOTS, VALUED_POSITIONS)
    assert isinstance(advice, LineupAdvice)
    with pytest.raises(Exception):
        advice.current_points = 1.0
