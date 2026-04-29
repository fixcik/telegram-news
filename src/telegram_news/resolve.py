from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from telethon import TelegramClient


class ParseError(ValueError):
    """Raised when a string cannot be interpreted as a Telegram peer reference."""


@dataclass(frozen=True)
class ResolvedPeer:
    peer_id: int
    title: str
    username: str | None
    kind: Literal["channel", "megagroup", "chat"]


# https://t.me/c/<internal_id>/<msg_id>?  msg_id is optional and ignored.
_PRIVATE_RE = re.compile(r"^(?:https?://)?t\.me/c/(\d+)(?:/\d+)?/?$")
# https://t.me/<username>/<msg_id>?
_PUBLIC_URL_RE = re.compile(r"^(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{3,})(?:/\d+)?/?$")
# tg://resolve?domain=<username>
_TG_RESOLVE_RE = re.compile(r"^tg://resolve\?domain=([A-Za-z][A-Za-z0-9_]{3,})$")
# Bare or @-prefixed username.
_BARE_USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,})$")
# Raw -100xxxx numeric id.
_RAW_NUMERIC_RE = re.compile(r"^-100\d{6,}$")
# Invite forms we explicitly reject.
_INVITE_RE = re.compile(r"^(?:https?://)?t\.me/(?:\+|joinchat/)\S+$")


def parse_link(raw: str) -> tuple[Literal["username", "peer_id"], object]:
    """Parse a link/handle into ('username', name) or ('peer_id', -100…).

    Raises ParseError for invite links and unrecognised input.
    """
    s = (raw or "").strip()
    if not s:
        raise ParseError("empty input")

    if _INVITE_RE.match(s):
        raise ParseError(
            "invite links not supported — join the chat from the Telegram app, "
            "then pick it from 'From my chats'"
        )

    m = _PRIVATE_RE.match(s)
    if m:
        return ("peer_id", -1_000_000_000_000 - int(m.group(1)))

    if _RAW_NUMERIC_RE.match(s):
        return ("peer_id", int(s))

    m = _TG_RESOLVE_RE.match(s)
    if m:
        return ("username", m.group(1))

    m = _PUBLIC_URL_RE.match(s)
    if m:
        return ("username", m.group(1))

    m = _BARE_USERNAME_RE.match(s)
    if m:
        return ("username", m.group(1))

    raise ParseError(f"could not interpret as Telegram link: {s!r}")
