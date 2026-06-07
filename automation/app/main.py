"""
Automation Server — main FastAPI application.

Startup sequence:
  1. Import all module models (registers tables with SQLAlchemy Base)
  2. init_db() — create all tables
  3. create_bot_app() — create Telegram Application + register built-in commands
  4. load_modules() — discover modules/, register their handlers + schedule jobs
  5. Start APScheduler
  6. Start Telegram bot polling (background asyncio task)
"""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# Module models are auto-imported by core.registry during load_modules().
# No explicit model imports are needed here.


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = config.missing()
    if missing:
        logger.warning("Missing config: %s — some features disabled", missing)

    from core.bot import create_bot_app, bot_app as _bot
    bot = create_bot_app()

    from core.scheduler import get_scheduler
    scheduler = get_scheduler()

    from core.registry import load_modules
    from core.bot import AUTHORIZED
    load_modules(bot, scheduler, AUTHORIZED)  # auto-imports all module models

    from core.db import init_db
    init_db()  # called after load_modules() so all tables are registered
    logger.info("✅ Database initialised")

    scheduler.start()
    logger.info("✅ Scheduler started")

    await bot.initialize()
    await bot.start()

    # Register commands with Telegram for native auto-complete menu
    from telegram import BotCommand
    from core.registry import get_loaded

    # Build a lookup: command → first help string for it
    def _cmd_hint(m: dict, cmd: str) -> str:
        for h in m.get("help", []):
            if f"/{cmd}" in h:
                # Strip markdown backticks and leading slash+cmd for a clean description
                clean = h.replace("`", "").strip()
                # Telegram descriptions max 256 chars
                return clean[:90] if len(clean) > 90 else clean
        return f"{m['name']} — {cmd}"

    cmds = [
        BotCommand("start",  "Interactive module menu"),
        BotCommand("menu",   "Interactive module menu"),
        BotCommand("status", "System status & uptime"),
        BotCommand("help",   "Full command reference"),
        BotCommand("cancel", "Cancel running tasks"),
    ]
    for m in get_loaded():
        for c in m["commands"]:
            cmds.append(BotCommand(c, _cmd_hint(m, c)))
    await bot.bot.set_my_commands(cmds)

    await bot.updater.start_polling(drop_pending_updates=True)
    logger.info("✅ Telegram bot polling started")

    yield

    await bot.updater.stop()
    await bot.stop()
    await bot.shutdown()
    scheduler.shutdown(wait=False)
    logger.info("Shutdown complete")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="basheer.net Automation Server",
    description="Self-hosted multi-module automation platform — ai.basheer.net",
    version="1.0.0",
    lifespan=lifespan,
)


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/")
def root():
    from core.registry import get_loaded
    return {
        "service": "basheer.net Automation Server",
        "url": "https://ai.basheer.net",
        "modules": [m["name"] for m in get_loaded()],
    }


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": int(time.time())}


class GenerateRequest(BaseModel):
    topic: str
    category: str = "Technology"
    secret: str = ""


@app.post("/generate")
async def webhook_generate(req: GenerateRequest):
    """REST webhook to trigger blog generation (e.g. from GitHub Actions or Zapier)."""
    if config.webhook_secret and req.secret != config.webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid secret")

    from modules.blog.pipeline import run_blog_pipeline
    try:
        url, draft = await run_blog_pipeline(req.topic, req.category)
        return {"status": "success", "topic": req.topic, "url": url}
    except Exception as e:
        logger.error("Webhook generate failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.exception_handler(Exception)
async def global_error(request, exc):
    logger.error("Unhandled: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": str(exc)})
