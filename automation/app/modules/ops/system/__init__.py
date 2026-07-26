"""
System module — agentic domain package manager.

Commands:
  /system install <domain>   — install a domain package from GitHub and restart
  /system remove <domain>    — uninstall a domain package and restart
  /system list               — list all installed plugin packages
  /system restart            — graceful restart (Docker will bring bot back up)

Security: all commands are gated by the AUTHORIZED filter defined in bot.py.
The bot restarts itself via sys.exit(0); docker-compose restart: unless-stopped
brings it back up automatically within ~3 seconds.

Domain packages are expected to live at:
  git+https://github.com/<GITHUB_ORG>/<repo>.git@main

The GITHUB_ORG is read from config (falls back to "bash07").
Installed packages are persisted in /app/domain_modules.txt which is
bind-mounted from the host, so they survive container rebuilds.
"""
import logging
import os
import subprocess
import sys
import asyncio

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config

logger = logging.getLogger(__name__)

NAME = "system"
DESCRIPTION = "⚙️ System — agentic module manager"
DOMAIN_MODULES_FILE = "/app/domain_modules.txt"
GITHUB_ORG = os.environ.get("GITHUB_ORG", "bash07")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _git_url(repo_name: str) -> str:
    """Build the pip-installable git URL for a domain repo."""
    return f"git+https://github.com/{GITHUB_ORG}/{repo_name}.git@main"


def _read_domain_urls() -> list[str]:
    """Return all non-comment, non-empty lines from domain_modules.txt."""
    if not os.path.exists(DOMAIN_MODULES_FILE):
        return []
    with open(DOMAIN_MODULES_FILE, "r") as f:
        return [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]


def _write_domain_urls(urls: list[str]) -> None:
    """Overwrite the data section of domain_modules.txt with the given URLs."""
    # Preserve the comment header, then write URLs
    header_lines: list[str] = []
    if os.path.exists(DOMAIN_MODULES_FILE):
        with open(DOMAIN_MODULES_FILE, "r") as f:
            for line in f:
                if line.strip().startswith("#") or line.strip() == "":
                    header_lines.append(line)
                else:
                    break  # first non-comment line reached
    with open(DOMAIN_MODULES_FILE, "w") as f:
        f.writelines(header_lines)
        for url in urls:
            f.write(url + "\n")


async def _pip_install(url: str) -> tuple[bool, str]:
    """Run pip install for a git URL in a subprocess. Returns (success, output)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--no-cache-dir", url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        output = stdout.decode(errors="replace").strip()
        return proc.returncode == 0, output
    except asyncio.TimeoutError:
        return False, "pip install timed out after 120s"
    except Exception as e:
        return False, str(e)


async def _pip_uninstall(package_name: str) -> tuple[bool, str]:
    """Run pip uninstall -y for a package name."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "uninstall", "-y", package_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode(errors="replace").strip()
        return proc.returncode == 0, output
    except asyncio.TimeoutError:
        return False, "pip uninstall timed out after 30s"
    except Exception as e:
        return False, str(e)


def _schedule_restart(delay: float = 2.0) -> None:
    """Schedule a clean exit so Docker can restart the container."""
    async def _exit():
        await asyncio.sleep(delay)
        logger.info("Restarting bot process via sys.exit(0)…")
        sys.exit(0)

    asyncio.ensure_future(_exit())


# ── Command handler ───────────────────────────────────────────────────────────

async def system_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /system <subcommand> [args]

    Subcommands:
      install <domain>   install git+github.com/<org>/automation-<domain>
      remove  <domain>   uninstall a previously installed domain
      list               list installed plugin packages
      restart            graceful restart (for manual use)
    """
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "⚙️ *System — Module Manager*\n\n"
            "`/system install <domain>` — install a domain from GitHub\n"
            "`/system remove <domain>`  — uninstall a domain\n"
            "`/system list`             — list installed plugins\n"
            "`/system restart`          — graceful restart\n",
            parse_mode="Markdown",
        )
        return

    sub = args[0].lower()

    # ── /system list ─────────────────────────────────────────────────────────
    if sub == "list":
        urls = _read_domain_urls()
        if not urls:
            await update.message.reply_text("📦 No external domain packages installed.")
        else:
            lines = ["📦 *Installed domain packages:*\n"]
            for url in urls:
                lines.append(f"  • `{url}`")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # ── /system restart ───────────────────────────────────────────────────────
    if sub == "restart":
        await update.message.reply_text(
            "🔄 Restarting engine… I'll be back in ~5 seconds."
        )
        _schedule_restart(delay=2.0)
        return

    # ── /system install <domain> ──────────────────────────────────────────────
    if sub == "install":
        if len(args) < 2:
            await update.message.reply_text("Usage: `/system install <domain>`", parse_mode="Markdown")
            return

        domain = args[1].lower()
        repo_name = f"automation-{domain}"
        url = _git_url(repo_name)

        # Check not already installed
        existing = _read_domain_urls()
        if url in existing:
            await update.message.reply_text(f"✅ `{domain}` is already installed.", parse_mode="Markdown")
            return

        msg = await update.message.reply_text(
            f"📦 Installing `{domain}` from GitHub…\n`{url}`",
            parse_mode="Markdown",
        )

        success, output = await _pip_install(url)

        if not success:
            tail = output[-800:] if len(output) > 800 else output
            await msg.edit_text(
                f"❌ Installation failed:\n```\n{tail}\n```",
                parse_mode="Markdown",
            )
            return

        # Persist to domain_modules.txt
        existing.append(url)
        _write_domain_urls(existing)

        await msg.edit_text(
            f"✅ `{domain}` installed successfully.\n"
            f"📝 Saved to `domain_modules.txt`.\n"
            f"🔄 Restarting engine to load new module… (~5s)",
            parse_mode="Markdown",
        )
        _schedule_restart(delay=2.0)
        return

    # ── /system remove <domain> ───────────────────────────────────────────────
    if sub == "remove":
        if len(args) < 2:
            await update.message.reply_text("Usage: `/system remove <domain>`", parse_mode="Markdown")
            return

        domain = args[1].lower()
        repo_name = f"automation-{domain}"
        url = _git_url(repo_name)

        existing = _read_domain_urls()
        if url not in existing:
            await update.message.reply_text(
                f"⚠️ `{domain}` is not in the installed list.", parse_mode="Markdown"
            )
            return

        msg = await update.message.reply_text(
            f"🗑 Removing `{domain}`…", parse_mode="Markdown"
        )

        # pip uninstall uses the distribution name (repo_name, not git URL)
        success, output = await _pip_uninstall(repo_name)

        # Remove from persistence regardless (pip may have already removed it)
        updated = [u for u in existing if u != url]
        _write_domain_urls(updated)

        if success:
            await msg.edit_text(
                f"✅ `{domain}` removed.\n"
                f"🔄 Restarting engine… (~5s)",
                parse_mode="Markdown",
            )
        else:
            await msg.edit_text(
                f"⚠️ Uninstall may have partially failed, but entry removed from list.\n"
                f"🔄 Restarting to apply… (~5s)",
                parse_mode="Markdown",
            )

        _schedule_restart(delay=2.0)
        return

    # ── Unknown subcommand ────────────────────────────────────────────────────
    await update.message.reply_text(
        f"❓ Unknown subcommand `{sub}`. Use `/system` to see available commands.",
        parse_mode="Markdown",
    )


# ── Module registration ───────────────────────────────────────────────────────

def register(bot_app, scheduler, authorized_filter) -> dict:
    bot_app.add_handler(CommandHandler("system", system_handler, filters=authorized_filter))

    return {
        "name": NAME,
        "description": DESCRIPTION,
        "commands": ["system"],
        "help": [
            "`/system install <domain>` — install a domain module from GitHub",
            "`/system remove <domain>`  — remove a domain module",
            "`/system list`             — list installed plugins",
            "`/system restart`          — graceful engine restart",
        ],
    }
