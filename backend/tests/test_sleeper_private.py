"""Payload shape and token handling for Sleeper's private GraphQL API.

Live calls cannot be tested here - the endpoint needs a real session token and
a real league. What is testable is that the bodies we would send are correct,
which is most of the risk: these are writes against a real roster.
"""

import base64
import json
import time

import pytest

from ffb import sleeper_private as sp


def make_jwt(user_id="123", display_name="lucas", exp=None) -> str:
    exp = exp if exp is not None else int(time.time()) + 3600
    payload = {"user_id": user_id, "display_name": display_name, "exp": exp}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


# --- token ------------------------------------------------------------------


def test_inspect_token_reads_identity_and_expiry():
    info = sp.inspect_token(make_jwt(user_id="999", display_name="jpicci"))
    assert info.user_id == "999"
    assert info.display_name == "jpicci"
    assert info.is_expired is False
    assert info.seconds_remaining > 0


def test_expired_token_is_detected_without_a_request():
    info = sp.inspect_token(make_jwt(exp=int(time.time()) - 60))
    assert info.is_expired is True
    assert info.seconds_remaining == 0


def test_a_non_jwt_is_rejected_clearly():
    with pytest.raises(sp.SleeperAuthError, match="not a JWT"):
        sp.inspect_token("just-a-string")


def test_client_refuses_to_start_without_a_token(monkeypatch):
    monkeypatch.delenv(sp.TOKEN_ENV, raising=False)
    with pytest.raises(sp.SleeperAuthError, match=sp.TOKEN_ENV):
        sp.SleeperPrivateClient()


# --- starters ordering: the dangerous one -----------------------------------


def test_starting_slots_drops_bench_ir_and_taxi():
    positions = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF",
                 "BN", "BN", "BN", "IR", "TAXI"]
    assert sp.starting_slots(positions) == [
        "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"
    ]


def test_starting_slots_preserves_order():
    """Order is meaning here - index 0 must stay the first slot."""
    assert sp.starting_slots(["WR", "QB", "BN", "RB"]) == ["WR", "QB", "RB"]


def test_starting_slots_handles_a_roster_with_no_bench():
    assert sp.starting_slots(["QB", "RB"]) == ["QB", "RB"]


# --- payloads ---------------------------------------------------------------


def test_set_starters_payload_keeps_the_given_order():
    payload = sp.set_starters_payload("L1", 4, ["p1", "p2", "p3"])
    assert payload["operationName"] == "update_roster_starters"
    assert payload["variables"]["starters"] == ["p1", "p2", "p3"]
    assert payload["variables"]["roster_id"] == 4


def test_set_starters_copies_the_list_it_was_given():
    """A later mutation of the caller's list must not change a stored action."""
    starters = ["p1", "p2"]
    payload = sp.set_starters_payload("L1", 1, starters)
    starters.append("p3")
    assert payload["variables"]["starters"] == ["p1", "p2"]


def test_waiver_claim_maps_adds_and_drops_to_the_roster():
    payload = sp.waiver_claim_payload("L1", 7, add_player_id="a", drop_player_id="d", faab_bid=15)
    variables = payload["variables"]
    assert variables["adds"] == {"a": 7}
    assert variables["drops"] == {"d": 7}
    assert variables["waiver_budget"] == 15


def test_waiver_claim_without_a_drop_sends_empty_drops():
    variables = sp.waiver_claim_payload("L1", 7, add_player_id="a")["variables"]
    assert variables["drops"] == {}
    assert variables["waiver_budget"] == 0


def test_add_drop_payload_shape():
    variables = sp.add_drop_payload("L1", 2, add_player_id="a", drop_player_id="d")["variables"]
    assert variables["adds"] == {"a": 2}
    assert variables["drops"] == {"d": 2}


def test_propose_trade_sends_ours_and_receives_theirs():
    payload = sp.propose_trade_payload(
        "L1", roster_id=1, target_roster_id=5,
        send_player_ids=["mine"], receive_player_ids=["theirs"],
    )
    variables = payload["variables"]
    # adds land on our roster, drops leave for theirs
    assert variables["adds"] == {"theirs": 1}
    assert variables["drops"] == {"mine": 5}
    assert set(variables["consenter_ids"]) == {1, 5}


def test_every_payload_names_its_operation():
    """The op name is sent as a header too, so a mismatch is a real bug."""
    payloads = [
        sp.set_starters_payload("L", 1, []),
        sp.waiver_claim_payload("L", 1, add_player_id="a"),
        sp.add_drop_payload("L", 1, add_player_id="a"),
        sp.propose_trade_payload("L", 1, 2, [], []),
        sp.league_transactions_payload("L"),
    ]
    for payload in payloads:
        name = payload["operationName"]
        assert name, "payload has no operationName"
        assert name in payload["query"], f"{name} missing from its own query text"


def test_payloads_are_json_serialisable():
    """They get stored in a JSON column before anyone approves them."""
    payload = sp.waiver_claim_payload("L1", 3, add_player_id="a", faab_bid=5)
    assert json.loads(json.dumps(payload)) == payload


# --- error handling ---------------------------------------------------------


@pytest.mark.parametrize(
    "errors,expected",
    [
        ([{"message": "nope"}], "nope"),
        ({"message": "single dict"}, "single dict"),
        (["bare string"], "bare string"),
        ([{"code": "unauthorized"}], "unauthorized"),
    ],
)
def test_error_shapes_are_all_flattened(errors, expected):
    """Sleeper returns errors in several shapes; all must produce a message."""
    assert expected in sp._error_text(errors)


# --- transport: Sleeper answers failures with HTTP 200 ----------------------


def _client_with(handler, token=None):
    import httpx

    client = sp.SleeperPrivateClient(token=token or make_jwt())
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_errors_arrive_as_http_200_and_still_raise():
    import httpx

    def handler(request):
        return httpx.Response(200, json={"errors": [{"message": "roster locked"}]})

    with _client_with(handler) as client:
        with pytest.raises(sp.SleeperAuthError, match="roster locked"):
            client.send(sp.waiver_claim_payload("L1", 1, add_player_id="a"))


def test_successful_send_returns_the_operation_data():
    import httpx

    def handler(request):
        return httpx.Response(
            200, json={"data": {"create_waiver_claim": {"transaction_id": "t9"}}}
        )

    with _client_with(handler) as client:
        result = client.send(sp.waiver_claim_payload("L1", 1, add_player_id="a"))
    assert result == {"transaction_id": "t9"}


def test_expired_token_fails_before_any_request_is_made():
    import httpx

    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"data": {}})

    client = _client_with(handler, token=make_jwt(exp=int(time.time()) - 10))
    with client:
        with pytest.raises(sp.SleeperAuthError, match="expired"):
            client.send(sp.set_starters_payload("L1", 1, []))
    assert sent == [], "an expired token must not spend a request"


def test_the_auth_header_carries_the_raw_token_with_no_bearer_prefix():
    import httpx

    seen = {}
    token = make_jwt()

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={"data": {"update_roster_starters": {}}})

    with _client_with(handler, token=token) as client:
        client.send(sp.set_starters_payload("L1", 1, ["p"]))

    assert seen["authorization"] == token
    assert not seen["authorization"].lower().startswith("bearer")
    assert seen["origin"] == "https://sleeper.com"
    assert seen["x-sleeper-graphql-op"] == "update_roster_starters"
