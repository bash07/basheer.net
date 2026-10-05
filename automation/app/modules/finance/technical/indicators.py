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
_DIVERGENCE_LOOKBACK  = 90   # candles scanned for RSI divergence peaks/troughs
_DIVERGENCE_ORDER     = 3    # argrelextrema order — local extremum window half-width
                              # (3 = trough needs 3 recovery candles each side on 1D chart)


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
    emas:              EMASet
    trend:             str              # "bullish" | "bearish" | "mixed"
    volume_ratio:      float | None
    volume_signal:     str              # "spike" | "elevated" | "normal" | "low"
    rsi_divergence:    str              # "bullish" | "bearish" | "none"
    crossovers:        list[str] = field(default_factory=list)
    signals:           list[str] = field(default_factory=list)
    potential_signals: list[str] = field(default_factory=list)
    ohlcv_json:        str       = ""
    ema_series:        dict      = field(default_factory=dict)  # name → list[float]
    rsi_series:     list[float] = field(default_factory=list)
    volume_profile:    dict      = field(default_factory=dict)  # poc, vah, val
    mtf_aligned:       bool | None = None
    mtf_note:          str | None = None


# ── Main computation ──────────────────────────────────────────────────────────

def compute_ta(df, symbol: str, timeframe: str = "1d", df_weekly=None) -> TAResult:
    """
    Compute RSI, EMAs, volume, trend, and crossovers from an OHLCV DataFrame.

    Args:
        df:        pandas DataFrame with Open/High/Low/Close/Volume columns
                   and DatetimeIndex. Needs ≥ 210 rows for EMA200.
        symbol:    Ticker symbol (labelling only).
        timeframe: Timeframe string (labelling only).
        df_weekly: Optional weekly OHLCV DataFrame for MTF trend alignment.
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
    from modules.finance._shared.indicators import calc_ema
    ema21  = calc_ema(close, 21)
    ema50  = calc_ema(close, 50)
    ema100 = calc_ema(close, 100)
    ema200 = calc_ema(close, 200)

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
    signals.extend(_rsi_cross_signals(rsi_s))
    if rsi_div == "bullish":
        signals.append("🟢 RSI Bullish Divergence (price lower low, RSI higher low)")
    elif rsi_div == "bearish":
        signals.append("🔴 RSI Bearish Divergence (price higher high, RSI lower high)")
    if vol_sig == "spike":
        signals.append(f"Volume spike ({vol_ratio:.1f}× avg)")
    signals.extend(crossovers)

    # Multi-timeframe (MTF) trend evaluation
    mtf_aligned, mtf_note = (None, None)
    if timeframe.lower() in ("1d", "daily"):
        mtf_aligned, mtf_note = _evaluate_mtf(df, df_weekly, trend)
        if mtf_aligned is False and mtf_note:
            signals.append(f"⚠️ MTF Conflict: {mtf_note}")

    # Potential / Prospective setups
    potential_signals = _detect_potential_signals(close, rsi_s, ema21, ema50, ema200, current_price)

    # Serialise for chart renderer
    ohlcv_json = df[["Open", "High", "Low", "Close", "Volume"]].to_json(orient="split")
    ema_series = {
        "ema21":  ema21.tolist(), "ema50":  ema50.tolist(),
        "ema100": ema100.tolist(), "ema200": ema200.tolist(),
    }

    # Volume profile (POC, VAH, VAL) for chart overlay
    try:
        from modules.finance._shared.indicators import calc_volume_profile
        vp_core = calc_volume_profile(df, lookback=min(len(df), 100))
        vp_dict = {
            "poc": round(vp_core.poc, 4),
            "vah": round(vp_core.vah, 4),
            "val": round(vp_core.val, 4),
        }
    except Exception:
        vp_dict = {}

    return TAResult(
        symbol=symbol, timeframe=timeframe,
        current_price=current_price,
        rsi=rsi_val, rsi_signal=rsi_sig,
        emas=emas, trend=trend,
        volume_ratio=vol_ratio, volume_signal=vol_sig,
        rsi_divergence=rsi_div,
        crossovers=crossovers, signals=signals,
        potential_signals=potential_signals,
        ohlcv_json=ohlcv_json, ema_series=ema_series,
        rsi_series=rsi_s.tolist(),
        volume_profile=vp_dict,
        mtf_aligned=mtf_aligned,
        mtf_note=mtf_note,
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _rsi(close, period: int = 14):
    from modules.finance._shared.indicators import calc_rsi
    return calc_rsi(close, period=period)


def _rsi_class(rsi: float | None) -> str:
    if rsi is None: return "neutral"
    if rsi > 70:    return "overbought"
    if rsi < 30:    return "oversold"
    return "neutral"


def _rsi_cross_signals(rsi_s) -> list[str]:
    """Detect RSI boundary transitions (Solution A: only emit alerts on crossings).

    Prevents repetitive alert spam while an asset lingers in oversold or overbought zones.
    """
    if len(rsi_s) < 2:
        return []
    curr = float(rsi_s.iloc[-1])
    prev = float(rsi_s.iloc[-2])

    signals: list[str] = []

    # Crossing into oversold
    if curr < 30.0 and prev >= 30.0:
        signals.append(f"RSI Entered OVERSOLD ({curr:.1f})")
    elif curr < 20.0 and prev >= 20.0:
        signals.append(f"🚨 RSI Deep OVERSOLD Below 20 ({curr:.1f})")
    # Recovering from oversold
    elif curr >= 30.0 and prev < 30.0:
        signals.append(f"🟢 RSI Recovered Above 30 ({curr:.1f})")

    # Crossing into overbought
    if curr > 70.0 and prev <= 70.0:
        signals.append(f"RSI Entered OVERBOUGHT ({curr:.1f})")
    elif curr > 80.0 and prev <= 80.0:
        signals.append(f"🚨 RSI Extreme OVERBOUGHT Above 80 ({curr:.1f})")
    # Pulling back from overbought
    elif curr <= 70.0 and prev > 70.0:
        signals.append(f"🔴 RSI Pulled Back Below 70 ({curr:.1f})")

    return signals



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
        from modules.finance._shared.extrema import argrelextrema
    except ImportError:
        logger.warning("[ta] numpy not available — RSI divergence skipped")
        return "none"

    n   = _DIVERGENCE_LOOKBACK
    ord_ = _DIVERGENCE_ORDER

    # Slice to the lookback window
    price = close.values[-n:]
    rsi   = rsi_series.values[-n:]

    if len(price) < ord_ * 2 + 1:
        return "none"  # not enough data

    # ── 1. Bullish Divergence ─────────────────────────────────────────────────
    troughs = argrelextrema(price, np.less, order=ord_)[0]

    # (A) Check confirmed trough pairs (most recent confirmed troughs)
    if len(troughs) >= 2:
        for i in range(len(troughs) - 1, max(-1, len(troughs) - 4), -1):
            for j in range(i - 1, max(-1, i - 4), -1):
                i2, i1 = int(troughs[i]), int(troughs[j])
                dist = i2 - i1
                recency = len(price) - 1 - i2
                if recency <= 15 and 4 <= dist <= 35:
                    # Guard against intervening lower trough that breaks the swing structure
                    if np.min(price[i1:i2 + 1]) >= price[i2] * 0.995:
                        if price[i2] < price[i1] and (rsi[i2] - rsi[i1]) >= 1.5:
                            if min(rsi[i1], rsi[i2]) <= 45.0:
                                logger.debug(
                                    "[ta] Confirmed Bullish RSI divergence: price %.2f→%.2f, RSI %.1f→%.1f",
                                    price[i1], price[i2], rsi[i1], rsi[i2],
                                )
                                return "bullish"

    # (B) Check active/emerging bullish divergence on recent candles (last 5 bars)
    for cur_idx in range(len(price) - 1, max(len(price) - 6, ord_), -1):
        p_cur = price[cur_idx]
        r_cur = rsi[cur_idx]
        for t in reversed(troughs):
            t_idx = int(t)
            dist = cur_idx - t_idx
            if 4 <= dist <= 35:
                if np.min(price[t_idx:cur_idx + 1]) >= min(p_cur, price[t_idx]) * 0.995:
                    if p_cur < price[t_idx] and (r_cur - rsi[t_idx]) >= 1.5 and min(r_cur, rsi[t_idx]) <= 45.0:
                        bounce_rsi = float(np.max(rsi[t_idx + 1:cur_idx])) if cur_idx > t_idx + 1 else r_cur
                        if bounce_rsi > rsi[t_idx] + 2.0:
                            logger.debug(
                                "[ta] Active Bullish RSI divergence: price trough %.2f (idx %d) → %.2f (idx %d), RSI %.1f → %.1f",
                                price[t_idx], t_idx, p_cur, cur_idx, rsi[t_idx], r_cur,
                            )
                            return "bullish"

    # ── 2. Bearish Divergence ─────────────────────────────────────────────────
    peaks = argrelextrema(price, np.greater, order=ord_)[0]

    # (A) Check confirmed peak pairs
    if len(peaks) >= 2:
        for i in range(len(peaks) - 1, max(-1, len(peaks) - 4), -1):
            for j in range(i - 1, max(-1, i - 4), -1):
                i2, i1 = int(peaks[i]), int(peaks[j])
                dist = i2 - i1
                recency = len(price) - 1 - i2
                if recency <= 15 and 4 <= dist <= 35:
                    # Guard against intervening higher peak that breaks the swing structure
                    if np.max(price[i1:i2 + 1]) <= price[i2] * 1.005:
                        if price[i2] > price[i1] and (rsi[i1] - rsi[i2]) >= 1.5:
                            if max(rsi[i1], rsi[i2]) >= 55.0:
                                logger.debug(
                                    "[ta] Confirmed Bearish RSI divergence: price %.2f→%.2f, RSI %.1f→%.1f",
                                    price[i1], price[i2], rsi[i1], rsi[i2],
                                )
                                return "bearish"

    # (B) Check active/emerging bearish divergence on recent candles
    for cur_idx in range(len(price) - 1, max(len(price) - 6, ord_), -1):
        p_cur = price[cur_idx]
        r_cur = rsi[cur_idx]
        for p in reversed(peaks):
            p_idx = int(p)
            dist = cur_idx - p_idx
            if 4 <= dist <= 35:
                if np.max(price[p_idx:cur_idx + 1]) <= max(p_cur, price[p_idx]) * 1.005:
                    if p_cur > price[p_idx] and (rsi[p_idx] - r_cur) >= 1.5 and max(r_cur, rsi[p_idx]) >= 55.0:
                        pullback_rsi = float(np.min(rsi[p_idx + 1:cur_idx])) if cur_idx > p_idx + 1 else r_cur
                        if pullback_rsi < rsi[p_idx] - 2.0:
                            logger.debug(
                                "[ta] Active Bearish RSI divergence: price peak %.2f (idx %d) → %.2f (idx %d), RSI %.1f → %.1f",
                                price[p_idx], p_idx, p_cur, cur_idx, rsi[p_idx], r_cur,
                            )
                            return "bearish"

    return "none"


# ── Potential / Prospective Setup Detection ───────────────────────────────────

def _detect_potential_signals(close, rsi_series, ema21, ema50, ema200, current_price: float) -> list[str]:
    """Detect prospective/developing patterns such as curling divergence and EMA convergence."""
    potentials: list[str] = []

    # 1. Potential Divergence (curling momentum at swing extremes)
    pot_div = _potential_divergence(close, rsi_series, current_price)
    if pot_div:
        potentials.append(pot_div)

    # 2. EMA Convergence Watch (pre-crossover)
    ema_convs = _ema_convergences(current_price, ema21, ema50, ema200)
    potentials.extend(ema_convs)

    return potentials


def _potential_divergence(close, rsi_series, current_price: float) -> str | None:
    """Detect developing divergence where price tests a swing extreme while RSI curls counter-trend."""
    try:
        import numpy as np
        from modules.finance._shared.extrema import argrelextrema
    except ImportError:
        return None

    n = _DIVERGENCE_LOOKBACK
    ord_ = _DIVERGENCE_ORDER

    price = close.values[-n:]
    rsi   = rsi_series.values[-n:]

    if len(price) < ord_ * 2 + 1 or len(rsi) < 3:
        return None

    curr_rsi = float(rsi[-1])
    prev_rsi = float(rsi[-2])
    rsi_curling_up   = curr_rsi > prev_rsi
    rsi_curling_down = curr_rsi < prev_rsi

    # Developing Bullish Divergence
    troughs = argrelextrema(price, np.less, order=ord_)[0]
    for t in reversed(troughs):
        t_idx = int(t)
        dist = len(price) - 1 - t_idx
        if 4 <= dist <= 35:
            prior_price = float(price[t_idx])
            prior_rsi   = float(rsi[t_idx])
            if prior_rsi <= 45.0 and np.min(price[t_idx:]) >= min(current_price, prior_price) * 0.99:
                price_ratio = (current_price - prior_price) / prior_price
                # Price is testing support or making lower low (within 2.5% above or lower)
                if price_ratio <= 0.025 and (curr_rsi - prior_rsi) >= 1.0 and rsi_curling_up:
                    return (
                        f"⚠️ Developing Bull Divergence: Price testing {current_price:,.2f} "
                        f"with RSI curling up ({curr_rsi:.1f} vs prior {prior_rsi:.1f})"
                    )

    # Developing Bearish Divergence
    peaks = argrelextrema(price, np.greater, order=ord_)[0]
    for p in reversed(peaks):
        p_idx = int(p)
        dist = len(price) - 1 - p_idx
        if 4 <= dist <= 35:
            prior_price = float(price[p_idx])
            prior_rsi   = float(rsi[p_idx])
            if prior_rsi >= 55.0 and np.max(price[p_idx:]) <= max(current_price, prior_price) * 1.01:
                price_ratio = (current_price - prior_price) / prior_price
                # Price is testing resistance or making higher high (within 2.5% below or higher)
                if price_ratio >= -0.025 and (prior_rsi - curr_rsi) >= 1.0 and rsi_curling_down:
                    return (
                        f"⚠️ Developing Bear Divergence: Price testing {current_price:,.2f} "
                        f"with RSI curling down ({curr_rsi:.1f} vs prior {prior_rsi:.1f})"
                    )

    return None


def _ema_convergences(current_price: float, ema21, ema50, ema200) -> list[str]:
    """Detect moving average pairs narrowing towards a crossover (spread <= 1.0%)."""
    convs: list[str] = []
    pairs = [
        (21, 50,  ema21, ema50,  "21/50"),
        (50, 200, ema50, ema200, "50/200"),
    ]
    if current_price <= 0:
        return convs

    for fn, sn, fs, ss, label in pairs:
        if len(fs) < 3 or len(ss) < 3:
            continue
        spread_curr = abs(float(fs.iloc[-1]) - float(ss.iloc[-1])) / current_price
        spread_prev = abs(float(fs.iloc[-2]) - float(ss.iloc[-2])) / current_price

        if spread_curr <= 0.010 and spread_curr < spread_prev:
            spread_pct = spread_curr * 100
            fast_curr = float(fs.iloc[-1])
            slow_curr = float(ss.iloc[-1])
            if fast_curr < slow_curr:
                convs.append(
                    f"⏳ Converging: EMA{fn} is {spread_pct:.1f}% below EMA{sn} ({label} Bullish Cross watch)"
                )
            else:
                convs.append(
                    f"⏳ Converging: EMA{fn} is {spread_pct:.1f}% above EMA{sn} ({label} Bearish Cross watch)"
                )
    return convs


def _resample_weekly(df):
    try:
        import pandas as pd
        if not isinstance(df.index, pd.DatetimeIndex) or len(df) < 50:
            return None
        agg = {}
        for col in ("Open", "High", "Low", "Close", "Volume"):
            if col in df.columns:
                if col == "Open":
                    agg[col] = "first"
                elif col == "High":
                    agg[col] = "max"
                elif col == "Low":
                    agg[col] = "min"
                elif col == "Close":
                    agg[col] = "last"
                elif col == "Volume":
                    agg[col] = "sum"
        df_w = df.resample("W-FRI").agg(agg).dropna()
        return df_w
    except Exception:
        return None


def _evaluate_mtf(df, df_weekly, daily_trend: str) -> tuple[bool | None, str | None]:
    """Check multi-timeframe alignment between 1D and 1W trends."""
    if df_weekly is None:
        df_weekly = _resample_weekly(df)

    if df_weekly is None or len(df_weekly) < 20:
        return None, None

    try:
        from modules.finance._shared.indicators import calc_ema
        close_w = df_weekly["Close"]
        fast_period = 21 if len(df_weekly) >= 50 else 10
        slow_period = 50 if len(df_weekly) >= 50 else 20

        ema_f = calc_ema(close_w, fast_period)
        ema_s = calc_ema(close_w, slow_period)

        c_val = float(close_w.iloc[-1])
        f_val = float(ema_f.iloc[-1])
        s_val = float(ema_s.iloc[-1])

        if c_val > f_val > s_val:
            w_trend = "bullish"
        elif c_val < f_val < s_val:
            w_trend = "bearish"
        else:
            w_trend = "mixed"

        if daily_trend == "bullish" and w_trend == "bearish":
            return False, "Weekly Bearish opposes Daily Bullish (Caution)"
        elif daily_trend == "bearish" and w_trend == "bullish":
            return False, "Weekly Bullish opposes Daily Bearish (Caution)"
        elif daily_trend == "bullish" and w_trend == "bullish":
            return True, "Bullish Alignment (1D + 1W)"
        elif daily_trend == "bearish" and w_trend == "bearish":
            return True, "Bearish Alignment (1D + 1W)"
        else:
            return None, f"1D {daily_trend.title()} / 1W {w_trend.title()}"
    except Exception:
        return None, None


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
    ]
    if r.mtf_note:
        lines.append(f"🌐 MTF: *{r.mtf_note}*")
    lines.extend([
        "——————————",
        f"RSI(14): `{rsi_str}` — {r.rsi_signal.title()} | Divergence: {div_str}",
        f"EMA21: `{r.emas.ema21:,.2f}` | EMA50: `{r.emas.ema50:,.2f}`",
        f"EMA100: `{r.emas.ema100:,.2f}` | EMA200: `{r.emas.ema200:,.2f}`",
        f"Volume: `{vol_str} avg` ({r.volume_signal.title()})",
        "——————————",
    ])
    if r.signals:
        lines.append("⚡ *Signals:*")
        lines.extend(f"  {s}" for s in r.signals)
    else:
        lines.append("⚡ *Signals:* None detected")

    if r.potential_signals:
        lines.append("")
        lines.append("🔮 *Potential Setups:*")
        lines.extend(f"  {s}" for s in r.potential_signals)

    return "\n".join(lines)
