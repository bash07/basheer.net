"""Market hours helper for the finance domain.

Used by the 30-min TA signal scanner (finance/technical/jobs.py) to skip
tickers whose market is currently closed — avoiding unnecessary API calls
and noise from stale/flat price data outside trading hours.

All times are in IST (Asia/Kolkata) to match the platform's GENERIC_TIMEZONE.

Usage:
    from modules.finance._shared.market_hours import is_market_open

    if is_market_open("us_stock"):
        run_ta_scan(ticker)
"""
import zoneinfo
from datetime import datetime

_IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# Market hours in IST — (open_hour, open_min, close_hour, close_min)
# Overnight sessions (open > close in minutes) are handled correctly.
_HOURS: dict[str, tuple[int, int, int, int]] = {
    "us_index":     (19,  0,  1, 30),   # NYSE/Nasdaq: 9:30 AM – 4 PM ET = 7 PM – 1:30 AM IST
    "us_stock":     (19,  0,  1, 30),
    "indian_index": ( 9, 15, 15, 30),   # NSE: 9:15 AM – 3:30 PM IST
    "indian_stock": ( 9, 15, 15, 30),
    "crypto":       ( 0,  0, 23, 59),   # 24/7 — always open
    "forex":        ( 6, 30, 23,  0),   # Approximate — FX is 24/5
    "commodity":    ( 9,  0, 23, 30),   # Approximate (COMEX/MCX overlap)
}

# Markets that are closed on weekends (Saturday = 5, Sunday = 6)
_WEEKDAY_ONLY: frozenset[str] = frozenset(
    {"us_index", "us_stock", "indian_index", "indian_stock"}
)


def is_market_open(category: str) -> bool:
    """
    Return True if the market for the given ticker category is currently open.

    Args:
        category: One of us_index, us_stock, indian_index, indian_stock,
                  crypto, forex, commodity.

    Returns:
        True if the market is currently in session.
        For unknown categories, returns True (fail-open — better to over-check
        than silently skip tickers).
    """
    if category == "crypto":
        return True

    now = datetime.now(tz=_IST)

    # Weekend check
    if category in _WEEKDAY_ONLY and now.weekday() >= 5:
        return False

    hours = _HOURS.get(category)
    if hours is None:
        return True  # unknown category — fail-open

    open_h, open_m, close_h, close_m = hours
    open_mins  = open_h  * 60 + open_m
    close_mins = close_h * 60 + close_m
    now_mins   = now.hour * 60 + now.minute

    if open_mins > close_mins:
        # Overnight session — wraps past midnight (e.g. US: 7 PM – 1:30 AM)
        return now_mins >= open_mins or now_mins <= close_mins
    else:
        return open_mins <= now_mins <= close_mins


def open_categories() -> list[str]:
    """Return the list of categories whose markets are currently open."""
    return [cat for cat in _HOURS if is_market_open(cat)]
