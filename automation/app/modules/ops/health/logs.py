"""Log inspector and reader for bot.log.

Provides utilities to:
- Configure or locate bot.log file in data directory
- Read last N lines safely (capped to prevent Telegram overflow)
- Filter lines for warnings and errors
"""
import logging
import os
from typing import Optional

from config import config

logger = logging.getLogger(__name__)

DEFAULT_LOG_LINES = 25
MAX_LOG_LINES = 100
DEFAULT_ERROR_LINES = 20
MAX_TELEGRAM_MESSAGE_CHARS = 4000


def get_log_file_path() -> str:
    """Return the absolute path to bot.log."""
    data_dir = getattr(config, "data_dir", "/app/data")
    if os.path.isdir(data_dir):
        return os.path.join(data_dir, "bot.log")
    fallback_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
    os.makedirs(fallback_dir, exist_ok=True)
    return os.path.join(fallback_dir, "bot.log")


def ensure_file_logging() -> None:
    """Attach a RotatingFileHandler to the root logger if not already attached."""
    from logging.handlers import RotatingFileHandler

    log_file = get_log_file_path()
    log_dir = os.path.dirname(log_file)
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass

    root = logging.getLogger()
    # Check if a file handler targeting log_file is already present
    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler):
            try:
                if os.path.abspath(handler.baseFilename) == os.path.abspath(log_file):
                    return
            except Exception:
                pass

    try:
        # 10 MB per file, keep 3 backups
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        logger.info("Attached RotatingFileHandler to %s", log_file)
    except Exception as e:
        logger.warning("Failed to attach RotatingFileHandler to %s: %s", log_file, e)


def read_last_n_lines(n: int = DEFAULT_LOG_LINES, log_path: Optional[str] = None) -> list[str]:
    """Read the last n lines of the log file using reverse seek or tailing.

    Capped at MAX_LOG_LINES.
    """
    path = log_path or get_log_file_path()
    if not os.path.exists(path):
        return []

    n = max(1, min(n, MAX_LOG_LINES))
    lines: list[str] = []

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            # For small to medium files, readlines is fast.
            # If large, reading chunks from the end is efficient.
            f.seek(0, os.SEEK_END)
            file_size = f.tell()

            buffer_size = 8192
            lines_found = 0
            block_end = file_size
            data = ""

            while block_end > 0 and lines_found <= n:
                block_start = max(0, block_end - buffer_size)
                f.seek(block_start)
                chunk = f.read(block_end - block_start)
                data = chunk + data
                lines_found = data.count("\n")
                block_end = block_start

            all_lines = data.splitlines()
            lines = all_lines[-n:]
    except Exception as e:
        logger.warning("Error reading log file %s: %s", path, e)
        return [f"[Error reading log file: {e}]"]

    return lines


def read_recent_errors(limit: int = DEFAULT_ERROR_LINES, log_path: Optional[str] = None) -> list[str]:
    """Read recent WARNING and ERROR log records up to `limit`."""
    path = log_path or get_log_file_path()
    if not os.path.exists(path):
        return []

    limit = max(1, min(limit, MAX_LOG_LINES))
    # Scan from end
    recent_lines = read_last_n_lines(n=500, log_path=path)
    error_keywords = ["[ERROR]", "[WARNING]", "[CRITICAL]", "ERROR", "WARNING", "CRITICAL"]

    matched = []
    for line in recent_lines:
        if any(kw in line for kw in error_keywords):
            matched.append(line)

    return matched[-limit:]


def format_logs_for_telegram(lines: list[str], title: str = "Logs") -> str:
    """Format log lines inside a Telegram code block, truncating if over character limit."""
    if not lines:
        return f"📜 *{title}*\n\n_No log entries found._"

    joined = "\n".join(lines)
    # Ensure fits inside Telegram message length (limit is ~4096)
    max_payload = 3800
    if len(joined) > max_payload:
        joined = joined[-max_payload:]
        # Align to newline
        if "\n" in joined:
            joined = "..." + joined[joined.index("\n"):]

    return f"📜 *{title}* ({len(lines)} lines):\n```\n{joined}\n```"
