from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

BOT_API = "https://api.telegram.org"
MAX_LEN = 4096  # Telegram per-message limit


def _split_for_telegram(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split text into Telegram-sized chunks at paragraph/line boundaries."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def send_to_channel(token: str, target: str, text: str) -> None:
    """Send HTML-formatted message(s) via Bot API.

    Telegram HTML supports <b>, <i>, <u>, <s>, <a href>, <code>, <pre>, <blockquote>.
    Web preview is enabled only on the first chunk so TG renders the first URL
    as a card; subsequent chunks have it disabled to avoid noise.

    On HTML parse failure (broken tags from the LLM), retries the whole message
    as plain text — content delivery wins over visual polish.
    """
    url = f"{BOT_API}/bot{token}/sendMessage"
    chunks = _split_for_telegram(text)

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, chunk in enumerate(chunks):
            payload = {
                "chat_id": target,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": i != 0,
            }
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                continue

            # HTML parse error → fallback to plain text (drop tags via simple regex)
            if "can't parse entities" in resp.text.lower():
                import re
                plain = re.sub(r"<[^>]+>", "", chunk)
                payload_plain = {
                    "chat_id": target,
                    "text": plain,
                    "disable_web_page_preview": i != 0,
                }
                resp2 = await client.post(url, json=payload_plain)
                if resp2.status_code == 200:
                    continue
                raise RuntimeError(
                    f"Bot API sendMessage failed in plain-text fallback "
                    f"(chunk {i + 1}/{len(chunks)}): {resp2.status_code} {resp2.text}"
                )

            raise RuntimeError(
                f"Bot API sendMessage failed (chunk {i + 1}/{len(chunks)}): "
                f"{resp.status_code} {resp.text}"
            )
