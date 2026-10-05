"""Watchdog background jobs for host resource and health monitoring.

Runs every 15 minutes:
- Checks available RAM (alert if < 150 MB)
- Checks disk usage (alert if > 85% or free < 1.5 GB)
- Checks database bloat (alert if SQLite DB + WAL > 500 MB)
Dispatches proactive Telegram notifications on anomalies with deduplication / cooldown.
"""
import logging
import time
from typing import Optional

from config import config
from .formatters import format_health_alert
from .probes import get_full_system_snapshot

logger = logging.getLogger(__name__)

# State for alert cooldown (avoid spamming every 15 minutes for the same issue)
# key: alert_reason -> timestamp_seconds
_last_alert_times: dict[str, float] = {}
COOLDOWN_SECONDS = 3600  # 1 hour cooldown per alert reason


async def send_health_telegram(message: str) -> None:
    """Send alert message to Telegram admin chat."""
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return
    try:
        from core.bot import bot_app
        if bot_app and bot_app.bot:
            await bot_app.bot.send_message(
                chat_id=config.telegram_chat_id,
                text=message,
                parse_mode="Markdown",
            )
            return
    except Exception as e:
        logger.warning("Could not send health alert via bot_app: %s, falling back to httpx", e)

    try:
        import httpx
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
                json={
                    "chat_id": config.telegram_chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                },
                timeout=5,
            )
    except Exception as e:
        logger.error("Failed to dispatch health alert to Telegram: %s", e)


def evaluate_health_thresholds(snapshot: dict) -> list[tuple[str, str, str]]:
    """Evaluate system snapshot against health thresholds.

    Returns list of tuples: (alert_key, severity, description)
    """
    alerts: list[tuple[str, str, str]] = []
    mem = snapshot.get("memory", {})
    storage = snapshot.get("storage", {})

    # 1. RAM Critical (< 150 MB available)
    avail_mb = mem.get("avail_mb", 0.0)
    if avail_mb < 150.0:
        alerts.append((
            "mem_critical",
            "CRITICAL",
            f"Low Memory Warning: Only {avail_mb:.1f} MB RAM available (< 150 MB threshold)!",
        ))

    # 2. Disk Critical (> 85% used or < 1.5 GB free)
    used_pct = storage.get("used_pct", 0.0)
    free_gb = storage.get("free_gb", 0.0)
    if used_pct >= 85.0 or free_gb < 1.5:
        alerts.append((
            "disk_critical",
            "WARNING",
            f"High Disk Usage: {used_pct:.1f}% used ({free_gb:.1f} GB free on {storage.get('mount_path', '/app/data')})",
        ))

    # 3. Database Bloat (> 500 MB total SQLite DB + WAL)
    auto_db_bytes = storage.get("automation_db_bytes", 0)
    auto_wal_bytes = storage.get("automation_wal_bytes", 0)
    total_db_mb = (auto_db_bytes + auto_wal_bytes) / (1024.0 * 1024.0)
    if total_db_mb > 500.0:
        alerts.append((
            "db_bloat",
            "WARNING",
            f"Database Bloat: automation.db + WAL is {total_db_mb:.1f} MB (> 500 MB threshold)",
        ))

    return alerts


async def check_system_health_job() -> list[str]:
    """15-minute APScheduler watchdog job."""
    global _last_alert_times
    now = time.time()
    triggered_reasons: list[str] = []

    try:
        snapshot = get_full_system_snapshot()
        evaluations = evaluate_health_thresholds(snapshot)

        for alert_key, severity, desc in evaluations:
            last_sent = _last_alert_times.get(alert_key, 0.0)
            if now - last_sent >= COOLDOWN_SECONDS:
                _last_alert_times[alert_key] = now
                triggered_reasons.append(desc)
                msg = format_health_alert(severity, desc, snapshot)
                logger.warning("System Health Alert Triggered: [%s] %s", severity, desc)
                await send_health_telegram(msg)
            else:
                logger.info("Health alert %s suppressed by cooldown", alert_key)

    except Exception as e:
        logger.error("Error executing check_system_health_job: %s", e, exc_info=True)

    return triggered_reasons
