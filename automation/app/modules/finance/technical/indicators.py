"""Technical indicator computations for the finance domain.

All pandas/numpy imports are LAZY (inside compute_ta) to prevent loading
heavy packages at module import time on the 1 GB e2-micro instance.

Usage:
    from modules.finance.technical.indicators import compute_ta, format_ta_summary
    result = compute_ta(df, symbol="AAPL", timeframe="1d")
    text   = format_ta_summary(result)
"""
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_CROSSOVER_LOOKBACK   = 3    # candles to look back for crossover detection
_DIVERGENCE_LOOKBACK  = 60   # candles scanned for RSI divergence peaks/troughs
_DIVERGENCE_ORDER     = 5    # argrelextrema order — local extremum window half-width


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class EMASet:
    ema21:  float | None = None
    ema50:  float | None = None
    ema100: float | None = None
    ema200: float | None = None


@dataclass
class TAResult:
    symbol:         str
    timeframe:      str
    current_price:  float
    rsi:            float | None
    rsi_signal:     str              # "overbought" | "oversold" | "neutral"
    emas:           EMASet
    trend:          str              # "bullish" | "bearish" | "mixed"
    volume_ratio:   float | None
    volume_signal:  str              # "spike" | "elevated" | "normal" | "low"
    rsi_divergence: str              # "bullish" | "bearish" | "none"
    crossovers:     list[str] = field(default_factory=list)
    signals:        list[str] = field(default_factory=list)
    ohlcv_json:     str        = ""
    ema_series:     dict       = field(default_factory=dict)  # name → list[float]
    rsi_series:     list[float] = field(default_factory=list)


# ── Main computation ──────────────────────────────────────────────────────────

def compute_ta(df, symbol: str, timeframe: str = "1d") -> TAResult:
    """
    Compute RSI, EMAs, volume, trend, and crossovers from an OHLCV DataFrame.

    Args:
        df:        pandas DataFrame with Open/High/Low/Close/Volume columns
                   and DatetimeIndex. Needs ≥ 210 rows for EMA200.
        symbol:    Ticker symbol (labelling only).
        timeframe: Timeframe string (labelling only).
    """
    # All heavy imports are lazy here
    close  = df["Close"]
    volume = df["Volume"]
    current_price = float(close.iloc[-1])

    # RSI(14)
    rsi_s     = _rsi(close, 14)
    rsi_val   = float(rsi_s.iloc[-1]) if len(rsi_s) else None
    rsi_sig   = _rsi_class(rsi_val)

    # EMAs
    ema21  = close.ewm(span=21,  adjust=False).mean()
    ema50  = close.ewm(span=50,  adjust=False).mean()
    ema100 = close.ewm(span=100, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()

    emas = EMASet(
        ema21=float(ema21.iloc[-1]),   ema50=float(ema50.iloc[-1]),
        ema100=float(ema100.iloc[-1]), ema200=float(ema200.iloc[-1]),
    )

    # Trend
    trend = _trend(current_price, emas)

    # Volume
    avg20 = volume.rolling(20).mean()
    vol_ratio = float(volume.iloc[-1] / avg20.iloc[-1]) if avg20.iloc[-1] > 0 else None
    vol_sig   = _vol_class(vol_ratio)

    # Crossovers
    crossovers = _crossovers(ema21, ema50, ema200)

    # RSI Divergence
    rsi_div = _rsi_divergence(close, rsi_s)

    # Signal summary list
    signals: list[str] = []
    if rsi_sig != "neutral":
        signals.append(f"RSI {rsi_sig.upper()} ({rsi_val:.1f})")
    if rsi_div == "bullish":
        signals.append("🟢 RSI Bullish Divergence (price lower low, RSI higher low)")
    elif rsi_div == "bearish":
        signals.append("🔴 RSI Bearish Divergence (price higher high, RSI lower high)")
    if vol_sig == "spike":
        signals.append(f"Volume spike ({vol_ratio:.1f}× avg)")
    signals.extend(crossovers)

    # Serialise for chart renderer
    ohlcv_json = df[["Open", "High", "Low", "Close", "Volume"]].to_json(orient="split")
    ema_series = {
        "ema21":  ema21.tolist(), "ema50":  ema50.tolist(),
        "ema100": ema100.tolist(), "ema200": ema200.tolist(),
    }

    return TAResult(
        symbol=symbol, timeframe=timeframe,
        current_price=current_price,
        rsi=rsi_val, rsi_signal=rsi_sig,
        emas=emas, trend=trend,
        volume_ratio=vol_ratio, volume_signal=vol_sig,
        rsi_divergence=rsi_div,
        crossovers=crossovers, signals=signals,
        ohlcv_json=ohlcv_json, ema_series=ema_series,
        rsi_series=rsi_s.tolist(),
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _rsi(close, period: int = 14):
    delta    = close.diff()
    gain     = delta.where(delta > 0, 0.0)
    loss     = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("inf"))
    return 100 - (100 / (1 + rs))


def _rsi_class(rsi: float | None) -> str:
    if rsi is None: return "neutral"
    if rsi > 70:    return "overbought"
    if rsi < 30:    return "oversold"
    return "neutral"


def _trend(price: float, e: EMASet) -> str:
    if all(x is not None for x in [e.ema21, e.ema50, e.ema200]):
        if price > e.ema21 > e.ema50 > e.ema200: return "bullish"
        if price < e.ema21 < e.ema50 < e.ema200: return "bearish"
    return "mixed"


def _vol_class(r: float | None) -> str:
    if r is None:  return "normal"
    if r >= 2.0:   return "spike"
    if r >= 1.5:   return "elevated"
    if r < 0.5:    return "low"
    return "normal"


def _crossovers(ema21, ema50, ema200) -> list[str]:
    lb = _CROSSOVER_LOOKBACK + 1
    found: list[str] = []
    pairs = [
        (21, 50,  ema21, ema50,  "21/50"),
        (50, 200, ema50, ema200, "50/200"),
        (21, 200, ema21, ema200, "21/200"),
    ]
    for fn, sn, fs, ss, label in pairs:
        if len(fs) < lb: continue
        prev_above = fs.iloc[-lb] > ss.iloc[-lb]
        curr_above = fs.iloc[-1]  > ss.iloc[-1]
        if not prev_above and curr_above:
            found.append(f"🟢 Bullish crossover: EMA{fn} above EMA{sn} ({label})")
        elif prev_above and not curr_above:
            found.append(f"🔴 Bearish crossover: EMA{fn} below EMA{sn} ({label})")
    return found


def _rsi_divergence(close, rsi_series) -> str:
    """Detect bullish or bearish RSI divergence over the last N candles.

    Strategy:
      - Bearish divergence: price makes a higher high but RSI makes a lower high
        → momentum is weakening despite rising price (potential reversal down).
      - Bullish divergence: price makes a lower low but RSI makes a higher low
        → momentum is recovering despite falling price (potential reversal up).

    Returns:
        "bullish" | "bearish" | "none"
    """
    try:
        import numpy as np
        from scipy.signal import argrelextrema  # lightweight — already a numpy dependency
    except ImportError:
        logger.warning("[ta] scipy not available — RSI divergence skipped")
        return "none"

    n   = _DIVERGENCE_LOOKBACK
    ord_ = _DIVERGENCE_ORDER

    # Slice to the lookback window
    price = close.values[-n:]
    rsi   = rsi_series.values[-n:]

    if len(price) < ord_ * 2 + 1:
        return "none"  # not enough data

    # ── Local troughs (for bullish divergence) ────────────────────────────────
    trough_idx = argrelextrema(price, np.less, order=ord_)[0]
    if len(trough_idx) >= 2:
        i1, i2 = int(trough_idx[-2]), int(trough_idx[-1])   # two most recent troughs
        price_lower_low = price[i2] < price[i1]
        rsi_higher_low  = rsi[i2]   > rsi[i1]
        if price_lower_low and rsi_higher_low:
            logger.debug(
                "[ta] Bullish RSI divergence: price trough %.2f→%.2f, RSI %.1f→%.1f",
                price[i1], price[i2], rsi[i1], rsi[i2],
            )
            return "bullish"

    # ── Local peaks (for bearish divergence) ──────────────────────────────────
    peak_idx = argrelextrema(price, np.greater, order=ord_)[0]
    if len(peak_idx) >= 2:
        i1, i2 = int(peak_idx[-2]), int(peak_idx[-1])        # two most recent peaks
        price_higher_high = price[i2] > price[i1]
        rsi_lower_high    = rsi[i2]   < rsi[i1]
        if price_higher_high and rsi_lower_high:
            logger.debug(
                "[ta] Bearish RSI divergence: price peak %.2f→%.2f, RSI %.1f→%.1f",
                price[i1], price[i2], rsi[i1], rsi[i2],
            )
            return "bearish"

    return "none"


# ── Formatter ─────────────────────────────────────────────────────────────────

def format_ta_summary(r: TAResult) -> str:
    """Render a TAResult as a Telegram Markdown message."""
    t_emoji = {"bullish": "📈", "bearish": "📉", "mixed": "➡️"}.get(r.trend, "➡️")
    rsi_str = f"{r.rsi:.1f}" if r.rsi is not None else "N/A"
    vol_str = f"{r.volume_ratio:.1f}×" if r.volume_ratio is not None else "N/A"

    div_str = {
        "bullish": "🟢 Bullish Divergence",
        "bearish": "🔴 Bearish Divergence",
        "none":    "—",
    }.get(r.rsi_divergence, "—")

    lines = [
        f"📊 *{r.symbol}* — {r.timeframe.upper()}",
        f"💵 Price: `{r.current_price:,.2f}`",
        f"{t_emoji} Trend: *{r.trend.title()} Alignment*",
        "——————————",
        f"RSI(14): `{rsi_str}` — {r.rsi_signal.title()} | Divergence: {div_str}",
        f"EMA21: `{r.emas.ema21:,.2f}` | EMA50: `{r.emas.ema50:,.2f}`",
        f"EMA100: `{r.emas.ema100:,.2f}` | EMA200: `{r.emas.ema200:,.2f}`",
        f"Volume: `{vol_str} avg` ({r.volume_signal.title()})",
        "——————————",
    ]
    if r.signals:
        lines.append("⚡ *Signals:*")
        lines.extend(f"  {s}" for s in r.signals)
    else:
        lines.append("⚡ *Signals:* None detected")
    return "\n".join(lines)
