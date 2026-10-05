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
    sector:           str | None = None
    industry:         str | None = None
    market_cap:       float | None = None   # USD
    # Valuation
    pe_ratio:         float | None = None   # Trailing P/E
    pb_ratio:         float | None = None   # Price-to-Book
    # Financial health
    current_ratio:    float | None = None   # Current Assets / Current Liabilities
    debt_to_equity:   float | None = None   # D/E (decimal; yfinance reports %, we divide /100)
    # Income quality
    dividend_yield:   float | None = None   # decimal (0.015 = 1.5%)
    eps_growth:       float | None = None   # TTM EPS growth (decimal)
    revenue_growth:   float | None = None   # TTM revenue growth (decimal)
    roe:              float | None = None   # Return on Equity
    profit_margin:    float | None = None   # Net profit margin
    # Calendar
    next_earnings_date: date | None = None
    # Quarterly margins trend (trailing 4Q)
    margin_trend_warning: str | None = None
    quarterly_margins: list[dict] | None = None


def _compute_margin_trends(ticker) -> tuple[str | None, list[dict]]:
    """Compute operating and gross margin trends across trailing quarters."""
    try:
        q_financials = getattr(ticker, "quarterly_financials", None)
        if q_financials is None or q_financials.empty:
            q_financials = getattr(ticker, "quarterly_income_stmt", None)
        if q_financials is None or q_financials.empty:
            return None, []

        cols = list(q_financials.columns)[:4]  # Up to 4 quarters
        margins = []
        for col in reversed(cols):  # Oldest to newest
            rev = None
            for rk in ("Total Revenue", "Operating Revenue", "Revenue"):
                if rk in q_financials.index:
                    rev = q_financials.loc[rk, col]
                    break

            op_inc = None
            for ok in ("Operating Income", "Net Income from Continuing Operation Net Minority Interest", "Net Income"):
                if ok in q_financials.index:
                    op_inc = q_financials.loc[ok, col]
                    break

            gross = q_financials.loc["Gross Profit", col] if "Gross Profit" in q_financials.index else None

            op_margin = (op_inc / rev) if (rev and op_inc is not None and rev > 0) else None
            gross_margin = (gross / rev) if (rev and gross is not None and rev > 0) else None

            date_str = str(col.date()) if hasattr(col, "date") else str(col)
            margins.append({
                "date": date_str,
                "operating_margin": round(op_margin, 4) if op_margin is not None else None,
                "gross_margin": round(gross_margin, 4) if gross_margin is not None else None,
            })

        # Detect consecutive decline
        op_vals = [m["operating_margin"] for m in margins if m["operating_margin"] is not None]
        if len(op_vals) >= 3 and op_vals[-1] < op_vals[-2] < op_vals[-3]:
            warning = f"Margin Compression Alert: Operating margin has declined for 3 consecutive quarters ({op_vals[-3]:.1%} → {op_vals[-2]:.1%} → {op_vals[-1]:.1%})."
            return warning, margins

        return None, margins
    except Exception:
        return None, []


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

    warning, q_margins = _compute_margin_trends(ticker)

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
        margin_trend_warning=warning,
        quarterly_margins=q_margins,
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
