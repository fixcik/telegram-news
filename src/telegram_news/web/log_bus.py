from __future__ import annotations

import asyncio
import collections
import logging
import time
from typing import AsyncIterator


class LogBus:
    """In-memory ring buffer + pub/sub for log records.

    Records are tuples (time_str, level, message). Each subscriber gets a queue;
    on subscribe, the existing buffer is replayed first, then new records stream
    live. Designed for single-process homelab use — no thread-safety beyond what
    asyncio gives us.
    """

    def __init__(self, capacity: int = 300):
        self.buffer: collections.deque = collections.deque(maxlen=capacity)
        self.subscribers: list[asyncio.Queue] = []

    def emit(self, level: str, msg: str) -> None:
        rec = (time.strftime("%H:%M:%S"), level, msg)
        self.buffer.append(rec)
        for q in list(self.subscribers):
            try:
                q.put_nowait(rec)
            except asyncio.QueueFull:
                pass

    async def subscribe(self) -> AsyncIterator[tuple[str, str, str]]:
        q: asyncio.Queue = asyncio.Queue(maxsize=400)
        self.subscribers.append(q)
        try:
            for rec in list(self.buffer):
                yield rec
            while True:
                yield await q.get()
        finally:
            try:
                self.subscribers.remove(q)
            except ValueError:
                pass


class LogBusHandler(logging.Handler):
    """Logging handler that forwards records into a LogBus."""

    def __init__(self, bus: LogBus):
        super().__init__()
        self.bus = bus
        self.setFormatter(logging.Formatter("%(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.bus.emit(record.levelname, self.format(record))
        except Exception:
            pass
