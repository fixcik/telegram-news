from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

from .config import Config, Group, validate_anchor
from .db import bots_upsert, groups_upsert

log = logging.getLogger(__name__)


def import_yaml(cfg: Config, groups_path: str | Path = "groups.yaml") -> tuple[int, int, int]:
    """One-time bootstrap: read groups.yaml + bot tokens from env into the DB.

    Returns (bots_count, groups_count, channels_count). Idempotent (UPSERT).
    """
    with open(groups_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    bots_n = 0
    for b in raw.get("bots", []):
        token = os.environ.get(b["token_env"])
        if not token:
            raise RuntimeError(
                f"Bot '{b['name']}': env var {b['token_env']} not set; "
                f"can't import token. Set it in .env and re-run."
            )
        bots_upsert(cfg.storage.db_path, b["name"], token)
        bots_n += 1

    groups_n = 0
    channels_n = 0
    for g in raw.get("groups", []):
        cron = g.get("cron")
        interval_hours = g.get("interval_hours")
        interval_anchor = g.get("interval_anchor")

        if cron and interval_hours:
            raise RuntimeError(
                f"Group '{g['name']}': both cron and interval_hours set in yaml"
            )
        if not cron and not interval_hours:
            cron = cfg.schedule.default_cron

        if interval_anchor:
            validate_anchor(interval_anchor, ctx=f"Group '{g['name']}'")

        channels = list(g.get("channels", []))
        group = Group(
            name=g["name"],
            interests=g["interests"],
            channels=channels,
            bot=g["bot"],
            target=g["target"],
            cron=cron,
            interval_hours=float(interval_hours) if interval_hours else None,
            interval_anchor=interval_anchor,
            instructions=g.get("instructions"),
        )
        groups_upsert(cfg.storage.db_path, group)
        groups_n += 1
        channels_n += len(channels)

    log.info(
        "Imported from yaml: bots=%d groups=%d channels=%d",
        bots_n, groups_n, channels_n,
    )
    return bots_n, groups_n, channels_n
