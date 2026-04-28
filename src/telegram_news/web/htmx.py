from __future__ import annotations

import json

from fastapi import Request, Response


def is_htmx(request: Request) -> bool:
    return bool(request.headers.get("hx-request"))


def htmx_response(
    *,
    status_code: int = 200,
    toast: str | None = None,
    toast_type: str = "info",
    body: str = "",
) -> Response:
    """Return a tiny response that signals HTMX to fire a 'toast' event.

    The body defaults to empty so swap targets disappear when paired with
    hx-swap="outerHTML" or "delete".
    """
    headers: dict[str, str] = {}
    if toast:
        headers["HX-Trigger"] = json.dumps(
            {"toast": {"text": toast, "type": toast_type}}
        )
    return Response(
        content=body,
        status_code=status_code,
        media_type="text/html",
        headers=headers,
    )
