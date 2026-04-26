"""Monitor module — scheduled check job (every 5 minutes)."""
import logging
from .checker import check_all_and_alert

logger = logging.getLogger(__name__)


async def check_all_job():
    """Run every 5 minutes. Alerts are sent inside check_all_and_alert()."""
    try:
        summary = await check_all_and_alert()
        if summary["checked"]:
            logger.info(
                "Monitor check: %d checked, %d up, %d down",
                summary["checked"], summary["up"], summary["down"],
            )
    except Exception as e:
        logger.error("Monitor check job failed: %s", e, exc_info=True)
