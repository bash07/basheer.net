"""Conversational AI assistant — multi-agent router.

Architecture:
  Router agent (nano tier) receives every free-text Telegram message and
  delegates to the appropriate domain sub-agent via tool-calling:

    • Blog/content requests  → modules/blog/chat_tools.handle_blog_request()
    • Finance/trading queries → modules/finance/analyst (Phase 4 stub for now)
    • Ops/monitor queries     → get_monitor_status() handled inline
    • General questions       → answered directly by the router

Conversation history (last 30 messages) is maintained at the router level.
Sub-agent calls are single-shot — they receive the user text but not the
full history. This is intentional and acceptable for action-oriented requests.

Phase 4 note: Replace the handle_finance_request stub body with a call to
  modules.finance.analyst.handle_finance_request(text)
"""
import logging
from collections import deque

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage

from core.llm import get_model

logger = logging.getLogger(__name__)

# ── Conversation history ──────────────────────────────────────────────────────
_history: deque[ModelMessage] = deque(maxlen=30)

# ── Router agent ──────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """
You are a personal automation assistant embedded in a Telegram bot.
You manage a multi-domain platform: blog content, site monitoring, and
financial market tracking.

Your capabilities (via tools):
  - Blog: generate posts, get content ideas, show recent posts
  - Finance: track tickers, run TA/fundamental analysis, manage portfolio
  - Ops: check uptime status of monitored websites
  - General: answer knowledge questions directly

Routing rules (pick ONE tool per message):
  - Blog/content/writing requests          → handle_blog_request
  - Finance/trading/market/crypto/portfolio → handle_finance_request
  - Site status/uptime/monitoring          → get_monitor_status
  - General questions needing no action    → answer directly, no tool needed

Keep all replies concise. This is Telegram, not a document editor.
Plain text only — no markdown headers unless they genuinely help.
""".strip()

router = Agent(
    model=get_model("nano"),
    system_prompt=_SYSTEM_PROMPT,
)


# ── Delegation tools ──────────────────────────────────────────────────────────

@router.tool_plain
async def handle_blog_request(text: str) -> str:
    """
    Handle blog and content creation requests.
    Use for: writing/generating posts, content ideas, draft posts,
    showing recent published posts.
    """
    logger.info("[router] → blog assistant: %r", text[:80])
    from modules.blog.chat_tools import handle_blog_request as _delegate
    return await _delegate(text)


@router.tool_plain
async def handle_finance_request(text: str) -> str:
    """
    Handle finance, trading, and market analysis requests.
    Use for: ticker prices, technical analysis, charts, RSI, EMAs,
    fundamental analysis, crypto, forex, commodities, watchlists, price alerts.
    """
    logger.info("[router] → finance assistant: %r", text[:80])
    return await _dispatch_finance(text)


@router.tool_plain
def get_monitor_status() -> str:
    """
    Return the current uptime status of all monitored websites.
    Use when the user asks if sites are up, down, or about monitor/uptime status.
    """
    logger.info("[router] Tool: get_monitor_status()")
    try:
        from sqlalchemy import select
        from core.db import get_db
        from modules.monitor.models import Endpoint

        with get_db() as db:
            endpoints = db.execute(
                select(Endpoint).where(Endpoint.enabled == True)  # noqa: E712
            ).scalars().all()

        if not endpoints:
            return "No endpoints are being monitored yet."

        lines = ["Monitor status:"]
        for ep in endpoints:
            if ep.last_is_up is None:
                status = "⬜ Not checked yet"
            elif ep.last_is_up:
                status = "🟢 UP"
            else:
                status = "🔴 DOWN"
            checked = ep.last_checked_at.strftime("%H:%M") if ep.last_checked_at else "never"
            lines.append(f"{status}  {ep.name}  (last checked: {checked})")
        return "\n".join(lines)
    except Exception as e:
        logger.error("[router] get_monitor_status failed: %s", e, exc_info=True)
        return f"Could not retrieve monitor status: {e}"


# ── Finance dispatcher ────────────────────────────────────────────────────────

_INTENT_PROMPT = """\
You are a finance request parser. The user sent: "{text}"

Reply with EXACTLY one JSON object and nothing else:
  {{"intent": "<fundamental|ta|price|watchlist>", "symbol": "<TICKER or null>"}}

intent rules:
  fundamental — asks about company fundamentals, valuation, P/E, balance sheet, Graham, FA
  ta          — asks about technical analysis, RSI, EMA, chart, MACD, signals, momentum
  price       — asks the current price / quote of a specific ticker
  watchlist   — asks to list watchlist, prices for all tickers, or no specific ticker

symbol rules:
  - Upper-case the ticker. Use yfinance conventions (INFY.NS for Indian stocks, BTC-USD for crypto).
  - If the user types a company name like "Infosys", infer the primary listing ticker.
  - If no specific ticker can be inferred, set symbol to null.
"""


async def _dispatch_finance(text: str) -> str:
    """Parse intent + ticker from free text and delegate to the right finance function."""
    import json
    from pydantic_ai import Agent

    # ── Step 1: parse intent & symbol via a nano LLM call ────────────────────
    parser = Agent(model=get_model("nano"), system_prompt="You are a JSON-only responder.")
    try:
        raw = await parser.run(_INTENT_PROMPT.format(text=text))
        parsed = json.loads(str(raw.output).strip())
        intent = parsed.get("intent", "price")
        symbol: str | None = parsed.get("symbol") or None
    except Exception as e:
        logger.warning("[finance] intent parse failed: %s", e)
        intent, symbol = "price", None

    logger.info("[finance] intent=%s symbol=%s", intent, symbol)

    if symbol is None and intent != "watchlist":
        return (
            "I couldn't identify a ticker symbol from your message. "
            "Try something like: 'Fundamental analysis of AAPL' or 'RSI for BTC-USD'."
        )

    # ── Step 2: delegate ──────────────────────────────────────────────────────
    try:
        if intent == "fundamental":
            return await _run_fundamental(symbol)
        elif intent == "ta":
            return await _run_ta(symbol)
        elif intent == "watchlist":
            return await _run_watchlist_prices()
        else:  # price
            return await _run_price(symbol)
    except Exception as e:
        logger.error("[finance] dispatch error intent=%s symbol=%s: %s", intent, symbol, e, exc_info=True)
        return f"❌ Could not complete the finance request for `{symbol}`: {e}"


async def _run_fundamental(symbol: str) -> str:
    """Fetch fundamentals and return a text report (no Telegram objects)."""
    import asyncio
    from modules.finance.fundamental.fetcher import fetch_fundamentals
    from modules.finance.fundamental.strategies import STRATEGIES, DEFAULT_METHOD
    from modules.finance.fundamental.analyst import generate_verdict
    from modules.finance.fundamental.formatter import format_full_report

    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, fetch_fundamentals, symbol)
    strategy = STRATEGIES[DEFAULT_METHOD]()
    result = strategy.evaluate(data)
    result.verdict = await generate_verdict(data, result)
    return format_full_report(data, result)


async def _run_ta(symbol: str) -> str:
    """Run TA and return a text summary (no chart image in chat mode)."""
    from modules.finance.technical.charts import fetch_ta_only
    from modules.finance.technical.indicators import format_ta_summary

    result = await fetch_ta_only(symbol)
    return format_ta_summary(result)


async def _run_price(symbol: str) -> str:
    """Fetch the current price card for a single ticker."""
    import asyncio
    from modules.finance.watchlist.prices import fetch_price_detail, format_price_card

    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, fetch_price_detail, symbol)
    return format_price_card(info)


async def _run_watchlist_prices() -> str:
    """Fetch prices for all watchlisted tickers and return a grouped summary."""
    import asyncio
    from sqlalchemy import select
    from core.db import get_db
    from modules.finance.watchlist.models import WatchlistEntry
    from modules.finance.watchlist.prices import fetch_prices_batch, format_price_row
    from modules.finance.watchlist.commands import _CATEGORY_LABELS

    rows: list[dict] = []
    with get_db() as db:
        for e in db.execute(
            select(WatchlistEntry).order_by(WatchlistEntry.category, WatchlistEntry.symbol)
        ).scalars().all():
            rows.append({"symbol": e.symbol, "category": e.category})

    if not rows:
        return "Your watchlist is empty. Use `/watch <ticker>` to add tickers."

    loop = asyncio.get_event_loop()
    prices = await loop.run_in_executor(None, fetch_prices_batch, [r["symbol"] for r in rows])

    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["category"], []).append(r["symbol"])

    lines = ["💹 *Watchlist Prices*\n"]
    for cat, symbols in grouped.items():
        lines.append(f"*{_CATEGORY_LABELS.get(cat, cat)}*")
        for sym in symbols:
            lines.append(format_price_row(sym, prices.get(sym)))
        lines.append("")
    return "\n".join(lines).strip()


# ── Public interface ──────────────────────────────────────────────────────────

async def handle_message(text: str) -> str:
    """
    Process a free-text Telegram message and return the router's reply.
    Maintains conversation history across calls (in-memory, resets on restart).
    """
    try:
        result = await router.run(text, message_history=list(_history))
        _history.extend(result.all_messages())
        return str(result.output)
    except Exception as e:
        logger.error("[router] error: %s", e, exc_info=True)
        return f"⚠️ Something went wrong: {e}"


def clear_history() -> None:
    """Clear the conversation history (called by /cancel)."""
    _history.clear()
