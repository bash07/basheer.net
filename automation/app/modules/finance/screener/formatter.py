"""Telegram message formatters for screener digests and status cards."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from .schemas import ScreenReport, ScreenedCandidate

_CONCEPT_BADGES = {
    "concept_a": "🚀 RS-Leader",
    "concept_b": "⚡ Squeeze",
    "concept_c": "🌊 Vol-Flow",
    "concept_d": "🏛️ Quality-FA",
    "concept_e": "🏆 Sector-Lead",
}


def format_screener_report_markdown(report: ScreenReport) -> str:
    """Format a ScreenReport into a Telegram markdown card."""
    lines: list[str] = []
    cadence_title = "Weekly Technical" if report.cadence == "weekly" else "Monthly Fundamental"
    icon = "📈" if report.cadence == "weekly" else "🏛️"

    lines.append(f"{icon} *{cadence_title} Screener — {report.universe}*")
    lines.append(f"📅 `{report.screened_at.strftime('%Y-%m-%d %H:%M UTC')}`")
    lines.append(f"📊 Evaluated: `{report.total_universe_size}` | Qualified: `{report.qualified_count}` | Populated: `{len(report.candidates)}`\n")

    # If weekly report with sector rankings
    if report.sector_rankings:
        lines.append("🏆 *Leading Nifty Sectors (20D Momentum):*")
        for s in report.sector_rankings[:3]:
            ret_str = f"+{s['avg_return_pct']}%" if s['avg_return_pct'] >= 0 else f"{s['avg_return_pct']}%"
            lines.append(f"  • *{s['sector']}*: `{ret_str}` (Breadth: {s['breadth_pct']}%)")
        lines.append("")

    if not report.candidates:
        lines.append("ℹ️ _No candidates met the minimum qualification threshold this cycle._")
        return "\n".join(lines)

    lines.append("🎯 *Top Screened Candidates & Assigned Strategies:*")
    for idx, c in enumerate(report.candidates, 1):
        badges = " ".join(f"`[{_CONCEPT_BADGES.get(tag, tag)}]`" for tag in c.concept_tags)
        strats = ", ".join(f"`{s}`" for s in c.strategy_types) if c.strategy_types else "All"
        lines.append(f"*{idx}. {c.display_name}* (`{c.symbol}`)")
        lines.append(f"   ₹{c.current_price:,.2f} | Score: *{c.score}* | {badges}")
        lines.append(f"   Strategies: {strats}")
        lines.append("")

    lines.append("💡 _Auto-populated to Watchlist with 7-day TTL. Downstream trading scanner will evaluate only mapped setups._")
    return "\n".join(lines)


def format_screener_status_markdown(
    weekly_entries: list,
    monthly_entries: list,
    permanent_count: int,
) -> str:
    """Format summary of active dynamic watchlist items."""
    lines: list[str] = []
    lines.append("📋 *Dynamic Screener Watchlist Status*\n")
    lines.append(f"🔒 *Permanent Core Symbols*: `{permanent_count}`")
    lines.append(f"⚡ *Weekly TA Active Candidates*: `{len(weekly_entries)}`")
    lines.append(f"🏛️ *Monthly FA Active Candidates*: `{len(monthly_entries)}`\n")

    now = datetime.now(timezone.utc)

    if weekly_entries:
        lines.append("📈 *Active Weekly TA Equities:*")
        for e in weekly_entries:
            exp_str = ""
            if e.expires_at:
                exp_at = e.expires_at if e.expires_at.tzinfo else e.expires_at.replace(tzinfo=timezone.utc)
                hours_left = max(0, int((exp_at - now).total_seconds() // 3600))
                exp_str = f"({hours_left}h left)"
            lines.append(f"  • *{e.symbol}*: `{e.strategy_types or 'All'}` {exp_str}")
        lines.append("")

    if monthly_entries:
        lines.append("🏛️ *Active Monthly FA Equities:*")
        for e in monthly_entries:
            lines.append(f"  • *{e.symbol}*: `{e.concept_tags or 'FA'}`")

    return "\n".join(lines)
