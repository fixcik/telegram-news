from __future__ import annotations

import json
import logging
from urllib.parse import quote

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...config import Group, validate_anchor
from ...db import (
    bots_list, channels_with_titles, groups_delete, groups_get, groups_upsert,
)
from ...scheduler_ctl import describe_schedule
from ..htmx import htmx_response, is_htmx

log = logging.getLogger(__name__)

router = APIRouter(prefix="/groups")


def _build_group_from_form(
    name: str,
    schedule_kind: str,
    cron: str,
    interval_hours: str,
    interval_anchor: str,
    interests: str,
    instructions: str,
    bot: str,
    target_peer: str,
    target_original: str,
    target_title: str,
    channel_peers: list[str],
    channel_originals: list[str],
    channel_titles: list[str],
    max_messages: str,
    max_age: str,
    min_length: str,
) -> tuple[Group | None, list[str], list[str], str | None]:
    """Returns (group, original_channels_parallel, display_titles_parallel, error)."""
    cron_v: str | None = None
    interval_v: float | None = None
    anchor_v: str | None = None

    if schedule_kind == "cron":
        cron = cron.strip()
        if not cron:
            return None, [], [], "Поле cron пустое"
        try:
            CronTrigger.from_crontab(cron)
        except ValueError as e:
            return None, [], [], f"Невалидный cron: {e}"
        cron_v = cron
    elif schedule_kind == "interval":
        if not interval_hours.strip():
            return None, [], [], "Поле interval_hours пустое"
        try:
            interval_v = float(interval_hours)
        except ValueError:
            return None, [], [], f"interval_hours должен быть числом, получено {interval_hours!r}"
        if interval_v <= 0:
            return None, [], [], "interval_hours должен быть > 0"
        if interval_anchor.strip():
            try:
                anchor_v = validate_anchor(interval_anchor.strip())
            except ValueError as e:
                return None, [], [], str(e)
    else:
        return None, [], [], f"Неизвестный schedule_kind: {schedule_kind!r}"

    if not channel_peers:
        return None, [], [], "Не выбран ни один источник"
    if len(channel_peers) != len(channel_originals) or len(channel_peers) != len(channel_titles):
        return None, [], [], "Внутренняя ошибка формы: длины списков не совпадают"

    if not name.strip():
        return None, [], [], "Имя группы пустое"
    if not bot.strip():
        return None, [], [], "Не выбран бот"
    if not target_peer.strip():
        return None, [], [], "Канал-цель не задан"

    def _maybe_int(s: str, ctx: str) -> int | None:
        s = s.strip()
        if not s:
            return None
        try:
            v = int(s)
        except ValueError:
            raise ValueError(f"{ctx}: ожидается целое число, получено {s!r}")
        if v < 0:
            raise ValueError(f"{ctx}: должно быть >= 0")
        return v

    try:
        max_msgs_v = _maybe_int(max_messages, "max_messages_per_channel")
        max_age_v = _maybe_int(max_age, "max_age_days")
        min_len_v = _maybe_int(min_length, "min_message_length")
    except ValueError as e:
        return None, [], [], str(e)

    group = Group(
        name=name.strip(),
        interests=interests,
        channels=channel_peers,
        bot=bot.strip(),
        target=target_peer.strip(),
        cron=cron_v,
        interval_hours=interval_v,
        interval_anchor=anchor_v,
        instructions=instructions.strip() or None,
        max_messages_per_channel=max_msgs_v,
        max_age_days=max_age_v,
        min_message_length=min_len_v,
        target_title=target_title.strip() or None,
    )
    return group, channel_originals, channel_titles, None


def _annotate_group_for_form(db_path, group: Group | None) -> Group | None:
    if group is None:
        return None
    rows = channels_with_titles(db_path, group.name)
    resolved = []
    for r in rows:
        resolved.append({
            "peer_id": r["channel"],
            "title": r["display_title"] or r["channel"],
            "username": None,
            "kind": "channel",
            "original": r["channel"],
        })
    group.resolved_channels = resolved
    return group


def _render_form(
    request: Request,
    *,
    mode: str,                # "new" | "edit"
    group: Group | None,
    bots: list,
    error: str | None = None,
):
    cfg = request.app.state.cfg
    annotated = _annotate_group_for_form(cfg.storage.db_path, group)
    return request.app.state.templates.TemplateResponse(
        request, "group_form.html",
        {
            "mode": mode,
            "group": annotated,
            "bots": bots,
            "error": error,
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def new_form(request: Request):
    cfg = request.app.state.cfg
    bots = bots_list(cfg.storage.db_path)
    if not bots:
        return RedirectResponse(
            f"/bots?error={quote('Сначала зарегистрируй хотя бы одного бота')}",
            status_code=303,
        )
    return _render_form(request, mode="new", group=None, bots=bots)


@router.post("/new")
async def new_submit(
    request: Request,
    name: str = Form(...),
    schedule_kind: str = Form(...),
    cron: str = Form(""),
    interval_hours: str = Form(""),
    interval_anchor: str = Form(""),
    interests: str = Form(""),
    instructions: str = Form(""),
    bot: str = Form(...),
    target_peer: str = Form(...),
    target_peer__original: str = Form(""),
    target_peer__title: str = Form(""),
    channel_peers: list[str] = Form(default=[]),
    channel_peers__original: list[str] = Form(default=[]),
    channel_peers__title: list[str] = Form(default=[]),
    max_messages_per_channel: str = Form(""),
    max_age_days: str = Form(""),
    min_message_length: str = Form(""),
):
    cfg = request.app.state.cfg
    bots = bots_list(cfg.storage.db_path)

    group, originals, titles, err = _build_group_from_form(
        name, schedule_kind, cron, interval_hours, interval_anchor,
        interests, instructions, bot,
        target_peer, target_peer__original, target_peer__title,
        channel_peers, channel_peers__original, channel_peers__title,
        max_messages_per_channel, max_age_days, min_message_length,
    )
    if err:
        return _render_form(request, mode="new", group=None, bots=bots, error=err)

    if groups_get(cfg.storage.db_path, group.name):
        return _render_form(
            request, mode="new", group=None, bots=bots,
            error=f"Группа с именем '{group.name}' уже существует",
        )

    groups_upsert(
        cfg.storage.db_path, group,
        original_channels=originals, display_titles=titles,
    )
    request.app.state.scheduler_ctl.add_group(group)
    return RedirectResponse(
        f"/?flash={quote(f'Группа {group.name} создана')}", status_code=303,
    )


@router.get("/{name}/edit", response_class=HTMLResponse)
async def edit_form(name: str, request: Request):
    cfg = request.app.state.cfg
    group = groups_get(cfg.storage.db_path, name)
    if not group:
        return RedirectResponse(
            f"/?error={quote(f'Группа {name} не найдена')}", status_code=303,
        )
    bots = bots_list(cfg.storage.db_path)
    return _render_form(request, mode="edit", group=group, bots=bots)


@router.post("/{name}/edit")
async def edit_submit(
    name: str,
    request: Request,
    schedule_kind: str = Form(...),
    cron: str = Form(""),
    interval_hours: str = Form(""),
    interval_anchor: str = Form(""),
    interests: str = Form(""),
    instructions: str = Form(""),
    bot: str = Form(...),
    target_peer: str = Form(...),
    target_peer__original: str = Form(""),
    target_peer__title: str = Form(""),
    channel_peers: list[str] = Form(default=[]),
    channel_peers__original: list[str] = Form(default=[]),
    channel_peers__title: list[str] = Form(default=[]),
    max_messages_per_channel: str = Form(""),
    max_age_days: str = Form(""),
    min_message_length: str = Form(""),
):
    cfg = request.app.state.cfg
    bots = bots_list(cfg.storage.db_path)

    if not groups_get(cfg.storage.db_path, name):
        return RedirectResponse(
            f"/?error={quote(f'Группа {name} не найдена')}", status_code=303,
        )

    group, originals, titles, err = _build_group_from_form(
        name, schedule_kind, cron, interval_hours, interval_anchor,
        interests, instructions, bot,
        target_peer, target_peer__original, target_peer__title,
        channel_peers, channel_peers__original, channel_peers__title,
        max_messages_per_channel, max_age_days, min_message_length,
    )
    if err:
        existing = groups_get(cfg.storage.db_path, name)
        return _render_form(request, mode="edit", group=existing, bots=bots, error=err)

    groups_upsert(
        cfg.storage.db_path, group,
        original_channels=originals, display_titles=titles,
    )
    request.app.state.scheduler_ctl.update_group(group)
    return RedirectResponse(
        f"/?flash={quote(f'Группа {group.name} обновлена')}", status_code=303,
    )


@router.get("/{name}/schedule-cell", response_class=HTMLResponse)
async def schedule_cell(name: str, request: Request):
    cfg = request.app.state.cfg
    group = groups_get(cfg.storage.db_path, name)
    if not group:
        return HTMLResponse('<td class="schedule-cell">—</td>')
    return request.app.state.templates.TemplateResponse(
        request, "_schedule_cell.html",
        {"group": group, "description": describe_schedule(group)},
    )


@router.get("/{name}/schedule-edit", response_class=HTMLResponse)
async def schedule_edit(name: str, request: Request):
    cfg = request.app.state.cfg
    group = groups_get(cfg.storage.db_path, name)
    if not group:
        return HTMLResponse('<td class="schedule-cell">—</td>')
    return request.app.state.templates.TemplateResponse(
        request, "_schedule_edit.html", {"group": group},
    )


@router.post("/{name}/schedule")
async def schedule_update(
    name: str,
    request: Request,
    kind: str = Form(...),
    cron: str = Form(""),
    interval_hours: str = Form(""),
    interval_anchor: str = Form(""),
):
    cfg = request.app.state.cfg
    group = groups_get(cfg.storage.db_path, name)
    if not group:
        return htmx_response(
            status_code=404, toast=f"Группа {name} не найдена", toast_type="error",
        )

    new_cron: str | None = None
    new_interval: float | None = None
    new_anchor: str | None = None

    if kind == "cron":
        cron_v = cron.strip()
        if not cron_v:
            return htmx_response(
                status_code=400, toast="Cron пустой", toast_type="error",
            )
        try:
            CronTrigger.from_crontab(cron_v)
        except ValueError as e:
            return htmx_response(
                status_code=400, toast=f"Невалидный cron: {e}", toast_type="error",
            )
        new_cron = cron_v
    elif kind == "interval":
        try:
            new_interval = float(interval_hours)
        except ValueError:
            return htmx_response(
                status_code=400, toast="Часы должны быть числом", toast_type="error",
            )
        if new_interval <= 0:
            return htmx_response(
                status_code=400, toast="Часы должны быть > 0", toast_type="error",
            )
        anchor_v = interval_anchor.strip()
        if anchor_v:
            try:
                new_anchor = validate_anchor(anchor_v)
            except ValueError as e:
                return htmx_response(
                    status_code=400, toast=str(e), toast_type="error",
                )
    else:
        return htmx_response(
            status_code=400, toast=f"Неизвестный kind={kind}", toast_type="error",
        )

    new_group = Group(
        name=group.name,
        interests=group.interests,
        channels=group.channels,
        bot=group.bot,
        target=group.target,
        cron=new_cron,
        interval_hours=new_interval,
        interval_anchor=new_anchor,
        instructions=group.instructions,
    )
    groups_upsert(cfg.storage.db_path, new_group)
    request.app.state.scheduler_ctl.update_group(new_group)

    response = request.app.state.templates.TemplateResponse(
        request, "_schedule_cell.html",
        {"group": new_group, "description": describe_schedule(new_group)},
    )
    response.headers["HX-Trigger"] = json.dumps(
        {"toast": {"text": f"Расписание {name} обновлено"}}
    )
    return response


@router.post("/{name}/delete")
async def delete_submit(name: str, request: Request):
    cfg = request.app.state.cfg
    if not groups_get(cfg.storage.db_path, name):
        if is_htmx(request):
            return htmx_response(
                status_code=404, toast=f"Группа {name} не найдена", toast_type="error",
            )
        return RedirectResponse(
            f"/?error={quote(f'Группа {name} не найдена')}", status_code=303,
        )
    groups_delete(cfg.storage.db_path, name)
    request.app.state.scheduler_ctl.remove_group(name)

    if is_htmx(request):
        return htmx_response(toast=f"Группа {name} удалена")
    return RedirectResponse(
        f"/?flash={quote(f'Группа {name} удалена')}", status_code=303,
    )
