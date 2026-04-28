from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...db import groups_get, groups_list, last_digest_at
from ...runner import run_group
from ...scheduler_ctl import describe_schedule
from ..htmx import htmx_response, is_htmx

log = logging.getLogger(__name__)

router = APIRouter()


def _row_data(cfg, scheduler_ctl, g):
    next_run = scheduler_ctl.next_run_time(g.name)
    return {
        "name": g.name,
        "schedule": describe_schedule(g),
        "channels": g.channels,
        "bot": g.bot,
        "target": g.target,
        "last_digest_at": last_digest_at(cfg.storage.db_path, g.name) or "—",
        "next_run": next_run.strftime("%Y-%m-%d %H:%M %Z") if next_run else "—",
        "group": g,
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    cfg = request.app.state.cfg
    scheduler_ctl = request.app.state.scheduler_ctl

    groups = groups_list(cfg.storage.db_path)
    rows = [_row_data(cfg, scheduler_ctl, g) for g in groups]

    return request.app.state.templates.TemplateResponse(
        request, "dashboard.html",
        {"rows": rows, "tz": cfg.schedule.timezone},
    )


@router.post("/groups/{name}/run-now")
async def run_now(name: str, request: Request):
    cfg = request.app.state.cfg
    client = request.app.state.client

    group = groups_get(cfg.storage.db_path, name)
    if not group:
        if is_htmx(request):
            return htmx_response(
                status_code=404, toast=f"Группа {name} не найдена", toast_type="error",
            )
        return RedirectResponse(
            f"/?error={quote(f'Группа {name} не найдена')}", status_code=303,
        )

    async def _run_safely():
        try:
            await run_group(cfg, client, group)
        except Exception:
            log.exception("Manual run failed for group=%s", group.name)

    asyncio.create_task(_run_safely())
    log.info("Manual run queued for group=%s", name)

    if is_htmx(request):
        return htmx_response(toast=f"▶ {name}: запуск в очереди")
    return RedirectResponse(
        f"/?flash={quote(f'Запуск группы {name} поставлен в очередь')}",
        status_code=303,
    )


@router.get("/groups/{name}/cells/next-run", response_class=HTMLResponse)
async def cell_next_run(name: str, request: Request):
    scheduler_ctl = request.app.state.scheduler_ctl
    next_run = scheduler_ctl.next_run_time(name)
    text = next_run.strftime("%Y-%m-%d %H:%M %Z") if next_run else "—"
    return request.app.state.templates.TemplateResponse(
        request, "_next_run_cell.html", {"name": name, "text": text},
    )


@router.get("/groups/{name}/cells/last-digest", response_class=HTMLResponse)
async def cell_last_digest(name: str, request: Request):
    cfg = request.app.state.cfg
    text = last_digest_at(cfg.storage.db_path, name) or "—"
    return request.app.state.templates.TemplateResponse(
        request, "_last_digest_cell.html", {"name": name, "text": text},
    )
