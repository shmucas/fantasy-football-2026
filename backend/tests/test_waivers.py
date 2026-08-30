"""A pickup is only worth a claim if it beats what the league can already start."""

from ffb.draft.strategy import Player
from ffb.waivers import MIN_VORP, rank_pickups, starting_needs

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN"]
REPLACEMENT = {"QB": 250.0, "RB": 150.0, "WR": 150.0, "TE": 100.0, "DEF": 90.0}


def player(pid: str, pos: str, points: float, name: str | None = None) -> Player:
    return Player(
        player_id=pid,
        name=name or f"{pos}{pid}",
        position=pos,
        proj_points=points,
        proj_stdev=20.0,
        adp=50.0,
    )


def test_needs_ignore_flex_and_bench():
    # Being "short at FLEX" is not a fact about any one position.
    needs = starting_needs(SLOTS)
    assert needs["RB"] == 2 and needs["WR"] == 2 and needs["QB"] == 1
    assert "FLEX" not in needs and "BN" not in needs


def test_needs_skip_positions_the_pool_cannot_reconcile():
    needs = starting_needs(SLOTS)
    assert "K" not in needs and "DEF" not in needs


def test_a_player_below_replacement_is_not_a_pickup():
    available = [player("a", "WR", 120.0)]  # replacement is 150
    assert rank_pickups(available, [], REPLACEMENT, SLOTS) == []


def test_a_player_above_replacement_is_ranked():
    available = [player("a", "WR", 200.0, "Good WR")]
    out = rank_pickups(available, [], REPLACEMENT, SLOTS)
    assert [p["name"] for p in out] == ["Good WR"]
    assert out[0]["vorp"] == 50.0


def test_ranking_is_by_value_over_replacement_not_raw_points():
    # The TE scores fewer points but clears a much lower bar, which is the
    # whole reason replacement level exists.
    available = [player("w", "WR", 160.0, "WR"), player("t", "TE", 140.0, "TE")]
    out = rank_pickups(available, [], REPLACEMENT, SLOTS)
    assert [p["name"] for p in out] == ["TE", "WR"]


def test_defenses_are_never_recommended():
    # Pool DEF ids ("ffc_1327") never match Sleeper's ("SEA"), so every defense
    # always looks unrostered. Ranking them would put 22 wrong answers on top.
    available = [player("ffc_1327", "DEF", 140.0), player("a", "WR", 200.0)]
    out = rank_pickups(available, [], REPLACEMENT, SLOTS)
    assert [p["position"] for p in out] == ["WR"]


def test_a_pickup_that_fills_an_empty_starting_slot_is_flagged():
    available = [player("a", "QB", 300.0, "Real QB")]
    out = rank_pickups(available, [], REPLACEMENT, SLOTS)  # I roster no QB
    assert out[0]["fills_need"] is True
    assert "short at QB" in out[0]["reason"]


def test_a_position_already_covered_is_not_flagged_as_a_need():
    available = [player("a", "QB", 300.0)]
    mine = [player("mine", "QB", 290.0)]
    out = rank_pickups(available, mine, REPLACEMENT, SLOTS)
    assert out[0]["fills_need"] is False
    assert "short at" not in out[0]["reason"]


def test_marginal_pickups_are_not_worth_churning_the_bench_for():
    available = [player("a", "WR", 150.0 + MIN_VORP / 2)]
    assert rank_pickups(available, [], REPLACEMENT, SLOTS) == []


def test_the_shortlist_respects_its_limit():
    available = [player(str(i), "WR", 200.0 + i) for i in range(20)]
    assert len(rank_pickups(available, [], REPLACEMENT, SLOTS, limit=5)) == 5
