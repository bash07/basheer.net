"""Telegram commands for the uptime monitor module."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, desc
from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_db
from .models import CheckResult, Endpoint
from .checker import check_all_and_alert

logger = logging.getLogger(__name__)


async def addurl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /addurl <url> [name]
    Example: /addurl https://basheer.net My Blog
    """
    if not context.args:
        await update.message.reply_text(
            "Usage: `/addurl <url> [name]`\nExample: `/addurl https://basheer.net My Blog`",
            parse_mode="Markdown",
        )
        return

    url = context.args[0]
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    name = " ".join(context.args[1:]) if len(context.args) > 1 else url

    with get_db() as db:
        existing = db.execute(select(Endpoint).where(Endpoint.name == name)).scalar_one_or_none()
        if existing:
            await update.message.reply_text(f"⚠️ An endpoint named *{name}* already exists.", parse_mode="Markdown")
            return
        db.add(Endpoint(name=name, url=url))

    await update.message.reply_text(
        f"✅ Added: *{name}*\n🔗 {url}\n\nChecks run every 5 minutes.",
        parse_mode="Markdown",
    )


async def rmurl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/rmurl <name> — remove a monitored endpoint."""
    if not context.args:
        await update.message.reply_text("Usage: `/rmurl <name>`", parse_mode="Markdown")
        return

    name = " ".join(context.args)
    with get_db() as db:
        ep = db.execute(select(Endpoint).where(Endpoint.name == name)).scalar_one_or_none()
        if not ep:
            await update.message.reply_text(f"❌ No endpoint named *{name}* found.", parse_mode="Markdown")
            return
        db.delete(ep)

    await update.message.reply_text(f"🗑 Removed: *{name}*", parse_mode="Markdown")


async def urls_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/urls — list all monitored endpoints with current status."""
    with get_db() as db:
        eps = db.execute(select(Endpoint).order_by(Endpoint.name)).scalars().all()

    if not eps:
        await update.message.reply_text(
            "No endpoints monitored yet.\nUse `/addurl <url> [name]` to add one.",
            parse_mode="Markdown",
        )
        return

    lines = ["🌐 *Monitored Endpoints:*\n"]
    for ep in eps:
        status = "🟢" if ep.last_is_up else ("🔴" if ep.last_is_up is False else "⚪")
        checked = ep.last_checked_at.strftime("%H:%M") if ep.last_checked_at else "never"
        enabled = "" if ep.enabled else " _(disabled)_"
        lines.append(f"{status} *{ep.name}*{enabled}\n   `{ep.url}`\n   Last checked: {checked}")

    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/check — manually trigger a check of all endpoints right now."""
    msg = await update.message.reply_text("🔄 Checking all endpoints...")
    summary = await check_all_and_alert()
    text = (
        f"✅ Check complete\n\n"
        f"Checked: {summary['checked']} | "
        f"🟢 {summary['up']} up | "
        f"🔴 {summary['down']} down"
    )
    if summary["alerts"]:
        text += f"\n\n⚠️ Alerts sent: {', '.join(summary['alerts'])}"
    await msg.edit_text(text)


async def uptime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /uptime [name] — show uptime stats for all endpoints or a specific one.
    Stats for last 24h.
    """
    target_name = " ".join(context.args) if context.args else None
    since = datetime.now(timezone.utc) - timedelta(hours=24)

    with get_db() as db:
        if target_name:
            eps = db.execute(
                select(Endpoint).where(Endpoint.name == target_name)
            ).scalars().all()
            if not eps:
                await update.message.reply_text(f"❌ No endpoint named *{target_name}*.", parse_mode="Markdown")
                return
        else:
            eps = db.execute(select(Endpoint)).scalars().all()

        lines = ["📊 *Uptime Stats (last 24h):*\n"]
        for ep in eps:
            total = db.execute(
                select(func.count(CheckResult.id))
                .where(CheckResult.endpoint_id == ep.id, CheckResult.checked_at >= since)
            ).scalar() or 0
            up_count = db.execute(
                select(func.count(CheckResult.id))
                .where(CheckResult.endpoint_id == ep.id,
                       CheckResult.checked_at >= since,
                       CheckResult.is_up == True)  # noqa: E712
            ).scalar() or 0
            avg_ms = db.execute(
                select(func.avg(CheckResult.response_time_ms))
                .where(CheckResult.endpoint_id == ep.id,
                       CheckResult.checked_at >= since,
                       CheckResult.is_up == True)  # noqa: E712
            ).scalar()

            pct = (up_count / total * 100) if total else 0
            bar = "🟢" if pct >= 99 else ("🟡" if pct >= 95 else "🔴")
            avg_str = f"{int(avg_ms)}ms avg" if avg_ms else "n/a"
            lines.append(f"{bar} *{ep.name}*\n   {pct:.1f}% uptime ({up_count}/{total} checks) | {avg_str}")

    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")
