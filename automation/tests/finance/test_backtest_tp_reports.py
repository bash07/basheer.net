"""Unit tests for Multi-Stage Take-Profit (TP1/TP2/BE) and Excursion (MFE/MAE) Reporting in backtest.py."""
import pandas as pd
import pytest
from dataclasses import dataclass
from modules.finance.trader.backtest import (
    _evaluate_outcome,
    OutcomeEvaluation,
    SetupResult,
    BacktestReport,
)


@dataclass
class DummySetup:
    entry_mid: float
    stop_loss: float
    tp1: float
    tp2: float | None = None
    direction: str = "long"


class TestMultiStageTPEvaluation:
    def test_full_runner_win_tp1_then_tp2(self):
        """Trade reaches TP1 on bar 1, moves SL to BE, then reaches TP2 on bar 2."""
        setup = DummySetup(entry_mid=100.0, stop_loss=90.0, tp1=120.0, tp2=140.0, direction="long")
        # risk = 10.0, rr_tp1 = 2.0, rr_tp2 = 4.0
        # Bar 1: reaches 125.0 (TP1 hit), low=98 (above initial SL 90)
        # Bar 2: reaches 145.0 (TP2 hit), low=105 (above BE 100)
        future_bars = pd.DataFrame([
            {"High": 125.0, "Low": 98.0, "Close": 122.0},
            {"High": 145.0, "Low": 105.0, "Close": 142.0},
        ])

        ev = _evaluate_outcome(setup, future_bars)
        assert ev.outcome == "WIN"
        assert ev.exit_state == "FULL_WIN"
        assert ev.tp1_hit is True
        assert ev.tp2_hit is True
        assert ev.be_hit is False
        assert ev.bars_to_tp1 == 1
        assert ev.bars_to_tp2 == 2
        # Blended RR: 0.5 * 2.0 + 0.5 * 4.0 = 3.0
        assert pytest.approx(ev.blended_rr, 0.01) == 3.0
        assert pytest.approx(ev.mfe_rr, 0.01) == 4.5  # (145 - 100)/10 = 4.5
        assert pytest.approx(ev.mae_rr, 0.01) == -0.2  # (100 - 98)/10 = -0.2

    def test_scratch_win_tp1_then_be_hit(self):
        """Trade reaches TP1 on bar 1, moves SL to BE, then drops to entry on bar 2."""
        setup = DummySetup(entry_mid=100.0, stop_loss=90.0, tp1=120.0, tp2=150.0, direction="long")
        # risk = 10.0, rr_tp1 = 2.0
        # Bar 1: reaches 122.0 (TP1 hit), low=96
        # Bar 2: pulls back to 99.0 (below entry 100.0 -> BE stop triggered)
        future_bars = pd.DataFrame([
            {"High": 122.0, "Low": 96.0, "Close": 118.0},
            {"High": 115.0, "Low": 99.0, "Close": 100.0},
        ])

        ev = _evaluate_outcome(setup, future_bars)
        assert ev.outcome == "WIN"
        assert ev.exit_state == "SCRATCH_WIN"
        assert ev.tp1_hit is True
        assert ev.tp2_hit is False
        assert ev.be_hit is True
        assert ev.bars_to_tp1 == 1
        # Blended RR: 0.5 * 2.0 + 0.5 * 0.0 = 1.0
        assert pytest.approx(ev.blended_rr, 0.01) == 1.0

    def test_full_loss_sl_hit_before_tp1(self):
        """Trade drops directly to Stop Loss without ever reaching TP1."""
        setup = DummySetup(entry_mid=100.0, stop_loss=95.0, tp1=110.0, tp2=120.0, direction="long")
        # risk = 5.0
        # Bar 1: chops between 96 and 102
        # Bar 2: drops to 94.0 (hits SL 95.0)
        future_bars = pd.DataFrame([
            {"High": 102.0, "Low": 96.0, "Close": 98.0},
            {"High": 99.0, "Low": 94.0, "Close": 94.5},
        ])

        ev = _evaluate_outcome(setup, future_bars)
        assert ev.outcome == "LOSS"
        assert ev.exit_state == "FULL_LOSS"
        assert ev.tp1_hit is False
        assert ev.tp2_hit is False
        assert ev.be_hit is False
        assert ev.blended_rr == -1.0
        assert ev.bars_to_result == 2

    def test_short_direction_tp1_and_tp2(self):
        """Short trade reaches TP1 on bar 1, then reaches TP2 on bar 2."""
        setup = DummySetup(entry_mid=100.0, stop_loss=110.0, tp1=80.0, tp2=60.0, direction="short")
        # risk = 10.0, rr_tp1 = 2.0, rr_tp2 = 4.0
        future_bars = pd.DataFrame([
            {"High": 102.0, "Low": 78.0, "Close": 82.0},  # Low 78 <= TP1 80
            {"High": 90.0, "Low": 55.0, "Close": 58.0},   # Low 55 <= TP2 60
        ])

        ev = _evaluate_outcome(setup, future_bars)
        assert ev.outcome == "WIN"
        assert ev.exit_state == "FULL_WIN"
        assert ev.tp1_hit is True
        assert ev.tp2_hit is True
        assert pytest.approx(ev.blended_rr, 0.01) == 3.0

    def test_ambiguous_candle_defaults_to_sl_first(self):
        """If a single candle touches both TP1 and SL, conservative default is SL first."""
        setup = DummySetup(entry_mid=100.0, stop_loss=90.0, tp1=120.0, tp2=140.0, direction="long")
        future_bars = pd.DataFrame([
            {"High": 125.0, "Low": 85.0, "Close": 100.0},
        ])

        ev = _evaluate_outcome(setup, future_bars)
        assert ev.outcome == "LOSS"
        assert ev.exit_state == "FULL_LOSS"
        assert ev.tp1_hit is False


class TestBacktestReportCompute:
    def test_report_compute_runner_conversion_and_expectancy(self):
        report = BacktestReport(
            ticker="TEST", category="crypto", mode="intraday",
            date_from="2026-01-01", date_to="2026-01-10",
            total_bars=500, setups_found=4,
        )

        r1 = SetupResult(
            fired_at="2026-01-01 10:00", fired_bar=10, setup_name="OB Retracement",
            direction="long", bias_label="bull", hidden_div="none", conf_score=7.0,
            conf_label="high", entry_mid=100.0, stop_loss=90.0, tp1=120.0, rr_ratio=2.0,
            outcome="WIN", bars_to_result=3, realised_rr=2.0,
            exit_state="FULL_WIN", tp1_hit=True, tp2_hit=True, be_hit=False,
            bars_to_tp1=2, bars_to_tp2=4, blended_rr=3.0, mfe_rr=4.0, mae_rr=-0.3, tp_efficiency=75.0,
        )
        r2 = SetupResult(
            fired_at="2026-01-02 10:00", fired_bar=20, setup_name="OB Retracement",
            direction="long", bias_label="bull", hidden_div="none", conf_score=7.0,
            conf_label="high", entry_mid=100.0, stop_loss=90.0, tp1=120.0, rr_ratio=2.0,
            outcome="WIN", bars_to_result=2, realised_rr=2.0,
            exit_state="SCRATCH_WIN", tp1_hit=True, tp2_hit=False, be_hit=True,
            bars_to_tp1=2, bars_to_tp2=-1, blended_rr=1.0, mfe_rr=2.2, mae_rr=-0.5, tp_efficiency=45.5,
        )
        r3 = SetupResult(
            fired_at="2026-01-03 10:00", fired_bar=30, setup_name="OB Retracement",
            direction="long", bias_label="bull", hidden_div="none", conf_score=6.0,
            conf_label="medium", entry_mid=100.0, stop_loss=90.0, tp1=120.0, rr_ratio=2.0,
            outcome="LOSS", bars_to_result=4, realised_rr=-1.0,
            exit_state="FULL_LOSS", tp1_hit=False, tp2_hit=False, be_hit=False,
            bars_to_tp1=-1, bars_to_tp2=-1, blended_rr=-1.0, mfe_rr=0.5, mae_rr=-1.0, tp_efficiency=0.0,
        )
        r4 = SetupResult(
            fired_at="2026-01-04 10:00", fired_bar=40, setup_name="OB Retracement",
            direction="long", bias_label="bull", hidden_div="none", conf_score=6.0,
            conf_label="medium", entry_mid=100.0, stop_loss=90.0, tp1=120.0, rr_ratio=2.0,
            outcome="LOSS", bars_to_result=5, realised_rr=-1.0,
            exit_state="FULL_LOSS", tp1_hit=False, tp2_hit=False, be_hit=False,
            bars_to_tp1=-1, bars_to_tp2=-1, blended_rr=-1.0, mfe_rr=0.2, mae_rr=-1.0, tp_efficiency=0.0,
        )

        report.results = [r1, r2, r3, r4]
        report.compute()

        # Legacy Win Rate: 2 wins / 4 closed = 50.0%
        assert report.win_rate == 50.0
        assert report.tp1_wins == 2
        assert report.tp2_wins == 1
        assert report.be_scratches == 1
        # Runner conversion: 1 out of 2 TP1 winners reached TP2 = 50.0%
        assert report.runner_conv_pct == 50.0
        # Blended Expectancy: (3.0 + 1.0 - 1.0 - 1.0) / 4 = 2.0 / 4 = +0.50R
        assert pytest.approx(report.blended_expectancy, 0.01) == 0.50
        # Avg MFE: (4.0 + 2.2 + 0.5 + 0.2) / 4 = 1.725R
        assert pytest.approx(report.avg_mfe, 0.01) == 1.725
