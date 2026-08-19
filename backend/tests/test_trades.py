"""A trade only counts if both sides gain and both rosters still start legally."""

from ffb.draft.strategy import Player
from ffb.trades import (
    active_player_ids,
    TeamRoster,
    find_trades,
    is_feasible,
    modelled_slots,
    rank_for_me,
    roster_value,
    shopping_signal,
)

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN"]
VALUED_POSITIONS = {"QB", "RB", "WR", "TE"}
REPLACEMENT = {"QB": 200.0, "RB": 100.0, "WR": 100.0, "TE": 80.0}


def player(pid: str, pos: str, points: float, name: str | None = None) -> Player:
    return Player(
        player_id=pid,
        name=name or f"{pos}{pid}",
        position=pos,
        proj_points=points,
        proj_stdev=20.0,
        adp=50.0,
    )


def team(roster_id: int, players, unknown: int = 0) -> TeamRoster:
    return TeamRoster(roster_id, f"team{roster_id}", list(players), unknown)


def legal_roster(
    rb_points=(200.0, 190.0, 180.0),
    wr_points=(200.0, 190.0, 180.0),
    prefix: str = "a",
) -> list[Player]:
    """A roster that fills every modelled slot and has real depth to trade.

    `prefix` keeps player ids unique per team, the way real rosters are.
    """
    roster = [player(f"{prefix}q", "QB", 300.0), player(f"{prefix}t", "TE", 150.0)]
    for i, pts in enumerate(rb_points, start=1):
        roster.append(player(f"{prefix}r{i}", "RB", pts))
    for i, pts in enumerate(wr_points, start=1):
        roster.append(player(f"{prefix}w{i}", "WR", pts))
    return roster


# Slot handling.


def test_positions_the_pool_cannot_value_are_dropped_from_the_slots():
    """The pools carry no K and no DEF ids that match a Sleeper roster, so those
    slots would read as permanently unfillable and reject every trade."""
    assert modelled_slots(SLOTS, VALUED_POSITIONS) == [
        "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"
    ]


def test_feasible_roster_fills_every_modelled_slot():
    slots = modelled_slots(SLOTS, VALUED_POSITIONS)
    assert is_feasible(legal_roster(), 0, slots, SLOTS) is True


def test_a_roster_missing_a_starting_quarterback_is_not_feasible():
    slots = modelled_slots(SLOTS, VALUED_POSITIONS)
    roster = [p for p in legal_roster() if p.position != "QB"]
    assert is_feasible(roster, 0, slots, SLOTS) is False


def test_players_outside_the_pool_still_take_up_roster_spots():
    slots = modelled_slots(SLOTS, VALUED_POSITIONS)
    roster = legal_roster()
    # 8 valued players plus 3 unknowns is 11 bodies for 11 slots: still fits.
    assert is_feasible(roster, 3, slots, SLOTS) is True
    assert is_feasible(roster, 4, slots, SLOTS) is False


# Valuation.


def test_bench_counts_less_than_the_starting_lineup():
    slots = modelled_slots(SLOTS, VALUED_POSITIONS)
    starters_only = roster_value(legal_roster(), slots, REPLACEMENT)
    with_bench = roster_value(legal_roster() + [player("b", "TE", 120.0)], slots, REPLACEMENT)
    gain = with_bench - starters_only
    assert 0 < gain < 120.0 - REPLACEMENT["TE"]


def test_a_bench_player_below_replacement_adds_nothing():
    slots = modelled_slots(SLOTS, VALUED_POSITIONS)
    base = roster_value(legal_roster(), slots, REPLACEMENT)
    padded = roster_value(legal_roster() + [player("b", "RB", 10.0)], slots, REPLACEMENT)
    assert padded == base


# The search.


def test_positional_surplus_trade_is_found():
    """I am deep at RB and thin at WR, they are the mirror image."""
    me = team(1, legal_roster(rb_points=(260.0, 250.0, 240.0, 230.0), wr_points=(120.0, 110.0)))
    them = team(2, legal_roster(rb_points=(120.0, 110.0), wr_points=(260.0, 250.0, 240.0, 230.0), prefix="b"))
    ideas = find_trades(me, [them], SLOTS, REPLACEMENT, VALUED_POSITIONS)
    assert ideas
    best = ideas[0]
    assert best.roster_id == 2
    assert best.my_surplus > 0 and best.their_surplus > 0
    assert [p.position for p in best.send] == ["RB"]
    assert [p.position for p in best.receive] == ["WR"]


def test_two_identical_rosters_have_nothing_to_trade():
    me = team(1, legal_roster())
    them = team(2, legal_roster(prefix="b"))
    assert find_trades(me, [them], SLOTS, REPLACEMENT, VALUED_POSITIONS) == []


def test_a_lopsided_swap_is_not_offered():
    """One side gaining is not enough: the other manager has to say yes."""
    me = team(1, legal_roster(rb_points=(200.0, 100.0, 90.0)))
    them = team(2, legal_roster(rb_points=(200.0, 300.0, 90.0), prefix="b"))
    for idea in find_trades(me, [them], SLOTS, REPLACEMENT, VALUED_POSITIONS):
        assert idea.their_surplus > 0


def test_a_trade_that_empties_a_starting_slot_is_rejected():
    """They hold exactly one QB, so no package may take him."""
    me = team(1, legal_roster(rb_points=(280.0, 270.0, 260.0, 250.0), wr_points=(120.0, 110.0)))
    them = team(2, legal_roster(rb_points=(120.0, 110.0), wr_points=(280.0, 270.0, 260.0, 250.0), prefix="b"))
    ideas = find_trades(me, [them], SLOTS, REPLACEMENT, VALUED_POSITIONS)
    assert ideas
    assert all("QB" not in [p.position for p in i.receive] for i in ideas)


def test_a_full_roster_cannot_take_back_more_players_than_it_sends():
    me = team(
        1, legal_roster(rb_points=(260.0, 250.0, 240.0, 230.0), wr_points=(120.0, 110.0)), unknown=3
    )
    them = team(
        2, legal_roster(rb_points=(120.0, 110.0), wr_points=(260.0, 250.0, 240.0, 230.0)), unknown=3
    )
    for idea in find_trades(me, [them], SLOTS, REPLACEMENT, VALUED_POSITIONS):
        assert len(idea.receive) <= len(idea.send)


def test_results_are_ordered_by_joint_surplus():
    me = team(1, legal_roster(rb_points=(260.0, 250.0, 240.0, 230.0), wr_points=(120.0, 110.0)))
    a = team(2, legal_roster(rb_points=(120.0, 110.0), wr_points=(260.0, 250.0, 240.0, 230.0), prefix="b"))
    b = team(3, legal_roster(rb_points=(150.0, 140.0), wr_points=(210.0, 200.0, 190.0, 180.0), prefix="c"))
    ideas = find_trades(me, [a, b], SLOTS, REPLACEMENT, VALUED_POSITIONS)
    joint = [i.joint_surplus for i in ideas]
    assert joint == sorted(joint, reverse=True)


def test_ranking_for_me_can_differ_from_the_joint_ranking():
    me = team(1, legal_roster(rb_points=(260.0, 250.0, 240.0, 230.0), wr_points=(120.0, 110.0)))
    a = team(2, legal_roster(rb_points=(120.0, 110.0), wr_points=(260.0, 250.0, 240.0, 230.0), prefix="b"))
    b = team(3, legal_roster(rb_points=(150.0, 140.0), wr_points=(210.0, 200.0, 190.0, 180.0), prefix="c"))
    ideas = find_trades(me, [a, b], SLOTS, REPLACEMENT, VALUED_POSITIONS)
    mine = [i.my_surplus for i in rank_for_me(ideas)]
    assert mine == sorted(mine, reverse=True)


def test_each_team_is_capped_so_one_partner_cannot_flood_the_list():
    me = team(1, legal_roster(rb_points=(260.0, 250.0, 240.0, 230.0), wr_points=(120.0, 110.0)))
    them = team(2, legal_roster(rb_points=(120.0, 110.0), wr_points=(260.0, 250.0, 240.0, 230.0), prefix="b"))
    ideas = find_trades(me, [them], SLOTS, REPLACEMENT, VALUED_POSITIONS, per_team_limit=2)
    assert len(ideas) <= 2


# Optional shopping signal, from trade history including offers that never landed.


def test_shopping_signal_counts_who_offered_whom():
    txns = [
        {"type": "trade", "status": "rejected", "drops": {"w1": 2, "r1": 1}},
        {"type": "trade", "status": "cancelled", "drops": {"w1": 2}},
    ]
    assert shopping_signal(txns) == {(2, "w1"): 2, (1, "r1"): 1}


def test_shopping_signal_ignores_non_trades():
    txns = [{"type": "waiver", "drops": {"w1": 2}}]
    assert shopping_signal(txns) == {}


def test_no_history_leaves_the_search_unchanged():
    """The GraphQL history needs a token we may not have, so it is optional."""
    me = team(1, legal_roster(rb_points=(260.0, 250.0, 240.0, 230.0), wr_points=(120.0, 110.0)))
    them = team(2, legal_roster(rb_points=(120.0, 110.0), wr_points=(260.0, 250.0, 240.0, 230.0), prefix="b"))
    without = find_trades(me, [them], SLOTS, REPLACEMENT, VALUED_POSITIONS)
    with_empty = find_trades(me, [them], SLOTS, REPLACEMENT, VALUED_POSITIONS, shopping={})
    assert [i.joint_surplus for i in without] == [i.joint_surplus for i in with_empty]


def test_a_player_they_have_shopped_is_flagged_in_the_notes():
    me = team(1, legal_roster(rb_points=(260.0, 250.0, 240.0, 230.0), wr_points=(120.0, 110.0)))
    them = team(2, legal_roster(rb_points=(120.0, 110.0), wr_points=(260.0, 250.0, 240.0, 230.0), prefix="b"))
    ideas = find_trades(
        me, [them], SLOTS, REPLACEMENT, VALUED_POSITIONS, shopping={(2, "bw1"): 3}
    )
    flagged = [i for i in ideas if any(p.player_id == "bw1" for p in i.receive)]
    assert flagged
    assert flagged[0].shopping_hits == 3
    assert any("offered" in n for n in flagged[0].notes)


def test_a_package_that_adds_a_body_for_no_extra_gain_is_dropped():
    """Throwing an extra player into a deal that was already good enough is noise:
    the 1-for-1 survives and the 2-for-1 wrapped around it does not."""
    me = team(1, legal_roster(rb_points=(260.0, 250.0, 240.0, 230.0), wr_points=(120.0, 110.0)))
    them = team(2, legal_roster(rb_points=(120.0, 110.0), wr_points=(260.0, 250.0, 240.0, 230.0), prefix="b"))
    ideas = find_trades(me, [them], SLOTS, REPLACEMENT, VALUED_POSITIONS, per_team_limit=20)
    assert any(len(i.send) == 1 and len(i.receive) == 1 for i in ideas)
    for a in ideas:
        for b in ideas:
            if a is b:
                continue
            smaller = len(b.send) + len(b.receive) < len(a.send) + len(a.receive)
            better_for_both = b.my_surplus >= a.my_surplus and b.their_surplus >= a.their_surplus
            assert not (smaller and better_for_both)


def test_our_own_unfillable_roster_is_an_error_not_an_empty_answer():
    """An empty list means "no trade helps". A roster the pool cannot even file
    is a modelling failure and has to say so."""
    me = team(1, [p for p in legal_roster() if p.position != "QB"])
    them = team(2, legal_roster(prefix="b"))
    try:
        find_trades(me, [them], SLOTS, REPLACEMENT, VALUED_POSITIONS)
    except ValueError as exc:
        assert "cannot fill" in str(exc)
    else:
        raise AssertionError("expected a ValueError")


def test_a_partner_we_cannot_file_is_skipped_not_fatal():
    me = team(1, legal_roster(rb_points=(260.0, 250.0, 240.0, 230.0), wr_points=(120.0, 110.0)))
    broken = team(2, [p for p in legal_roster(prefix="b") if p.position != "QB"])
    good = team(3, legal_roster(rb_points=(120.0, 110.0), wr_points=(260.0, 250.0, 240.0, 230.0), prefix="c"))
    ideas = find_trades(me, [broken, good], SLOTS, REPLACEMENT, VALUED_POSITIONS)
    assert ideas
    assert all(i.roster_id == 3 for i in ideas)


def test_ir_and_taxi_players_do_not_count_against_roster_capacity():
    """A league can allow IR beyond its roster_positions, and Sleeper lists those
    players in `players` anyway. Counting them made one stash enough to mark a
    roster unfileable, which quietly removed that manager from the search."""
    raw = {
        "roster_id": 3,
        "players": ["a", "b", "c", "hurt", "rook"],
        "reserve": ["hurt"],
        "taxi": ["rook"],
    }
    assert active_player_ids(raw) == ["a", "b", "c"]


def test_active_player_ids_handles_missing_and_null_keys():
    assert active_player_ids({"players": ["a"]}) == ["a"]
    assert active_player_ids({"players": None, "reserve": None}) == []
    assert active_player_ids({}) == []
