from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/logs/stream")
async def stream(request: Request):
    bus = request.app.state.log_bus

    async def event_generator():
        try:
            async for ts, level, msg in bus.subscribe():
                if await request.is_disconnected():
                    break
                payload = json.dumps({"time": ts, "level": level, "msg": msg})
                yield f"data: {payload}\n\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
