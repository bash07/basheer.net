"""Nifty 100 Multi-Concept Universe Screener UI Router."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse

from core.security import require_auth, require_csrf
from core.templates import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Universe Screener"])

# Cached last report in memory to avoid re-running slow yfinance scans on every page reload
_last_report = None


@router.get("/screener", response_class=HTMLResponse)
async def screener_page(request: Request, user: str = Depends(require_auth)):
    """Render Nifty 100 Concepts A–E Universe Screener."""
    global _last_report

    return templates.TemplateResponse(
        request=request,
        name="screener/index.html",
        context={
            "title": "ai.basheer.net — Nifty 100 Screener",
            "active_page": "screener",
            "csrf_token": request.state.csrf_token,
            "report": _last_report,
        },
    )


@router.post("/api/screener/run", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
async def run_screener(request: Request, user: str = Depends(require_auth)):
    """Execute live Nifty 100 universe screen across Concepts A–E."""
    global _last_report
    try:
        from modules.finance.screener.engine import run_weekly_ta_screen
        # Run synchronous screen (or in executor if needed)
        _last_report = run_weekly_ta_screen(top_n=15, auto_populate=True)
    except Exception as e:
        logger.error("[screener_ui] Universe screen failed: %s", e, exc_info=True)

    return templates.TemplateResponse(
        request=request,
        name="screener/partials/results.html",
        context={
            "report": _last_report,
            "csrf_token": request.state.csrf_token,
        },
    )
