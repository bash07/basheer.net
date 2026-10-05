"""Markdown formatter for system health and resource metrics.

Formats system snapshots into clean, readable cards for Telegram messages.
"""
from typing import Any


def _format_bytes(num_bytes: int) -> str:
    """Format bytes into human-friendly string (B, KB, MB, GB)."""
    if num_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    val = float(num_bytes)
    idx = 0
    while val >= 1024.0 and idx < len(units) - 1:
        val /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(val)} B"
    return f"{val:.1f} {units[idx]}"


def _format_uptime(seconds: float) -> str:
    """Format seconds into human-readable uptime (e.g., 4d 12h 31m or 2h 15m)."""
    secs = int(max(0.0, seconds))
    days, remainder = divmod(secs, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def format_system_health_card(snapshot: dict[str, Any]) -> str:
    """Format the full system snapshot into a Telegram Markdown message.

    Example layout:
    🖥️ *System Health — e2-micro (1 GB)*
    ━━━━━━━━━━━━━━━━━━━━
    🧠 *Memory (RAM):*
      • Used: 412.0 MB / 980.0 MB (42.0%)
      • Available: 568.0 MB 🟢
      • Bot Process RSS: 142.0 MB

    ⚡ *CPU & Load:*
      • Load Avg: 0.18, 0.22, 0.15
      • Process Threads: 8

    💾 *Disk & Storage (/app/data):*
      • Free: 14.2 GB / 20.0 GB (71.0% free) 🟢
      • automation.db: 4.8 MB (WAL: 128.0 KB)
      • cache.db: 12.1 MB

    🧵 *Engine Vitality:*
      • Python Threads: 8
      • Bot Uptime: 4d 12h 31m
    """
    mem = snapshot.get("memory", {})
    cpu = snapshot.get("cpu", {})
    proc = snapshot.get("process", {})
    storage = snapshot.get("storage", {})

    # Memory status indicator
    avail_mb = mem.get("avail_mb", 0.0)
    if avail_mb < 150.0:
        mem_status = "🔴"
    elif avail_mb < 300.0:
        mem_status = "🟡"
    else:
        mem_status = "🟢"

    # Disk status indicator
    used_pct = storage.get("used_pct", 0.0)
    free_gb = storage.get("free_gb", 0.0)
    if used_pct >= 85.0 or free_gb < 1.5:
        disk_status = "🔴"
    elif used_pct >= 75.0 or free_gb < 3.0:
        disk_status = "🟡"
    else:
        disk_status = "🟢"

    lines = [
        "🖥️ *System Health — e2-micro (1 GB)*",
        "━━━━━━━━━━━━━━━━━━━━",
        "🧠 *Memory (RAM):*",
        f"  • Used: {mem.get('used_mb', 0.0):.1f} MB / {mem.get('total_mb', 0.0):.1f} MB ({mem.get('used_pct', 0.0):.1f}%)",
        f"  • Available: {avail_mb:.1f} MB {mem_status}",
    ]

    # Container cgroup memory if running under Docker with limits
    if mem.get("cgroup_limit_mb"):
        lines.append(f"  • Container Limit: {mem.get('cgroup_usage_mb', 0.0):.1f} MB / {mem.get('cgroup_limit_mb', 0.0):.1f} MB")

    rss_mb = proc.get("rss_mb", 0.0)
    if rss_mb > 0.0:
        lines.append(f"  • Bot Process RSS: {rss_mb:.1f} MB")

    # CPU & Load
    lines.append("")
    lines.append("⚡ *CPU & Load:*")
    l1, l5, l15 = cpu.get("load_1m"), cpu.get("load_5m"), cpu.get("load_15m")
    if l1 is not None and l5 is not None and l15 is not None:
        lines.append(f"  • Load Avg: {l1:.2f}, {l5:.2f}, {l15:.2f}")
    else:
        lines.append("  • Load Avg: N/A (non-POSIX / host limited)")

    # Disk & Storage
    lines.append("")
    lines.append(f"💾 *Disk & Storage ({storage.get('mount_path', '/app/data')}):*")
    lines.append(f"  • Free: {storage.get('free_gb', 0.0):.1f} GB / {storage.get('total_gb', 0.0):.1f} GB ({storage.get('free_pct', 0.0):.1f}% free) {disk_status}")
    lines.append(f"  • automation.db: {_format_bytes(storage.get('automation_db_bytes', 0))} (WAL: {_format_bytes(storage.get('automation_wal_bytes', 0))})")
    lines.append(f"  • cache.db: {_format_bytes(storage.get('cache_db_bytes', 0))}")

    # Engine vitality
    lines.append("")
    lines.append("🧵 *Engine Vitality:*")
    lines.append(f"  • Python Threads: {proc.get('thread_count', 1)}")
    lines.append(f"  • Bot Uptime: {_format_uptime(proc.get('uptime_seconds', 0.0))}")

    return "\n".join(lines)


def format_health_alert(alert_type: str, details: str, snapshot: dict[str, Any]) -> str:
    """Format proactive critical/warning alert message."""
    mem = snapshot.get("memory", {})
    storage = snapshot.get("storage", {})

    header = "🚨 *CRITICAL SYSTEM ALERT*" if alert_type.upper() == "CRITICAL" else "⚠️ *SYSTEM WARNING*"

    lines = [
        header,
        f"*{details}*",
        "",
        "📊 *Snapshot:*",
        f"• RAM Available: {mem.get('avail_mb', 0.0):.1f} MB / {mem.get('total_mb', 0.0):.1f} MB",
        f"• Disk Usage: {storage.get('used_pct', 0.0):.1f}% (Free: {storage.get('free_gb', 0.0):.1f} GB)",
        f"• SQLite DBs: automation.db ({_format_bytes(storage.get('automation_db_bytes', 0))}), WAL ({_format_bytes(storage.get('automation_wal_bytes', 0))}), cache.db ({_format_bytes(storage.get('cache_db_bytes', 0))})",
        "",
        "Use `/sys` or `/logs errors` for diagnostics.",
    ]
    return "\n".join(lines)
