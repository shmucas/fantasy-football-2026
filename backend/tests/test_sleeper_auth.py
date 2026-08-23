"""The kill switch is the thing standing between a bug and a real trade offer
landing in twelve people's inboxes, so it gets tests of its own."""

import base64
import json
import time

import pytest

from ffb.sleeper_auth import (
    PlannedWrite,
    SleeperAuthClient,
    SleeperAuthError,
    WritesDisabled,
    inspect_token,
    writes_enabled,
)


def make_token(exp_offset: int = 86400, user_id: int = 123, name: str = "tester") -> str:
    now = int(time.time())
    payload = {
        "user_id": user_id,
        "display_name": name,
        "iat": now,
        "exp": now + exp_offset,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


def test_inspect_token_reads_identity_and_expiry():
    info = inspect_token(make_token(exp_offset=3600, user_id=42, name="lucas"))
    assert info.user_id == "42"
    assert info.display_name == "lucas"
    assert not info.is_expired
    assert 3500 < info.seconds_remaining <= 3600


def test_inspect_token_rejects_non_jwt():
    with pytest.raises(SleeperAuthError):
        inspect_token("not-a-jwt")


def test_expiring_soon_flags_a_token_worth_replacing():
    assert inspect_token(make_token(exp_offset=5 * 86400)).expiring_soon
    assert not inspect_token(make_token(exp_offset=200 * 86400)).expiring_soon


def test_expired_token_is_refused_at_construction(monkeypatch):
    monkeypatch.delenv("FFB_ALLOW_WRITES", raising=False)
    with pytest.raises(SleeperAuthError, match="expired"):
        SleeperAuthClient(token=make_token(exp_offset=-60))


def test_missing_token_names_the_env_var(monkeypatch):
    monkeypatch.delenv("SLEEPER_TOKEN", raising=False)
    with pytest.raises(SleeperAuthError, match="SLEEPER_TOKEN"):
        SleeperAuthClient()


def test_writes_are_off_unless_the_switch_is_exactly_one(monkeypatch):
    for value in ["", "0", "true", "yes", "TRUE"]:
        monkeypatch.setenv("FFB_ALLOW_WRITES", value)
        assert not writes_enabled(), f"{value!r} should not enable writes"
    monkeypatch.setenv("FFB_ALLOW_WRITES", "1")
    assert writes_enabled()


def test_client_defaults_to_dry_run_when_switch_is_off(monkeypatch):
    monkeypatch.delenv("FFB_ALLOW_WRITES", raising=False)
    client = SleeperAuthClient(token=make_token())
    assert client.dry_run is True


def test_mutations_describe_instead_of_firing_in_dry_run(monkeypatch):
    monkeypatch.delenv("FFB_ALLOW_WRITES", raising=False)
    client = SleeperAuthClient(token=make_token())

    def explode(*a, **kw):
        raise AssertionError("dry run must not touch the network")

    monkeypatch.setattr(client, "_gql", explode)

    planned = client.propose_trade("L1", adds=[("p1", 2)], drops=[("p2", 3)])
    assert isinstance(planned, PlannedWrite)
    assert planned.operation == "propose_trade"
    # adds and drops become parallel lists, which is the shape Sleeper wants.
    assert planned.variables["k_adds"] == ["p1"]
    assert planned.variables["v_adds"] == [2]
    assert planned.variables["k_drops"] == ["p2"]
    assert planned.variables["v_drops"] == [3]

    assert isinstance(client.set_starters("L1", 5, ["p1"]), PlannedWrite)
    assert isinstance(client.submit_waiver_claim("L1", 5, "p1", faab_bid=7), PlannedWrite)
    assert isinstance(client.accept_trade("L1", "t1", 1), PlannedWrite)


def test_make_draft_pick_shapes_the_mutation(monkeypatch):
    monkeypatch.delenv("FFB_ALLOW_WRITES", raising=False)
    client = SleeperAuthClient(token=make_token())
    planned = client.make_draft_pick("D1", "9509", pick_no=2)
    assert isinstance(planned, PlannedWrite)
    assert planned.operation == "draft_pick_player"
    assert planned.variables == {
        "sport": "nfl", "player_id": "9509", "draft_id": "D1", "pick_no": 2,
    }
    assert isinstance(client.set_autopick("D1"), PlannedWrite)


def test_explicit_dry_run_false_still_needs_the_env_switch(monkeypatch):
    """Belt and braces: passing dry_run=False in code is not enough on its own."""
    monkeypatch.delenv("FFB_ALLOW_WRITES", raising=False)
    client = SleeperAuthClient(token=make_token(), dry_run=False)
    with pytest.raises(WritesDisabled, match="FFB_ALLOW_WRITES"):
        client.propose_trade("L1", adds=[("p1", 2)], drops=[("p2", 3)])


def test_waiver_claim_shapes_adds_and_drops_as_roster_maps(monkeypatch):
    monkeypatch.delenv("FFB_ALLOW_WRITES", raising=False)
    client = SleeperAuthClient(token=make_token())
    planned = client.submit_waiver_claim("L1", 9, "add1", drop_player_id="drop1", faab_bid=12)
    assert planned.variables["adds"] == {"add1": 9}
    assert planned.variables["drops"] == {"drop1": 9}
    assert planned.variables["waiver_budget"] == 12


def test_dry_run_description_is_readable():
    planned = PlannedWrite("propose_trade", {"league_id": "L1"})
    text = planned.describe()
    assert text.startswith("[dry run] propose_trade")
    assert "L1" in text
