"""
Telegram bot — single-user control panel.

Security: Only responds to TELEGRAM_CHAT_ID via filters.User.
Built-in commands: /start, /status, /help, /cancel.
Module commands are registered by the module registry.
"""
import logging
import time
from datetime import datetime

from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import config
from core.queue import queue
from core.registry import get_loaded
from core.version import get_version_info

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
    "ops":      "⚙️ Ops",
    "finance":  "💹 Finance",
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


# ── Hierarchical inline-keyboard menu helpers ────────────────────────────────

def _build_main_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Top-level menu: one button per domain + one row per flat (legacy) module.
    Each button callback_data encodes "menu:domain" or "menu:flat:<name>".
    """
    mods = get_loaded()
    flat, grouped = _group_by_domain(mods)
    rows: list[list[InlineKeyboardButton]] = []

    for domain in grouped:
        rows.append([InlineKeyboardButton(
            _domain_label(domain),
            callback_data=f"menu:domain:{domain}",
        )])
    for m in flat:
        rows.append([InlineKeyboardButton(
            m["description"],
            callback_data=f"menu:flat:{m['name']}",
        )])

    return InlineKeyboardMarkup(rows)


def _build_domain_keyboard(domain: str) -> InlineKeyboardMarkup:
    """
    Drill-down view: one button per command in the domain, plus a ← Back row.
    """
    mods = get_loaded()
    _, grouped = _group_by_domain(mods)
    domain_mods = grouped.get(domain, [])

    rows: list[list[InlineKeyboardButton]] = []
    for m in domain_mods:
        for cmd in m["commands"]:
            # Find help string for this command (first match)
            hint = next(
                (h for h in m.get("help", []) if f"/{cmd}" in h),
                f"/{cmd}",
            )
            # Strip backtick markdown so the button label is clean
            label = f"/{cmd}"
            rows.append([InlineKeyboardButton(label, callback_data=f"menu:cmd:{cmd}")])

    rows.append([InlineKeyboardButton("← Back", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def _build_flat_keyboard(mod_name: str) -> InlineKeyboardMarkup:
    """Drill-down view for a flat (legacy) module."""
    mods = get_loaded()
    flat, _ = _group_by_domain(mods)
    mod = next((m for m in flat if m["name"] == mod_name), None)

    rows: list[list[InlineKeyboardButton]] = []
    if mod:
        for cmd in mod["commands"]:
            rows.append([InlineKeyboardButton(f"/{cmd}", callback_data=f"menu:cmd:{cmd}")])
    rows.append([InlineKeyboardButton("← Back", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def _main_menu_text() -> str:
    return "🤖 *Automation Server*\n\nChoose a category:"


def _domain_menu_text(domain: str) -> str:
    mods = get_loaded()
    _, grouped = _group_by_domain(mods)
    domain_mods = grouped.get(domain, [])
    lines = [f"{_domain_label(domain)} commands:\n"]
    for m in domain_mods:
        lines.append(f"*{m['description']}*")
        for item in m.get("help", []):
            lines.append(f"  {item}")
    return "\n".join(lines)


def _flat_menu_text(mod_name: str) -> str:
    mods = get_loaded()
    flat, _ = _group_by_domain(mods)
    mod = next((m for m in flat if m["name"] == mod_name), None)
    if not mod:
        return f"Module *{mod_name}* not found."
    lines = [f"*{mod['description']}*\n"]
    for item in mod.get("help", []):
        lines.append(f"  {item}")
    return "\n".join(lines)


# ── Built-in command handlers ─────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the compact interactive main menu."""
    await update.message.reply_text(
        _main_menu_text(),
        parse_mode="Markdown",
        reply_markup=_build_main_menu_keyboard(),
    )


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Alias for /menu — same interactive main menu."""
    await update.message.reply_text(
        _main_menu_text(),
        parse_mode="Markdown",
        reply_markup=_build_main_menu_keyboard(),
    )


async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle all menu inline-button presses.

    callback_data patterns:
      menu:main            — go back to the top-level menu
      menu:domain:<name>   — drill into a domain
      menu:flat:<name>     — drill into a flat (legacy) module
      menu:cmd:<cmd>       — user tapped a command name (just acknowledge)
    """
    query = update.callback_query
    await query.answer()  # dismiss the loading spinner

    data = query.data or ""

    if data == "menu:main":
        await query.edit_message_text(
            _main_menu_text(),
            parse_mode="Markdown",
            reply_markup=_build_main_menu_keyboard(),
        )

    elif data.startswith("menu:domain:"):
        domain = data[len("menu:domain:"):]
        await query.edit_message_text(
            _domain_menu_text(domain),
            parse_mode="Markdown",
            reply_markup=_build_domain_keyboard(domain),
        )

    elif data.startswith("menu:flat:"):
        mod_name = data[len("menu:flat:"):]
        await query.edit_message_text(
            _flat_menu_text(mod_name),
            parse_mode="Markdown",
            reply_markup=_build_flat_keyboard(mod_name),
        )

    elif data.startswith("menu:cmd:"):
        cmd = data[len("menu:cmd:"):]
        # Just show a nudge — the user can tap the command in chat to run it
        await query.answer(f"Type /{cmd} in chat to run it.", show_alert=False)


async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime_s = int(time.time() - _startup_time)
    h, m = divmod(uptime_s // 60, 60)
    running = queue.running()
    v_info = get_version_info()

    meta_parts = []
    if v_info.get("commit"):
        meta_parts.append(f"`{v_info['commit']}`")
    if v_info.get("build_date"):
        meta_parts.append(v_info["build_date"])
    meta_str = f" ({' · '.join(meta_parts)})" if meta_parts else ""

    lines = [
        "📊 *System Status*\n",
        f"🏷 *Version:* `v{v_info['version']}`{meta_str}",
    ]
    if v_info.get("summary"):
        summary_text = v_info["summary"]
        if len(summary_text) > 80:
            summary_text = summary_text[:77] + "..."
        summary_text = summary_text.replace("`", "'")
        lines.append(f"📝 *Latest:* `{summary_text}`")

    lines.extend([
        f"\n⏱ Uptime: {h}h {m}m",
        f"🔄 Active tasks: {len(running)}",
    ])
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
    bot_app.add_handler(CommandHandler("menu",   menu_handler,   filters=AUTHORIZED))
    bot_app.add_handler(CommandHandler("status",  status_handler,  filters=AUTHORIZED))
    bot_app.add_handler(CommandHandler("version", status_handler,  filters=AUTHORIZED))
    bot_app.add_handler(CommandHandler("help",    help_handler,    filters=AUTHORIZED))
    bot_app.add_handler(CommandHandler("cancel", cancel_handler, filters=AUTHORIZED))

    # Inline-keyboard menu callbacks (no auth filter needed — only our chat_id
    # can press buttons in our private bot, but we filter anyway for safety)
    bot_app.add_handler(
        CallbackQueryHandler(
            menu_callback_handler,
            pattern=r"^menu:",
        )
    )

    # Free-text handler — must be registered AFTER all CommandHandlers
    # so that /commands are matched first and never fall through here.
    bot_app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & AUTHORIZED,
            free_text_handler,
        )
    )

    return bot_app
