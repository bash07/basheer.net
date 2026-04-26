"""Shared APScheduler AsyncIOScheduler singleton."""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from config import config

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=config.timezone)
    return _scheduler
