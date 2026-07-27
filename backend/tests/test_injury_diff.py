"""The alerter's job is to stay quiet unless something actually changed."""

from ffb.alerts.diff import RosteredPlayer, diff_statuses, format_message
from ffb.alerts.discord import split_message
from ffb.alerts.injuries import report_status


def player(pid: str, name: str, leagues=("Miller League",)) -> RosteredPlayer:
    return RosteredPlayer(pid, name, "RB", "SF", tuple(leagues))


ROSTER = {"1": player("1", "Christian McCaffrey"), "2": player("2", "Deebo Samuel")}


def test_new_injury_is_announced():
    res = diff_statuses(ROSTER, {"1": "Questionable"}, {}, week=3)
    assert len(res.alerts) == 1
    assert res.alerts[0].current == "Questionable"
    assert res.alerts[0].previous is None
    assert res.next_state == {"1": "Questionable"}


def test_unchanged_status_is_silent():
    """The job runs on a schedule, so a static report must not re-post."""
    res = diff_statuses(ROSTER, {"1": "Out"}, {"1": "Out"}, week=3)
    assert res.alerts == []
    # State is still carried forward, so the row is not dropped.
    assert res.next_state == {"1": "Out"}


def test_downgrade_is_flagged_as_worse():
    res = diff_statuses(ROSTER, {"1": "Out"}, {"1": "Questionable"}, week=3)
    assert len(res.alerts) == 1
    assert res.alerts[0].worsened is True


def test_upgrade_is_not_flagged_as_worse():
    res = diff_statuses(ROSTER, {"1": "Questionable"}, {"1": "Out"}, week=3)
    assert res.alerts[0].worsened is False


def test_dropping_off_the_report_clears():
    res = diff_statuses(ROSTER, {}, {"1": "Out"}, week=4)
    assert len(res.alerts) == 1
    alert = res.alerts[0]
    assert alert.cleared is True
    assert alert.previous == "Out"
    # None means "delete the stored row".
    assert res.next_state == {"1": None}


def test_healthy_player_never_seen_stays_silent():
    res = diff_statuses(ROSTER, {}, {}, week=1)
    assert res.alerts == []
    assert res.next_state == {}


def test_players_not_on_my_roster_are_ignored():
    res = diff_statuses(ROSTER, {"99": "Out"}, {}, week=2)
    assert res.alerts == []


def test_worst_news_sorts_first_and_cleared_last():
    roster = {
        "1": player("1", "Aaron"),
        "2": player("2", "Zach"),
        "3": player("3", "Mid"),
    }
    res = diff_statuses(
        roster,
        {"1": "Questionable", "2": "Out"},
        {"3": "Out"},
        week=5,
    )
    order = [a.player.name for a in res.alerts]
    assert order == ["Zach", "Aaron", "Mid"]
    assert res.alerts[-1].cleared is True


def test_message_names_every_league_a_player_is_rostered_in():
    roster = {"1": player("1", "CMC", leagues=("Miller League", "Maxxing"))}
    res = diff_statuses(roster, {"1": "Out"}, {}, week=6)
    msg = format_message(res.alerts, 6)
    assert "Miller League, Maxxing" in msg
    assert "week 6" in msg


def test_empty_alert_list_produces_no_message():
    assert format_message([], 3) == ""


# report_status: what counts as news on a raw nflverse row.


def test_report_status_prefers_the_official_designation():
    assert report_status({"report_status": "Out", "practice_status": "Limited"}) == "Out"


def test_full_participation_is_not_news():
    row = {"report_status": None, "practice_status": "Full Participation in Practice"}
    assert report_status(row) is None


def test_limited_practice_without_a_designation_is_news():
    row = {"report_status": None, "practice_status": "Limited Participation in Practice"}
    assert report_status(row) == "Practice: Limited Participation in Practice"


def test_blank_row_is_not_news():
    assert report_status({"report_status": None, "practice_status": None}) is None


# Discord's 2000 character ceiling.


def test_short_message_is_one_chunk():
    assert split_message("hello\nworld", limit=100) == ["hello\nworld"]


def test_split_breaks_on_line_boundaries():
    text = "\n".join(["x" * 40] * 5)
    chunks = split_message(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "\n".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_a_single_overlong_line_is_hard_split_not_dropped():
    chunks = split_message("y" * 250, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == "y" * 250
