#!/usr/bin/env python3
"""
Trade + RSI Backtest Engine  -  finance/trader module
====================================================

Replays the scanner bar-by-bar on historical data with ZERO lookahead bias.
For each triggered setup, simulates outcome by checking which level was hit
first in the subsequent candles.

HOW IT WORKS
------------
check_all() accepts any DataFrame and always reads the *last* bar as "current".
To replay history we simply slice the DataFrame at each past bar:

    for bar_i in range(warmup, total_bars):
        ltf_slice = full_ltf.iloc[:bar_i]      # scanner sees only past data
        htf_slice = htf_before(full_htf, bar_ts)
        setups    = check_all(ticker, category, htf_slice, ltf_slice)
        outcome   = evaluate(setup, full_ltf.iloc[bar_i:bar_i+50])

USAGE
-----
From automation/app/ with venv active:

    python -m modules.finance.trader.backtest --ticker AAPL --category us_stock
    python -m modules.finance.trader.backtest --ticker BTC-USD --category crypto
    python -m modules.finance.trader.backtest --ticker AAPL --category us_stock --from 2026-01-01
    python -m modules.finance.trader.backtest --ticker AAPL --category us_stock --step 3 --verbose
    python -m modules.finance.trader.backtest --ticker AAPL --category us_stock --out results.csv

OPTIONS
-------
  --ticker    REQUIRED  Ticker symbol (e.g. AAPL, BTC-USD, RELIANCE.NS)
  --category  REQUIRED  us_stock | us_index | crypto | indian_stock | forex | commodity
  --from      Optional  Start date YYYY-MM-DD  (default: 6 months ago)
  --to        Optional  End date   YYYY-MM-DD  (default: today)
  --setup     Optional  Filter to one setup name
  --step      Optional  Scan every N bars, default 1 (use 3-5 for speed)
  --min-score Optional  Min confluence score, default 5.0
  --max-bars  Optional  Forward bars to evaluate outcome, default 50
  --out       Optional  CSV file path to save individual trades
  --verbose   Optional  Print individual trade table
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional, NamedTuple

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backtest")

# Provider interval hard limits
_YF_1M_MAX_DAYS  = 7      # 1m data only available for last 7 days
_YF_5M_MAX_DAYS  = 59     # 5m data only available for last 59 days
_YF_1H_MAX_DAYS  = 729    # 1H data only available for last 729 days
_LTF_WARMUP_DAYS = 90     # calendar-day warmup before date_from for LTF
_HTF_WARMUP_DAYS = 730    # HTF warmup (weekly/daily is unlimited)


def _interval_to_timedelta(interval: str) -> timedelta:
    """Return candle duration timedelta for lookahead-free candle closure verification."""
    inv = interval.lower().strip()
    if inv.endswith("m"):
        return timedelta(minutes=int(inv[:-1]))
    if inv.endswith("h"):
        return timedelta(hours=int(inv[:-1]))
    if inv.endswith("d"):
        return timedelta(days=int(inv[:-1]))
    if inv.endswith("w") or inv.endswith("wk"):
        val = inv[:-2] if inv.endswith("wk") else inv[:-1]
        return timedelta(weeks=int(val))
    return timedelta(hours=1)


def _max_days_for_interval(inv: str) -> int:
    """Return maximum historical days supported by yfinance for the interval."""
    inv = inv.lower().strip()
    if inv == "1m":
        return _YF_1M_MAX_DAYS
    if inv in ("2m", "5m", "15m", "30m"):
        return _YF_5M_MAX_DAYS
    if inv in ("60m", "1h", "4h"):
        return _YF_1H_MAX_DAYS
    return 3650


def _warmup_days_for_interval(inv: str) -> int:
    """Return required warmup calendar days for the interval."""
    inv = inv.lower().strip()
    if inv == "1m":
        return 2
    if inv in ("2m", "5m", "15m", "30m"):
        return 5
    if inv in ("60m", "1h", "4h"):
        return 60
    return _HTF_WARMUP_DAYS



# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class OutcomeEvaluation(NamedTuple):
    outcome:        str
    bars_to_result: int
    realised_rr:    float
    tp2:            Optional[float] = None
    exit_state:     str   = "OPEN"     # "FULL_WIN" | "SCRATCH_WIN" | "FULL_LOSS" | "TP1_OPEN" | "OPEN"
    tp1_hit:        bool  = False
    tp2_hit:        bool  = False
    be_hit:         bool  = False
    bars_to_tp1:    int   = -1
    bars_to_tp2:    int   = -1
    blended_rr:     float = 0.0        # OMS scaled: 50% @ TP1 + 50% @ (TP2 or BE)
    mfe_rr:         float = 0.0        # Maximum Favorable Excursion in R-units
    mae_rr:         float = 0.0        # Maximum Adverse Excursion in R-units
    tp_efficiency:  float = 0.0        # blended_rr / mfe_rr * 100%


@dataclass
class SetupResult:
    fired_at:       str
    fired_bar:      int
    setup_name:     str
    direction:      str
    bias_label:     str
    hidden_div:     str
    conf_score:     float
    conf_label:     str
    entry_mid:      float
    stop_loss:      float
    tp1:            float
    rr_ratio:       float
    regime:         str   = ""
    macro_regime:   str   = ""
    outcome:        str   = "OPEN"
    bars_to_result: int   = -1
    realised_rr:    float = 0.0

    # ── Multi-Stage TP & Excursion Extensions ──
    tp2:            Optional[float] = None
    exit_state:     str   = "OPEN"     # "FULL_WIN" | "SCRATCH_WIN" | "FULL_LOSS" | "TP1_OPEN" | "OPEN"
    tp1_hit:        bool  = False
    tp2_hit:        bool  = False
    be_hit:         bool  = False
    bars_to_tp1:    int   = -1
    bars_to_tp2:    int   = -1
    blended_rr:     float = 0.0
    mfe_rr:         float = 0.0
    mae_rr:         float = 0.0
    tp_efficiency:  float = 0.0


@dataclass
class BacktestReport:
    ticker:          str
    category:        str
    mode:            str
    date_from:       str
    date_to:         str
    total_bars:      int
    setups_found:    int
    wins:            int   = 0
    losses:          int   = 0
    open_:           int   = 0
    win_rate:        float = 0.0
    avg_rr_won:      float = 0.0
    avg_bars:        float = 0.0
    results:         list[SetupResult] = field(default_factory=list)

    # Multi-stage TP & Excursion aggregates
    tp1_wins:           int   = 0
    tp2_wins:           int   = 0
    be_scratches:       int   = 0
    runner_conv_pct:    float = 0.0
    blended_expectancy: float = 0.0
    avg_blended_won_rr: float = 0.0
    avg_mfe:            float = 0.0
    avg_mae:            float = 0.0
    avg_tp_efficiency:  float = 0.0
    avg_bars_tp1:       float = 0.0
    avg_bars_tp2:       float = 0.0
    avg_bars_sl:        float = 0.0

    def compute(self) -> None:
        closed = [r for r in self.results if r.outcome != "OPEN"]
        self.wins     = sum(1 for r in self.results if r.outcome == "WIN")
        self.losses   = sum(1 for r in self.results if r.outcome == "LOSS")
        self.open_    = sum(1 for r in self.results if r.outcome == "OPEN")
        self.win_rate = (self.wins / len(closed) * 100) if closed else 0.0
        won = [r for r in self.results if r.outcome == "WIN"]
        self.avg_rr_won = sum(r.realised_rr for r in won) / len(won) if won else 0.0
        bars_known = [r.bars_to_result for r in self.results if r.bars_to_result > 0]
        self.avg_bars = sum(bars_known) / len(bars_known) if bars_known else 0.0

        # Multi-stage TP & Excursion aggregates
        self.tp1_wins = sum(1 for r in self.results if r.tp1_hit)
        self.tp2_wins = sum(1 for r in self.results if r.tp2_hit)
        self.be_scratches = sum(1 for r in self.results if r.be_hit)
        self.runner_conv_pct = (self.tp2_wins / self.tp1_wins * 100.0) if self.tp1_wins else 0.0

        closed_blended = [r for r in self.results if r.exit_state in ("FULL_WIN", "SCRATCH_WIN", "FULL_LOSS")]
        self.blended_expectancy = (sum(r.blended_rr for r in closed_blended) / len(closed_blended)) if closed_blended else 0.0

        won_blended = [r for r in self.results if r.exit_state in ("FULL_WIN", "SCRATCH_WIN")]
        self.avg_blended_won_rr = (sum(r.blended_rr for r in won_blended) / len(won_blended)) if won_blended else 0.0

        mfe_known = [r.mfe_rr for r in self.results if r.mfe_rr > 0]
        self.avg_mfe = sum(mfe_known) / len(mfe_known) if mfe_known else 0.0

        mae_known = [r.mae_rr for r in self.results if r.mae_rr < 0]
        self.avg_mae = sum(mae_known) / len(mae_known) if mae_known else 0.0

        eff_known = [r.tp_efficiency for r in self.results if r.tp_efficiency > 0]
        self.avg_tp_efficiency = sum(eff_known) / len(eff_known) if eff_known else 0.0

        tp1_bars = [r.bars_to_tp1 for r in self.results if r.bars_to_tp1 > 0]
        self.avg_bars_tp1 = sum(tp1_bars) / len(tp1_bars) if tp1_bars else 0.0

        tp2_bars = [r.bars_to_tp2 for r in self.results if r.bars_to_tp2 > 0]
        self.avg_bars_tp2 = sum(tp2_bars) / len(tp2_bars) if tp2_bars else 0.0

        sl_bars = [r.bars_to_result for r in self.results if r.outcome == "LOSS" and r.bars_to_result > 0]
        self.avg_bars_sl = sum(sl_bars) / len(sl_bars) if sl_bars else 0.0


# ---------------------------------------------------------------------------
# Core Replay Engine
# ---------------------------------------------------------------------------

def run_backtest(
    ticker:           str,
    category:         str,
    date_from:        date,
    date_to:          date,
    setup_filter:     Optional[str] = None,
    step:             int   = 1,
    min_score:        float = 5.0,
    max_outcome_bars: int   = 50,
    disabled_setups:  Optional[set] = None,   # e.g. {"Liquidity Sweep + FVG"}
    style:            Optional[str] = None,   # "swing" | "intraday" | "scalping"
    regime_filter:    Optional[str] = None,   # e.g. "Trend", "Chop", "Squeeze"
    macro_filter:     Optional[str] = None,   # e.g. "bull", "bear", "ranging"
) -> BacktestReport:
    """Run bar-by-bar replay backtest across 3-tier multi-timeframe matrix."""
    from modules.finance.trader.schemas import scan_mode_for, timeframe_triad_for
    from modules.finance.trader.setups import check_all
    import modules.finance.trader.setups as setups_mod

    original_min_score    = setups_mod._MIN_SCORE
    original_disabled     = setups_mod.DISABLED_SETUPS.copy()
    setups_mod._MIN_SCORE = min_score

    # --disable flag: ADD to whatever the config already disables (not replace)
    effective_disabled = setups_mod.DISABLED_SETUPS.copy()
    if disabled_setups:
        effective_disabled |= disabled_setups
    setups_mod.DISABLED_SETUPS = effective_disabled

    try:
        mode = style if style else scan_mode_for(category)
        triad = timeframe_triad_for(mode)
        htf_tf = triad.htf
        mtf_tf = triad.mtf
        trigger_tf = triad.trigger

        # Lookback clamping to respect provider limits
        today = date.today()
        min_days_limit = min(
            _max_days_for_interval(triad.trigger),
            _max_days_for_interval(triad.mtf),
        )
        warmup_days = _warmup_days_for_interval(triad.trigger)
        earliest_allowed = today - timedelta(days=min_days_limit - warmup_days)
        if date_from < earliest_allowed:
            print(
                f"[NOTICE] Lookback clamped for interval constraints ({triad.trigger}/{triad.mtf}): "
                f"requested {date_from}, adjusted to {earliest_allowed} (max {min_days_limit} days supported)."
            )
            date_from = earliest_allowed
        if date_from >= date_to:
            date_from = date_to - timedelta(days=1)

        print("\n" + "=" * 60)
        print(f"Strategy Backtest: {ticker} | {mode.upper()} style")
        print(f"Period:  {date_from}  to  {date_to}")
        print(f"HTF (Where): {htf_tf.upper()}  |  MTF (When): {mtf_tf.upper()}  |  Trigger (How): {trigger_tf.upper()}")
        if macro_filter:
            print(f"Macro Filter : '{macro_filter.title()}' (evaluating all active strategies in this macro trend)")
        if regime_filter:
            print(f"Regime Filter: '{regime_filter}' (testing all active strategies in this regime)")
        if not macro_filter and not regime_filter:
            print("Regime Mode  : All macro trends (Bull/Bear/Ranging) & regimes tested (post-hoc breakdown)")
        print("=" * 60)

        from modules.finance.trader.setups import ALL_SETUP_NAMES
        active_setups   = [n for n in ALL_SETUP_NAMES if n not in effective_disabled]
        disabled_setups_list = [n for n in ALL_SETUP_NAMES if n in effective_disabled]
        print(f"Active setups  : {', '.join(active_setups) if active_setups else 'NONE'}")
        if disabled_setups_list:
            print(f"Disabled setups: {', '.join(disabled_setups_list)}")
        print()
        print("Loading historical data ...")
        full_trigger, full_mtf, full_htf = _download_full_triad_history(
            ticker, triad, date_from, date_to, category=category
        )

        if full_trigger is None or full_trigger.empty:
            print("[ERROR] No Trigger data available.")
            return _empty_report(ticker, category, mode, date_from, date_to)
        if full_mtf is None or full_mtf.empty:
            print("[ERROR] No MTF data available.")
            return _empty_report(ticker, category, mode, date_from, date_to)
        if full_htf is None or full_htf.empty:
            print("[ERROR] No HTF data available.")
            return _empty_report(ticker, category, mode, date_from, date_to)

        # Normalise indices to timezone-naive for safe comparison
        full_trigger = _strip_tz(full_trigger)
        full_mtf     = _strip_tz(full_mtf)
        full_htf     = _strip_tz(full_htf)

        # Find the first bar that is on or after date_from
        start_ts = datetime.combine(date_from, datetime.min.time())
        warmup   = 60   # minimum bars before we start scanning

        # Find replay start index: first bar >= date_from
        replay_start = None
        for i, ts in enumerate(full_trigger.index):
            if ts >= start_ts and i >= warmup:
                replay_start = i
                break

        if replay_start is None:
            print(f"[WARN] Not enough Trigger bars before {date_from} for warmup.")
            replay_start = min(warmup, len(full_trigger) - 1)

        end_ts = datetime.combine(date_to + timedelta(days=1), datetime.min.time())
        replay_end = len(full_trigger)
        for i, ts in enumerate(full_trigger.index):
            if ts >= end_ts:
                replay_end = i
                break

        n = len(full_trigger)
        total_replay_bars = max(0, replay_end - replay_start)
        print(f"Trigger bars total: {n} | replay window: {total_replay_bars} | step: {step}")

        # ── Pre-compute static indicator series on the FULL dataset (Issue 6 fix) ──
        print("  Pre-computing indicator series & market regimes...")
        from modules.finance.trader.regimes import (
            precompute_regime_indicators,
            classify_bar_regime,
            classify_macro_regime,
        )

        _close = full_trigger["Close"]

        # EMA 8/21 (used by EMA Pullback strategy)
        _ema8     = _close.ewm(span=8,  adjust=False).mean().rename("ema_8")
        _ema21    = _close.ewm(span=21, adjust=False).mean().rename("ema_21")

        full_trigger = precompute_regime_indicators(full_trigger)
        full_trigger["ema_8"]  = _ema8
        full_trigger["ema_21"] = _ema21
        print("  Indicator pre-computation done. Starting replay...")

        report = BacktestReport(
            ticker=ticker, category=category, mode=mode,
            date_from=str(date_from), date_to=str(date_to),
            total_bars=total_replay_bars, setups_found=0,
        )

        trigger_delta = _interval_to_timedelta(triad.trigger)
        mtf_delta     = _interval_to_timedelta(triad.mtf)
        htf_delta     = _interval_to_timedelta(triad.htf)

        # Dedup store: "setup_name|direction" -> last bar_i it fired
        dedup_last: dict[str, int] = {}
        bar_range   = range(replay_start, replay_end, step)
        total_steps = len(bar_range)

        for idx, bar_i in enumerate(bar_range):
            # Progress indicator (no special chars)
            if idx % max(1, total_steps // 20) == 0:
                pct = idx / total_steps * 100
                ts  = str(full_trigger.index[bar_i])[:16]
                print(f"  [{pct:4.0f}%] bar {bar_i}/{n}  {ts}", end="\r", flush=True)

            trigger_slice = full_trigger.iloc[:bar_i].copy()
            if len(trigger_slice) < warmup:
                continue

            trigger_close_ts = trigger_slice.index[-1] + trigger_delta

            # Zero-lookahead masking: only closed candles at or prior to trigger completion
            mtf_slice = full_mtf[full_mtf.index + mtf_delta <= trigger_close_ts].copy()
            htf_slice = full_htf[full_htf.index + htf_delta <= trigger_close_ts].copy()

            if len(htf_slice) < 20 or len(mtf_slice) < 20:
                continue

            try:
                triggered = check_all(
                    ticker,
                    category,
                    htf_slice,
                    trigger_slice,
                    df_mtf=mtf_slice,
                    df_trigger=trigger_slice,
                    mode=mode,
                )
            except Exception as e:
                logger.debug("check_all error at bar %d: %s", bar_i, e)
                continue

            for setup in triggered:
                if setup_filter and setup.setup_name != setup_filter:
                    continue

                # Classify prevailing market regime on trigger candle
                regime_info = classify_bar_regime(full_trigger, bar_i=bar_i - 1)
                active_regime = regime_info.name
                macro_reg = classify_macro_regime(setup.bias.bias, adx=regime_info.adx, micro_regime=active_regime)

                # Filter by macro trend if requested (e.g. 'bull', 'bear', 'ranging')
                if macro_filter and macro_filter.lower() != macro_reg.lower():
                    continue

                # Filter by micro regime name/code if requested (tests all strategies in this regime)
                if regime_filter and (
                    regime_filter.lower() not in active_regime.lower()
                    and regime_filter.lower() not in regime_info.code.lower()
                ):
                    continue

                dedup_key = f"{setup.setup_name}|{setup.direction}"
                last_bar  = dedup_last.get(dedup_key, -9999)
                if bar_i - last_bar < 24:
                    continue   # same setup fired within 24 bars — skip

                dedup_last[dedup_key] = bar_i
                report.setups_found  += 1

                future = full_trigger.iloc[bar_i: bar_i + max_outcome_bars]
                ev = _evaluate_outcome(setup, future)

                report.results.append(SetupResult(
                    fired_at=str(trigger_slice.index[-1])[:16],
                    fired_bar=bar_i,
                    setup_name=setup.setup_name,
                    direction=setup.direction,
                    bias_label=setup.bias.bias,
                    hidden_div=setup.bias.hidden_div,
                    conf_score=setup.confluence.score,
                    conf_label=setup.confluence.label,
                    entry_mid=round(setup.entry_mid, 4),
                    stop_loss=round(setup.stop_loss, 4),
                    tp1=round(setup.tp1, 4),
                    rr_ratio=round(setup.rr_ratio, 2),
                    regime=active_regime,
                    macro_regime=macro_reg,
                    outcome=ev.outcome,
                    bars_to_result=ev.bars_to_result,
                    realised_rr=round(ev.realised_rr, 2),
                    tp2=round(ev.tp2, 4) if ev.tp2 is not None else None,
                    exit_state=ev.exit_state,
                    tp1_hit=ev.tp1_hit,
                    tp2_hit=ev.tp2_hit,
                    be_hit=ev.be_hit,
                    bars_to_tp1=ev.bars_to_tp1,
                    bars_to_tp2=ev.bars_to_tp2,
                    blended_rr=round(ev.blended_rr, 2),
                    mfe_rr=round(ev.mfe_rr, 2),
                    mae_rr=round(ev.mae_rr, 2),
                    tp_efficiency=round(ev.tp_efficiency, 1),
                ))

        print(f"\n  Done. {report.setups_found} setups found across {total_replay_bars} bars.")
        report.compute()
        return report

    finally:
        setups_mod._MIN_SCORE   = original_min_score
        setups_mod.DISABLED_SETUPS = original_disabled

        if disabled_setups:
            print(f"  Disabled during run: {', '.join(sorted(disabled_setups))}")


def _empty_report(ticker, category, mode, date_from, date_to) -> BacktestReport:
    return BacktestReport(
        ticker=ticker, category=category, mode=mode,
        date_from=str(date_from), date_to=str(date_to),
        total_bars=0, setups_found=0,
    )


# ---------------------------------------------------------------------------
# Outcome Evaluation
# ---------------------------------------------------------------------------

def _evaluate_outcome(setup, future_bars) -> OutcomeEvaluation:
    """Evaluate multi-stage outcome (TP1 partial scale-out + BE stop + TP2 runner) and price excursions."""
    if future_bars is None or future_bars.empty:
        return OutcomeEvaluation(outcome="OPEN", bars_to_result=-1, realised_rr=0.0)

    entry = float(setup.entry_mid)
    sl    = float(setup.stop_loss)
    tp1   = float(setup.tp1)
    risk  = abs(entry - sl)

    if risk <= 1e-9:
        return OutcomeEvaluation(outcome="OPEN", bars_to_result=-1, realised_rr=0.0)

    # Derive or extract TP2
    setup_tp2 = getattr(setup, "tp2", None)
    if setup_tp2 is not None and abs(setup_tp2 - entry) > 1e-9:
        tp2 = float(setup_tp2)
    else:
        # Replicate OMS engine default (1.5x of TP1 distance from entry)
        tp1_dist = abs(tp1 - entry)
        tp2 = entry + (tp1_dist * 1.5 if setup.direction == "long" else -tp1_dist * 1.5)

    rr_tp1 = abs(tp1 - entry) / risk
    rr_tp2 = abs(tp2 - entry) / risk

    max_fav_r   = 0.0
    max_adv_r   = 0.0
    tp1_hit     = False
    tp2_hit     = False
    be_hit      = False
    bars_to_tp1 = -1
    bars_to_tp2 = -1
    bars_to_res = -1
    legacy_out  = "OPEN"
    legacy_rr   = 0.0
    exit_state  = "OPEN"
    blended_rr  = 0.0

    for i, (_, row) in enumerate(future_bars.iterrows(), start=1):
        hi = float(row["High"])
        lo = float(row["Low"])

        # Track Maximum Favorable (MFE) and Adverse (MAE) Excursions
        if setup.direction == "long":
            fav_r = (hi - entry) / risk
            adv_r = (entry - lo) / risk
        else:
            fav_r = (entry - lo) / risk
            adv_r = (hi - entry) / risk

        if fav_r > max_fav_r:
            max_fav_r = fav_r
        if adv_r > max_adv_r:
            max_adv_r = adv_r

        # --- Stage 1: Before TP1 is hit ---
        if not tp1_hit:
            if setup.direction == "long":
                c_tp1 = hi >= tp1
                c_sl  = lo <= sl
            else:
                c_tp1 = lo <= tp1
                c_sl  = hi >= sl

            # Ambiguous candle policy: conservative capital preservation assumes SL hit first
            if c_tp1 and c_sl:
                c_tp1 = False
                c_sl  = True

            if c_sl:
                legacy_out = "LOSS"
                legacy_rr  = -1.0
                blended_rr = -1.0
                exit_state = "FULL_LOSS"
                bars_to_res = i
                break

            if c_tp1:
                tp1_hit = True
                bars_to_tp1 = i
                bars_to_res = i
                legacy_out  = "WIN"
                legacy_rr   = rr_tp1

                # Check if TP2 was also reached on this same expansion candle
                same_tp2 = (hi >= tp2) if setup.direction == "long" else (lo <= tp2)
                if same_tp2:
                    tp2_hit = True
                    bars_to_tp2 = i
                    exit_state = "FULL_WIN"
                    blended_rr = (0.5 * rr_tp1) + (0.5 * rr_tp2)
                    break
                else:
                    # Runner continues into subsequent bars (identical to oms/engine.py)
                    continue

        # --- Stage 2: After TP1 is hit (50% runner targeting TP2 with SL at BE) ---
        else:
            if setup.direction == "long":
                r_tp2 = hi >= tp2
                r_be  = lo <= entry
            else:
                r_tp2 = lo <= tp2
                r_be  = hi >= entry

            if r_tp2 and r_be:
                # Ambiguous: conservative default assumes BE
                r_tp2 = False
                r_be  = True

            if r_tp2:
                tp2_hit = True
                bars_to_tp2 = i
                exit_state = "FULL_WIN"
                blended_rr = (0.5 * rr_tp1) + (0.5 * rr_tp2)
                break
            elif r_be:
                be_hit = True
                exit_state = "SCRATCH_WIN"
                blended_rr = 0.5 * rr_tp1
                break

    # If simulation period finished while trade was still running
    if tp1_hit and not tp2_hit and not be_hit:
        exit_state = "TP1_OPEN"
        last_close = float(future_bars.iloc[-1]["Close"])
        if setup.direction == "long":
            unrealized_r = (last_close - entry) / risk
        else:
            unrealized_r = (entry - last_close) / risk
        unrealized_r = max(0.0, unrealized_r)  # protected by breakeven stop
        blended_rr = (0.5 * rr_tp1) + (0.5 * unrealized_r)
    elif not tp1_hit and exit_state == "OPEN":
        legacy_out = "OPEN"
        legacy_rr  = 0.0
        blended_rr = 0.0
        exit_state = "OPEN"

    tp_eff = (blended_rr / max_fav_r * 100.0) if max_fav_r > 0 and blended_rr > 0 else 0.0

    return OutcomeEvaluation(
        outcome=legacy_out,
        bars_to_result=bars_to_res,
        realised_rr=round(legacy_rr, 4),
        tp2=round(tp2, 4),
        exit_state=exit_state,
        tp1_hit=tp1_hit,
        tp2_hit=tp2_hit,
        be_hit=be_hit,
        bars_to_tp1=bars_to_tp1,
        bars_to_tp2=bars_to_tp2,
        blended_rr=round(blended_rr, 4),
        mfe_rr=round(max_fav_r, 4),
        mae_rr=-round(max_adv_r, 4),
        tp_efficiency=round(tp_eff, 2),
    )


# ---------------------------------------------------------------------------
# Data Download
# ---------------------------------------------------------------------------

def _download_full_triad_history(
    ticker: str,
    triad: object,  # TimeframeTriad
    date_from: date,
    date_to: date,
    category: Optional[str] = None,
):
    """Download HTF, MTF, and Trigger history respecting yfinance interval limits."""
    today    = date.today()
    fetch_to = date_to + timedelta(days=1)

    def _dl(start: date, end: date, interval: str, label: str):
        """Download and normalise one OHLCV block, with local disk caching."""
        import os
        import pandas as pd
        
        cache_dir = os.path.join(os.path.dirname(__file__), ".backtest_cache")
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"{ticker}_{interval}_{start}_{end}.pkl")

        # 1. Try to load from cache
        if os.path.exists(cache_file):
            try:
                df = pd.read_pickle(cache_file)
                print(
                    f"  {label} ({interval}): {len(df)} bars  "
                    f"[{str(df.index[0])[:10]} -> {str(df.index[-1])[:10]}] (cached)"
                )
                return df
            except Exception as e:
                logger.debug("Failed to load cache %s: %s", cache_file, e)
                # Fall through to download

        # 2. Not in cache (or cache corrupt) — download via DataProvider
        try:
            from modules.finance.trader.data import get_provider
            provider = get_provider(category)
            start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
            end_dt   = datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc)
            df = provider.fetch_ohlcv(ticker, interval=interval, start=start_dt, end=end_dt)
        except Exception as e:
            logger.error("[backtest] DataProvider fetch failed for %s (%s): %s", ticker, interval, e)
            df = None

        if df is None or df.empty:
            print(f"[ERROR] No {label} data returned ({ticker} {interval}).")
            return None

        # Flatten MultiIndex columns produced by newer yfinance versions
        if isinstance(df.columns, pd.MultiIndex):
            # e.g. ("Close", "AAPL") -> "Close"
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

        # Normalise column names to Title case
        df.columns = [str(c).strip().title() for c in df.columns]

        # Keep only OHLCV
        keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        if len(keep) < 4:
            print(f"[ERROR] {label} DataFrame missing required columns. Got: {list(df.columns)}")
            return None

        df = df[keep].dropna()
        
        # 3. Save to cache
        try:
            df.to_pickle(cache_file)
        except Exception as e:
            logger.debug("Failed to write cache %s: %s", cache_file, e)
            
        print(
            f"  {label} ({interval}): {len(df)} bars  "
            f"[{str(df.index[0])[:10]} -> {str(df.index[-1])[:10]}] (downloaded)"
        )
        return df

    htf_warmup_start = max(date_from - timedelta(days=_warmup_days_for_interval(triad.htf)),
                           today - timedelta(days=_max_days_for_interval(triad.htf) - 1))
    mtf_warmup_start = max(date_from - timedelta(days=_warmup_days_for_interval(triad.mtf)),
                           today - timedelta(days=_max_days_for_interval(triad.mtf) - 1))
    trigger_warmup_start = max(date_from - timedelta(days=_warmup_days_for_interval(triad.trigger)),
                               today - timedelta(days=_max_days_for_interval(triad.trigger) - 1))

    df_htf     = _dl(htf_warmup_start, fetch_to, triad.htf, "HTF")
    df_mtf     = _dl(mtf_warmup_start, fetch_to, triad.mtf, "MTF")
    df_trigger = _dl(trigger_warmup_start, fetch_to, triad.trigger, "Trigger")

    return df_trigger, df_mtf, df_htf


def _download_full_history(
    ticker: str,
    mode: str,
    date_from: date,
    date_to: date,
    category: Optional[str] = None,
):
    """Backward compatible wrapper downloading 2-tier history."""
    from modules.finance.trader.schemas import timeframe_triad_for
    triad = timeframe_triad_for(mode)
    df_trigger, _, df_htf = _download_full_triad_history(ticker, triad, date_from, date_to, category=category)
    return df_trigger, df_htf


def _strip_tz(df):
    """Remove timezone info from a DatetimeIndex to avoid comparison errors."""
    if df is None or df.empty:
        return df
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df = df.copy()
        df.index = df.index.tz_localize(None)
    return df


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(report: BacktestReport, verbose: bool = False) -> None:
    sep = "-" * 60

    print("\n" + "=" * 60)
    print(f"  BACKTEST RESULTS -- {report.ticker}")
    print("=" * 60)
    print(f"  Mode:          {report.mode.upper()} ({report.date_from} to {report.date_to})")
    print(f"  Total bars:    {report.total_bars}")
    print(f"  Setups found:  {report.setups_found}")
    print(sep)

    if report.setups_found == 0:
        print("  No setups triggered in this period.")
        print("  Suggestions:")
        print("    - Lower --min-score to 4.0")
        print("    - Try --step 1 (scan every bar)")
        print("    - Widen the date range")
        print("=" * 60)
        return

    closed = report.wins + report.losses
    single_exp = 0.0
    if closed > 0:
        wr  = report.win_rate / 100
        single_exp = (wr * report.avg_rr_won) - ((1 - wr) * 1.0)

    print("  Single TP1 Benchmark (100% exit at TP1):")
    print(f"    WIN  (TP1 hit) : {report.wins:3d}  ({report.win_rate:.1f}%)")
    print(f"    LOSS (SL  hit) : {report.losses:3d}  ({(100 - report.win_rate):.1f}% of closed)")
    print(f"    OPEN (pending) : {report.open_:3d}")
    print(f"    Avg R:R (wins) : {report.avg_rr_won:.2f}x")
    print(f"    Avg bars/exit  : {report.avg_bars:.1f}")
    if closed > 0:
        print(f"    Expectancy     : {single_exp:+.2f}R per trade")

    # Multi-Stage OMS Scaled Execution summary
    closed_blended = [r for r in report.results if r.exit_state in ("FULL_WIN", "SCRATCH_WIN", "FULL_LOSS")]
    if closed_blended:
        full_wins = sum(1 for r in report.results if r.exit_state == "FULL_WIN")
        scratches = sum(1 for r in report.results if r.exit_state == "SCRATCH_WIN")
        losses    = sum(1 for r in report.results if r.exit_state == "FULL_LOSS")
        tp1_n     = report.tp1_wins
        print(f"\n  OMS Scaled Execution (50% TP1 + 50% TP2 / Breakeven SL):")
        print(f"    Full Win (TP1+TP2) : {full_wins:3d}  ({full_wins/len(closed_blended)*100:.1f}%)")
        print(f"    Scratch  (TP1+BE)  : {scratches:3d}  ({scratches/len(closed_blended)*100:.1f}%)")
        print(f"    Full Loss (SL)     : {losses:3d}  ({losses/len(closed_blended)*100:.1f}%)")
        print(f"    Runner Conv (TP2)  : {report.runner_conv_pct:.1f}% ({full_wins}/{tp1_n} TP1 winners reached TP2)")
        scratch_decay_pct = (100.0 - report.runner_conv_pct) if tp1_n else 0.0
        print(f"    BE Scratch Decay   : {scratch_decay_pct:.1f}% ({scratches}/{tp1_n} runners stopped at BE)")
        print(f"    Avg Blended Win RR : {report.avg_blended_won_rr:.2f}x")
        delta_str = f"({report.blended_expectancy - single_exp:+.2f}R delta vs single TP1)" if closed > 0 else ""
        print(f"    Blended Expectancy : {report.blended_expectancy:+.2f}R per trade  {delta_str}")

    # By setup
    print(f"\n  {'Setup':<30} {'N':>4} {'W':>4} {'WR%':>6} {'AvgRR':>7}")
    print(f"  {sep}")
    by_setup: dict[str, list] = {}
    for r in report.results:
        by_setup.setdefault(r.setup_name, []).append(r)
    for name, rows in by_setup.items():
        wins     = sum(1 for r in rows if r.outcome == "WIN")
        closed_n = sum(1 for r in rows if r.outcome != "OPEN")
        wr_pct   = wins / closed_n * 100 if closed_n else 0.0
        won      = [r for r in rows if r.outcome == "WIN"]
        avg_rr   = sum(r.realised_rr for r in won) / len(won) if won else 0.0
        print(f"  {name:<30} {len(rows):>4} {wins:>4} {wr_pct:>5.1f}% {avg_rr:>6.2f}x")

    # Multi-Stage Take-Profit & Runner Conversion Report
    print(f"\n  {'MULTI-STAGE TAKE-PROFIT & RUNNER CONVERSION REPORT':^95}")
    print(f"  {'='*95}")
    print(f"  {'Setup':<28} {'N':>4} {'TP1%':>6} {'TP2%':>6} {'Conv%':>7} {'BE%':>6} {'SingleExp':>10} {'ScaledExp':>10} {'Delta':>8}")
    print(f"  {'-'*95}")
    for name, rows in by_setup.items():
        n = len(rows)
        tp1_hits = sum(1 for r in rows if r.tp1_hit)
        tp2_hits = sum(1 for r in rows if r.tp2_hit)
        be_hits  = sum(1 for r in rows if r.be_hit)
        c_n = sum(1 for r in rows if r.outcome != "OPEN")
        tp1_pct = (tp1_hits / c_n * 100.0) if c_n else 0.0
        tp2_pct = (tp2_hits / c_n * 100.0) if c_n else 0.0
        conv_pct = (tp2_hits / tp1_hits * 100.0) if tp1_hits else 0.0
        be_pct   = (be_hits / tp1_hits * 100.0) if tp1_hits else 0.0

        w_rows = [r for r in rows if r.outcome == "WIN"]
        avg_rr_w = sum(r.realised_rr for r in w_rows) / len(w_rows) if w_rows else 0.0
        wr_r = tp1_pct / 100.0
        s_exp = (wr_r * avg_rr_w) - ((1.0 - wr_r) * 1.0) if c_n else 0.0

        c_blended = [r for r in rows if r.exit_state in ("FULL_WIN", "SCRATCH_WIN", "FULL_LOSS")]
        sc_exp = sum(r.blended_rr for r in c_blended) / len(c_blended) if c_blended else 0.0
        d_exp = sc_exp - s_exp

        print(
            f"  {name:<28} {n:>4} {tp1_pct:>5.1f}% {tp2_pct:>5.1f}% {conv_pct:>6.1f}% {be_pct:>5.1f}% "
            f"{s_exp:>+9.2f}R {sc_exp:>+9.2f}R {d_exp:>+7.2f}R"
        )

    # Excursion & Take-Profit Efficiency Report (MFE / MAE)
    print(f"\n  {'EXCURSION & TAKE-PROFIT EFFICIENCY REPORT (MFE / MAE)':^95}")
    print(f"  {'='*95}")
    print(f"  {'Setup':<28} {'AvgMFE':>8} {'AvgMAE':>8} {'TPEffic%':>9} {'BarsTP1':>8} {'BarsTP2':>8} {'BarsSL':>7}")
    print(f"  {'-'*95}")
    for name, rows in by_setup.items():
        mfe_vals = [r.mfe_rr for r in rows if r.mfe_rr > 0]
        avg_mfe = sum(mfe_vals) / len(mfe_vals) if mfe_vals else 0.0
        mae_vals = [r.mae_rr for r in rows if r.mae_rr < 0]
        avg_mae = sum(mae_vals) / len(mae_vals) if mae_vals else 0.0
        eff_vals = [r.tp_efficiency for r in rows if r.tp_efficiency > 0]
        avg_eff = sum(eff_vals) / len(eff_vals) if eff_vals else 0.0

        b_tp1 = [r.bars_to_tp1 for r in rows if r.bars_to_tp1 > 0]
        avg_b_tp1 = sum(b_tp1) / len(b_tp1) if b_tp1 else 0.0
        b_tp2 = [r.bars_to_tp2 for r in rows if r.bars_to_tp2 > 0]
        avg_b_tp2 = sum(b_tp2) / len(b_tp2) if b_tp2 else 0.0
        b_sl = [r.bars_to_result for r in rows if r.outcome == "LOSS" and r.bars_to_result > 0]
        avg_b_sl = sum(b_sl) / len(b_sl) if b_sl else 0.0

        print(
            f"  {name:<28} {avg_mfe:>+7.2f}R {avg_mae:>+7.2f}R {avg_eff:>8.1f}% "
            f"{avg_b_tp1:>8.1f} {avg_b_tp2:>8.1f} {avg_b_sl:>7.1f}"
        )

    # By Macro Trend (Bull / Bear / Ranging)
    print(f"\n  {'Macro Trend':<24} {'N':>4} {'W':>4} {'L':>4} {'WR%':>6} {'AvgRR':>7} {'Expectancy':>11}")
    print(f"  {sep}")
    by_macro: dict[str, list] = {}
    for r in report.results:
        by_macro.setdefault(r.macro_regime or "Unclassified", []).append(r)
    for m_name in ["Bull", "Bear", "Ranging"]:
        rows = by_macro.get(m_name, [])
        if not rows:
            continue
        wins     = sum(1 for r in rows if r.outcome == "WIN")
        losses   = sum(1 for r in rows if r.outcome == "LOSS")
        closed_n = wins + losses
        wr_pct   = (wins / closed_n * 100) if closed_n else 0.0
        won      = [r for r in rows if r.outcome == "WIN"]
        avg_rr   = sum(r.realised_rr for r in won) / len(won) if won else 0.0
        wr_ratio = wr_pct / 100.0
        exp_val  = (wr_ratio * avg_rr) - ((1.0 - wr_ratio) * 1.0) if closed_n else 0.0
        exp_str  = f"{exp_val:+.2f}R" if closed_n else "N/A"
        print(f"  {m_name:<24} {len(rows):>4} {wins:>4} {losses:>4} {wr_pct:>5.1f}% {avg_rr:>6.2f}x {exp_str:>11}")

    # Directional Alignment: Long vs Short in Bull / Bear / Ranging
    print(f"\n  Trade Direction vs Macro Trend (Pro-Trend vs Counter-Trend Alignment):")
    print(f"  {sep}")
    for dir_label in ["long", "short"]:
        dir_rows = [r for r in report.results if r.direction == dir_label]
        if not dir_rows:
            continue
        print(f"  • {dir_label.upper()} Trades (Total: {len(dir_rows)}):")
        for m_name in ["Bull", "Bear", "Ranging"]:
            matching = [r for r in dir_rows if r.macro_regime == m_name]
            if not matching:
                continue
            w = sum(1 for r in matching if r.outcome == "WIN")
            c = sum(1 for r in matching if r.outcome != "OPEN")
            wr = f"{w/c*100:.1f}%" if c else "0.0%"
            won_m = [r for r in matching if r.outcome == "WIN"]
            avg_rr_m = sum(r.realised_rr for r in won_m) / len(won_m) if won_m else 0.0
            alignment = " [Pro-Trend]" if (dir_label == "long" and m_name == "Bull") or (dir_label == "short" and m_name == "Bear") else (" [Counter-Trend]" if (dir_label == "long" and m_name == "Bear") or (dir_label == "short" and m_name == "Bull") else "")
            print(f"      - {m_name:<10} n={len(matching):2d}  wins={w:2d}  WR={wr:<6} (AvgRR: {avg_rr_m:.2f}x){alignment}")

    # Cross-Tab: Setup vs Macro Trend
    if len(by_setup) > 1 or len(by_macro) > 1:
        print(f"\n  Cross-Tab: Setup Performance by Macro Trend:")
        print(f"  {sep}")
        for sname in sorted(by_setup.keys()):
            s_rows = by_setup[sname]
            print(f"  • {sname}:")
            for m_name in ["Bull", "Bear", "Ranging"]:
                matching = [r for r in s_rows if r.macro_regime == m_name]
                if not matching:
                    continue
                w = sum(1 for r in matching if r.outcome == "WIN")
                c = sum(1 for r in matching if r.outcome != "OPEN")
                wr = f"{w/c*100:.1f}%" if c else "0.0%"
                won_m = [r for r in matching if r.outcome == "WIN"]
                avg_rr_m = sum(r.realised_rr for r in won_m) / len(won_m) if won_m else 0.0
                print(f"      - {m_name:<10} n={len(matching):2d}  wins={w:2d}  WR={wr:<6} (AvgRR: {avg_rr_m:.2f}x)")

    # By Market Regime (STRATEGY_PLAYBOOK.md Section 3)
    print(f"\n  {'Market Regime (Micro)':<32} {'N':>4} {'W':>4} {'L':>4} {'WR%':>6} {'AvgRR':>7} {'Expectancy':>11}")
    print(f"  {sep}")
    by_regime: dict[str, list] = {}
    for r in report.results:
        by_regime.setdefault(r.regime or "Unclassified", []).append(r)
    for reg_name, rows in sorted(by_regime.items(), key=lambda x: -len(x[1])):
        wins     = sum(1 for r in rows if r.outcome == "WIN")
        losses   = sum(1 for r in rows if r.outcome == "LOSS")
        closed_n = wins + losses
        wr_pct   = (wins / closed_n * 100) if closed_n else 0.0
        won      = [r for r in rows if r.outcome == "WIN"]
        avg_rr   = sum(r.realised_rr for r in won) / len(won) if won else 0.0
        wr_ratio = wr_pct / 100.0
        exp_val  = (wr_ratio * avg_rr) - ((1.0 - wr_ratio) * 1.0) if closed_n else 0.0
        exp_str  = f"{exp_val:+.2f}R" if closed_n else "N/A"
        print(f"  {reg_name:<32} {len(rows):>4} {wins:>4} {losses:>4} {wr_pct:>5.1f}% {avg_rr:>6.2f}x {exp_str:>11}")

    # Regime-Stratified Runner Conversion Report
    if by_regime:
        print(f"\n  {'REGIME-STRATIFIED RUNNER CONVERSION REPORT':^95}")
        print(f"  {'='*95}")
        print(f"  {'Market Regime (Micro)':<30} {'N':>4} {'TP1%':>6} {'TP2%':>6} {'Conv%':>7} {'BE%':>6} {'ScaledExp':>10} {'Optimal Policy':<22}")
        print(f"  {'-'*95}")
        for reg_name, rows in sorted(by_regime.items(), key=lambda x: -len(x[1])):
            n = len(rows)
            tp1_hits = sum(1 for r in rows if r.tp1_hit)
            tp2_hits = sum(1 for r in rows if r.tp2_hit)
            be_hits  = sum(1 for r in rows if r.be_hit)
            c_n = sum(1 for r in rows if r.outcome != "OPEN")
            tp1_pct = (tp1_hits / c_n * 100.0) if c_n else 0.0
            tp2_pct = (tp2_hits / c_n * 100.0) if c_n else 0.0
            conv_pct = (tp2_hits / tp1_hits * 100.0) if tp1_hits else 0.0
            be_pct   = (be_hits / tp1_hits * 100.0) if tp1_hits else 0.0

            c_blended = [r for r in rows if r.exit_state in ("FULL_WIN", "SCRATCH_WIN", "FULL_LOSS")]
            sc_exp = sum(r.blended_rr for r in c_blended) / len(c_blended) if c_blended else 0.0

            if conv_pct >= 50.0:
                policy = "50% TP1 + TP2 Runner"
            elif tp1_pct >= 35.0:
                policy = "100% Full Exit at TP1"
            else:
                policy = "Stand Aside / Filter"

            print(
                f"  {reg_name:<30} {n:>4} {tp1_pct:>5.1f}% {tp2_pct:>5.1f}% {conv_pct:>6.1f}% {be_pct:>5.1f}% "
                f"{sc_exp:>+9.2f}R {policy:<22}"
            )

    # Cross-Tab: Setup vs Market Regime
    if len(by_setup) > 1 or len(by_regime) > 1:
        print(f"\n  Cross-Tab: Setup Performance by Microstructure Regime:")
        print(f"  {sep}")
        for sname in sorted(by_setup.keys()):
            s_rows = by_setup[sname]
            print(f"  • {sname}:")
            for rname in sorted(by_regime.keys()):
                matching = [r for r in s_rows if r.regime == rname]
                if not matching:
                    continue
                w = sum(1 for r in matching if r.outcome == "WIN")
                c = sum(1 for r in matching if r.outcome != "OPEN")
                wr = f"{w/c*100:.1f}%" if c else "0.0%"
                won_m = [r for r in matching if r.outcome == "WIN"]
                avg_rr_m = sum(r.realised_rr for r in won_m) / len(won_m) if won_m else 0.0
                print(f"      - {rname:<30} n={len(matching):2d}  wins={w:2d}  WR={wr:<6} (AvgRR: {avg_rr_m:.2f}x)")

    # By bias
    print(f"\n  {'Bias':<22} {'N':>4} {'W':>4} {'WR%':>6}")
    by_bias: dict[str, list] = {}
    for r in report.results:
        by_bias.setdefault(r.bias_label, []).append(r)
    for label, rows in sorted(by_bias.items()):
        wins     = sum(1 for r in rows if r.outcome == "WIN")
        closed_n = sum(1 for r in rows if r.outcome != "OPEN")
        wr_pct   = wins / closed_n * 100 if closed_n else 0.0
        print(f"  {label:<22} {len(rows):>4} {wins:>4} {wr_pct:>5.1f}%")

    # By confidence
    print(f"\n  Confidence breakdown:")
    by_conf: dict[str, list] = {}
    for r in report.results:
        by_conf.setdefault(r.conf_label, []).append(r)
    for label in ["high", "medium", "low"]:
        rows = by_conf.get(label, [])
        if not rows:
            continue
        wins     = sum(1 for r in rows if r.outcome == "WIN")
        closed_n = sum(1 for r in rows if r.outcome != "OPEN")
        wr_pct   = wins / closed_n * 100 if closed_n else 0.0
        tag = {"high": "[H]", "medium": "[M]", "low": "[L]"}.get(label, "[ ]")
        print(f"  {tag} {label.upper():<8} n={len(rows):3d}  wins={wins:3d}  WR={wr_pct:.1f}%")

    if verbose and report.results:
        print(f"\n  Individual trades (last 30):")
        print(f"  {'Date':<17} {'Setup':<21} {'Macro':<8} {'Regime':<20} {'Dir':<5} {'Sc':>4} {'TP1':>5} {'Scale':>5} {'ExitState':<11} {'MFE':>6} {'MAE':>6} {'Bars':>5}")
        print(f"  {'-'*115}")
        for r in report.results[-30:]:
            tag = {"FULL_WIN": "[W2]", "SCRATCH_WIN": "[BE]", "FULL_LOSS": "[L ]", "TP1_OPEN": "[W?]", "OPEN": "[? ]"}.get(r.exit_state, "[  ]")
            reg_short = (r.regime[:18] + "..") if len(r.regime) > 20 else r.regime
            print(
                f"  {r.fired_at:<17} {r.setup_name:<21} {r.macro_regime:<8} {reg_short:<20} {r.direction:<5} "
                f"{r.conf_score:>4.1f} {r.rr_ratio:>4.1f}x {r.blended_rr:>4.1f}x {tag} {r.exit_state:<11} {r.mfe_rr:>+5.1f}R {r.mae_rr:>+5.1f}R {r.bars_to_result:>5}"
            )

    print("=" * 70 + "\n")


def save_csv(report: BacktestReport, path: str) -> None:
    import csv
    import dataclasses
    if not report.results:
        print("No results to save.")
        return
    fields = [f.name for f in dataclasses.fields(SetupResult)]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in report.results:
            writer.writerow(dataclasses.asdict(r))
    print(f"Saved {len(report.results)} rows to {path}")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trade + RSI backtest engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ticker",    required=True)
    parser.add_argument("--category",  required=True,
                        help="us_stock | us_index | crypto | indian_stock | forex | commodity")
    parser.add_argument("--from",      dest="date_from", default=None,
                        help="Start date YYYY-MM-DD (default: 6 months ago)")
    parser.add_argument("--to",        dest="date_to",   default=None,
                        help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--setup",     default=None,
                        help='Filter: "OB Retracement" | "Liquidity Sweep + FVG" | "Displacement + FVG"')
    parser.add_argument("--step",      type=int,   default=1,
                        help="Scan every N bars (default 1; use 3-5 for speed)")
    parser.add_argument("--min-score", type=float, default=5.0,
                        help="Min confluence score (default 5.0)")
    parser.add_argument("--max-bars",  type=int,   default=50,
                        help="Forward bars to evaluate outcome (default 50)")
    parser.add_argument("--disable",   nargs="+",  default=[],
                        metavar="SETUP",
                        help=(
                            'Disable one or more setups by name. '
                            'Example: --disable "Liquidity Sweep + FVG" "OB Retracement"'
                        ))
    parser.add_argument("--list-setups", action="store_true",
                        help="Print all available setup names and exit")
    parser.add_argument("--trend", "--macro-regime", dest="macro_trend", default=None,
                        choices=["bull", "bear", "ranging"],
                        help="Filter backtest to evaluate all strategies within a specific macro trend: bull | bear | ranging")
    parser.add_argument("--regime",    default=None,
                        help="Filter backtest to evaluate all strategies within a specific market regime")
    parser.add_argument("--list-regimes", action="store_true",
                        help="Print all market regimes and macro trends, then exit")
    parser.add_argument("--style",     default=None,
                        choices=["swing", "intraday", "scalping"],
                        help="Override trading style: swing | intraday | scalping (default: inferred from category)")
    parser.add_argument("--out",       default=None, help="Save trades to CSV path")
    parser.add_argument("--verbose",   action="store_true",
                        help="Print individual trade table")

    # Handle --list-setups before full parse so --ticker/--category aren't required
    if "--list-setups" in sys.argv:
        from modules.finance.trader.setups import ALL_SETUP_NAMES
        print("Available setups:")
        for name in ALL_SETUP_NAMES:
            print(f"  - {name}")
        sys.exit(0)

    # Handle --list-regimes before full parse
    if "--list-regimes" in sys.argv:
        from modules.finance.trader.regimes import ALL_REGIME_NAMES, ALL_MACRO_REGIMES
        print("\nMacro Trend Environments:")
        print("=" * 60)
        for i, m in enumerate(ALL_MACRO_REGIMES, 1):
            print(f"  {i}. {m} (filter via --trend {m.lower()})")
        print("\nMicrostructure Market Regimes (from STRATEGY_PLAYBOOK.md):")
        print("=" * 60)
        for i, name in enumerate(ALL_REGIME_NAMES, 1):
            print(f"  {i}. {name}")
        print("=" * 60)
        print("Note: In backtesting, all strategies are tested across all regimes without prohibition.")
        print("Empirical performance data will be used to calibrate router rules later.\n")
        sys.exit(0)

    args = parser.parse_args()

    # --list-setups: just print names and exit
    if args.list_setups:
        from modules.finance.trader.setups import ALL_SETUP_NAMES
        print("Available setups:")
        for name in ALL_SETUP_NAMES:
            print(f"  - {name}")
        sys.exit(0)

    today = date.today()
    date_from = (
        datetime.strptime(args.date_from, "%Y-%m-%d").date()
        if args.date_from else today - timedelta(days=180)
    )
    date_to = (
        datetime.strptime(args.date_to, "%Y-%m-%d").date()
        if args.date_to else today
    )

    if date_from >= date_to:
        print("[ERROR] --from must be before --to")
        sys.exit(1)

    report = run_backtest(
        ticker=args.ticker.upper(),
        category=args.category.lower(),
        date_from=date_from,
        date_to=date_to,
        setup_filter=args.setup,
        step=args.step,
        min_score=args.min_score,
        max_outcome_bars=args.max_bars,
        disabled_setups=set(args.disable) if args.disable else None,
        style=args.style,
        regime_filter=args.regime,
        macro_filter=args.macro_trend,
    )

    print_report(report, verbose=args.verbose)

    if args.out:
        save_csv(report, args.out)


if __name__ == "__main__":
    import os
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    main()
