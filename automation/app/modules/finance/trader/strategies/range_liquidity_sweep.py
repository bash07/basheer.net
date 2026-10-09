"""Setup 5 — Range Liquidity Sweep (Mean Reversion) strategy.

Designed for sideways, consolidating, and choppy market regimes.
Fades false breakouts / liquidity sweeps (Swing Failure Patterns) occurring at
confirmed Support and Resistance (S/R) and Volume Area (VAH/VAL) boundaries.
"""
from __future__ import annotations

import logging
from typing import Optional

from .base import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)

_MIN_RR = 1.5          # Minimum R:R required to TP1 (or >= 2.0 to TP2)
_MIN_RANGE_ATR = 1.5   # Range width must be >= 1.5x ATR to ensure trade viability
_MIN_WICK_RATIO = 0.40 # Rejection wick must account for at least 40% of candle range


class RangeLiquiditySweepStrategy(BaseStrategy):
    """Fades failed breakouts at range boundaries in non-trending regimes.

    Gates:
      1. Regime: ADX < 25 or non-directional bias (strong trends rejected).
      2. Boundaries: S/R range width (VAH - VAL or Swing H - Swing L) >= 1.5 * ATR.
      3. Sweep Trigger: Last completed bar pierced boundary but closed back inside
         with >= 40% rejection wick.
      4. Confluence: Confluence score >= 5.0 (includes S/R hits + VP + RSI extremes).
      5. Risk/Reward: Target TP1 at equilibrium/POC, TP2 at opposite boundary.
    """

    name = "Range Liquidity Sweep"
    description = "Fades liquidity sweeps / failed breakouts at S/R range boundaries"
    setup_type = "range"
    priority = 35
    requires_directional_bias = False

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

        # ── Gate 1: Regime Filter (Sideways / Consolidation) ─────────────────
        adx_val = ctx.adx if ctx.adx is not None else 20.0
        bias = ctx.bias
        # Reject strong HTF directional trends
        if adx_val >= 25.0 and bias is not None and bias.bias in ("strong_bull", "strong_bear"):
            logger.debug(
                "[trader.strategy] %s: Range sweep skipped — strong trend (ADX=%.1f, bias=%s)",
                ctx.ticker, adx_val, bias.bias,
            )
            return None

        # ── Gate 2: Boundary Resolution & Range Height Filter ───────────────
        vp = ctx.volume_profile
        atr = ctx.atr_14 or (ctx.current_price * 0.01)

        # Primary boundaries: Volume Profile VAH/VAL; Fallback: Swing High/Low
        res = vp.vah if (vp and vp.vah > 0) else None
        sup = vp.val if (vp and vp.val > 0) else None

        if res is None or sup is None:
            # Fallback to structure swing points
            if ctx.structure and ctx.structure.points:
                highs = [p.price for p in ctx.structure.points if p.kind == "high"]
                lows = [p.price for p in ctx.structure.points if p.kind == "low"]
                if highs and lows:
                    res = max(highs)
                    sup = min(lows)

        if res is None or sup is None or res <= sup:
            return None

        range_height = res - sup
        if range_height < (_MIN_RANGE_ATR * atr):
            logger.debug(
                "[trader.strategy] %s: Range too tight (%.4f < %.4f ATR threshold)",
                ctx.ticker, range_height, _MIN_RANGE_ATR * atr,
            )
            return None

        # ── Gate 3: Sweep & Candle Rejection Trigger ─────────────────────────
        df = ctx.df_ltf
        if df is None or len(df) < 5:
            return None

        # Check the last completed bar (or the most recent bar)
        trigger_bar = None
        direction = None
        sweep_extreme = 0.0

        # Scan the last 3 bars for a qualifying sweep & rejection
        lookback_bars = min(3, len(df))
        for i in range(1, lookback_bars + 1):
            bar = df.iloc[-i]
            bar_high = float(bar["High"])
            bar_low = float(bar["Low"])
            bar_open = float(bar["Open"])
            bar_close = float(bar["Close"])
            bar_range = bar_high - bar_low

            if bar_range <= 0:
                continue

            # Bearish Sweep at Resistance (Short Setup)
            if bar_high >= res and bar_close < res:
                upper_wick = bar_high - max(bar_open, bar_close)
                if (upper_wick / bar_range) >= _MIN_WICK_RATIO:
                    direction = "short"
                    trigger_bar = bar
                    sweep_extreme = bar_high
                    break

            # Bullish Sweep at Support (Long Setup)
            elif bar_low <= sup and bar_close > sup:
                lower_wick = min(bar_open, bar_close) - bar_low
                if (lower_wick / bar_range) >= _MIN_WICK_RATIO:
                    direction = "long"
                    trigger_bar = bar
                    sweep_extreme = bar_low
                    break

        if direction is None or trigger_bar is None:
            return None

        current_price = ctx.current_price
        # Guard: Price must still be inside or near the boundary zone
        if direction == "short" and current_price > (res + 0.5 * atr):
            return None
        if direction == "long" and current_price < (sup - 0.5 * atr):
            return None

        # ── Gate 4: Stop-Loss, Targets, and R:R Execution ───────────────────
        buffer = 0.25 * atr
        midpoint = (vp.poc if (vp and vp.poc and sup < vp.poc < res) else (res + sup) / 2.0)

        if direction == "short":
            stop_loss = sweep_extreme + buffer
            tp1 = midpoint
            tp2 = sup + (0.20 * atr)
            invalidation = f"Candle closes above sweep high {sweep_extreme:,.4f}"
            entry_low = min(current_price, float(trigger_bar["Close"]))
            entry_high = max(current_price, float(trigger_bar["Close"]))
        else:
            stop_loss = sweep_extreme - buffer
            tp1 = midpoint
            tp2 = res - (0.20 * atr)
            invalidation = f"Candle closes below sweep low {sweep_extreme:,.4f}"
            entry_low = min(current_price, float(trigger_bar["Close"]))
            entry_high = max(current_price, float(trigger_bar["Close"]))

        entry_mid = (entry_low + entry_high) / 2.0
        rr_tp1 = _calc_rr(entry_mid, stop_loss, tp1)
        rr_tp2 = _calc_rr(entry_mid, stop_loss, tp2)

        # Allow trade if TP1 gives favorable R:R or if full rotation to TP2 gives >= 2.0
        if rr_tp1 < _MIN_RR and rr_tp2 < 2.0:
            logger.debug(
                "[trader.strategy] %s: R:R insufficient (TP1=%.2f, TP2=%.2f)",
                ctx.ticker, rr_tp1, rr_tp2,
            )
            return None

        # ── Gate 5: Confluence Scoring (0.0 to 10.0 scale) ───────────────────
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

        # RSI Momentum Exhaustion Bonus
        rsi_series = ctx.rsi_14
        if rsi_series is not None and not rsi_series.empty:
            last_rsi = float(rsi_series.iloc[-1])
            if direction == "short" and last_rsi >= 65.0:
                report.score = min(report.score + 1.5, 10.0)
                report.sr_hits.append(f"RSI overbought ({last_rsi:.1f})")
            elif direction == "long" and last_rsi <= 35.0:
                report.score = min(report.score + 1.5, 10.0)
                report.sr_hits.append(f"RSI oversold ({last_rsi:.1f})")

        # Swept Liquidity Bonus from liq_list
        liq_kind = "eqh" if direction == "short" else "eql"
        swept_levels = [l for l in ctx.liq_list if l.swept and l.kind == liq_kind]
        if swept_levels:
            report.score = min(report.score + 1.0, 10.0)
            report.sr_hits.append(f"Swept {liq_kind.upper()} liquidity level")

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
