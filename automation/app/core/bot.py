"""
Telegram bot — single-user control panel.

Security: Only responds to TELEGRAM_CHAT_ID via filters.User.
Built-in commands: /start, /status, /help, /cancel.
Module commands are registered by the module registry.
"""
import logging
import time
from datetime import datetime

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import config
from core.queue import queue
from core.registry import get_loaded

logger = logging.getLogger(__name__)

# ── Globals ──────────────────────────────────────────────────────────────────
bot_app: Application = None          # Set by create_bot_app()
AUTHORIZED: filters.BaseFilter = None  # Set by create_bot_app()
_startup_time = time.time()


# ── Built-in command handlers ─────────────────────────────────────────────────

# ── Domain display helpers ───────────────────────────────────────────────────

# Maps domain folder name → display label for Telegram messages.
_DOMAIN_LABELS: dict[str, str] = {
    "content":  "📝 Content",
    "ops":      "🛠 Ops",
}


def _domain_label(domain: str) -> str:
    """Human-readable label for a domain name."""
    return _DOMAIN_LABELS.get(domain, f"🔧 {domain.capitalize()}")


def _group_by_domain(modules: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """
    Split loaded modules into:
      flat    — legacy flat modules (domain=None), shown without a header
      grouped — dict of domain → [modules], shown under domain headers
    """
    flat: list[dict] = []
    grouped: dict[str, list[dict]] = {}
    for m in modules:
        domain = m.get("domain")
        if domain is None:
            flat.append(m)
        else:
            grouped.setdefault(domain, []).append(m)
    return flat, grouped


# ── Built-in command handlers ─────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mods = get_loaded()
    flat, grouped = _group_by_domain(mods)
    lines = ["🤖 *Automation Server*\n", "*Loaded modules:*"]

    # Flat legacy modules — no domain header (backward-compatible display)
    for m in flat:
        lines.append(f"• {m['description']}")
        for cmd in m["commands"]:
            lines.append(f"  `/{cmd}`")

    # Domain-grouped modules — show under labelled headers
    for domain, domain_mods in grouped.items():
        lines.append(f"\n*{_domain_label(domain)}:*")
        for m in domain_mods:
            lines.append(f"• {m['description']}")
            for cmd in m["commands"]:
                lines.append(f"  `/{cmd}`")

    lines.append("\nType /help for full command list.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime_s = int(time.time() - _startup_time)
    h, m = divmod(uptime_s // 60, 60)
    running = queue.running()

    lines = [
        "📊 *System Status*\n",
        f"⏱ Uptime: {h}h {m}m",
        f"🔄 Active tasks: {len(running)}",
    ]
    if running:
        for t in running:
            elapsed = int(time.time() - t.started_at)
            model_tag = f" · `{t.model_info}`" if t.model_info else ""
            lines.append(f"  • [{t.module}] {t.name}{model_tag} ({elapsed}s)")

    # ── LLM configuration snapshot ────────────────────────────────────────────
    lines.append("\n🤖 *LLM Config:*")
    lines.append(f"  Primary: `{config.llm_model}` ({config.llm_provider})")
    if config.llm_nano_model:
        nano_provider = config.llm_nano_provider or config.llm_provider
        lines.append(f"  Nano:    `{config.llm_nano_model}` ({nano_provider})")
    if config.llm_fallback_model:
        fb_provider = config.llm_fallback_provider or config.llm_provider
        lines.append(f"  Fallback: `{config.llm_fallback_model}` ({fb_provider})")

    lines.append(f"\n🕐 Server time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mods = get_loaded()
    flat, grouped = _group_by_domain(mods)
    lines = [
        "📖 *Command Reference*\n",
        "*Global:*",
        "`/start` — welcome & module list",
        "`/status` — uptime & running tasks",
        "`/cancel` — cancel all running tasks",
        "`/help` — this message\n",
    ]

    # Flat legacy modules — no domain header (backward-compatible display)
    for m in flat:
        lines.append(f"*{m['description']}:*")
        for item in m.get("help", []):
            lines.append(f"  {item}")
        lines.append("")

    # Domain-grouped modules — one header per domain, all commands beneath it
    for domain, domain_mods in grouped.items():
        lines.append(f"*{_domain_label(domain)}:*")
        for m in domain_mods:
            for item in m.get("help", []):
                lines.append(f"  {item}")
        lines.append("")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.chat import clear_history
    clear_history()
    count = queue.cancel_all()
    if count:
        await update.message.reply_text(f"🛑 Cancelled {count} running task(s). Conversation history cleared.")
    else:
        await update.message.reply_text("🗑 Conversation history cleared. No tasks were running.")


# ── Free-text message handler ───────────────────────────────────────────────────

async def free_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle any non-command text message by routing it through the ChatAgent.
    Shows a 'Thinking…' placeholder while the agent works, then edits it
    with the final reply so the conversation feels responsive.
    """
    from core.chat import handle_message

    thinking = await update.message.reply_text("💡 Thinking…")
    reply = await handle_message(update.message.text)
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=thinking.message_id,
        text=reply,
    )


# ── Bot application factory ───────────────────────────────────────────────────

def create_bot_app() -> Application:
    global bot_app, AUTHORIZED

    AUTHORIZED = filters.User(user_id=config.telegram_chat_id)

    bot_app = Application.builder().token(config.telegram_bot_token).build()

    # Built-in slash commands
    bot_app.add_handler(CommandHandler("start",  start_handler,  filters=AUTHORIZED))
    bot_app.add_handler(CommandHandler("status", status_handler, filters=AUTHORIZED))
    bot_app.add_handler(CommandHandler("help",   help_handler,   filters=AUTHORIZED))
    bot_app.add_handler(CommandHandler("cancel", cancel_handler, filters=AUTHORIZED))

    # Free-text handler — must be registered AFTER all CommandHandlers
    # so that /commands are matched first and never fall through here.
    bot_app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & AUTHORIZED,
            free_text_handler,
        )
    )

    return bot_app
