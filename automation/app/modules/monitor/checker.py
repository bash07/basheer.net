"""
HTTP endpoint checker — performs a single GET request and records the result.
Sends Telegram alerts on state changes (up→down, down→up) and slow responses.
"""
import logging
import time
from datetime import datetime

import httpx

from config import config
from core.db import get_db
from .models import CheckResult, Endpoint

logger = logging.getLogger(__name__)
TIMEOUT = 10  # seconds


async def _send_telegram(message: str) -> None:
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
                json={"chat_id": config.telegram_chat_id, "text": message, "parse_mode": "Markdown"},
                timeout=5,
            )
    except Exception as e:
        logger.warning("Telegram alert failed: %s", e)


async def check_endpoint(endpoint: Endpoint) -> CheckResult:
    """Perform HTTP GET, return a CheckResult (not yet saved to DB)."""
    start = time.monotonic()
    result = CheckResult(
        endpoint_id=endpoint.id,
        checked_at=datetime.utcnow(),
    )
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
            r = await client.get(endpoint.url)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result.is_up = r.status_code < 500
        result.status_code = r.status_code
        result.response_time_ms = elapsed_ms
    except Exception as e:
        result.is_up = False
        result.error = str(e)[:300]

    return result


async def check_all_and_alert(bot=None) -> dict:
    """Check all enabled endpoints, save results, send state-change alerts."""
    from sqlalchemy import select

    with get_db() as db:
        endpoints = db.execute(
            select(Endpoint).where(Endpoint.enabled == True)  # noqa: E712
        ).scalars().all()

    summary = {"checked": 0, "up": 0, "down": 0, "alerts": []}

    for ep in endpoints:
        result = await check_endpoint(ep)
        summary["checked"] += 1
        if result.is_up:
            summary["up"] += 1
        else:
            summary["down"] += 1

        with get_db() as db:
            db.add(result)
            db_ep = db.get(Endpoint, ep.id)
            prev_status = db_ep.last_is_up
            db_ep.last_is_up = result.is_up
            db_ep.last_checked_at = result.checked_at

        # Alert on state changes
        if prev_status is True and not result.is_up:
            msg = (
                f"🔴 *DOWN* — {ep.name}\n"
                f"🔗 {ep.url}\n"
                f"❌ {result.error or f'HTTP {result.status_code}'}"
            )
            await _send_telegram(msg)
            summary["alerts"].append(f"DOWN: {ep.name}")

        elif prev_status is False and result.is_up:
            msg = (
                f"🟢 *RECOVERED* — {ep.name}\n"
                f"🔗 {ep.url}\n"
                f"✅ HTTP {result.status_code} in {result.response_time_ms}ms"
            )
            await _send_telegram(msg)
            summary["alerts"].append(f"UP: {ep.name}")

        # Alert on slow response (but only when up)
        elif (result.is_up and result.response_time_ms
              and result.response_time_ms > ep.alert_threshold_ms):
            msg = (
                f"🟡 *SLOW* — {ep.name}\n"
                f"🔗 {ep.url}\n"
                f"⏱ Response: {result.response_time_ms}ms (threshold: {ep.alert_threshold_ms}ms)"
            )
            await _send_telegram(msg)
            summary["alerts"].append(f"SLOW: {ep.name}")

    return summary
