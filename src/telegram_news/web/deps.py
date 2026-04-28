from __future__ import annotations

from fastapi import Request
from fastapi.responses import RedirectResponse
from telethon import TelegramClient

from ..config import Config
from ..scheduler_ctl import SchedulerCtl


def get_cfg(request: Request) -> Config:
    return request.app.state.cfg


def get_client(request: Request) -> TelegramClient:
    return request.app.state.client


def get_scheduler_ctl(request: Request) -> SchedulerCtl:
    return request.app.state.scheduler_ctl


async def require_auth(request: Request):
    """Redirect to /auth if Telethon user is not authorized.

    Used by routes that need an authorized user (everything except /auth/*).
    """
    client: TelegramClient = request.app.state.client
    if not await client.is_user_authorized():
        return RedirectResponse("/auth", status_code=303)
    return None
