"""Which way a message reaches Discord.

There are two routes and they fail differently, so the choice between them has
to be deliberate rather than whichever env var happens to be set.
"""

import pytest

from ffb.alerts import discord


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (discord.WEBHOOK_ENV, discord.BOT_TOKEN_ENV, discord.CHANNEL_ENV):
        monkeypatch.delenv(name, raising=False)


def test_a_bot_token_without_a_channel_is_not_usable():
    # A token with nowhere to post would otherwise look configured and then
    # fail at send time, reporting the wrong missing setting.
    import os

    os.environ[discord.BOT_TOKEN_ENV] = "x"
    assert discord.bot_credentials() is None
    assert discord.configured() is False


def test_bot_wins_when_both_are_set(monkeypatch):
    monkeypatch.setenv(discord.BOT_TOKEN_ENV, "tok")
    monkeypatch.setenv(discord.CHANNEL_ENV, "123")
    monkeypatch.setenv(discord.WEBHOOK_ENV, "https://example.invalid/hook")

    seen = {}

    def fake_bot(chunks, token, channel, timeout):
        seen["route"] = "bot"
        return len(chunks)

    monkeypatch.setattr(discord, "_post_as_bot", fake_bot)
    assert discord.post("hello") == 1
    assert seen["route"] == "bot"


def test_webhook_is_used_when_there_is_no_bot(monkeypatch):
    monkeypatch.setenv(discord.WEBHOOK_ENV, "https://example.invalid/hook")
    seen = {}

    def fake_hook(chunks, target, timeout):
        seen["route"] = "webhook"
        return len(chunks)

    monkeypatch.setattr(discord, "_post_to_webhook", fake_hook)
    assert discord.post("hello") == 1
    assert seen["route"] == "webhook"


def test_an_explicit_url_still_forces_the_webhook(monkeypatch):
    # Callers that already hold a webhook URL must keep working unchanged, even
    # once a bot is configured.
    monkeypatch.setenv(discord.BOT_TOKEN_ENV, "tok")
    monkeypatch.setenv(discord.CHANNEL_ENV, "123")
    seen = {}

    def fake_hook(chunks, target, timeout):
        seen["target"] = target
        return len(chunks)

    monkeypatch.setattr(discord, "_post_to_webhook", fake_hook)
    discord.post("hello", url="https://example.invalid/explicit")
    assert seen["target"] == "https://example.invalid/explicit"


def test_no_route_at_all_says_both_options():
    with pytest.raises(RuntimeError, match="No way to reach Discord"):
        discord.post("hello")


def test_nothing_is_sent_for_an_empty_message(monkeypatch):
    monkeypatch.setenv(discord.BOT_TOKEN_ENV, "tok")
    monkeypatch.setenv(discord.CHANNEL_ENV, "123")
    assert discord.post("   \n  ") == 0
