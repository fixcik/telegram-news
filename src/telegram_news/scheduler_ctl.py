from __future__ import annotations

import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from telethon import TelegramClient

from .config import Config, Group
from .runner import run_group

log = logging.getLogger(__name__)


def build_trigger(group: Group, tz: ZoneInfo):
    if group.cron:
        return CronTrigger.from_crontab(group.cron, timezone=tz)

    if group.interval_hours is None:
        raise ValueError(
            f"Group '{group.name}': no schedule (cron or interval_hours required)"
        )

    start_date = None
    if group.interval_anchor:
        h, m = map(int, group.interval_anchor.split(":"))
        today = datetime.now(tz).date()
        start_date = datetime.combine(today, time(h, m), tzinfo=tz)

    return IntervalTrigger(
        hours=group.interval_hours,
        start_date=start_date,
        timezone=tz,
    )


def describe_schedule(group: Group) -> str:
    if group.cron:
        return f"cron={group.cron!r}"
    anchor = f" @ {group.interval_anchor}" if group.interval_anchor else ""
    return f"every {group.interval_hours}h{anchor}"


class SchedulerCtl:
    """Owns the AsyncIOScheduler and provides live add/update/remove for groups."""

    def __init__(
        self,
        scheduler: AsyncIOScheduler,
        cfg: Config,
        client: TelegramClient,
    ) -> None:
        self.scheduler = scheduler
        self.cfg = cfg
        self.client = client
        self.tz = ZoneInfo(cfg.schedule.timezone)

    @staticmethod
    def _job_id(group_name: str) -> str:
        return f"group:{group_name}"

    def add_group(self, group: Group) -> None:
        trigger = build_trigger(group, self.tz)
        self.scheduler.add_job(
            run_group,
            trigger,
            kwargs={"cfg": self.cfg, "client": self.client, "group": group},
            id=self._job_id(group.name),
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
            replace_existing=True,
        )
        log.info(
            "Scheduled group=%s %s target=%s",
            group.name, describe_schedule(group), group.target,
        )

    def update_group(self, group: Group) -> None:
        # Easiest: remove then add (replace_existing covers this anyway, but
        # this also handles cases where the trigger type changed).
        self.remove_group(group.name)
        self.add_group(group)

    def remove_group(self, name: str) -> None:
        try:
            self.scheduler.remove_job(self._job_id(name))
            log.info("Unscheduled group=%s", name)
        except JobLookupError:
            pass

    def populate_all(self, groups: list[Group]) -> None:
        for group in groups:
            try:
                self.add_group(group)
            except Exception:
                log.exception("Failed to schedule group=%s", group.name)

    def next_run_time(self, group_name: str):
        job = self.scheduler.get_job(self._job_id(group_name))
        return job.next_run_time if job else None
