"""Dynamic Trailing Stop Loss Manager for the Order Management System (OMS).

Implements Plan 2 of the Future Expansion Roadmap:
  - Activation Trigger at +1.5R favorable price movement.
  - Chandelier Exit (Peak Price - k * ATR_14) and EMA-21 trailing stops.
  - Monotonic ratchet guarantee (stop loss can only tighten, never loosen).
  - Breakeven floor/ceiling guarantee once +1.5R is unlocked.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

from ..models import Position

logger = logging.getLogger(__name__)


class TrailingStopManager:
    """Evaluates and ratchets dynamic trailing stop loss levels for open positions."""

    def __init__(
        self,
        enabled: bool = True,
        style: str = "chandelier",  # "chandelier" | "ema21" | "r_multiple"
        activation_r: float = 1.5,
        atr_multiplier: float = 2.0,
        atr_period: int = 14,
    ) -> None:
        self.enabled = enabled
        self.style = style.lower().strip()
        self.activation_r = activation_r
        self.atr_multiplier = atr_multiplier
        self.atr_period = atr_period
        self._load_config_overrides()

    def _load_config_overrides(self) -> None:
        """Load trailing stop settings from trader_config.json."""
        try:
            from ..manage import load_config
            cfg = load_config().get("oms", {})
            if "trailing_stop_enabled" in cfg:
                self.enabled = bool(cfg["trailing_stop_enabled"])
            if "trailing_stop_style" in cfg:
                self.style = str(cfg["trailing_stop_style"]).lower().strip()
            if "trailing_activation_r" in cfg:
                self.activation_r = float(cfg["trailing_activation_r"])
            if "trailing_atr_multiplier" in cfg:
                self.atr_multiplier = float(cfg["trailing_atr_multiplier"])
            if "trailing_atr_period" in cfg:
                self.atr_period = int(cfg["trailing_atr_period"])
        except Exception as e:
            logger.debug("[oms.trailing] Could not load config overrides: %s", e)

    def evaluate_bar(
        self,
        pos: Position,
        bar: Any,
        df: Optional[pd.DataFrame] = None,
    ) -> tuple[float, bool, str]:
        """Evaluate a price bar and ratchet trailing stop if appropriate.

        Args:
            pos: Open Position instance.
            bar: Current candle (pd.Series or mapping with 'High', 'Low', 'Close').
            df: Optional OHLCV DataFrame for indicator calculation (ATR, EMA).

        Returns:
            (new_stop_loss, was_ratcheted, reason_message)
        """
        if not self.enabled:
            return pos.stop_loss, False, "Trailing stop disabled"

        try:
            bar_high = float(bar["High"])
            bar_low = float(bar["Low"])
            bar_close = float(bar.get("Close", (bar_high + bar_low) / 2))
        except (KeyError, TypeError, ValueError) as err:
            return pos.stop_loss, False, f"Invalid bar data: {err}"

        # 1. Determine Initial Risk Distance R
        initial_sl = pos.initial_stop_loss or pos.stop_loss
        unit_risk = abs(pos.entry_price - initial_sl)
        if unit_risk <= 1e-9:
            unit_risk = max(pos.entry_price * 0.01, 1e-4)

        # 2. Check Activation at +1.5R
        if not pos.trailing_stop_active:
            if pos.direction == "long":
                target_trigger = pos.entry_price + (self.activation_r * unit_risk)
                if bar_high >= target_trigger:
                    pos.trailing_stop_active = True
                    pos.peak_price = bar_high
                    logger.info(
                        "[oms.trailing] Position #%d unlocked trailing stop (+%.2fR reached: %.4f >= %.4f)",
                        pos.id, self.activation_r, bar_high, target_trigger,
                    )
            else:
                target_trigger = pos.entry_price - (self.activation_r * unit_risk)
                if bar_low <= target_trigger:
                    pos.trailing_stop_active = True
                    pos.peak_price = bar_low
                    logger.info(
                        "[oms.trailing] Position #%d unlocked trailing stop (+%.2fR reached: %.4f <= %.4f)",
                        pos.id, self.activation_r, bar_low, target_trigger,
                    )

        if not pos.trailing_stop_active:
            return pos.stop_loss, False, "Trailing stop threshold not yet reached"

        # 3. Update Peak Price
        if pos.direction == "long":
            pos.peak_price = max(pos.peak_price or pos.entry_price, bar_high)
        else:
            pos.peak_price = min(pos.peak_price or pos.entry_price, bar_low)

        # 4. Compute Candidate Trailing Level
        candidate_sl = self._calculate_candidate(pos, bar, df, unit_risk)
        if candidate_sl is None:
            return pos.stop_loss, False, "Could not compute candidate stop"

        # 5. Monotonic Ratchet Guarantee & Breakeven Floor/Ceiling
        if pos.direction == "long":
            # Must not be below breakeven (entry_price)
            candidate_sl = max(candidate_sl, pos.entry_price)
            candidate_sl = round(candidate_sl, 4)
            if candidate_sl > pos.stop_loss:
                old_sl = pos.stop_loss
                pos.stop_loss = candidate_sl
                msg = f"Ratcheted Long SL from {old_sl:.4f} to {candidate_sl:.4f} via {self.style}"
                logger.info("[oms.trailing] Position #%d %s", pos.id, msg)
                return candidate_sl, True, msg
        else:
            # Must not be above breakeven (entry_price)
            candidate_sl = min(candidate_sl, pos.entry_price)
            candidate_sl = round(candidate_sl, 4)
            if candidate_sl < pos.stop_loss:
                old_sl = pos.stop_loss
                pos.stop_loss = candidate_sl
                msg = f"Ratcheted Short SL from {old_sl:.4f} to {candidate_sl:.4f} via {self.style}"
                logger.info("[oms.trailing] Position #%d %s", pos.id, msg)
                return candidate_sl, True, msg

        return pos.stop_loss, False, "Stop held at existing level"

    def _calculate_candidate(
        self,
        pos: Position,
        bar: Any,
        df: Optional[pd.DataFrame],
        unit_risk: float,
    ) -> Optional[float]:
        """Compute candidate stop price based on selected strategy."""
        peak = pos.peak_price or pos.entry_price

        # Strategy A: Chandelier Exit (Peak - k * ATR)
        if self.style == "chandelier":
            atr = self._get_atr(df, unit_risk)
            if pos.direction == "long":
                return peak - (self.atr_multiplier * atr)
            else:
                return peak + (self.atr_multiplier * atr)

        # Strategy B: EMA-21 Trailing Stop
        elif self.style == "ema21":
            ema_val = self._get_ema21(df)
            if ema_val is not None:
                return ema_val
            # Fallback to Chandelier if EMA not calculable
            atr = self._get_atr(df, unit_risk)
            return peak - (self.atr_multiplier * atr) if pos.direction == "long" else peak + (self.atr_multiplier * atr)

        # Strategy C: Fixed R-multiple trail (trails 1R behind peak)
        elif self.style == "r_multiple":
            if pos.direction == "long":
                return peak - unit_risk
            else:
                return peak + unit_risk

        return None

    def _get_atr(self, df: Optional[pd.DataFrame], unit_risk: float) -> float:
        """Extract or compute ATR-14, falling back to 50% unit risk."""
        if df is not None and not df.empty and len(df) >= self.atr_period:
            try:
                from modules.finance._shared.indicators import calc_atr
                val = calc_atr(df, self.atr_period)
                if val and val > 0:
                    return float(val)
            except Exception:
                pass
        return max(unit_risk * 0.5, 1e-4)

    def _get_ema21(self, df: Optional[pd.DataFrame]) -> Optional[float]:
        """Compute latest EMA-21 from OHLCV DataFrame."""
        if df is not None and not df.empty and len(df) >= 21 and "Close" in df.columns:
            try:
                from modules.finance._shared.indicators import calc_ema
                ema_s = calc_ema(df["Close"], span=21)
                if not ema_s.empty:
                    val = float(ema_s.iloc[-1])
                    if val > 0:
                        return val
            except Exception:
                pass
        return None
