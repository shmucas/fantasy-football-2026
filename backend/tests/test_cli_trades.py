"""An empty trade list has to say which kind of empty it is."""

import json

from ffb.cli_trades import (
    STATUS_MY_ROSTER_INFEASIBLE,
    STATUS_NO_TRADES,
    STATUS_NOTHING_EVALUATED,
    STATUS_OK,
    build_report,
    idea_dict,
    load_shopping,
    render_text,
    resolve_league_id,
    status_for,
)
from ffb.draft.strategy import Player
from ffb.trades import TeamRoster, TradeIdea


def player(pid: str, pos: str, points: float) -> Player:
    return Player(
        player_id=pid,
        name=f"{pos}{pid}",
        position=pos,
        proj_points=points,
        proj_stdev=20.0,
        adp=50.0,
    )


def me() -> TeamRoster:
    return TeamRoster(5, "me", [player("a", "RB", 200.0)], 2)


def idea(my: float = 10.0, theirs: float = 5.0, hits: int = 0) -> TradeIdea:
    return TradeIdea(
        roster_id=7,
        owner_name="them",
        send=[player("a", "RB", 200.0)],
        receive=[player("b", "WR", 210.0)],
        my_surplus=my,
        their_surplus=theirs,
        shopping_hits=hits,
        notes=["a note"],
    )


def report(evaluated: int, total: int, ideas: list, hits: int = 0) -> dict:
    return build_report(
        "League", "123", me(), total, evaluated, ideas, 5, "no SLEEPER_TOKEN set",
        hits, False,
    )


# League argument.


def test_a_configured_key_resolves_to_its_sleeper_id():
    assert resolve_league_id("miller_league_hs") == "1315433135034355712"


def test_an_unknown_argument_is_passed_through_as_a_raw_league_id():
    assert resolve_league_id("1391439647548129280") == "1391439647548129280"


# Telling the kinds of empty apart.


def test_no_rosters_evaluated_is_not_a_clean_negative_answer():
    assert status_for(0, []) == STATUS_NOTHING_EVALUATED


def test_rosters_evaluated_with_nothing_found_is_a_real_negative_answer():
    assert status_for(11, []) == STATUS_NO_TRADES


def test_ideas_found_is_ok():
    assert status_for(11, [idea()]) == STATUS_OK


def test_the_report_counts_evaluated_and_skipped_rosters():
    out = report(evaluated=9, total=13, ideas=[])
    assert out["rosters_evaluated"] == 9
    assert out["rosters_skipped_infeasible"] == 4
    assert out["status"] == STATUS_NO_TRADES
    assert "real negative answer" in out["message"]


def test_an_all_infeasible_league_says_it_is_a_data_problem():
    out = report(evaluated=0, total=14, ideas=[])
    assert out["status"] == STATUS_NOTHING_EVALUATED
    assert "data problem" in out["message"]


def test_the_report_carries_the_unvalued_players_on_my_roster():
    out = report(evaluated=1, total=1, ideas=[idea()])
    assert (out["my_valued_players"], out["my_unvalued_players"]) == (1, 2)


# Output shapes.


def test_json_output_is_parseable_and_keeps_both_orderings():
    out = report(evaluated=2, total=2, ideas=[idea(my=1.0), idea(my=20.0)])
    parsed = json.loads(json.dumps(out))
    assert parsed["ideas_found"] == 2
    assert parsed["by_my_surplus"][0]["my_surplus"] == 20.0
    assert parsed["by_joint_surplus"][0]["my_surplus"] == 1.0


def test_the_limit_caps_each_ordering():
    ideas = [idea(my=float(i)) for i in range(1, 8)]
    out = build_report("L", "1", me(), 3, 3, ideas, 2, "disabled", 0, False)
    assert len(out["by_joint_surplus"]) == 2
    assert len(out["by_my_surplus"]) == 2
    assert out["ideas_found"] == 7


def test_an_idea_serializes_both_sides_of_the_package():
    out = idea_dict(idea(hits=3))
    assert out["send"][0]["name"] == "RBa"
    assert out["receive"][0]["position"] == "WR"
    assert out["shopping_hits"] == 3


def test_text_output_leads_with_the_numbers():
    text = render_text(report(evaluated=9, total=13, ideas=[idea()]), 5)
    first_lines = text.splitlines()[:2]
    assert "1 idea(s)" in first_lines[1]
    assert "9/13 rosters evaluated" in first_lines[1]
    assert "4 skipped infeasible" in first_lines[1]


def test_empty_text_output_explains_itself_instead_of_printing_nothing():
    text = render_text(report(evaluated=0, total=14, ideas=[]), 5)
    assert "data problem" in text
    assert "Best for me" not in text


def test_a_pre_draft_league_is_named_rather_than_read_as_all_infeasible():
    from ffb.cli_trades import STATUS_ROSTERS_EMPTY, explain

    assert "not drafted yet" in explain(STATUS_ROSTERS_EMPTY, 0, 13, 0)


def test_a_roster_we_cannot_file_is_reported_as_a_failure_not_as_no_trades():
    out = report(evaluated=0, total=13, ideas=[])
    out["status"] = STATUS_MY_ROSTER_INFEASIBLE
    assert out["status"] != STATUS_NO_TRADES


# Optional enrichment.


def test_shopping_enrichment_is_skipped_without_a_token(monkeypatch):
    monkeypatch.delenv("SLEEPER_TOKEN", raising=False)
    signal, note = load_shopping("123")
    assert signal == {}
    assert "no SLEEPER_TOKEN" in note


def test_a_failing_token_call_never_takes_the_cli_down(monkeypatch):
    monkeypatch.setenv("SLEEPER_TOKEN", "bogus")
    signal, note = load_shopping("123")
    assert signal == {}
    assert note  # says why, and the caller carries on with public data


def test_shopping_counts_survive_rows_without_an_explicit_type(monkeypatch):
    """get_trades filters to trades server-side, so a row missing `type` still
    counts. Without the default, shopping_signal would silently return {}."""
    monkeypatch.setenv("SLEEPER_TOKEN", "x")

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_trades(self, league_id, statuses=None, limit=200):
            return [{"drops": {"4034": 7}}, {"drops": {"4034": 7}}]

    import ffb.sleeper_auth as auth

    monkeypatch.setattr(auth, "SleeperAuthClient", lambda *a, **k: FakeClient())
    signal, note = load_shopping("123")
    assert signal == {(7, "4034"): 2}
    assert "2 trade(s)" in note
