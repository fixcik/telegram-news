from __future__ import annotations

import logging

from telethon import TelegramClient

from .config import Config, Group
from .db import (
    bots_get,
    get_last_message_id,
    groups_get,
    groups_list,
    save_digest,
    set_last_message_id,
)
from .delivery import send_to_channel
from .summarize import summarize_group
from .tg import Message, ensure_connected, fetch_new_messages

log = logging.getLogger(__name__)


async def run_group(cfg: Config, client: TelegramClient, group: Group) -> None:
    """Run a single group: fetch its channels, summarize, deliver, commit cursors.

    Each (group, channel) cursor is independent, so groups can run on different
    schedules without cursor interference.
    """
    log.info("Running group=%s channels=%d", group.name, len(group.channels))

    await ensure_connected(client)

    all_messages: list[Message] = []
    max_id_by_channel: dict[str, int] = {}
    fetch_failed = False

    max_msgs = group.max_messages_per_channel or cfg.fetcher.max_messages_per_channel
    max_age = group.max_age_days or cfg.fetcher.max_age_days
    min_len = group.min_message_length if group.min_message_length is not None else 20

    for channel in group.channels:
        last_id = get_last_message_id(cfg.storage.db_path, group.name, channel)
        try:
            msgs = await fetch_new_messages(
                client,
                channel,
                last_id,
                max_msgs,
                max_age,
                min_len,
            )
        except Exception:
            log.exception("Fetch failed for %s in group %s", channel, group.name)
            fetch_failed = True
            continue

        log.info("  %s: %d new messages", channel, len(msgs))
        all_messages.extend(msgs)
        if msgs:
            max_id_by_channel[channel] = max(m.message_id for m in msgs)

    if fetch_failed:
        log.warning("Group %s: fetch errors, skipping summarize/deliver", group.name)
        return

    if not all_messages:
        log.info("Group %s: no new messages, skipping delivery", group.name)
        return

    bot = bots_get(cfg.storage.db_path, group.bot)
    if bot is None:
        log.error(
            "Group %s references unknown bot=%s; skipping delivery",
            group.name, group.bot,
        )
        return

    digest = await summarize_group(cfg, group, all_messages)

    if not digest:
        log.warning(
            "Group %s: empty digest from LLM (input=%d messages). "
            "Skipping delivery; advancing cursors so we don't loop on the same batch.",
            group.name, len(all_messages),
        )
        for channel, max_id in max_id_by_channel.items():
            set_last_message_id(cfg.storage.db_path, group.name, channel, max_id)
        return

    await send_to_channel(bot.token, group.target, digest)
    log.info(
        "Group %s: delivered to %s via bot=%s",
        group.name, group.target, bot.name,
    )

    save_digest(cfg.storage.db_path, group.name, len(all_messages), digest)

    for channel, max_id in max_id_by_channel.items():
        set_last_message_id(cfg.storage.db_path, group.name, channel, max_id)


async def run_once(
    cfg: Config, client: TelegramClient, only_group: str | None = None
) -> None:
    """Manually run all groups (or a single named group), ignoring schedule."""
    if only_group:
        target = groups_get(cfg.storage.db_path, only_group)
        if target is None:
            known = [g.name for g in groups_list(cfg.storage.db_path)]
            raise RuntimeError(
                f"No group named '{only_group}'. Known: {known}"
            )
        targets = [target]
    else:
        targets = groups_list(cfg.storage.db_path)

    log.info("Starting manual run for %d group(s)", len(targets))
    for group in targets:
        try:
            await run_group(cfg, client, group)
        except Exception:
            log.exception("Group %s failed", group.name)
