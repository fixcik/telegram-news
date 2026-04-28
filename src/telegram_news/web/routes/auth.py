from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from telethon.errors import SessionPasswordNeededError

from ...db import load_groups_for_runtime

log = logging.getLogger(__name__)

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


@router.get("/auth", response_class=HTMLResponse)
async def auth_page(request: Request):
    client = request.app.state.client
    if await client.is_user_authorized():
        return RedirectResponse("/", status_code=303)
    pending = request.app.state.pending_auth or {}
    if pending.get("needs_password"):
        return _templates(request).TemplateResponse(
            request, "auth_password.html", {}
        )
    if pending.get("phone_code_hash"):
        return _templates(request).TemplateResponse(
            request, "auth_code.html", {"phone": pending["phone"]}
        )
    return _templates(request).TemplateResponse(
        request, "auth_phone.html", {}
    )


@router.post("/auth/send-code")
async def auth_send_code(request: Request, phone: str = Form(...)):
    client = request.app.state.client
    phone = phone.strip()
    try:
        sent = await client.send_code_request(phone)
    except Exception as e:
        log.exception("send_code_request failed")
        return RedirectResponse(
            f"/auth?error=Не удалось отправить код: {e}", status_code=303
        )
    request.app.state.pending_auth = {
        "phone": phone,
        "phone_code_hash": sent.phone_code_hash,
    }
    return RedirectResponse("/auth", status_code=303)


@router.post("/auth/sign-in")
async def auth_sign_in(
    request: Request,
    code: str = Form(""),
    password: str = Form(""),
):
    client = request.app.state.client
    pending = request.app.state.pending_auth or {}

    if not pending.get("phone_code_hash") and not pending.get("needs_password"):
        return RedirectResponse(
            "/auth?error=Сессия истекла, начни заново", status_code=303
        )

    try:
        if pending.get("needs_password"):
            if not password:
                return RedirectResponse(
                    "/auth?error=Введи пароль 2FA", status_code=303
                )
            await client.sign_in(password=password)
        else:
            if not code:
                return RedirectResponse(
                    "/auth?error=Введи код из Telegram", status_code=303
                )
            await client.sign_in(
                phone=pending["phone"],
                code=code.strip(),
                phone_code_hash=pending["phone_code_hash"],
            )
    except SessionPasswordNeededError:
        request.app.state.pending_auth = {
            **pending,
            "needs_password": True,
        }
        return RedirectResponse("/auth", status_code=303)
    except Exception as e:
        log.exception("sign_in failed")
        return RedirectResponse(f"/auth?error=Ошибка входа: {e}", status_code=303)

    # Success — clear pending, populate scheduler with groups from DB
    request.app.state.pending_auth = {}
    cfg = request.app.state.cfg
    scheduler_ctl = request.app.state.scheduler_ctl
    scheduler_ctl.populate_all(load_groups_for_runtime(cfg.storage.db_path))
    log.info("User authenticated; scheduler populated from DB")
    return RedirectResponse("/?flash=Авторизация успешна", status_code=303)


@router.post("/auth/logout")
async def auth_logout(request: Request):
    """Force logout: delete session and require re-auth."""
    client = request.app.state.client
    await client.log_out()
    request.app.state.pending_auth = {}
    return RedirectResponse("/auth?flash=Вышли из аккаунта", status_code=303)
