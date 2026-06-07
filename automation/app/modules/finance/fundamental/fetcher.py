"""Fundamentals data fetcher — wraps yfinance Ticker.info.

All imports are lazy. Results are cached for 24 h (fundamentals_cache).
Only EQUITY quote types are supported; crypto/forex/indices raise ValueError.
"""
from dataclasses import dataclass
from datetime import date
import logging

logger = logging.getLogger(__name__)

# Categories that carry meaningful fundamental data
FUNDAMENTAL_CATEGORIES = frozenset({"us_stock", "indian_stock"})


@dataclass
class FundamentalsData:
    symbol:           str
    display_name:     str
    sector:           str | None
    industry:         str | None
    market_cap:       float | None   # USD
    # Valuation
    pe_ratio:         float | None   # Trailing P/E
    pb_ratio:         float | None   # Price-to-Book
    # Financial health
    current_ratio:    float | None   # Current Assets / Current Liabilities
    debt_to_equity:   float | None   # D/E (decimal; yfinance reports %, we divide /100)
    # Income quality
    dividend_yield:   float | None   # decimal (0.015 = 1.5%)
    eps_growth:       float | None   # TTM EPS growth (decimal)
    revenue_growth:   float | None   # TTM revenue growth (decimal)
    roe:              float | None   # Return on Equity
    profit_margin:    float | None   # Net profit margin
    # Calendar
    next_earnings_date: date | None


def fetch_fundamentals(symbol: str) -> FundamentalsData:
    """Fetch and cache fundamentals for a stock ticker (blocking — run in executor).

    Raises:
        ValueError: if the ticker is not an equity or data is unavailable.
    """
    from modules.finance._shared.cache import fundamentals_cache

    key = f"fundamentals:{symbol}"
    cached = fundamentals_cache.get(key)
    if cached:
        return cached

    import yfinance as yf  # lazy

    ticker = yf.Ticker(symbol)
    try:
        info = ticker.info
    except Exception as e:
        raise ValueError(f"Could not fetch data for '{symbol}': {e}")

    if not info:
        raise ValueError(f"No data returned for '{symbol}'.")

    quote_type = info.get("quoteType", "")
    if quote_type not in ("EQUITY", "ETF"):
        raise ValueError(
            f"'{symbol}' is a {quote_type or 'unknown'} instrument. "
            "Fundamental analysis only works for stocks and ETFs."
        )

    # D/E: yfinance returns it as a percentage (50.0 means 0.50 D/E)
    raw_de = info.get("debtToEquity")
    debt_to_equity = (raw_de / 100.0) if raw_de is not None else None

    result = FundamentalsData(
        symbol=symbol,
        display_name=info.get("longName") or info.get("shortName") or symbol,
        sector=info.get("sector"),
        industry=info.get("industry"),
        market_cap=info.get("marketCap"),
        pe_ratio=info.get("trailingPE"),
        pb_ratio=info.get("priceToBook"),
        current_ratio=info.get("currentRatio"),
        debt_to_equity=debt_to_equity,
        dividend_yield=info.get("dividendYield"),
        eps_growth=info.get("earningsGrowth"),
        revenue_growth=info.get("revenueGrowth"),
        roe=info.get("returnOnEquity"),
        profit_margin=info.get("profitMargins"),
        next_earnings_date=_earnings_date(ticker),
    )
    fundamentals_cache.set(key, result)
    return result


def _earnings_date(ticker) -> date | None:
    """Extract next earnings date from yfinance calendar (format varies by version)."""
    try:
        cal = ticker.calendar
        if cal is None:
            return None
        # New yfinance: calendar is a dict {"Earnings Date": [Timestamp, ...], ...}
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date", [])
            if dates:
                v = dates[0]
                return v.date() if hasattr(v, "date") else None
        # Old yfinance: calendar is a DataFrame
        if hasattr(cal, "columns") and "Earnings Date" in cal.columns:
            dates = cal["Earnings Date"].dropna()
            if len(dates):
                return dates.iloc[0].date()
        if hasattr(cal, "index") and "Earnings Date" in cal.index:
            row = cal.loc["Earnings Date"].dropna()
            if len(row):
                v = row.iloc[0]
                return v.date() if hasattr(v, "date") else None
    except Exception:
        pass
    return None
