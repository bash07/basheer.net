"""finance/_shared — internal utilities shared across all finance submodules.

The underscore prefix prevents the registry from treating this as a submodule
(it has no register() and won't be scanned as one regardless — but the prefix
makes the intent explicit to any developer reading the directory listing).

Contents:
  cache.py          — TTL in-memory cache (yfinance + CoinGecko responses)
  market_hours.py   — is_market_open(category) helper
  chart_renderer.py — subprocess-based matplotlib chart rendering
"""
