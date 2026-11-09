"""Automated test suite for Web UI, Security, 2FA, CSRF, and Routers."""
import os
import sys
import pytest
from fastapi.testclient import TestClient

# Ensure automation/app is on path
APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from config import config
from core.db import init_db
from core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    generate_csrf_token,
    _otp_store,
    _failed_attempts,
    scrub_secrets,
)
from main import app


@pytest.fixture(autouse=True)
def setup_test_db():
    init_db()
    _failed_attempts.clear()
    _otp_store.clear()
    yield


@pytest.fixture
def client():
    # Use TestClient without running full lifespan background loops during unit tests
    return TestClient(app, raise_server_exceptions=False)


def test_public_health(client):
    """Health check must always be public and 200 OK."""
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_root_browser_redirect(client):
    """Browser request to / redirects to /trader."""
    r = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/trader"


def test_unauthenticated_page_redirects_to_login(client):
    """Unauthenticated browser request to /trader redirects to /login."""
    r = client.get("/trader", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_login_page_renders(client):
    """Login view must render 200 OK."""
    r = client.get("/login")
    assert r.status_code == 200
    assert "ai.basheer.net" in r.text
    assert "Master Passphrase" in r.text


def test_auth_step1_wrong_passphrase(client):
    """Wrong passphrase returns 401."""
    r = client.post("/api/auth/request-otp", json={"passphrase": "wrong-password-123"})
    assert r.status_code == 401


def test_auth_full_2fa_flow(client):
    """Step 1 request-otp + Step 2 verify-otp unlocks terminal."""
    passphrase = config.dashboard_passphrase or "basheer2026!"
    r1 = client.post("/api/auth/request-otp", json={"passphrase": passphrase})
    assert r1.status_code == 200
    assert r1.json()["status"] == "otp_sent"

    # Inspect generated OTP code in test environment
    ip = "testclient"
    assert ip in _otp_store
    code = _otp_store[ip]["code"]

    # Step 2: Verify with correct code
    r2 = client.post("/api/auth/verify-otp", json={"code": code})
    assert r2.status_code == 200
    assert r2.json()["status"] == "authenticated"
    assert SESSION_COOKIE_NAME in r2.cookies

    # Now verify /api/auth/me reports authenticated
    client.cookies.set(SESSION_COOKIE_NAME, r2.cookies[SESSION_COOKIE_NAME])
    r3 = client.get("/api/auth/me")
    assert r3.status_code == 200
    assert r3.json()["authenticated"] is True


def test_authenticated_pages_render(client):
    """All main dashboard views render 200 OK for authenticated user."""
    session_token = create_session_token("basheer")
    client.cookies.set(SESSION_COOKIE_NAME, session_token)

    pages = [
        "/trader",
        "/watchlist",
        "/technical",
        "/screener",
        "/fundamental",
        "/blog",
        "/monitor",
        "/ops",
        "/chat",
    ]

    for p in pages:
        r = client.get(p)
        assert r.status_code == 200, f"Page {p} returned status {r.status_code}"
        assert "ai.basheer.net" in r.text


def test_csrf_protection_on_mutations(client):
    """State-changing mutations without valid CSRF token are blocked with 403."""
    session_token = create_session_token("basheer")
    client.cookies.set(SESSION_COOKIE_NAME, session_token)

    # Attempt mutation without CSRF header
    r = client.post("/api/watchlist/add", data={"ticker": "TESTSYM", "category": "crypto"})
    assert r.status_code == 403

    # Attempt mutation with valid CSRF token
    valid_csrf = generate_csrf_token(session_token)
    r2 = client.post(
        "/api/watchlist/add",
        data={"ticker": "TESTSYM", "category": "crypto"},
        headers={"X-CSRF-Token": valid_csrf},
    )
    assert r2.status_code in (200, 303)


def test_secret_scrubber():
    """Verify secrets are redacted."""
    raw = f"Connected with token {config.gemini_api_key} on bot"
    scrubbed = scrub_secrets(raw)
    assert config.gemini_api_key not in scrubbed or not config.gemini_api_key
    assert "***REDACTED***" in scrubbed if config.gemini_api_key else True


def test_htmx_partials_render(client):
    """HTMX partial endpoints return valid HTML fragments."""
    session_token = create_session_token("basheer")
    client.cookies.set(SESSION_COOKIE_NAME, session_token)

    # 1. Watchlist prices table partial
    r1 = client.get("/api/watchlist/prices?category=all")
    assert r1.status_code == 200
    assert "prices-tbody" in r1.text

    # 2. Ticker tape partial (publicly accessible header tape)
    r2 = client.get("/api/watchlist/ticker-tape")
    assert r2.status_code == 200

    # 3. Trader positions partial
    r3 = client.get("/api/trader/positions")
    assert r3.status_code == 200
    assert "positions-tbody" in r3.text

    # 4. Trader setups partial
    r4 = client.get("/api/trader/setups")
    assert r4.status_code == 200

    # 5. Ops logs partial
    r5 = client.get("/api/ops/logs")
    assert r5.status_code == 200
    assert "font-mono" in r5.text

    # 6. Blog ideas JSON endpoint
    r6 = client.get("/api/blog/ideas")
    assert r6.status_code == 200
    assert "ideas" in r6.json()


def test_watchlist_and_alerts_mutations_flow(client):
    """Test full cycle: add symbol, create alert, delete alert, delete symbol."""
    session_token = create_session_token("basheer")
    client.cookies.set(SESSION_COOKIE_NAME, session_token)
    csrf = generate_csrf_token(session_token)

    # Add Ticker
    r1 = client.post(
        "/api/watchlist/add",
        data={"ticker": "AAPL", "category": "us_stock"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r1.status_code == 200

    # Create static alert
    r2 = client.post(
        "/api/watchlist/alert",
        data={
            "ticker": "AAPL",
            "alert_type": "static",
            "condition": ">",
            "target_price": "250.0",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r2.status_code == 200

    # Create trailing alert
    r3 = client.post(
        "/api/watchlist/alert",
        data={
            "ticker": "AAPL",
            "alert_type": "trailing",
            "trail_pct": "-5.0",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r3.status_code == 200

    # Verify alerts exist via DB or page
    from core.db import get_db
    from modules.finance.watchlist.models import PriceAlert
    with get_db() as db:
        alerts = db.query(PriceAlert).filter(PriceAlert.symbol == "AAPL").all()
        assert len(alerts) >= 2
        alert_id = alerts[0].id

    # Delete alert
    r4 = client.post(
        f"/api/watchlist/alert/delete/{alert_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert r4.status_code == 200

    # Delete ticker (cascades alerts)
    r5 = client.post(
        "/api/watchlist/delete/AAPL",
        headers={"X-CSRF-Token": csrf},
    )
    assert r5.status_code == 200


def test_logout_clears_cookie(client):
    """Logout invalidates cookie and redirects to /login."""
    session_token = create_session_token("basheer")
    client.cookies.set(SESSION_COOKIE_NAME, session_token)

    r = client.post("/api/auth/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"

