"""Post to a Discord incoming webhook.

A webhook is deliberately all this needs: it is a plain HTTPS POST, so the
alerter can run as a scheduled job anywhere. A bot that answers slash commands
would need a long-lived process, which the current hosting does not provide.
"""

import os

import httpx

WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"

# Discord rejects a message body over 2000 characters.
MAX_CONTENT = 2000


def webhook_url() -> str | None:
    url = os.getenv(WEBHOOK_ENV, "").strip()
    return url or None


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


def post(text: str, url: str | None = None, timeout: float = 15.0) -> int:
    """Send `text` to the webhook. Returns how many messages were posted."""
    target = url or webhook_url()
    if not target:
        raise RuntimeError(
            f"No Discord webhook configured - set {WEBHOOK_ENV} to the webhook URL."
        )
    if not text.strip():
        return 0

    chunks = split_message(text)
    with httpx.Client(timeout=timeout) as client:
        for chunk in chunks:
            response = client.post(target, json={"content": chunk})
            response.raise_for_status()
    return len(chunks)
