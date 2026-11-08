"""Price fetching helpers shared between /price, /prices, and jobs.

All yfinance/pandas imports are lazy to keep main-process memory low.
"""
import logging
logger = logging.getLogger(__name__)

# Exported so jobs.py and the daily summary can use without circular import
_CATEGORY_LABELS_LOCAL: dict[str, str] = {
    "us_index":     "🇺🇸 US Indices",
    "us_stock":     "🇺🇸 US Stocks",
    "indian_index": "🇮🇳 Indian Indices",
    "indian_stock": "🇮🇳 Indian Stocks",
    "crypto":       "₿ Crypto",
    "forex":        "💱 Forex",
    "commodity":    "🪙 Commodities",
}


def fetch_price_detail(symbol: str) -> dict:
    """Fetch price + 24h change + day/52W ranges for a single ticker (blocking).

    Returns:
        {symbol, price, change, change_pct, day_high, day_low, week52_high, week52_low}
    Raises:
        ValueError: if the ticker has no price data.
    """
    from modules.finance._shared.cache import ticker_cache
    key = f"price_detail:{symbol}"
    cached = ticker_cache.get(key)
    if cached:
        return cached

    import yfinance as yf  # lazy
    fi = yf.Ticker(symbol).fast_info

    price = fi.last_price
    if price is None:
        raise ValueError(f"No price data for '{symbol}'. Check the symbol.")

    prev = fi.previous_close or price
    change = price - prev
    pct = (change / prev * 100) if prev else 0.0

    result = {
        "symbol":      symbol,
        "price":       float(price),
        "change":      float(change),
        "change_pct":  float(pct),
        "day_high":    _safe(fi, "day_high"),
        "day_low":     _safe(fi, "day_low"),
        "week52_high": _safe(fi, "fifty_two_week_high"),
        "week52_low":  _safe(fi, "fifty_two_week_low"),
    }
    ticker_cache.set(key, result)
    return result


def fetch_prices_batch(symbols: list[str]) -> dict[str, dict | None]:
    """Batch price fetch for multiple tickers (blocking, for executor use).

    Returns dict symbol → price_detail dict (or None on error).
    Checks the 5-min cache before fetching, so repeated calls are cheap.
    """
    from modules.finance._shared.cache import ticker_cache
    results: dict[str, dict | None] = {}
    to_fetch: list[str] = []

    for sym in symbols:
        cached = ticker_cache.get(f"price_detail:{sym}")
        if cached is not None:
            results[sym] = cached
        else:
            to_fetch.append(sym)

    if not to_fetch:
        return results

    import yfinance as yf  # lazy
    # yf.Tickers fetches metadata in batch
    tickers = yf.Tickers(" ".join(to_fetch))
    for sym in to_fetch:
        try:
            fi = tickers.tickers[sym].fast_info
            price = fi.last_price
            if price is None:
                results[sym] = None
                continue
            prev = fi.previous_close or price
            change = float(price) - float(prev)
            pct = (change / float(prev) * 100) if prev else 0.0
            info = {
                "symbol":      sym,
                "price":       float(price),
                "change":      change,
                "change_pct":  pct,
                "day_high":    _safe(fi, "day_high"),
                "day_low":     _safe(fi, "day_low"),
                "week52_high": _safe(fi, "fifty_two_week_high"),
                "week52_low":  _safe(fi, "fifty_two_week_low"),
            }
            results[sym] = info
            ticker_cache.set(f"price_detail:{sym}", info)
        except Exception as exc:
            logger.warning("[watchlist.prices] %s: %s", sym, exc)
            results[sym] = None

    return results


def render_range_bar(current: float, low: float | None, high: float | None, length: int = 10) -> str:
    """Render an ASCII position gauge for price within 52W range."""
    if low is None or high is None or high <= low or current < 0:
        return ""
    pos = (current - low) / (high - low)
    pos = max(0.0, min(1.0, pos))
    idx = int(round(pos * (length - 1)))
    bar = ["─"] * length
    bar[idx] = "●"
    pct = pos * 100
    return f"{low:,.2f} {''.join(bar)} {high:,.2f} ({pct:.0f}%)"


def format_price_card(info: dict) -> str:
    """Format a price_detail dict as a Telegram Markdown message."""
    pct = info["change_pct"]
    arrow = "📈" if pct >= 0 else "📉"
    sign = "+" if pct >= 0 else ""
    lines = [
        f"{arrow} *{info['symbol']}*",
        f"💵 `{info['price']:,.4f}` ({sign}{pct:.2f}%)",
    ]
    if info.get("day_high") and info.get("day_low"):
        lines.append(f"📊 Day  `{info['day_low']:,.4f}` — `{info['day_high']:,.4f}`")
    if info.get("week52_high") and info.get("week52_low"):
        gauge = render_range_bar(info["price"], info["week52_low"], info["week52_high"])
        if gauge:
            lines.append(f"📅 52W  `{gauge}`")
        else:
            lines.append(f"📅 52W  `{info['week52_low']:,.4f}` — `{info['week52_high']:,.4f}`")
    return "\n".join(lines)


def format_price_row(sym: str, info: dict | None) -> str:
    """One-liner for the /prices batch list."""
    if info is None:
        return f"⚪ `{sym}` — N/A"
    pct = info["change_pct"]
    arrow = "📈" if pct >= 0 else "📉"
    sign = "+" if pct >= 0 else ""
    return f"{arrow} `{sym}` `{info['price']:,.2f}` ({sign}{pct:.2f}%)"


def _safe(fi, attr: str):
    try:
        v = getattr(fi, attr, None)
        return float(v) if v is not None else None
    except Exception:
        return None
