"""Tests for routers/technical_ui.py and Wyckoff Pattern Progression Engine."""
import os
import sys
import numpy as np
import pandas as pd
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "app"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from core.db import init_db
from core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    _failed_attempts,
    _otp_store,
)
from main import app
from routers.technical_ui import _compute_extended_ta, _compute_wyckoff_structure
from modules.finance.technical.indicators import compute_ta


@pytest.fixture(autouse=True)
def setup_test_db():
    init_db()
    _failed_attempts.clear()
    _otp_store.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def auth_client(client):
    token = create_session_token("basheer")
    client.cookies.set(SESSION_COOKIE_NAME, token)
    return client


def _create_sample_ohlcv(n: int = 100, base_price: float = 100.0, trend: float = 0.0) -> pd.DataFrame:
    """Generate realistic OHLCV test dataframe."""
    np.random.seed(42)
    dates = pd.date_range(end="2026-03-01", periods=n, freq="D")
    closes = [base_price]
    for i in range(1, n):
        ret = np.random.normal(0.001 * trend, 0.015)
        closes.append(max(closes[-1] * (1.0 + ret), 1.0))
    closes = np.array(closes)
    highs = closes * (1.0 + np.abs(np.random.normal(0.005, 0.005, n)))
    lows = closes * (1.0 - np.abs(np.random.normal(0.005, 0.005, n)))
    opens = (closes + np.roll(closes, 1)) / 2.0
    opens[0] = closes[0]
    volume = np.random.randint(500000, 2000000, n).astype(float)
    return pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volume,
    }, index=dates)


class TestWyckoffStructureComputation:
    def test_wyckoff_structure_accumulation_defaults(self):
        df = _create_sample_ohlcv(100, base_price=130.0)
        res = _compute_wyckoff_structure(df, current_price=135.0, atr=2.5)

        assert res["valid"] is True
        assert res["state"] in ["accumulation", "distribution", "neutral"]
        assert "schematic_name" in res
        assert 0 <= res["progress_pct"] <= 100
        assert res["active_phase"] in ["A", "B", "C", "D", "E"]
        assert len(res["phases"]) == 5
        assert len(res["laws"]) == 3
        assert "pnf" in res
        assert res["pnf"]["horizontal_count"] >= 0

    def test_wyckoff_phase_progression_stepper(self):
        df = _create_sample_ohlcv(100, base_price=100.0)
        res = _compute_wyckoff_structure(df, current_price=105.0, atr=2.0)
        phases = res["phases"]

        assert phases[0]["key"] == "A"
        assert phases[1]["key"] == "B"
        assert phases[2]["key"] == "C"
        assert phases[3]["key"] == "D"
        assert phases[4]["key"] == "E"

        # Exactly one phase should be marked active
        active_phases = [p for p in phases if p["is_active"]]
        assert len(active_phases) == 1
        assert active_phases[0]["key"] == res["active_phase"]

    def test_wyckoff_laws_checklist(self):
        df = _create_sample_ohlcv(100, base_price=150.0)
        res = _compute_wyckoff_structure(df, current_price=152.0, atr=3.0)
        laws = res["laws"]

        assert len(laws) == 3
        assert "Supply & Demand" in laws[0]["name"]
        assert "Cause & Effect" in laws[1]["name"]
        assert "Effort vs. Result" in laws[2]["name"]
        for law in laws:
            assert "status" in law
            assert "desc" in law


class TestExtendedTAComputation:
    def test_moving_average_spectrum(self):
        df = _create_sample_ohlcv(220, base_price=100.0)
        ta = compute_ta(df, "NVDA", "1d")
        ext = _compute_extended_ta(df, ta)

        mas = ext["mas_table"]
        assert len(mas) == 6
        periods = [m["period"] for m in mas]
        assert "21" in periods
        assert "50" in periods
        assert "100" in periods
        assert "200" in periods

        # Momentum dict checks
        assert "macd" in ext
        assert "bb" in ext
        assert "bandwidth" in ext["bb"]
        assert "pct_b" in ext["bb"]
        assert ext["atr"] > 0


class TestTechnicalEndpoints:
    def test_unauthenticated_technical_redirects_to_login(self, client):
        # Browser request with Accept: text/html redirects to /login
        r = client.get("/technical", headers={"Accept": "text/html"}, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

        # Non-browser API request returns 401
        r_api = client.get("/technical")
        assert r_api.status_code == 401

    def test_authenticated_technical_page_renders_html(self, auth_client):
        sample_df = _create_sample_ohlcv(100, base_price=138.0)
        with patch("modules.finance.technical.charts.fetch_ohlcv_and_ta") as mock_fetch:
            ta = compute_ta(sample_df, "NVDA", "1d")
            mock_fetch.return_value = (sample_df, "1d", ta)

            r = auth_client.get("/technical?symbol=NVDA&tf=1d")
            assert r.status_code == 200
            assert "Technical & Wyckoff" in r.text
            assert "NVDA" in r.text
            assert "Wyckoff Pattern Progression" in r.text

    def test_api_technical_analyze_partial(self, auth_client):
        sample_df = _create_sample_ohlcv(100, base_price=138.0)
        with patch("modules.finance.technical.charts.fetch_ohlcv_and_ta") as mock_fetch:
            ta = compute_ta(sample_df, "NVDA", "1d")
            mock_fetch.return_value = (sample_df, "1d", ta)

            r = auth_client.get("/api/technical/analyze?symbol=NVDA&tf=1d")
            assert r.status_code == 200
            assert "NVDA" in r.text
            assert "Moving Average Spectrum" in r.text
            assert "Momentum & Volatility" in r.text
            assert "Multi-Extrema Divergence Engine" in r.text
            assert "Wyckoff Order Flow & P&F Engine" in r.text
            assert "PHASE A" in r.text
            assert "PHASE C" in r.text

    def test_api_technical_wyckoff_json(self, auth_client):
        sample_df = _create_sample_ohlcv(100, base_price=138.0)
        with patch("modules.finance.technical.charts.fetch_ohlcv_and_ta") as mock_fetch:
            ta = compute_ta(sample_df, "NVDA", "1d")
            mock_fetch.return_value = (sample_df, "1d", ta)

            r = auth_client.get("/api/technical/wyckoff?symbol=NVDA&tf=1d")
            assert r.status_code == 200
            data = r.json()
            assert data["symbol"] == "NVDA"
            assert "wyckoff" in data
            assert "progress_pct" in data["wyckoff"]
            assert "pnf" in data["wyckoff"]

    def test_api_technical_chart_image_streams_png(self, auth_client, tmp_path):
        dummy_png = tmp_path / "test_chart.png"
        dummy_png.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR...")

        with patch("modules.finance.technical.charts.fetch_and_render") as mock_render, \
             patch("modules.finance.technical.charts.fetch_ohlcv_and_ta") as mock_fetch:
            sample_df = _create_sample_ohlcv(100, base_price=138.0)
            ta = compute_ta(sample_df, "NVDA", "1d")
            mock_fetch.return_value = (sample_df, "1d", ta)
            mock_render.return_value = (str(dummy_png), ta)

            r = auth_client.get("/api/technical/chart-image?symbol=NVDA&tf=1d&wyckoff=1")
            assert r.status_code == 200
            assert r.headers["content-type"] == "image/png"
