from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...db import (
    bots_delete,
    bots_get,
    bots_list,
    bots_referencing_groups,
    bots_upsert,
)
from ..htmx import htmx_response, is_htmx

log = logging.getLogger(__name__)

router = APIRouter(prefix="/bots")


def _mask(token: str) -> str:
    if len(token) <= 6:
        return "•" * len(token)
    return "•" * 8 + token[-4:]


@router.get("", response_class=HTMLResponse)
async def list_view(request: Request):
    cfg = request.app.state.cfg
    bots = bots_list(cfg.storage.db_path)
    rows = [{"name": b.name, "masked": _mask(b.token)} for b in bots]
    return request.app.state.templates.TemplateResponse(
        request, "bot_list.html", {"rows": rows},
    )


@router.get("/new", response_class=HTMLResponse)
async def new_form(request: Request):
    return request.app.state.templates.TemplateResponse(
        request, "bot_form.html",
        {"mode": "new", "bot": None, "error": None},
    )


@router.post("/new")
async def new_submit(
    request: Request,
    name: str = Form(...),
    token: str = Form(...),
):
    cfg = request.app.state.cfg
    name = name.strip()
    token = token.strip()
    if not name or not token:
        return request.app.state.templates.TemplateResponse(
            request, "bot_form.html",
            {"mode": "new", "bot": None,
             "error": "Имя и токен обязательны"},
        )
    if bots_get(cfg.storage.db_path, name):
        return request.app.state.templates.TemplateResponse(
            request, "bot_form.html",
            {"mode": "new", "bot": None,
             "error": f"Бот с именем '{name}' уже есть"},
        )

    bots_upsert(cfg.storage.db_path, name, token)
    return RedirectResponse(
        f"/bots?flash={quote(f'Бот {name} добавлен')}", status_code=303,
    )


@router.get("/{name}/edit", response_class=HTMLResponse)
async def edit_form(name: str, request: Request):
    cfg = request.app.state.cfg
    bot = bots_get(cfg.storage.db_path, name)
    if not bot:
        return RedirectResponse(
            f"/bots?error={quote(f'Бот {name} не найден')}", status_code=303,
        )
    return request.app.state.templates.TemplateResponse(
        request, "bot_form.html",
        {"mode": "edit", "bot": bot, "error": None},
    )


@router.post("/{name}/edit")
async def edit_submit(
    name: str,
    request: Request,
    token: str = Form(...),
):
    cfg = request.app.state.cfg
    bot = bots_get(cfg.storage.db_path, name)
    if not bot:
        return RedirectResponse(
            f"/bots?error={quote(f'Бот {name} не найден')}", status_code=303,
        )
    token = token.strip()
    if not token:
        return request.app.state.templates.TemplateResponse(
            request, "bot_form.html",
            {"mode": "edit", "bot": bot,
             "error": "Токен пустой"},
        )

    bots_upsert(cfg.storage.db_path, name, token)
    return RedirectResponse(
        f"/bots?flash={quote(f'Токен бота {name} обновлён')}", status_code=303,
    )


@router.post("/{name}/delete")
async def delete_submit(name: str, request: Request):
    cfg = request.app.state.cfg
    if not bots_get(cfg.storage.db_path, name):
        if is_htmx(request):
            return htmx_response(
                status_code=404, toast=f"Бот {name} не найден", toast_type="error",
            )
        return RedirectResponse(
            f"/bots?error={quote(f'Бот {name} не найден')}", status_code=303,
        )

    using = bots_referencing_groups(cfg.storage.db_path, name)
    if using:
        msg = f"Бот {name} используется группами: {', '.join(using)}"
        if is_htmx(request):
            return htmx_response(status_code=409, toast=msg, toast_type="error")
        return RedirectResponse(
            f"/bots?error={quote(msg)}", status_code=303,
        )

    bots_delete(cfg.storage.db_path, name)

    if is_htmx(request):
        return htmx_response(toast=f"Бот {name} удалён")
    return RedirectResponse(
        f"/bots?flash={quote(f'Бот {name} удалён')}", status_code=303,
    )
