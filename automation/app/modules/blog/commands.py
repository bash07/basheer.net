"""Telegram command handlers for the blog module."""
import asyncio
import logging
import uuid

from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_db
from core.queue import queue
from config import config
from .pipeline import run_blog_pipeline, run_ideas_pipeline
from .models import BlogPost

logger = logging.getLogger(__name__)
_STAGES = ["🔍 Researching", "✍️  Writing", "✅ Editing", "📤 Publishing"]


async def _tick_status(bot, chat_id: int, msg_id: int):
    """Edit the status message every 20s to show pipeline progress."""
    for i in range(60):  # max 20 min
        await asyncio.sleep(20)
        stage = _STAGES[min(i // 4, len(_STAGES) - 1)]
        dots = "." * ((i % 3) + 1)
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=f"{stage}{dots}\n\n_This usually takes 2-5 minutes_",
                parse_mode="Markdown",
            )
        except Exception:
            pass


async def generate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /generate <topic> [--cat Category] [--draft]
    Example: /generate RTOS task scheduling --cat Embedded Systems --draft
    """
    if not context.args:
        await update.message.reply_text(
            "Usage: `/generate <topic> [--cat Category] [--draft]`\n"
            "Example: `/generate RTOS basics --cat Embedded Systems --draft`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    
    is_draft = False
    if "--draft" in raw:
        is_draft = True
        raw = raw.replace("--draft", "").strip()
        
    category = "Technology"
    if "--cat" in raw:
        parts = raw.split("--cat", 1)
        raw = parts[0].strip()
        category = parts[1].strip()
        
    topic = raw.strip()

    status_word = "draft" if is_draft else "live post"
    msg = await update.message.reply_text(
        f"🚀 *Blog pipeline started* (Mode: {status_word})\n\n📋 Topic: {topic}\n📂 Category: {category}\n\n🔍 Researching...",
        parse_mode="Markdown",
    )

    task_id = str(uuid.uuid4())[:8]

    async def _run():
        ticker = asyncio.create_task(
            _tick_status(context.bot, update.effective_chat.id, msg.message_id)
        )
        try:
            # PydanticAI pipeline is fully async — no to_thread needed
            url, draft = await run_blog_pipeline(topic, category, is_draft=is_draft)
            ticker.cancel()

            # Save to DB
            with get_db() as db:
                slug = url.rstrip("/").split("/")[-1]
                # mark DB status as draft so /posts can filter properly if needed, although currently it filters by status='published' which means drafts won't show in /posts unless we update that. Let's keep status='draft' in the DB.
                status_db = "draft" if is_draft else "published"
                db.add(BlogPost(title=draft.title, slug=slug, category=category, url=url, status=status_db))

            completion_text = f"✅ *Draft saved to GitHub!*\n\n📝 {draft.title}\n🔗 {url}\n\n_Review and merge in your repository._" if is_draft else f"✅ *Published!*\n\n📝 {draft.title}\n🔗 {url}\n\n_GitHub Actions is now building the site..._"
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg.message_id,
                text=completion_text,
                parse_mode="Markdown",
            )
        except asyncio.CancelledError:
            ticker.cancel()
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id, message_id=msg.message_id,
                text="🛑 Generation cancelled.",
            )
        except Exception as e:
            ticker.cancel()
            logger.error("Blog generation failed: %s", e, exc_info=True)
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id, message_id=msg.message_id,
                text=f"❌ Generation failed: {e}",
            )

    queue.submit(task_id, f"generate:{topic}", "blog", _run(), model_info=config.llm_model)


async def ideas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ideas — generate 5 content ideas and send to Telegram."""
    msg = await update.message.reply_text("💡 Generating content ideas...", parse_mode="Markdown")
    task_id = str(uuid.uuid4())[:8]

    async def _run():
        try:
            ideas = await run_ideas_pipeline()
            # Format and send to Telegram
            lines = ["📋 *Weekly Blog Ideas*\n"]
            for i, idea in enumerate(ideas, 1):
                lines.append(
                    f"{i}. *{idea.topic}*\n"
                    f"   📂 {idea.category}\n"
                    f"   💡 {idea.angle}\n"
                    f"   ⏰ {idea.why_now}"
                )
            lines.append("\nReply `/generate <topic>` to publish one!")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="\n\n".join(lines),
                parse_mode="Markdown",
            )
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id, message_id=msg.message_id,
                text="✅ Ideas sent! Check above ☝️",
            )
        except Exception as e:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id, message_id=msg.message_id,
                text=f"❌ Ideas failed: {e}",
            )

    queue.submit(task_id, "ideas", "blog", _run(), model_info=config.llm_model)


async def posts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/posts — show last 5 published posts."""
    with get_db() as db:
        from sqlalchemy import select, desc
        rows = db.execute(
            select(BlogPost).order_by(desc(BlogPost.published_at)).limit(5)
        ).scalars().all()

    if not rows:
        await update.message.reply_text("No posts published yet. Use /generate to create one!")
        return

    lines = ["📚 *Recent Posts:*\n"]
    for i, p in enumerate(rows, 1):
        lines.append(f"{i}. [{p.title}]({p.url})\n   `{p.category}` — {p.published_at.strftime('%d %b %Y')}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)
