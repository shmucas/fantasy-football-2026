"""Waiver decisions: one claim, or none, with a drop that cannot hurt us."""

from ffb.decide import waivers
from ffb.draft.strategy import Player

POSITIONS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"]


def p(pid: str, position: str, points: float, name: str | None = None) -> Player:
    return Player(
        player_id=pid, name=name or pid, position=position,
        proj_points=points, proj_stdev=1.0, adp=1.0,
    )


def league_pool() -> list[Player]:
    """Enough bodies that replacement level lands somewhere sensible."""
    pool = []
    for position, count in (("QB", 24), ("RB", 48), ("WR", 48), ("TE", 24)):
        for i in range(count):
            pool.append(p(f"{position}{i}", position, 300 - i * 5))
    return pool


def test_no_available_players_means_no_claim():
    assert waivers.best_claim([], [], league_pool(), POSITIONS, 12) is None


def test_a_marginal_upgrade_is_not_worth_a_claim():
    pool = league_pool()
    mine = [p("mine", "RB", 150)]
    barely_better = [p("target", "RB", 152)]
    claim = waivers.best_claim(mine, barely_better, pool, POSITIONS, 12)
    assert claim is None


def test_a_clear_upgrade_is_proposed():
    pool = league_pool()
    mine = [p("weak", "RB", 40, "Weak Guy")]
    great = p("stud", "RB", 290, "Stud Back")
    claim = waivers.best_claim(mine, [great], pool + [great], POSITIONS, 12)
    assert claim is not None
    assert claim.add.name == "Stud Back"
    assert "Stud Back" in claim.summary()


def test_the_best_available_is_chosen_not_the_first():
    pool = league_pool()
    mine = [p("weak", "RB", 20)]
    options = [p("ok", "RB", 120, "OK"), p("best", "RB", 295, "Best"), p("mid", "RB", 200, "Mid")]
    claim = waivers.best_claim(mine, options, pool + options, POSITIONS, 12)
    assert claim.add.name == "Best"


def test_a_starter_is_never_the_drop_candidate():
    """The claim must not quietly drop the only player at a starting slot."""
    pool = league_pool()
    only_te = p("te", "TE", 90, "Only TE")
    spare = p("spare", "RB", 30, "Spare")
    mine = [only_te, spare, p("rb1", "RB", 200), p("rb2", "RB", 190),
            p("qb", "QB", 250), p("wr1", "WR", 210), p("wr2", "WR", 205)]
    great = p("stud", "WR", 295, "Stud")
    claim = waivers.best_claim(mine, [great], pool + [great], POSITIONS, 12)
    assert claim is not None
    assert claim.drop is not None
    assert claim.drop.name != "Only TE", "dropped a player holding a starting slot"
    assert claim.drop.name == "Spare"


def test_no_drop_is_suggested_when_the_roster_has_room():
    pool = league_pool()
    mine = [p("weak", "RB", 20)]
    great = p("stud", "RB", 295)
    claim = waivers.best_claim(
        mine, [great], pool + [great], POSITIONS, 12, roster_is_full=False
    )
    assert claim.drop is None
    assert "dropping" not in claim.summary()


def test_no_droppable_player_means_no_drop_rather_than_a_bad_one():
    pool = league_pool()
    # Every player is holding a starting slot.
    mine = [p("qb", "QB", 250), p("rb1", "RB", 200), p("rb2", "RB", 190),
            p("wr1", "WR", 210), p("wr2", "WR", 205), p("te", "TE", 150)]
    great = p("stud", "RB", 295)
    claim = waivers.best_claim(mine, [great], pool + [great], POSITIONS, 12)
    if claim is not None:
        assert claim.drop is None


def test_faab_bid_is_zero_without_a_budget():
    pool = league_pool()
    great = p("stud", "RB", 295)
    claim = waivers.best_claim(
        [p("weak", "RB", 20)], [great], pool + [great], POSITIONS, 12, faab_budget_left=None
    )
    assert claim.faab_bid == 0


def test_faab_bid_stays_within_the_remaining_budget():
    pool = league_pool()
    great = p("stud", "RB", 295)
    for budget in (1, 10, 100):
        claim = waivers.best_claim(
            [p("weak", "RB", 20)], [great], pool + [great], POSITIONS, 12,
            faab_budget_left=budget,
        )
        assert 1 <= claim.faab_bid <= budget


def test_a_bigger_upgrade_bids_more():
    pool = league_pool()
    modest = p("modest", "RB", 220)
    huge = p("huge", "RB", 300)
    mine = [p("weak", "RB", 20)]
    a = waivers.best_claim(mine, [modest], pool + [modest], POSITIONS, 12, faab_budget_left=100)
    b = waivers.best_claim(mine, [huge], pool + [huge], POSITIONS, 12, faab_budget_left=100)
    assert a is not None and b is not None, "both should clear the minimum gain"
    assert b.faab_bid > a.faab_bid


def test_detail_explains_the_reasoning():
    pool = league_pool()
    great = p("stud", "RB", 295, "Stud Back")
    claim = waivers.best_claim(
        [p("weak", "RB", 20, "Weak")], [great], pool + [great], POSITIONS, 12
    )
    detail = claim.detail()
    assert "Stud Back" in detail
    assert "projected points" in detail
