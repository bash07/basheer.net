"""HTF RSI Bias Engine for the finance/trader module.

Determines directional bias from the Higher Timeframe (Weekly for swing,
Daily for intraday/crypto) using three RSI-derived signals:

  1. RSI Regime    — Is RSI consistently above or below 50? (Cardwell zones)
  2. Hidden Div    — Price HL + RSI LL (bull) / Price LH + RSI HH (bear)
                     Confirms trend CONTINUATION (opposite of regular divergence)
  3. Failure Swing — v1.1; only used for reversal setups

All pandas/numpy/scipy imports are LAZY — never at module level.

Usage:
    result = compute_htf_bias(df_weekly, mode="swing")

    # Use a custom bias provider:
    from modules.finance.trader.bias import register_bias_provider, MACDBiasProvider
    register_bias_provider("macd", MACDBiasProvider())
    result = compute_htf_bias(df_daily, mode="intraday", provider="macd")
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from .schemas import BiasResult, timeframes_for

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────

_RSI_PERIOD        = 14
_REGIME_WINDOW     = 8    # bars: RSI must be on one side of 50 for this many bars
_REGIME_THRESHOLD  = 50.0
_HD_ORDER          = 5    # argrelextrema half-width (bars) for swing detection
_HD_LOOKBACK       = 60   # bars scanned for hidden divergence pivots


# ── BiasProvider Interface ─────────────────────────────────────────────────────

class BiasProvider(ABC):
    """Abstract base class for HTF bias engines.

    To add a new bias method (e.g. MACD, ADX, MA slope):
      1. Subclass BiasProvider
      2. Implement compute(df_htf, mode) → BiasResult
      3. Register it: register_bias_provider("my_key", MyProvider())
      4. Use it:      compute_htf_bias(df, mode, provider="my_key")
    """

    @abstractmethod
    def compute(self, df_htf, mode: str) -> BiasResult:
        """Compute directional bias from an HTF OHLCV DataFrame.

        Args:
            df_htf: pd.DataFrame with Open/High/Low/Close/Volume and DatetimeIndex.
            mode:   \"swing\" | \"intraday\" — determines HTF/LTF label strings.

        Returns:
            BiasResult with .bias in:
                \"strong_bull\" | \"strong_bear\" | \"moderate_bull\" |
                \"moderate_bear\" | \"neutral\"
        """
        ...


# Provider registry — keyed by name string
_BIAS_PROVIDERS: dict[str, BiasProvider] = {}   # populated below after RSIBiasProvider


def register_bias_provider(name: str, provider: BiasProvider) -> None:
    """Register a custom BiasProvider under a given name."""
    _BIAS_PROVIDERS[name] = provider
    logger.debug("[trader.bias] Registered BiasProvider: %s", name)


# ── Public API ────────────────────────────────────────────────────────────────

def compute_htf_bias(df_htf, mode: str, provider: str = "rsi") -> BiasResult:
    """Compute directional bias from an HTF OHLCV DataFrame.

    Args:
        df_htf:    pandas DataFrame (DatetimeIndex, Open/High/Low/Close/Volume)
                   Needs >= 60 rows for meaningful signals.
        mode:      \"swing\" | \"intraday\"  — determines HTF/LTF label strings only.
        provider:  Name of the registered BiasProvider to use. Default: \"rsi\".

    Returns:
        BiasResult with .bias in:
            \"strong_bull\" | \"strong_bear\" | \"moderate_bull\" |
            \"moderate_bear\" | \"neutral\"
    """
    p = _BIAS_PROVIDERS.get(provider)
    if p is None:
        raise ValueError(
            f"Unknown BiasProvider '{provider}'. "
            f"Available: {list(_BIAS_PROVIDERS.keys())}"
        )
    return p.compute(df_htf, mode)


# ── RSI Bias Provider (default) ───────────────────────────────────────────────

class RSIBiasProvider(BiasProvider):
    """Default bias engine: RSI Cardwell regime + hidden divergence."""

    def compute(self, df_htf, mode: str) -> BiasResult:
        htf_tf, ltf_tf, vp_lookback = timeframes_for(mode)
        close = df_htf["Close"]

        rsi_series = _rsi(close, _RSI_PERIOD)
        rsi_val    = float(rsi_series.iloc[-1]) if len(rsi_series) else 50.0

        regime      = _rsi_regime(rsi_series)
        hd          = _hidden_divergence(close, rsi_series)
        bias, notes = _combine(regime, hd, rsi_val)

        return BiasResult(
            mode=mode,
            htf_tf=htf_tf,
            ltf_tf=ltf_tf,
            vp_lookback=vp_lookback,
            rsi_value=round(rsi_val, 2),
            regime=regime,
            hidden_div=hd,
            failure_swing="none",
            bias=bias,
            bias_notes=notes,
        )


# Register the default RSI provider so compute_htf_bias() works out of the box
register_bias_provider("rsi", RSIBiasProvider())


# ── Step 1 — RSI Regime ───────────────────────────────────────────────────────

def _rsi_regime(rsi_series) -> str:
    """Cardwell regime: RSI consistently above/below 50 in the last N bars.

    Returns: "bull" | "bear" | "neutral"
    """
    if len(rsi_series) < _REGIME_WINDOW:
        return "neutral"

    window = rsi_series.iloc[-_REGIME_WINDOW:]
    above  = (window > _REGIME_THRESHOLD).sum()
    below  = (window < _REGIME_THRESHOLD).sum()

    if above >= _REGIME_WINDOW - 1:   # allow 1 dip below 50 in the window
        return "bull"
    if below >= _REGIME_WINDOW - 1:
        return "bear"
    return "neutral"


# ── Step 2 — Hidden Divergence ────────────────────────────────────────────────

def _hidden_divergence(close, rsi_series) -> str:
    """Detect bullish or bearish HIDDEN RSI divergence.

    Hidden divergence signals trend CONTINUATION:
      Bullish HD: Price makes a Higher Low  (HL),  RSI makes a Lower Low  (LL)
                  → dip in RSI is fake; uptrend continues.
      Bearish HD: Price makes a Lower High (LH),  RSI makes a Higher High (HH)
                  → RSI bounce is fake; downtrend continues.

    NOTE: This is the INVERSE of regular divergence (which signals reversals).

    Returns: "bullish" | "bearish" | "none"
    """
    try:
        import numpy as np
        from scipy.signal import argrelextrema
    except ImportError:
        logger.warning("[trader.bias] scipy not available — hidden divergence skipped")
        return "none"

    n    = _HD_LOOKBACK
    ord_ = _HD_ORDER

    price = close.values[-n:]
    rsi   = rsi_series.values[-n:]

    if len(price) < ord_ * 2 + 1:
        return "none"

    # ── Bullish Hidden Divergence: troughs ───────────────────────────────────
    # Price: Higher Low (i2 trough > i1 trough)
    # RSI:   Lower Low  (i2 trough < i1 trough)
    trough_idx = argrelextrema(price, np.less, order=ord_)[0]
    if len(trough_idx) >= 2:
        i1, i2 = int(trough_idx[-2]), int(trough_idx[-1])
        price_higher_low = price[i2] > price[i1]   # HL in price
        rsi_lower_low    = rsi[i2]   < rsi[i1]     # LL in RSI
        if price_higher_low and rsi_lower_low:
            logger.debug(
                "[trader.bias] Bullish Hidden Div: price trough %.4f→%.4f, RSI %.1f→%.1f",
                price[i1], price[i2], rsi[i1], rsi[i2],
            )
            return "bullish"

    # ── Bearish Hidden Divergence: peaks ─────────────────────────────────────
    # Price: Lower High  (i2 peak < i1 peak)
    # RSI:   Higher High (i2 peak > i1 peak)
    peak_idx = argrelextrema(price, np.greater, order=ord_)[0]
    if len(peak_idx) >= 2:
        i1, i2 = int(peak_idx[-2]), int(peak_idx[-1])
        price_lower_high  = price[i2] < price[i1]  # LH in price
        rsi_higher_high   = rsi[i2]   > rsi[i1]    # HH in RSI
        if price_lower_high and rsi_higher_high:
            logger.debug(
                "[trader.bias] Bearish Hidden Div: price peak %.4f→%.4f, RSI %.1f→%.1f",
                price[i1], price[i2], rsi[i1], rsi[i2],
            )
            return "bearish"

    return "none"


# ── Step 3 — RSI Failure Swing (v1.1) ────────────────────────────────────────

def _failure_swing(rsi_series) -> str:
    """Pure RSI price-action reversal signal — reserved for v1.1 (Setup 4).

    Bullish: RSI < 30, bounces, stays > 30, breaks prior bounce high.
    Bearish: RSI > 70, drops,  stays < 70, breaks prior drop low.

    Returns: "bullish" | "bearish" | "none"
    """
    if len(rsi_series) < 20:
        return "none"

    rsi = rsi_series.values[-20:]

    # ── Bullish failure swing ────────────────────────────────────────────────
    try:
        dip_below_30 = next(i for i, v in enumerate(rsi) if v < 30)
        bounce_high  = max(rsi[dip_below_30:])
        # RSI must stay above 30 after bounce and then break bounce high
        after_dip = rsi[dip_below_30:]
        if all(v > 30 for v in after_dip[1:]) and rsi[-1] > bounce_high * 0.98:
            return "bullish"
    except StopIteration:
        pass

    # ── Bearish failure swing ────────────────────────────────────────────────
    try:
        spike_above_70 = next(i for i, v in enumerate(rsi) if v > 70)
        drop_low       = min(rsi[spike_above_70:])
        after_spike    = rsi[spike_above_70:]
        if all(v < 70 for v in after_spike[1:]) and rsi[-1] < drop_low * 1.02:
            return "bearish"
    except StopIteration:
        pass

    return "none"


# ── Step 4 — Combine signals into final bias ──────────────────────────────────

def _combine(regime: str, hd: str, rsi_val: float) -> tuple[str, str]:
    """Combine regime + hidden divergence into a single bias label + notes string.

    Returns:
        (bias_label, human_readable_notes)
    """
    # Both signals agree — strongest conviction
    if regime == "bull" and hd == "bullish":
        notes = (
            f"RSI {rsi_val:.1f} — bull regime (above 50) + "
            f"Bullish Hidden Divergence confirmed (HL price / LL RSI)"
        )
        return "strong_bull", notes

    if regime == "bear" and hd == "bearish":
        notes = (
            f"RSI {rsi_val:.1f} — bear regime (below 50) + "
            f"Bearish Hidden Divergence confirmed (LH price / HH RSI)"
        )
        return "strong_bear", notes

    # Only regime, no HD detected yet
    if regime == "bull" and hd == "none":
        notes = f"RSI {rsi_val:.1f} — bull regime (above 50). No HD yet; moderate bias."
        return "moderate_bull", notes

    if regime == "bear" and hd == "none":
        notes = f"RSI {rsi_val:.1f} — bear regime (below 50). No HD yet; moderate bias."
        return "moderate_bear", notes

    # HD fires but conflicts with regime — don't trade; note the conflict
    if hd != "none" and hd != regime.replace("bull", "bullish").replace("bear", "bearish"):
        notes = (
            f"RSI {rsi_val:.1f} — HD signal ({hd}) conflicts with regime ({regime}). "
            f"Skipping — conflicting signals."
        )
        return "neutral", notes

    notes = f"RSI {rsi_val:.1f} — neutral / choppy. No high-probability bias."
    return "neutral", notes


# ── RSI computation (mirrors technical/indicators._rsi) ──────────────────────

def _rsi(close, period: int = 14):
    """Wilder RSI via EWM. Reuses same formula as technical/indicators._rsi."""
    delta    = close.diff()
    gain     = delta.where(delta > 0, 0.0)
    loss     = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("inf"))
    return 100 - (100 / (1 + rs))
