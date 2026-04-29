from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import PeerChannel, PeerChat

from .config import Config

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://", re.IGNORECASE)


@dataclass
class Message:
    channel: str            # canonical: "-100…", "-<chat_id>", or legacy "@username"
    channel_title: str
    message_id: int
    date: datetime
    text: str
    link: str
    sender_name: str | None  # None for broadcast channels


def make_client(cfg: Config) -> TelegramClient:
    Path(cfg.telegram.session_path).parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(
        cfg.telegram.session_path,
        cfg.telegram.api_id,
        cfg.telegram.api_hash,
    )


async def _resolve_entity(client: TelegramClient, channel: str):
    """Inverse of resolve.parse_link: take stored DB value -> Telethon entity."""
    s = channel.strip()
    if s.startswith("@") or (s and s[0].isalpha()):
        return await client.get_entity(s if s.startswith("@") else f"@{s}")
    n = int(s)
    abs_n = abs(n)
    if abs_n >= 1_000_000_000_000:
        return await client.get_entity(PeerChannel(abs_n - 1_000_000_000_000))
    return await client.get_entity(PeerChat(abs_n))


def _format_sender(sender) -> str:
    if sender is None:
        return "Аноним"
    name = " ".join(
        x for x in (getattr(sender, "first_name", None), getattr(sender, "last_name", None)) if x
    ).strip()
    if name:
        return name
    title = getattr(sender, "title", None)
    if title:
        return title
    username = getattr(sender, "username", None)
    if username:
        return f"@{username}"
    return "Аноним"


def _build_link(entity, msg_id: int) -> str:
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"
    # Private channel/megagroup: t.me/c/<entity.id>/<msg>.
    # Telethon `Channel` objects expose `entity.id` already in post-`-100` form,
    # so no further math is needed here. For legacy `Chat` we emit the same form
    # as a best-effort link (Telegram does not have a public URL for small chats).
    return f"https://t.me/c/{abs(entity.id)}/{msg_id}"


async def fetch_new_messages(
    client: TelegramClient,
    channel: str,
    last_message_id: int,
    max_messages: int,
    max_age_days: int,
    min_length: int = 20,
) -> list[Message]:
    """Fetch messages newer than last_message_id, with group-aware filtering."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    out: list[Message] = []

    entity = await _resolve_entity(client, channel)
    is_broadcast = bool(getattr(entity, "broadcast", False))
    title = getattr(entity, "title", None) or getattr(entity, "username", None) or channel

    async for msg in client.iter_messages(entity, limit=max_messages):
        if msg.id <= last_message_id:
            break
        if msg.date < cutoff:
            break
        if msg.action is not None:
            continue  # join/leave/pin/avatar/call

        text = (msg.message or "").strip()
        if not text:
            continue
        if len(text) < min_length and not _URL_RE.search(text):
            continue

        if msg.reply_to is not None:
            text = "↳ " + text

        if is_broadcast:
            sender_name: str | None = None
        else:
            try:
                sender = await msg.get_sender()
            except Exception:
                sender = None
            sender_name = _format_sender(sender)

        out.append(
            Message(
                channel=channel,
                channel_title=title,
                message_id=msg.id,
                date=msg.date,
                text=text,
                link=_build_link(entity, msg.id),
                sender_name=sender_name,
            )
        )

    out.reverse()
    return out
