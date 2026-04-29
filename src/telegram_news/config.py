from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ScheduleConfig:
    default_cron: str
    timezone: str


@dataclass
class TelegramConfig:
    api_id: int
    api_hash: str
    session_path: str


@dataclass
class OpenRouterConfig:
    api_key: str
    model: str
    base_url: str
    temperature: float
    max_tokens: int
    request_timeout_s: float = 180.0


@dataclass
class FetcherConfig:
    max_messages_per_channel: int
    max_age_days: int


@dataclass
class StorageConfig:
    db_path: str


@dataclass
class WebConfig:
    host: str
    port: int


@dataclass
class Bot:
    name: str
    token: str


@dataclass
class Group:
    name: str
    interests: str
    channels: list[str]
    bot: str
    target: str
    cron: str | None = None
    interval_hours: float | None = None
    interval_anchor: str | None = None
    instructions: str | None = None
    max_messages_per_channel: int | None = None
    max_age_days: int | None = None
    min_message_length: int | None = None
    target_title: str | None = None


@dataclass
class Config:
    schedule: ScheduleConfig
    telegram: TelegramConfig
    openrouter: OpenRouterConfig
    fetcher: FetcherConfig
    storage: StorageConfig
    web: WebConfig


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Environment variable {name} is not set. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def validate_anchor(anchor: str, ctx: str = "") -> str:
    """Validate HH:MM time-of-day string. Returns canonical form or raises."""
    try:
        h, m = anchor.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError
    except (ValueError, AttributeError):
        prefix = f"{ctx}: " if ctx else ""
        raise ValueError(f"{prefix}interval_anchor must be 'HH:MM', got {anchor!r}")
    return anchor


def validate_schedule(group: Group) -> None:
    """Ensure exactly one of cron / interval_hours is set."""
    has_cron = bool(group.cron)
    has_interval = group.interval_hours is not None
    if has_cron and has_interval:
        raise ValueError(
            f"Group '{group.name}': specify either 'cron' or 'interval_hours', not both"
        )
    if not has_cron and not has_interval:
        raise ValueError(
            f"Group '{group.name}': must specify 'cron' or 'interval_hours'"
        )
    if group.interval_anchor:
        validate_anchor(group.interval_anchor, ctx=f"Group '{group.name}'")


def load_config(config_path: str | Path = "config.yaml") -> Config:
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return Config(
        schedule=ScheduleConfig(
            default_cron=raw["schedule"]["default_cron"],
            timezone=raw["schedule"]["timezone"],
        ),
        telegram=TelegramConfig(
            api_id=int(_require_env("TG_API_ID")),
            api_hash=_require_env("TG_API_HASH"),
            session_path=raw["telegram"]["session_path"],
        ),
        openrouter=OpenRouterConfig(
            api_key=_require_env("OPENROUTER_API_KEY"),
            model=raw["openrouter"]["model"],
            base_url=raw["openrouter"]["base_url"],
            temperature=float(raw["openrouter"]["temperature"]),
            max_tokens=int(raw["openrouter"]["max_tokens"]),
            request_timeout_s=float(raw["openrouter"].get("request_timeout_s", 180)),
        ),
        fetcher=FetcherConfig(**raw["fetcher"]),
        storage=StorageConfig(**raw["storage"]),
        web=WebConfig(
            host=raw["web"]["host"],
            port=int(raw["web"]["port"]),
        ),
    )
