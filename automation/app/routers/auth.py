"""Authentication router: Master Passphrase + Telegram 2FA OTP + Session Cookie."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from config import config
from core.templates import templates
from core.security import (
    CSRF_HEADER_NAME,
    LOCKOUT_WINDOW_SECONDS,
    SESSION_COOKIE_NAME,
    create_session_token,
    generate_and_send_otp,
    generate_csrf_token,
    get_client_ip,
    is_rate_limited,
    record_failed_attempt,
    reset_failed_attempts,
    verify_otp_code,
    verify_passphrase,
    verify_session_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Authentication"])


class PassphraseRequest(BaseModel):
    passphrase: str


class VerifyOtpRequest(BaseModel):
    code: str


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render dark cockpit login screen with Telegram 2FA."""
    # If already authenticated, redirect directly to dashboard
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie:
        valid, _ = verify_session_token(cookie)
        if valid:
            return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={"title": "ai.basheer.net — Terminal Login"},
    )


@router.post("/api/auth/request-otp")
async def request_otp(request: Request, payload: PassphraseRequest):
    """Step 1: Validate master passphrase and dispatch Telegram 2FA OTP."""
    ip = get_client_ip(request)

    if is_rate_limited(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Locked out for {LOCKOUT_WINDOW_SECONDS // 60} minutes.",
        )

    if not verify_passphrase(payload.passphrase):
        record_failed_attempt(ip)
        logger.warning("[auth] Invalid passphrase attempt from IP: %s", ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid master passphrase.",
        )

    # Valid passphrase — generate & send 6-digit code to Telegram
    success, msg = await generate_and_send_otp(ip)
    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg)

    logger.info("[auth] Telegram 2FA OTP dispatched for IP: %s", ip)
    return {"status": "otp_sent", "message": msg}


@router.post("/api/auth/verify-otp")
async def verify_otp(request: Request, payload: VerifyOtpRequest, response: Response):
    """Step 2: Validate Telegram 2FA OTP and issue signed HttpOnly session cookie."""
    ip = get_client_ip(request)

    if is_rate_limited(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Locked out.",
        )

    if not verify_otp_code(ip, payload.code):
        logger.warning("[auth] Failed OTP code from IP: %s", ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Telegram 2FA code.",
        )

    # Authentication successful! Create signed session token
    session_token = create_session_token(user="basheer", max_age_days=7)
    csrf_token = generate_csrf_token(session_token)

    # Determine if running over HTTPS
    is_secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"

    # Set hardened session cookie
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=7 * 86400,
        httponly=True,
        samesite="lax",  # lax ensures smooth browser redirects while preventing cross-site attacks
        secure=is_secure,
        path="/",
    )

    logger.info("[auth] Terminal unlocked successfully for IP: %s", ip)
    return {
        "status": "authenticated",
        "redirect": "/",
        "csrf_token": csrf_token,
    }


@router.post("/api/auth/logout")
async def logout(response: Response):
    """Log out and clear session cookie."""
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/api/auth/me")
async def auth_me(request: Request):
    """Return current session auth state."""
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    valid, user = verify_session_token(cookie or "")
    if valid:
        return {"authenticated": True, "user": user}
    return {"authenticated": False, "user": None}
