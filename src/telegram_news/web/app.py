from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Config
from ..db import init_db, load_groups_for_runtime
from ..dialog_cache import DialogCache
from ..scheduler_ctl import SchedulerCtl
from ..tg import make_client
from .log_bus import LogBus, LogBusHandler
from .routes import auth as auth_routes
from .routes import bots as bots_routes
from .routes import dashboard as dashboard_routes
from .routes import dialogs as dialogs_routes
from .routes import groups as groups_routes
from .routes import logs as logs_routes

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# Loggers we forward into the live UI log pane.
LIVE_LOGGERS = ("telegram_news", "apscheduler", "httpx")


def _attach_log_bus(bus: LogBus) -> LogBusHandler:
    handler = LogBusHandler(bus)
    handler.setLevel(logging.INFO)
    for name in LIVE_LOGGERS:
        logging.getLogger(name).addHandler(handler)
    return handler


def _detach_log_bus(handler: LogBusHandler) -> None:
    for name in LIVE_LOGGERS:
        logging.getLogger(name).removeHandler(handler)


def create_app(cfg: Config) -> FastAPI:
    init_db(cfg.storage.db_path)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = make_client(cfg)
        await client.connect()
        log.info("Telethon client connected")

        tz = ZoneInfo(cfg.schedule.timezone)
        scheduler = AsyncIOScheduler(timezone=tz)
        scheduler_ctl = SchedulerCtl(scheduler, cfg, client)
        dialog_cache = DialogCache()

        if await client.is_user_authorized():
            scheduler_ctl.populate_all(load_groups_for_runtime(cfg.storage.db_path))
            log.info("User authorized; scheduler populated from DB")
            try:
                await dialog_cache.refresh(client)
            except Exception:
                log.exception("Initial dialog cache refresh failed; picker will be empty until /api/dialogs/refresh")
        else:
            log.warning("User not authorized; scheduler will populate after /auth")

        scheduler.start()
        scheduler_ctl.start_health_check()

        log_bus = LogBus(capacity=400)
        log_handler = _attach_log_bus(log_bus)

        app.state.cfg = cfg
        app.state.client = client
        app.state.scheduler = scheduler
        app.state.scheduler_ctl = scheduler_ctl
        app.state.templates = templates
        app.state.pending_auth = {}
        app.state.log_bus = log_bus
        app.state.dialog_cache = dialog_cache

        try:
            yield
        finally:
            _detach_log_bus(log_handler)
            scheduler.shutdown(wait=False)
            await client.disconnect()
            log.info("Lifespan shutdown complete")

    app = FastAPI(title="telegram-news", lifespan=lifespan)
    STATIC_DIR.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.state.cfg = cfg

    @app.middleware("http")
    async def require_auth_middleware(request: Request, call_next):
        path = request.url.path
        if path.startswith("/auth") or path.startswith("/static"):
            return await call_next(request)
        client = getattr(request.app.state, "client", None)
        if client is None or not await client.is_user_authorized():
            return RedirectResponse("/auth", status_code=303)
        return await call_next(request)

    app.include_router(auth_routes.router)
    app.include_router(dashboard_routes.router)
    app.include_router(groups_routes.router)
    app.include_router(bots_routes.router)
    app.include_router(logs_routes.router)
    app.include_router(dialogs_routes.router)

    return app
