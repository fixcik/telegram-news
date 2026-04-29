from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ...resolve import ParseError, resolve

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/dialogs", response_class=HTMLResponse)
async def list_dialogs(request: Request, q: str = "", exclude: str = "", limit: int = 20):
    cache = getattr(request.app.state, "dialog_cache", None)
    if cache is None or cache.count == 0:
        return HTMLResponse(
            '<div class="picker-empty">Список чатов не загружен — нажми 🔄</div>'
        )
    excluded_ids: set[int] = set()
    for tok in exclude.split(","):
        tok = tok.strip()
        if tok:
            try:
                excluded_ids.add(int(tok))
            except ValueError:
                pass
    items = cache.search(q=q, exclude=excluded_ids, limit=max(1, min(limit, 50)))
    return request.app.state.templates.TemplateResponse(
        request, "_picker_results.html", {"items": items},
    )


@router.post("/resolve", response_class=HTMLResponse)
async def resolve_link(
    request: Request,
    link: str = Form(...),
    name: str = Form("channel_peers"),
):
    """Resolve a pasted link and return a chip HTML fragment.

    `name` selects the field-name set used in the chip's hidden inputs:
    `channel_peers` (multi) vs `target_peer` (single).
    """
    client = request.app.state.client
    try:
        peer = await resolve(client, link)
    except ParseError as e:
        return HTMLResponse(
            f'<small class="error">{_html_escape(str(e))}</small>',
            status_code=400,
        )
    except RuntimeError as e:
        return HTMLResponse(
            f'<small class="error">{_html_escape(str(e))}</small>',
            status_code=400,
        )
    return request.app.state.templates.TemplateResponse(
        request, "_chip.html",
        {"peer": peer, "name": name, "original": ""},
    )


@router.post("/dialogs/refresh")
async def refresh_dialogs(request: Request):
    cache = getattr(request.app.state, "dialog_cache", None)
    client = request.app.state.client
    if cache is None:
        return JSONResponse({"error": "cache not initialised"}, status_code=503)
    try:
        n = await cache.refresh(client)
    except Exception as e:
        log.exception("Dialog cache refresh failed")
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"count": n})


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )
