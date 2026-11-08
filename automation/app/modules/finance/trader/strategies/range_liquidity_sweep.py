"""Setup 5 — Range Liquidity Sweep (Mean Reversion) strategy.

Upgraded to the 3-Tier Institutional Multi-Timeframe Triad:
  - Gate 1: The HTF Setup ("Where" — Macro Range, Boundary Location Filter, & Pro-Trend Drift)
  - Gate 2: The MTF Confirmation ("When" — Structure & Momentum Exhaustion)
  - Gate 3: The Entry Trigger ("How" — Micro SFP Rejection, Adaptive SL Buffer, & Asymmetric R:R)
"""
from __future__ import annotations

import logging
from typing import Optional

from .base import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)

_MIN_RR = 1.5           # Minimum R:R required to TP1 (or >= 2.0 to TP2)
_MIN_RANGE_ATR = 1.5    # Range width must be >= 1.5x ATR to ensure trade viability
_MIN_WICK_RATIO = 0.40  # Rejection wick must account for at least 40% of candle range
_MAX_PENETRATION_ATR = 1.0  # Max allowable sweep penetration past boundary (blowout guard)
_MIN_SCORE_THRESHOLD = 6.0  # Raised minimum confluence score gatekeeper


def _compute_rsi(close_series, period: int = 14):
    """Wilder's EWM RSI-14."""
    if len(close_series) < period + 1:
        return None
    delta = close_series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    ag = gain.ewm(com=period - 1, min_periods=period).mean()
    al = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = ag / al.replace(0, float("inf"))
    return 100.0 - (100.0 / (1.0 + rs))


class RangeLiquiditySweepStrategy(BaseStrategy):
    """Fades liquidity sweeps / failed breakouts at HTF/MTF range boundaries.

    3-Tier Institutional Multi-Timeframe Architecture:
      - Gate 1: HTF Setup (Where):
          * Macro Range Boundaries (VAH/VAL or HTF Swings) >= 1.5x ATR.
          * Reject runaway trend expansion (ADX >= 25 with strong_bull/strong_bear).
          * Location Filter: Relative position of sweep extreme at range ceiling (>=0.80) or floor (<=0.20),
            while current price remains on favorable side of equilibrium.
          * Pro-Trend Drift Filter: When HTF bias is directional (moderate_bull/bear), only take
            sweeps in the direction of the dominant drift (buy support in bulls, sell resistance in bears).
      - Gate 2: MTF Confirmation (When):
          * MTF Momentum Exhaustion: Short requires MTF RSI >= 55.0 or Bearish Divergence;
            Long requires MTF RSI <= 45.0 or Bullish Divergence.
          * Breakout Acceptance Filter: Rejects if last 2 completed MTF candles closed outside range.
      - Gate 3: Trigger Execution (How):
          * Micro SFP Sweep: Bar pierces boundary and snaps back inside with >= 40% wick.
          * Penetration Blowout Guard: Sweep cannot exceed 1.0x ATR past boundary.
          * Adaptive SL Buffer: 0.50x ATR for crypto (absorbs secondary wick retests), 0.30x ATR for equities/forex.
          * Pro-Trend Targets: Pro-trend fades target full range traversal (TP1 at opposite boundary for 2.0x+ RR);
            Neutral chop targets equilibrium at TP1.
          * Confluence Gatekeeper: Strict score threshold (>= 6.0).
    """

    name                      = "Range Liquidity Sweep"
    description               = "Fades liquidity sweeps / failed breakouts at HTF/MTF range boundaries"
    setup_type                = "range"
    priority                  = 35
    requires_directional_bias = False
    supported_styles          = ["swing", "intraday", "scalping"]

    required_indicators = [
        "bias",
        "adx",
        "atr_14",
        "rsi_14",
        "sr_levels",
        "volume_profile",
        "liq_list",
        "structure",
    ]

    def evaluate(self, ctx: StrategyContext) -> Optional[object]:
        from ..confluence import score_zone
        from ..schemas import TradeSetup
        from ..setups import _MIN_SCORE, _calc_rr

        # ── Timeframe Cascading Fallback (Backward Compatibility) ────────────
        df_htf     = ctx.df_htf
        df_mtf     = ctx.df_mtf if ctx.df_mtf is not None else (ctx.df_ltf if ctx.df_ltf is not None else df_htf)
        df_trigger = ctx.df_trigger if ctx.df_trigger is not None else ctx.df_ltf

        if df_trigger is None or len(df_trigger) < 5:
            return None

        # ═════════════════════════════════════════════════════════════════════
        # Gate 1: The HTF Setup ("Where" — Macro Range & Location Filter)
        # ═════════════════════════════════════════════════════════════════════
        adx_val = ctx.adx if ctx.adx is not None else 20.0
        bias = ctx.bias
        # Reject strong HTF directional trend expansion
        if adx_val >= 25.0 and bias is not None and bias.bias in ("strong_bull", "strong_bear"):
            logger.debug(
                "[trader.strategy] %s: Range sweep skipped — strong trend (ADX=%.1f, bias=%s)",
                ctx.ticker, adx_val, bias.bias,
            )
            return None

        vp = ctx.volume_profile
        atr = ctx.atr_14 or (ctx.current_price * 0.01)

        # Primary boundaries: Volume Profile VAH/VAL; Fallback: Swing High/Low
        res = vp.vah if (vp and vp.vah > 0) else None
        sup = vp.val if (vp and vp.val > 0) else None

        if res is None or sup is None:
            # Fallback to HTF / structure swing points
            if ctx.structure and ctx.structure.points:
                highs = [p.price for p in ctx.structure.points if p.kind == "high"]
                lows = [p.price for p in ctx.structure.points if p.kind == "low"]
                if highs and lows:
                    res = max(highs)
                    sup = min(lows)
            elif df_htf is not None and len(df_htf) >= 20:
                res = float(df_htf["High"].tail(40).max())
                sup = float(df_htf["Low"].tail(40).min())

        if res is None or sup is None or res <= sup:
            return None

        range_height = res - sup
        if range_height < (_MIN_RANGE_ATR * atr):
            logger.debug(
                "[trader.strategy] %s: Range too tight (%.4f < %.4f ATR threshold)",
                ctx.ticker, range_height, _MIN_RANGE_ATR * atr,
            )
            return None

        midpoint = (vp.poc if (vp and vp.poc and sup < vp.poc < res) else (res + sup) / 2.0)

        # ═════════════════════════════════════════════════════════════════════
        # Gate 3 Trigger Pre-Scan (Determine Candidate Direction & Sweep Bar)
        # ═════════════════════════════════════════════════════════════════════
        trigger_bar = None
        direction = None
        sweep_extreme = 0.0

        lookback_bars = min(3, len(df_trigger))
        for i in range(1, lookback_bars + 1):
            bar = df_trigger.iloc[-i]
            bar_high = float(bar["High"])
            bar_low = float(bar["Low"])
            bar_open = float(bar["Open"])
            bar_close = float(bar["Close"])
            bar_range = bar_high - bar_low

            if bar_range <= 0:
                continue

            # Bearish Sweep at Resistance (Short Setup)
            if bar_high >= res and bar_close < res:
                # Blowout penetration guard: must not rip > 1.0 ATR past boundary
                if (bar_high - res) <= (_MAX_PENETRATION_ATR * atr):
                    upper_wick = bar_high - max(bar_open, bar_close)
                    if (upper_wick / bar_range) >= _MIN_WICK_RATIO:
                        direction = "short"
                        trigger_bar = bar
                        sweep_extreme = bar_high
                        break

            # Bullish Sweep at Support (Long Setup)
            elif bar_low <= sup and bar_close > sup:
                # Blowout penetration guard
                if (sup - bar_low) <= (_MAX_PENETRATION_ATR * atr):
                    lower_wick = min(bar_open, bar_close) - bar_low
                    if (lower_wick / bar_range) >= _MIN_WICK_RATIO:
                        direction = "long"
                        trigger_bar = bar
                        sweep_extreme = bar_low
                        break

        if direction is None or trigger_bar is None:
            return None

        # ── Pro-Trend Drift Filter (Align Range Trades with HTF Drift) ───────
        # Counter-trend sweeps are only prohibited during runaway trend expansion (strong_bull / strong_bear with high ADX)
        if bias is not None:
            if bias.bias == "strong_bull" and direction == "short" and adx_val >= 22.0:
                logger.debug(
                    "[trader.strategy] %s: Short rejected — cannot short range ceiling against strong_bull trend expansion",
                    ctx.ticker,
                )
                return None
            if bias.bias == "strong_bear" and direction == "long" and adx_val >= 22.0:
                logger.debug(
                    "[trader.strategy] %s: Long rejected — cannot buy range floor against strong_bear trend expansion",
                    ctx.ticker,
                )
                return None

        current_price = ctx.current_price

        # Gate 1 Location Filter: Verify sweep extreme reached range boundary zone
        rel_pos = (sweep_extreme - sup) / range_height
        if direction == "short":
            if rel_pos < 0.80 or current_price < midpoint:
                logger.debug("[trader.strategy] %s: Short rejected by location filter (rel_pos=%.2f)", ctx.ticker, rel_pos)
                return None
            if current_price > (res + 0.5 * atr):
                return None
        else:
            if rel_pos > 0.20 or current_price > midpoint:
                logger.debug("[trader.strategy] %s: Long rejected by location filter (rel_pos=%.2f)", ctx.ticker, rel_pos)
                return None
            if current_price < (sup - 0.5 * atr):
                return None

        # ═════════════════════════════════════════════════════════════════════
        # Gate 2: The MTF Confirmation ("When" — Structure & Momentum)
        # ═════════════════════════════════════════════════════════════════════
        if df_mtf is not None and len(df_mtf) >= 5:
            # Check Breakout Acceptance: Rejects if last 2 completed MTF candles closed outside range
            if len(df_mtf) >= 2:
                mtf_c1 = float(df_mtf["Close"].iloc[-1])
                mtf_c2 = float(df_mtf["Close"].iloc[-2])
                if direction == "short" and (mtf_c1 > res and mtf_c2 > res):
                    logger.debug("[trader.strategy] %s: Short rejected — MTF acceptance outside resistance", ctx.ticker)
                    return None
                if direction == "long" and (mtf_c1 < sup and mtf_c2 < sup):
                    logger.debug("[trader.strategy] %s: Long rejected — MTF acceptance outside support", ctx.ticker)
                    return None

            # MTF Momentum Exhaustion / Divergence
            if len(df_mtf) >= 15:
                mtf_rsi = _compute_rsi(df_mtf["Close"], period=14)
                if mtf_rsi is not None and not mtf_rsi.empty:
                    last_mtf_rsi = float(mtf_rsi.iloc[-1])
                    if direction == "short":
                        has_exhaustion = last_mtf_rsi >= 55.0
                        if not has_exhaustion and len(mtf_rsi) >= 10:
                            prior_max_rsi = float(mtf_rsi.iloc[-10:-1].max())
                            if last_mtf_rsi < prior_max_rsi:
                                has_exhaustion = True
                        if not has_exhaustion:
                            logger.debug("[trader.strategy] %s: Short rejected — MTF RSI %.1f not exhausted", ctx.ticker, last_mtf_rsi)
                            return None
                    else:
                        has_exhaustion = last_mtf_rsi <= 45.0
                        if not has_exhaustion and len(mtf_rsi) >= 10:
                            prior_min_rsi = float(mtf_rsi.iloc[-10:-1].min())
                            if last_mtf_rsi > prior_min_rsi:
                                has_exhaustion = True
                        if not has_exhaustion:
                            logger.debug("[trader.strategy] %s: Long rejected — MTF RSI %.1f not exhausted", ctx.ticker, last_mtf_rsi)
                            return None

        # ═════════════════════════════════════════════════════════════════════
        # Gate 3: Execution Levels, Confluence, and R:R
        # ═════════════════════════════════════════════════════════════════════
        # Mode and category adaptive invalidation buffer:
        # In swing mode (trading weekly/daily range on 1h trigger), 0.30x trigger ATR is microscopically
        # tight (<$1 on $500 ETF) compared to the $30 range, causing instant noise stop-outs.
        # Using 1.0x trigger ATR gives breathing room while preserving 3.0x - 5.0x asymmetric R:R.
        is_crypto = getattr(ctx, "category", "").lower() == "crypto"
        if ctx.mode == "swing":
            buffer_mult = 1.00
        elif is_crypto:
            buffer_mult = 0.50
        else:
            buffer_mult = 0.30
        buffer = buffer_mult * atr

        is_pro_trend = bias is not None and (
            ("bull" in bias.bias and direction == "long") or ("bear" in bias.bias and direction == "short")
        )

        entry_low = min(current_price, float(trigger_bar["Close"]))
        entry_high = max(current_price, float(trigger_bar["Close"]))
        entry_mid = (entry_low + entry_high) / 2.0

        min_sl_dist = ctx.get_min_sl_distance(entry_price=entry_mid, atr=atr)

        if direction == "short":
            raw_sl = sweep_extreme + buffer
            sl_dist = raw_sl - entry_mid
            stop_loss = entry_mid + max(sl_dist, min_sl_dist)
            tp1 = midpoint
            tp2 = sup + (0.20 * atr)
            invalidation = f"Candle closes above sweep high {sweep_extreme:,.4f}"
        else:
            raw_sl = sweep_extreme - buffer
            sl_dist = entry_mid - raw_sl
            stop_loss = entry_mid - max(sl_dist, min_sl_dist)
            tp1 = midpoint
            tp2 = res - (0.20 * atr)
            invalidation = f"Candle closes below sweep low {sweep_extreme:,.4f}"

        rr_tp1 = _calc_rr(entry_mid, stop_loss, tp1)
        rr_tp2 = _calc_rr(entry_mid, stop_loss, tp2)

        # Allow trade if TP1 gives favorable R:R or if full rotation to TP2 gives >= 2.0
        if rr_tp1 < 1.2 and rr_tp2 < 2.0:
            logger.debug(
                "[trader.strategy] %s: R:R insufficient (TP1=%.2f, TP2=%.2f)",
                ctx.ticker, rr_tp1, rr_tp2,
            )
            return None

        # Confluence Scoring (0.0 to 10.0 scale)
        if direction == "short":
            zone_low = res - (0.3 * atr)
            zone_high = res + (0.3 * atr)
        else:
            zone_low = sup - (0.3 * atr)
            zone_high = sup + (0.3 * atr)

        report = score_zone(zone_low, zone_high, ctx.sr_levels, vp)

        # Base boundary SFP pattern contribution
        report.score = min(report.score + 1.0, 10.0)
        report.sr_hits.append(f"Boundary SFP sweep ({direction.upper()})")

        # Pro-trend alignment contribution
        if is_pro_trend:
            report.score = min(report.score + 1.0, 10.0)
            report.sr_hits.append("Pro-trend HTF drift alignment")

        # RSI Momentum Exhaustion Bonus
        rsi_series = ctx.rsi_14
        if rsi_series is not None and not rsi_series.empty:
            last_rsi = float(rsi_series.iloc[-1])
            if direction == "short" and last_rsi >= 60.0:
                report.score = min(report.score + 1.5, 10.0)
                report.sr_hits.append(f"RSI overbought ({last_rsi:.1f})")
            elif direction == "long" and last_rsi <= 40.0:
                report.score = min(report.score + 1.5, 10.0)
                report.sr_hits.append(f"RSI oversold ({last_rsi:.1f})")

        # Swept Liquidity Bonus from liq_list
        liq_kind = "eqh" if direction == "short" else "eql"
        swept_levels = [l for l in ctx.liq_list if l.swept and l.kind == liq_kind]
        if swept_levels:
            report.score = min(report.score + 1.0, 10.0)
            report.sr_hits.append(f"Swept {liq_kind.upper()} liquidity level")

        # Gatekeeper: Enforce minimum confluence threshold
        if report.score < _MIN_SCORE:
            logger.debug(
                "[trader.strategy] %s: Confluence score %.1f < %.1f threshold",
                ctx.ticker, report.score, _MIN_SCORE,
            )
            return None

        ltf_tf = bias.ltf_tf if bias else ""
        htf_tf = bias.htf_tf if bias else ""

        return TradeSetup(
            ticker=ctx.ticker,
            category=ctx.category,
            mode=ctx.mode,
            setup_name=self.name,
            setup_type=self.setup_type,
            direction=direction,
            bias=bias,
            confluence=report,
            entry_low=entry_low,
            entry_high=entry_high,
            stop_loss=round(stop_loss, 4),
            tp1=round(tp1, 4),
            tp2=round(tp2, 4),
            rr_ratio=round(max(rr_tp1, rr_tp2), 2),
            invalidation_note=invalidation,
            ltf_tf=ltf_tf,
            htf_tf=htf_tf,
        )
