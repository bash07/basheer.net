"""Indicator Registry for the finance/trader module.

All indicators are lazy-computed on demand. Strategies declare which indicators
they need via ``required_indicators``, and the engine computes only those,
resolving inter-indicator dependencies automatically.

Built-in indicators auto-register at import time from the sub-modules below.
Supports dynamic parameterized indicators (e.g. ``ema_9``, ``sma_100``, ``rsi_21``, ``atr_20``).

Adding a custom indicator
-------------------------
Create or edit any .py file inside this package and use the ``register``
decorator::

    from modules.finance.trader.indicators import register

    @register("my_indicator")
    def my_indicator(df_ltf, df_htf, cache):
        ...
        return value

Built-in indicator keys
-----------------------
Price / Moving Averages:
    rsi_14, rsi_<N>         pd.Series   RSI (Wilder EWM) on LTF close
    ema_8, ema_21, ema_<N>  pd.Series   Exponential Moving Average
    sma_20, sma_<N>         pd.Series   Simple Moving Average
    atr_14, atr_<N>         float       Average True Range (last bar)
    macd, macd_signal, macd_hist pd.Series MACD (12, 26, 9)
    bb_upper, bb_lower, bb_mid, bb_width, bb_pct_b pd.Series Bollinger Bands
    stoch_k, stoch_d        pd.Series   Stochastic Oscillator
    adx, di_plus, di_minus  float/Series ADX & DMI Trend Strength

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
    bias            BiasResult (uses strategy preference or default)
    bias_rsi        BiasResult (RSI regime + hidden divergence)
    bias_macd       BiasResult (MACD trend & momentum)
    bias_ema        BiasResult (EMA 20/50/200 stack alignment)
    bias_composite  BiasResult (Consensus across multiple methods)
"""
from __future__ import annotations

import importlib
import logging
import re
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
    """
    def decorator(fn: Callable) -> Callable:
        _REGISTRY[name] = (fn, list(deps) if deps else [])
        return fn
    return decorator


# ── Dynamic Parameterized Indicator Resolution ───────────────────────────────

def _resolve_dynamic(name: str) -> bool:
    """Dynamically construct and register indicators matching patterns like ema_N, sma_N, rsi_N, atr_N."""
    # EMA: ema_<int>
    m = re.match(r"^ema_(\d+)$", name)
    if m:
        span = int(m.group(1))

        @register(name)
        def _dyn_ema(df_ltf, df_htf, cache, _span=span):
            if name in df_ltf.columns:
                return df_ltf[name]
            return df_ltf["Close"].ewm(span=_span, adjust=False).mean()

        return True

    # SMA: sma_<int>
    m = re.match(r"^sma_(\d+)$", name)
    if m:
        window = int(m.group(1))

        @register(name)
        def _dyn_sma(df_ltf, df_htf, cache, _w=window):
            if name in df_ltf.columns:
                return df_ltf[name]
            return df_ltf["Close"].rolling(_w).mean()

        return True

    # RSI: rsi_<int>
    m = re.match(r"^rsi_(\d+)$", name)
    if m:
        period = int(m.group(1))

        @register(name)
        def _dyn_rsi(df_ltf, df_htf, cache, _p=period):
            if name in df_ltf.columns:
                return df_ltf[name]
            from .rsi import calc_rsi
            return calc_rsi(df_ltf["Close"], _p)

        return True

    # ATR: atr_<int>
    m = re.match(r"^atr_(\d+)$", name)
    if m:
        period = int(m.group(1))

        @register(name)
        def _dyn_atr(df_ltf, df_htf, cache, _p=period) -> float:
            if name in df_ltf.columns:
                val = df_ltf[name].iloc[-1]
                if val == val:
                    return float(val)
            from .atr import calc_atr
            return calc_atr(df_ltf, _p)

        return True

    return False


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

    Returns:
        dict mapping indicator key -> computed value.
        Failed or unknown indicators map to ``None``.
    """
    cache: dict = dict(initial_cache) if initial_cache else {}
    computing: set[str] = set()   # cycle detection

    def _compute_one(name: str) -> None:
        if name in cache:
            return                      # already computed (or seeded)
        if name in computing:
            raise RuntimeError(
                f"[trader.indicators] Circular dependency detected for '{name}'. "
                f"Currently computing: {computing}"
            )
        entry = _REGISTRY.get(name)
        if entry is None:
            # Check dynamic resolvers for parameterized keys (ema_9, rsi_7, etc.)
            if _resolve_dynamic(name):
                entry = _REGISTRY.get(name)

        if entry is None:
            logger.warning("[trader.indicators] Unknown indicator: '%s'", name)
            cache[name] = None
            return

        fn, deps = entry
        computing.add(name)
        try:
            for dep in deps:               # depth-first dependency resolution
                _compute_one(dep)
            try:
                cache[name] = fn(df_ltf, df_htf, cache)
            except Exception as exc:
                logger.warning(
                    "[trader.indicators] Failed to compute '%s': %s", name, exc
                )
                cache[name] = None
        finally:
            computing.discard(name)

    for key in needed:
        _compute_one(key)

    return cache


# ── Helper for callers ────────────────────────────────────────────────────────

def list_indicators() -> list[str]:
    """Return all currently registered indicator names sorted alphabetically.

    Note:
        Dynamic parameterized indicators (e.g. ``ema_9``, ``sma_100``, ``rsi_21``,
        ``atr_20``) are constructed and added to the registry on-demand when requested
        by a strategy's ``required_indicators``.
    """
    return sorted(_REGISTRY.keys())


# ── Auto-load built-in indicator modules ──────────────────────────────────────

def _load_builtins() -> None:
    _modules = [
        "rsi", "ema", "atr", "structure", "volume_profile", "bias_indicator",
        "macd", "adx", "bollinger", "stochastic", "sma",
    ]
    for mod_name in _modules:
        try:
            importlib.import_module(f".{mod_name}", package=__name__)
        except Exception as exc:
            logger.warning(
                "[trader.indicators] Could not load built-in '%s': %s",
                mod_name, exc,
            )


_load_builtins()
