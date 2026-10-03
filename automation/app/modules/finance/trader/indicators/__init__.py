"""Indicator Registry for the finance/trader module.

All indicators are lazy-computed on demand. Strategies declare which indicators
they need via ``required_indicators``, and the engine computes only those,
resolving inter-indicator dependencies automatically.

Built-in indicators auto-register at import time from the sub-modules below.

Adding a custom indicator
-------------------------
Create or edit any .py file inside this package and use the ``register``
decorator::

    from modules.finance.trader.indicators import register

    @register("my_macd")
    def my_macd(df_ltf, df_htf, cache):
        close = df_ltf["Close"]
        ...
        return macd_value

    # With dependencies (cache["rsi_14"] already computed when my_fn runs):
    @register("my_signal", deps=["rsi_14"])
    def my_signal(df_ltf, df_htf, cache):
        rsi = cache["rsi_14"]
        ...
        return signal_value

Built-in indicator keys
-----------------------
Price:
    rsi_14          pd.Series   RSI-14 (Wilder EWM) on LTF close
    ema_8           pd.Series   EMA-8 on LTF close
    ema_21          pd.Series   EMA-21 on LTF close
    ema_50          pd.Series   EMA-50 on LTF close
    ema_200         pd.Series   EMA-200 on LTF close
    atr_14          float       ATR-14 scalar (last bar)

Structure (SMC / ICT):
    structure       SwingStructure
    bos_list        list[BOS]
    ob_list         list[OrderBlock]      (depends on bos_list)
    fvg_list        list[FVG]
    liq_list        list[LiquidityLevel]

Confluence:
    volume_profile  VolumeProfile
    sr_levels       list[SRLevel]

Bias:
    bias            BiasResult            (uses _mode from cache)

Private cache keys (set by compute_indicators caller):
    _mode           str     "swing" | "intraday"
    _category       str     watchlist category
"""
from __future__ import annotations

import importlib
import logging
from typing import Callable

logger = logging.getLogger(__name__)

# Registry: name -> (fn, deps)
# fn signature: fn(df_ltf, df_htf, cache: dict) -> value
_REGISTRY: dict[str, tuple[Callable, list[str]]] = {}


# ── Public decorator ──────────────────────────────────────────────────────────

def register(name: str, deps: list[str] | None = None):
    """Decorator to register an indicator computation function.

    Args:
        name: Unique indicator key (used in ``required_indicators``).
        deps: Other indicator keys that must be computed first.
              Their values will be in ``cache`` when your function runs.

    Example::

        @register("bos_list", deps=["structure"])
        def _bos(df_ltf, df_htf, cache):
            return detect_bos_choch(df_ltf, cache["structure"])
    """
    def decorator(fn: Callable) -> Callable:
        _REGISTRY[name] = (fn, list(deps) if deps else [])
        return fn
    return decorator


# ── Public compute function ───────────────────────────────────────────────────

def compute_indicators(
    needed: list[str],
    df_ltf,
    df_htf,
    initial_cache: dict | None = None,
) -> dict:
    """Compute a set of indicators, resolving dependencies automatically.

    Args:
        needed:        List of indicator keys to compute.
        df_ltf:        LTF OHLCV pd.DataFrame.
        df_htf:        HTF OHLCV pd.DataFrame.
        initial_cache: Seed values. Use private keys like ``_mode`` and
                       ``_category`` to pass context that indicators need.
                       Example: ``{"_mode": "swing", "_category": "us_stock"}``

    Returns:
        dict mapping indicator key -> computed value.
        Failed or unknown indicators map to ``None``.
    """
    cache: dict = dict(initial_cache) if initial_cache else {}

    def _compute_one(name: str) -> None:
        if name in cache:
            return                      # already computed (or seeded)
        entry = _REGISTRY.get(name)
        if entry is None:
            logger.warning("[trader.indicators] Unknown indicator: '%s'", name)
            cache[name] = None
            return
        fn, deps = entry
        for dep in deps:               # depth-first dependency resolution
            _compute_one(dep)
        try:
            cache[name] = fn(df_ltf, df_htf, cache)
        except Exception as exc:
            logger.warning(
                "[trader.indicators] Failed to compute '%s': %s", name, exc
            )
            cache[name] = None

    for key in needed:
        _compute_one(key)

    return cache


# ── Helper for callers ────────────────────────────────────────────────────────

def list_indicators() -> list[str]:
    """Return all registered indicator names sorted alphabetically."""
    return sorted(_REGISTRY.keys())


# ── Auto-load built-in indicator modules ──────────────────────────────────────
# Order does not matter — dependencies are resolved lazily.

def _load_builtins() -> None:
    _modules = ["rsi", "ema", "atr", "structure", "volume_profile", "bias_indicator"]
    for mod_name in _modules:
        try:
            importlib.import_module(f".{mod_name}", package=__name__)
        except Exception as exc:
            logger.warning(
                "[trader.indicators] Could not load built-in '%s': %s",
                mod_name, exc,
            )


_load_builtins()
