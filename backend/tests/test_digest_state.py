"""The digest speaks when the answer changes, and stays quiet when it does not."""

from ffb.alerts import state
from ffb.alerts.digest import changed


def idea(roster_id: int, send: list[str], receive: list[str], surplus: float = 1.0):
    return {
        "roster_id": roster_id,
        "my_surplus": surplus,
        "send": [{"player_id": p} for p in send],
        "receive": [{"player_id": p} for p in receive],
    }


def trades(*ideas) -> dict:
    return {"by_my_surplus": list(ideas)}


def print_of(report) -> str:
    return state.fingerprint(state.trades_identity(report))


def test_the_same_ideas_fingerprint_the_same_whatever_the_order():
    a = trades(idea(3, ["x"], ["y"]), idea(4, ["p"], ["q"]))
    b = trades(idea(4, ["p"], ["q"]), idea(3, ["x"], ["y"]))
    assert print_of(a) == print_of(b)


def test_drifting_surplus_is_not_a_change():
    # Projections move a little between runs. That is not new advice, and
    # treating it as new would defeat the whole mechanism.
    assert print_of(trades(idea(3, ["x"], ["y"], 12.4))) == print_of(
        trades(idea(3, ["x"], ["y"], 12.9))
    )


def test_a_different_player_is_a_change():
    assert print_of(trades(idea(3, ["x"], ["y"]))) != print_of(
        trades(idea(3, ["x"], ["z"]))
    )


def test_a_new_counterparty_is_a_change():
    assert print_of(trades(idea(3, ["x"], ["y"]))) != print_of(
        trades(idea(9, ["x"], ["y"]))
    )


def test_an_unanswered_offer_stops_pinging_but_a_new_one_does_not():
    one = {"offers": [{"transaction_id": "t1"}]}
    same = {"offers": [{"transaction_id": "t1"}]}
    more = {"offers": [{"transaction_id": "t1"}, {"transaction_id": "t2"}]}

    assert state.inbox_identity(one) == state.inbox_identity(same)
    assert state.inbox_identity(one) != state.inbox_identity(more)


def test_lineup_tracks_the_moves_not_the_points():
    a = {"start": [{"name": "A"}], "sit": [{"name": "B"}], "points_gained": 4.0}
    b = {"start": [{"name": "A"}], "sit": [{"name": "B"}], "points_gained": 4.6}
    assert state.lineup_identity(a) == state.lineup_identity(b)


def test_a_section_never_posted_before_counts_as_changed():
    # Otherwise the first run after a deploy would start out silent, which is
    # indistinguishable from a broken job.
    prints = {("L1", state.TRADES): "abc"}
    assert changed(prints, {}) == {state.TRADES}


def test_an_unchanged_section_is_not_reported():
    prints = {("L1", state.TRADES): "abc"}
    assert changed(prints, {"L1": {state.TRADES: "abc"}}) == set()


def test_only_the_league_that_moved_is_reported():
    prints = {
        ("L1", state.TRADES): "abc",
        ("L2", state.LINEUP): "def",
    }
    stored = {"L1": {state.TRADES: "abc"}, "L2": {state.LINEUP: "old"}}
    assert changed(prints, stored) == {state.LINEUP}


def test_one_league_changing_speaks_for_that_section():
    # Two leagues share a section. If either moved, the section is posted.
    prints = {("L1", state.TRADES): "same", ("L2", state.TRADES): "new"}
    stored = {"L1": {state.TRADES: "same"}, "L2": {state.TRADES: "old"}}
    assert changed(prints, stored) == {state.TRADES}


def test_a_reordered_waiver_shortlist_is_not_a_change():
    # Two near-equal pickups can swap places between runs without the advice
    # changing, and that must not count as news.
    a = {"pickups": [{"player_id": "p1"}, {"player_id": "p2"}]}
    b = {"pickups": [{"player_id": "p2"}, {"player_id": "p1"}]}
    assert state.waivers_identity(a) == state.waivers_identity(b)


def test_a_new_name_on_the_wire_is_a_change():
    a = {"pickups": [{"player_id": "p1"}]}
    b = {"pickups": [{"player_id": "p1"}, {"player_id": "p3"}]}
    assert state.waivers_identity(a) != state.waivers_identity(b)
