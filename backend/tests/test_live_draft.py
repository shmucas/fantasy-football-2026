"""Turn math + pick decision for the live draft bot, without the network."""

from collections import Counter

from ffb.draft.live import (
    _def_name_parts,
    choose_pick,
    is_my_turn,
    remap_defenses,
    remap_skill_ids,
    room_for,
    roster_positions_from_settings,
    snake_slot,
)
from ffb.draft.strategy import Player


def P(pid: str, pos: str, pts: float, name: str = "", adp: float = 500.0) -> Player:
    return Player(
        player_id=pid, name=name or f"{pos}{pid}", position=pos,
        proj_points=pts, proj_stdev=1.0, adp=adp, adp_stdev=1.0,
    )


POS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN"]


def test_snake_slot_runs_forward_then_backward():
    # 12 teams: pick 1 -> slot 1, pick 12 -> slot 12, pick 13 -> slot 12.
    assert snake_slot(1, 12) == 1
    assert snake_slot(12, 12) == 12
    assert snake_slot(13, 12) == 12
    assert snake_slot(24, 12) == 1
    assert snake_slot(25, 12) == 1


def test_is_my_turn_counts_from_zero():
    # slot 2 in a 12-team snake picks at overall #2, then #23, then #26.
    assert is_my_turn(1, 12, 2)  # picks_made=1 -> pick #2 is slot 2
    assert not is_my_turn(2, 12, 2)  # pick #3 is slot 3
    assert is_my_turn(22, 12, 2)  # pick #23 is slot 2 (round 2 reversal)
    assert is_my_turn(25, 12, 2)  # pick #26 is slot 2 (round 3)


def test_roster_positions_from_settings_matches_slots():
    settings = {
        "slots_qb": 1, "slots_rb": 2, "slots_wr": 2, "slots_te": 1,
        "slots_flex": 2, "slots_k": 1, "slots_def": 1, "slots_bn": 7,
    }
    slots = roster_positions_from_settings(settings)
    assert slots == [
        "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF",
        "BN", "BN", "BN", "BN", "BN", "BN", "BN",
    ]


def test_room_for_counts_starter_plus_flex():
    filled = Counter({"RB": 2})  # both RB starters taken
    assert room_for("RB", POS, filled) == 1  # 2 starters + 1 flex - 2 filled
    assert room_for("QB", POS, filled) == 1
    assert room_for("K", POS, filled) == 1


def test_choose_pick_fills_open_slot_before_value():
    # Only a QB slot is open and it is worse than every RB on the board:
    # the need rule must still take the QB, because the roster demands one.
    replacement = {"QB": 100.0, "RB": 150.0, "WR": 150.0, "TE": 100.0, "K": 0.0, "DEF": 80.0}
    my_roster = [
        P("rb1", "RB", 300), P("rb2", "RB", 290),
        P("wr1", "WR", 280), P("wr2", "WR", 270), P("te1", "TE", 200),
    ]
    available = [P("qb1", "QB", 250), P("rb3", "RB", 260)]
    choice = choose_pick(available, my_roster, POS, replacement, 3, 15)
    assert choice.player_id == "qb1"


def test_choose_pick_takes_best_vorp_when_no_slot_is_open():
    replacement = {"QB": 100.0, "RB": 150.0, "WR": 150.0, "TE": 100.0, "K": 0.0, "DEF": 80.0}
    # Every starter + flex slot is filled, so we are into bench rounds: best VORP wins.
    my_roster = [
        P("rb1", "RB", 300), P("rb2", "RB", 290), P("rb3", "RB", 280),
        P("wr1", "WR", 280), P("wr2", "WR", 270), P("wr3", "WR", 260),
        P("qb1", "QB", 250), P("te1", "TE", 200), P("k1", "K", 0), P("d1", "DEF", 120),
    ]
    available = [P("rb9", "RB", 240), P("wr9", "WR", 235)]
    assert choose_pick(available, my_roster, POS, replacement, 13, 15).player_id == "rb9"


def test_choose_pick_wont_take_defense_early():
    # A defense has the highest raw VORP, but in an early round it must be
    # skipped in favour of a skill player.
    replacement = {"QB": 100.0, "RB": 150.0, "WR": 150.0, "TE": 100.0, "K": 0.0, "DEF": 80.0}
    my_roster = [P("rb1", "RB", 300), P("wr1", "WR", 280)]
    available = [
        P("def1", "DEF", 160, name="Seattle Defense"),
        P("wr9", "WR", 240),
    ]
    choice = choose_pick(available, my_roster, POS, replacement, 6, 17)
    assert choice.player_id == "wr9"


def test_choose_pick_will_take_defense_in_final_rounds():
    # Late rounds: with no skill slot still open, the defense is now fair game.
    replacement = {"QB": 100.0, "RB": 150.0, "WR": 150.0, "TE": 100.0, "K": 0.0, "DEF": 80.0}
    my_roster = [
        P("rb1", "RB", 300), P("rb2", "RB", 290), P("rb3", "RB", 280),
        P("wr1", "WR", 280), P("wr2", "WR", 270), P("wr3", "WR", 260),
        P("qb1", "QB", 250), P("te1", "TE", 200), P("k1", "K", 0),
    ]
    available = [P("def1", "DEF", 160, name="Seattle Defense"), P("wr9", "WR", 240)]
    choice = choose_pick(available, my_roster, POS, replacement, 16, 17)
    assert choice.player_id == "def1"


def test_def_name_parts_resolve_city_and_mascot():
    assert _def_name_parts("Seattle Defense") == ("seattle", "")
    assert _def_name_parts("LA Rams Defense") == ("los angeles", "rams")
    assert _def_name_parts("New England Defense") == ("new england", "")
    assert _def_name_parts("SF 49ers Defense") == ("san francisco", "49ers")


def test_remap_defenses_swaps_ffc_ids_for_sleeper_team_ids():
    pool = [
        P("ffc_1327", "DEF", 140, name="Seattle Defense"),
        P("ffc_1328", "DEF", 129, name="LA Rams Defense"),
        P("9509", "RB", 375, name="Bijan Robinson"),
    ]
    ids = {
        ("seattle", "seahawks"): "SEA",
        ("los angeles", "rams"): "LAR",
    }
    out = remap_defenses(pool, ids)
    by_name = {p.name: p.player_id for p in out}
    assert by_name["Seattle Defense"] == "SEA"
    assert by_name["LA Rams Defense"] == "LAR"
    assert by_name["Bijan Robinson"] == "9509"


def test_remap_skill_ids_fixes_ffc_placeholder_ids():
    pool = [
        P("ffc_5196", "RB", 139, name="Kenny Gainwell"),
        P("ffc_5453", "TE", 112, name="Chig Okonkwo"),
        P("9509", "RB", 375, name="Bijan Robinson"),
    ]
    ids = {
        ("kenny gainwell", "RB"): "7567",
        ("chig okonkwo", "TE"): "8210",
    }
    out = remap_skill_ids(pool, ids)
    by_name = {p.name: p.player_id for p in out}
    assert by_name["Kenny Gainwell"] == "7567"
    assert by_name["Chig Okonkwo"] == "8210"
    assert by_name["Bijan Robinson"] == "9509"
