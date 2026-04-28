from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient

from .config import Config

log = logging.getLogger(__name__)


@dataclass
class Message:
    channel: str
    message_id: int
    date: datetime
    text: str
    link: str


def make_client(cfg: Config) -> TelegramClient:
    Path(cfg.telegram.session_path).parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(
        cfg.telegram.session_path,
        cfg.telegram.api_id,
        cfg.telegram.api_hash,
    )


async def fetch_new_messages(
    client: TelegramClient,
    channel: str,
    last_message_id: int,
    max_messages: int,
    max_age_days: int,
) -> list[Message]:
    """Fetch messages newer than last_message_id, capped by max_messages/max_age_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    out: list[Message] = []

    entity = await client.get_entity(channel)
    channel_username = getattr(entity, "username", None) or channel.lstrip("@")

    async for msg in client.iter_messages(entity, limit=max_messages):
        if msg.id <= last_message_id:
            break
        if msg.date < cutoff:
            break
        text = (msg.message or "").strip()
        if not text:
            continue
        out.append(
            Message(
                channel=channel,
                message_id=msg.id,
                date=msg.date,
                text=text,
                link=f"https://t.me/{channel_username}/{msg.id}",
            )
        )

    out.reverse()  # chronological
    return out
