"""Fundamental Valuation Models UI Router (Graham, Lynch, Buffett)."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from core.db import get_db
from core.security import require_auth, require_csrf
from core.templates import templates
from modules.finance.watchlist.models import WatchlistEntry
from modules.finance.fundamental.fetcher import FUNDAMENTAL_CATEGORIES, fetch_fundamentals
from modules.finance.fundamental.strategies import DEFAULT_METHOD, STRATEGIES

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Fundamental Models"])


@router.get("/fundamental", response_class=HTMLResponse)
async def fundamental_page(request: Request, user: str = Depends(require_auth)):
    """Render Fundamental Valuation Screen View."""
    return templates.TemplateResponse(
        request=request,
        name="fundamental/index.html",
        context={
            "title": "ai.basheer.net — Value Models",
            "active_page": "fundamental",
            "csrf_token": request.state.csrf_token,
            "strategies": list(STRATEGIES.keys()),
            "selected_strategy": DEFAULT_METHOD,
            "results": [],
        },
    )


@router.post("/api/fundamental/screen", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
async def screen_fundamentals(
    request: Request,
    method: str = Form(DEFAULT_METHOD),
    user: str = Depends(require_auth),
):
    """Screen all stock tickers in watchlist using selected valuation model."""
    strategy = STRATEGIES.get(method.lower(), STRATEGIES[DEFAULT_METHOD])

    with get_db() as db:
        entries = db.execute(
            select(WatchlistEntry).where(WatchlistEntry.category.in_(FUNDAMENTAL_CATEGORIES))
        ).scalars().all()

    results = []
    for e in entries:
        try:
            data = fetch_fundamentals(e.symbol)
            res = strategy.evaluate(data)
            results.append({
                "ticker": e.symbol,
                "current_price": data.current_price,
                "pe_ratio": data.pe_ratio,
                "pb_ratio": data.pb_ratio,
                "target_val": getattr(res, "graham_number", getattr(res, "fair_value", None)),
                "margin_of_safety": getattr(res, "margin_of_safety", 0.0),
                "score": res.score,
                "verdict": res.verdict if hasattr(res, "verdict") else ("ATTRACTIVE" if res.passed else "OVERVALUED"),
            })
        except Exception as exc:
            logger.debug("[fundamental_ui] Screening %s failed: %s", e.symbol, exc)

    return templates.TemplateResponse(
        request=request,
        name="fundamental/partials/screen_table.html",
        context={
            "results": results,
            "method": method,
            "csrf_token": request.state.csrf_token,
        },
    )
