"""Telegram message formatters for the fundamental analysis module."""
from datetime import date, datetime, timezone
from .fetcher import FundamentalsData
from .strategies.base import StrategyResult, CriterionResult


def format_full_report(data: FundamentalsData, result: StrategyResult) -> str:
    """Full /fundamental reply: header + criteria table + verdict."""
    mcap = _fmt_mcap(data.market_cap)
    header = (
        f"📊 *{data.symbol}* — {data.display_name}\n"
        f"🏭 {data.sector or 'N/A'} | {data.industry or 'N/A'}\n"
        f"💰 Market Cap: {mcap}\n"
    )
    if data.next_earnings_date:
        days = (data.next_earnings_date - datetime.now(timezone.utc).date()).days
        header += f"📅 Next Earnings: {data.next_earnings_date.strftime('%d %b %Y')} ({days}d)\n"

    score_line = (
        f"\n*{result.method.title()} Score: {result.score}/{result.max_score}* "
        f"({result.score}/{result.total} with data)\n"
        "——————————\n"
    )

    rows = []
    for c in result.criteria:
        icon = "✅" if c.passed else ("❌" if c.passed is False else "⚪")
        rows.append(f"{icon} {c.name}: `{c.actual}` (threshold: {c.threshold})")

    verdict_block = f"\n📝 *Verdict:*\n{result.verdict}" if result.verdict else ""
    margin_block = f"\n⚠️ *Margin Alert:*\n{data.margin_trend_warning}" if data.margin_trend_warning else ""

    return header + score_line + "\n".join(rows) + margin_block + verdict_block


def format_screen_row(symbol: str, result: StrategyResult | None, rank: int) -> str:
    """One compact line for the /screen ranked list."""
    if result is None:
        return f"{rank}. ⚪ `{symbol}` — no data"
    bar = "🟢" if result.pct >= 70 else ("🟡" if result.pct >= 40 else "🔴")
    return f"{rank}. {bar} `{symbol}` {result.score}/{result.max_score} ({result.pct:.0f}%)"


def format_screen_report(
    rows: list[tuple[str, StrategyResult | None]],
    method: str,
    timestamp: date,
) -> str:
    lines = [
        f"🔍 *{method.title()} Screen — Watchlist*",
        f"_{timestamp.strftime('%d %b %Y')}_\n",
    ]
    for rank, (sym, res) in enumerate(rows, 1):
        lines.append(format_screen_row(sym, res, rank))
    lines.append("\nUse `/fundamental <ticker>` for a full breakdown.")
    return "\n".join(lines)


def format_earnings_list(
    upcoming: list[tuple[str, str, date, int]],  # (symbol, display_name, date, days)
    days: int,
) -> str:
    if not upcoming:
        return f"No earnings in the next {days} days for your watchlisted stocks."

    lines = [f"📅 *Upcoming Earnings (next {days} days)*\n"]
    for sym, name, dt, d in upcoming:
        when = "tomorrow" if d == 1 else (f"in {d} days" if d > 1 else "today")
        lines.append(f"• *{sym}* — {name}\n  {dt.strftime('%d %b %Y')} ({when})")
    return "\n".join(lines)


# ── Private helpers ───────────────────────────────────────────────────────────

def _fmt_mcap(v: float | None) -> str:
    if v is None:
        return "N/A"
    if v >= 1e12:
        return f"${v/1e12:.2f}T"
    if v >= 1e9:
        return f"${v/1e9:.2f}B"
    if v >= 1e6:
        return f"${v/1e6:.2f}M"
    return f"${v:,.0f}"
