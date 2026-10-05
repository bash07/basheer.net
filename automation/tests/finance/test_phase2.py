"""Tests for Phase 2 enhancements:
1. Unified indicators in _shared/indicators (EMA, SMA, RSI, ATR, Bollinger, MACD, Stochastic, ADX, Volume Profile).
2. Backward compatibility of trader indicators and technical indicators delegating to _shared.
3. Volume profile generation and inclusion in TAResult.
4. Shared OHLCV cache key interoperability between technical and trader.
"""
import numpy as np
import pandas as pd
import pytest

from modules.finance._shared.indicators import (
    calc_ema,
    calc_sma,
    calc_rsi,
    calc_atr,
    calc_atr_series,
    calc_bollinger,
    calc_macd,
    calc_stochastic,
    calc_adx,
    calc_volume_profile,
)
from modules.finance.technical.indicators import compute_ta
from modules.finance._shared.cache import ticker_cache
from modules.finance.trader.data import YFinanceProvider


def test_unified_indicators_computation(sample_ohlcv):
    """Verify all unified indicators compute successfully on standard OHLCV."""
    close = sample_ohlcv["Close"]

    ema = calc_ema(close, 21)
    assert len(ema) == len(close)
    assert not ema.isna().all()

    sma = calc_sma(close, 20)
    assert len(sma) == len(close)

    rsi = calc_rsi(close, 14)
    assert len(rsi) == len(close)
    assert 0 <= rsi.dropna().iloc[-1] <= 100

    atr_val = calc_atr(sample_ohlcv, 14)
    assert atr_val > 0

    bb = calc_bollinger(close, 20)
    assert (bb.upper.dropna() >= bb.lower.dropna()).all()

    macd = calc_macd(close)
    assert len(macd.macd) == len(close)
    assert len(macd.signal) == len(close)

    stoch = calc_stochastic(sample_ohlcv)
    assert len(stoch.k) == len(close)

    adx = calc_adx(sample_ohlcv)
    assert len(adx.adx) == len(close)

    vp = calc_volume_profile(sample_ohlcv, lookback=50)
    assert vp.poc > 0
    assert vp.vah >= vp.poc >= vp.val


def test_ta_result_contains_volume_profile(sample_ohlcv):
    """Verify compute_ta includes volume profile summary in TAResult."""
    result = compute_ta(sample_ohlcv, symbol="AAPL", timeframe="1d")
    assert result.volume_profile is not None
    assert "poc" in result.volume_profile
    assert "vah" in result.volume_profile
    assert "val" in result.volume_profile
    assert result.volume_profile["vah"] >= result.volume_profile["poc"] >= result.volume_profile["val"]


def test_shared_ohlcv_cache_interoperability(sample_ohlcv):
    """Verify YFinanceProvider in trader/data can pick up OHLCV cached by technical."""
    sym = "TEST_INTEROP"
    cache_key = f"ohlcv:{sym}:6mo:1d"
    ticker_cache.set(cache_key, sample_ohlcv)

    provider = YFinanceProvider()
    retrieved = provider.fetch_ohlcv(sym, interval="1d", period="6mo")
    assert retrieved is not None
    assert len(retrieved) == len(sample_ohlcv)
    ticker_cache.delete(cache_key)
