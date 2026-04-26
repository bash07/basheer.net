"""Blog module — scheduled jobs."""
import logging

from .pipeline import run_ideas_pipeline

logger = logging.getLogger(__name__)


async def weekly_ideas_job():
    """Every Monday 9 AM — run ideas pipeline (natively async, no thread needed)."""
    logger.info("Running weekly ideas job...")
    try:
        await run_ideas_pipeline()
        logger.info("Weekly ideas job completed.")
    except Exception as e:
        logger.error("Weekly ideas job failed: %s", e, exc_info=True)
