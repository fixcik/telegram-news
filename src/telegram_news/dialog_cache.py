from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from telethon import TelegramClient

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedDialog:
    peer_id: int
    title: str
    username: str | None
    kind: Literal["channel", "megagroup", "chat"]


class DialogCache:
    def __init__(self) -> None:
        self._items: list[CachedDialog] = []

    @property
    def count(self) -> int:
        return len(self._items)

    async def refresh(self, client: TelegramClient) -> int:
        items: list[CachedDialog] = []
        async for d in client.iter_dialogs(limit=None):
            entity = d.entity
            if getattr(entity, "broadcast", False):
                kind: Literal["channel", "megagroup", "chat"] = "channel"
            elif getattr(entity, "megagroup", False):
                kind = "megagroup"
            elif hasattr(entity, "title"):
                kind = "chat"
            else:
                continue
            title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(entity.id)
            username = getattr(entity, "username", None)
            if kind in ("channel", "megagroup"):
                peer_id = -1_000_000_000_000 - entity.id
            else:
                peer_id = -entity.id
            items.append(CachedDialog(peer_id=peer_id, title=title, username=username, kind=kind))
        self._items = items
        log.info("Dialog cache refreshed: %d entries", len(items))
        return len(items)

    def search(self, q: str, exclude: set[int], limit: int) -> list[CachedDialog]:
        q_lower = (q or "").strip().lower()
        out: list[CachedDialog] = []
        for d in self._items:
            if d.peer_id in exclude:
                continue
            if q_lower:
                hay = f"{d.title} {d.username or ''}".lower()
                if q_lower not in hay:
                    continue
            out.append(d)
            if len(out) >= limit:
                break
        return out
