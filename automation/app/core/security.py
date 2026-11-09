"""Zero-Trust Security, Telegram 2FA OTP, CSRF & Session Management."""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time
from typing import Optional

import httpx
from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse

from config import config

logger = logging.getLogger(__name__)

# ── In-Memory Auth & Rate Limiting Stores ──────────────────────────────────────
# Rate limiting: IP -> list of failed attempt timestamps
_failed_attempts: dict[str, list[float]] = {}
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 900  # 15 minutes

# OTP storage: IP -> {"code": "123456", "expires_at": 123456789.0}
_otp_store: dict[str, dict] = {}
OTP_VALIDITY_SECONDS = 300  # 5 minutes

SESSION_COOKIE_NAME = "session_token"
CSRF_HEADER_NAME = "X-CSRF-Token"


# ── Rate Limiting ─────────────────────────────────────────────────────────────

def get_client_ip(request: Request) -> str:
    """Extract client IP respecting X-Forwarded-For if behind Caddy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_rate_limited(ip: str) -> bool:
    """Return True if IP has exceeded failed attempts in the lockout window."""
    now = time.time()
    attempts = [t for t in _failed_attempts.get(ip, []) if now - t < LOCKOUT_WINDOW_SECONDS]
    _failed_attempts[ip] = attempts
    return len(attempts) >= MAX_FAILED_ATTEMPTS


def record_failed_attempt(ip: str) -> None:
    now = time.time()
    attempts = [t for t in _failed_attempts.get(ip, []) if now - t < LOCKOUT_WINDOW_SECONDS]
    attempts.append(now)
    _failed_attempts[ip] = attempts


def reset_failed_attempts(ip: str) -> None:
    _failed_attempts.pop(ip, None)


# ── Passphrase Verification ───────────────────────────────────────────────────

def verify_passphrase(candidate: str) -> bool:
    """Verify master passphrase using timing-safe comparison."""
    if not candidate:
        return False
    expected = config.dashboard_passphrase or config.webhook_secret or "basheer2026!"
    return hmac.compare_digest(candidate.strip(), expected.strip())


# ── Telegram 2FA OTP ──────────────────────────────────────────────────────────

async def dispatch_telegram_otp(code: str) -> bool:
    """Send 6-digit OTP code to the owner's Telegram chat."""
    if not config.telegram_bot_token or not config.telegram_chat_id:
        logger.warning("[security] Telegram credentials not configured. OTP: %s (logged for dev)", code)
        return False

    message = (
        f"🔐 *ai.basheer.net Login Verification*\n\n"
        f"Your 6-digit access code is:\n"
        f"👉 `{code}`\n\n"
        f"_This code expires in 5 minutes. Do not share._"
    )

    # Try sending via running Telegram bot_app if available
    try:
        from core.bot import bot_app
        if bot_app and bot_app.bot:
            await bot_app.bot.send_message(
                chat_id=config.telegram_chat_id,
                text=message,
                parse_mode="Markdown",
            )
            return True
    except Exception as e:
        logger.debug("[security] Failed sending via bot_app, falling back to httpx: %s", e)

    # Direct httpx fallback
    try:
        url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": config.telegram_chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                },
            )
            return resp.status_code == 200
    except Exception as exc:
        logger.error("[security] Failed to send Telegram OTP: %s", exc)
        return False


async def generate_and_send_otp(ip: str) -> tuple[bool, str]:
    """Generate 6-digit code, store with expiry, and dispatch to Telegram."""
    if is_rate_limited(ip):
        return False, "Too many failed attempts. Locked for 15 minutes."

    # Cryptographically secure 6-digit number
    code = f"{secrets.randbelow(900000) + 100000}"
    _otp_store[ip] = {
        "code": code,
        "expires_at": time.time() + OTP_VALIDITY_SECONDS,
    }

    sent = await dispatch_telegram_otp(code)
    if sent:
        return True, "Verification code sent to your Telegram."
    return True, f"Code generated (dev mode/fallback code active)."


def verify_otp_code(ip: str, candidate_code: str) -> bool:
    """Validate 6-digit OTP code against store."""
    record = _otp_store.get(ip)
    if not record:
        return False

    if time.time() > record["expires_at"]:
        _otp_store.pop(ip, None)
        return False

    if hmac.compare_digest(candidate_code.strip(), record["code"].strip()):
        _otp_store.pop(ip, None)
        reset_failed_attempts(ip)
        return True

    record_failed_attempt(ip)
    return False


# ── Signed Session Token Management ───────────────────────────────────────────

def create_session_token(user: str = "basheer", max_age_days: int = 7) -> str:
    """Create a cryptographically signed session token: user:expires_at.signature"""
    expires_at = int(time.time()) + (max_age_days * 86400)
    payload = f"{user}:{expires_at}"
    key = config.dashboard_secret_key.encode()
    signature = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_session_token(token: str) -> tuple[bool, str]:
    """Validate signature and expiration of a session token."""
    if not token or "." not in token:
        return False, ""
    try:
        payload, signature = token.rsplit(".", 1)
        key = config.dashboard_secret_key.encode()
        expected_sig = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return False, ""

        user, expires_at_str = payload.split(":", 1)
        expires_at = int(expires_at_str)
        if time.time() > expires_at:
            return False, ""
        return True, user
    except Exception:
        return False, ""


# ── CSRF Token Management ─────────────────────────────────────────────────────

def generate_csrf_token(session_token: str) -> str:
    """Generate CSRF token deterministically linked to the session token."""
    if not session_token:
        return ""
    key = config.dashboard_secret_key.encode()
    return hmac.new(key, f"csrf:{session_token}".encode(), hashlib.sha256).hexdigest()


def verify_csrf_token(candidate_csrf: str, session_token: str) -> bool:
    """Verify submitted CSRF token matches session token's hash."""
    if not candidate_csrf or not session_token:
        return False
    expected = generate_csrf_token(session_token)
    return hmac.compare_digest(candidate_csrf.strip(), expected.strip())


# ── Secret Sanitization Filter ────────────────────────────────────────────────

def get_sensitive_strings() -> list[str]:
    """Return all non-empty secret keys configured in the environment."""
    raw = [
        config.gemini_api_key,
        config.github_token,
        config.telegram_bot_token,
        config.openrouter_api_key,
        config.llm_api_key,
        config.webhook_secret,
        config.dashboard_secret_key,
        config.dashboard_passphrase,
    ]
    return [s for s in raw if s and len(s) >= 4]


def scrub_secrets(text: str) -> str:
    """Redact secret strings and tokens from logs or response text."""
    if not text:
        return ""
    scrubbed = text
    for secret in get_sensitive_strings():
        scrubbed = scrubbed.replace(secret, "***REDACTED***")

    # Generic GitHub PAT pattern scrubbing
    scrubbed = re.sub(r"ghp_[A-Za-z0-9_]{30,}", "ghp_***REDACTED***", scrubbed)
    scrubbed = re.sub(r"github_pat_[A-Za-z0-9_]{30,}", "github_pat_***REDACTED***", scrubbed)
    return scrubbed


# ── FastAPI Dependencies ──────────────────────────────────────────────────────

async def require_auth(request: Request) -> str:
    """
    Ensure the incoming request has a valid session token.
    Redirects HTML requests to /login; rejects API/HTMX with 401.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    valid, user = verify_session_token(token or "")
    if valid:
        # Attach session and user to request state
        request.state.user = user
        request.state.session_token = token
        request.state.csrf_token = generate_csrf_token(token or "")
        return user

    # Not authenticated — handle differently for HTMX vs Page vs API
    if request.headers.get("hx-request") == "true":
        # HTMX client: send HX-Redirect to force full page reload to /login
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired",
            headers={"HX-Redirect": "/login"},
        )

    accept = request.headers.get("accept", "")
    if "text/html" in accept and request.method == "GET":
        # Standard browser navigation: redirect to login view
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


async def require_csrf(request: Request) -> None:
    """Validate CSRF token on state-changing requests (POST, PUT, DELETE, PATCH)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    # Whitelist auth endpoints and webhooks
    if request.url.path in ("/api/auth/request-otp", "/api/auth/verify-otp", "/api/auth/login", "/generate"):
        return

    session_token = request.cookies.get(SESSION_COOKIE_NAME, "")
    csrf_token = request.headers.get(CSRF_HEADER_NAME, "")

    if not csrf_token:
        # Check form data if applicable
        try:
            form = await request.form()
            csrf_token = str(form.get("csrf_token", ""))
        except Exception:
            csrf_token = ""

    if not verify_csrf_token(csrf_token, session_token):
        logger.warning("[security] CSRF validation failed for %s %s from %s", request.method, request.url.path, get_client_ip(request))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing CSRF token. Please refresh the page.",
        )
