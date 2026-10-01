"""Post to Discord, by bot token if one is configured and by webhook otherwise.

A webhook is the simplest thing that works: a plain HTTPS POST, so the alerter
can run as a scheduled job anywhere. It has one bad property, which cost a week
of silence in September. A webhook URL is a bearer credential sitting in plain
text, and Discord scans public repositories for them and deletes the ones it
finds. One committed `.env.bak` and the channel goes quiet.

So a bot token is preferred when both it and a channel id are set. It is not
more secure in itself, but it is revocable and rotatable without touching every
job's config, and the same bot can read messages, which is what a conversational
assistant needs. The webhook stays as the fallback so nothing that already works
has to change.
"""

import os

import httpx

WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"
BOT_TOKEN_ENV = "DISCORD_BOT_TOKEN"
CHANNEL_ENV = "DISCORD_CHANNEL_ID"

API = "https://discord.com/api/v10"


class WebhookGone(RuntimeError):
    """The webhook URL is well-formed but Discord no longer has it."""


class BotPostFailed(RuntimeError):
    """The bot could not post: usually a revoked token or a channel it cannot see."""


# Discord rejects a message body over 2000 characters.
MAX_CONTENT = 2000


def webhook_url() -> str | None:
    url = os.getenv(WEBHOOK_ENV, "").strip()
    return url or None


def bot_credentials() -> tuple[str, str] | None:
    """(token, channel_id) when the bot route is fully configured.

    Both are required: a token with no channel has nowhere to post, and saying
    so here keeps the caller from falling back to a webhook it also lacks and
    reporting the wrong missing setting.
    """
    token = os.getenv(BOT_TOKEN_ENV, "").strip()
    channel = os.getenv(CHANNEL_ENV, "").strip()
    if token and channel:
        return token, channel
    return None


def configured() -> bool:
    """Whether there is any way to reach Discord at all."""
    return bot_credentials() is not None or webhook_url() is not None


def split_message(text: str, limit: int = MAX_CONTENT) -> list[str]:
    """Break a message on line boundaries so no chunk exceeds Discord's limit.

    A single line longer than the limit is hard-split rather than dropped.
    """
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _post_as_bot(chunks: list[str], token: str, channel: str, timeout: float) -> int:
    headers = {"Authorization": f"Bot {token}"}
    with httpx.Client(timeout=timeout) as client:
        for chunk in chunks:
            response = client.post(
                f"{API}/channels/{channel}/messages",
                headers=headers,
                json={"content": chunk},
            )
            if response.status_code in (401, 403, 404):
                # Named separately because the three mean different things and
                # the fix differs: a dead token, a channel the bot was never
                # invited to, and a channel that no longer exists.
                raise BotPostFailed(
                    f"Discord refused the bot post with {response.status_code}. "
                    f"401 means {BOT_TOKEN_ENV} is revoked, 403 means the bot is "
                    f"not allowed to post in channel {channel}, and 404 means "
                    f"that channel does not exist."
                )
            response.raise_for_status()
    return len(chunks)


def _post_to_webhook(chunks: list[str], target: str, timeout: float) -> int:
    with httpx.Client(timeout=timeout) as client:
        for chunk in chunks:
            response = client.post(target, json={"content": chunk})
            if response.status_code == 404:
                # Discord deletes a webhook outright when it finds the URL
                # published somewhere public, and the job then fails with a 404
                # whose URL is masked in the log as a secret. Say what it means,
                # because "404 for url ***" sends you looking in the wrong place.
                raise WebhookGone(
                    "Discord says this webhook no longer exists (404). It was "
                    "deleted, not expired. Either set "
                    f"{BOT_TOKEN_ENV} and {CHANNEL_ENV} to post as the bot, or "
                    "create a new webhook in Server Settings > Integrations > "
                    f"Webhooks and update {WEBHOOK_ENV}."
                )
            response.raise_for_status()
    return len(chunks)


def post(text: str, url: str | None = None, timeout: float = 15.0) -> int:
    """Send `text` to Discord. Returns how many messages were posted.

    An explicit `url` still forces the webhook, so a caller that has one in hand
    keeps working exactly as before.
    """
    if not text.strip():
        return 0

    chunks = split_message(text)

    if url:
        return _post_to_webhook(chunks, url, timeout)

    bot = bot_credentials()
    if bot:
        return _post_as_bot(chunks, bot[0], bot[1], timeout)

    target = webhook_url()
    if not target:
        raise RuntimeError(
            f"No way to reach Discord: set {BOT_TOKEN_ENV} and {CHANNEL_ENV} to "
            f"post as the bot, or {WEBHOOK_ENV} to post to a webhook."
        )
    return _post_to_webhook(chunks, target, timeout)
