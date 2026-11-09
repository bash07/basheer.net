"""Watchlist & Markets UI Router (All 7 Categories, Batch Quotes, Alerts)."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from core.db import get_db
from core.security import require_auth, require_csrf
from core.templates import templates
from modules.finance.watchlist.models import PriceAlert, WatchlistEntry
from modules.finance.watchlist.prices import _CATEGORY_LABELS_LOCAL, fetch_prices_batch

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Watchlist & Markets"])


def _redirect_or_refresh(request: Request, default_url: str = "/watchlist"):
    """Support both HTMX dynamic refresh and full browser form redirection."""
    if request.headers.get("hx-request") == "true":
        return Response(status_code=status.HTTP_200_OK, headers={"HX-Refresh": "true"})
    referer = request.headers.get("referer") or default_url
    return RedirectResponse(url=referer, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/watchlist", response_class=HTMLResponse)
async def watchlist_page(request: Request, category: Optional[str] = None, user: str = Depends(require_auth)):
    """Full Watchlist & Price Tracking View."""
    with get_db() as db:
        query = select(WatchlistEntry)
        if category and category != "all":
            query = query.where(WatchlistEntry.category == category)
        entries = db.execute(query.order_by(WatchlistEntry.symbol)).scalars().all()
        alerts = db.execute(select(PriceAlert).where(PriceAlert.triggered == False).order_by(PriceAlert.created_at.desc())).scalars().all()

    symbols = [e.symbol for e in entries]
    quotes = {}
    try:
        if symbols:
            quotes = fetch_prices_batch(symbols)
    except Exception as e:
        logger.warning("[watchlist_ui] Batch price fetch failed: %s", e)

    return templates.TemplateResponse(
        request=request,
        name="watchlist/index.html",
        context={
            "title": "ai.basheer.net — Watchlist & Markets",
            "active_page": "watchlist",
            "csrf_token": request.state.csrf_token,
            "entries": entries,
            "quotes": quotes,
            "alerts": alerts,
            "current_category": category or "all",
            "category_labels": _CATEGORY_LABELS_LOCAL,
        },
    )


@router.get("/api/watchlist/prices", response_class=HTMLResponse)
async def get_prices_partial(request: Request, category: Optional[str] = "all", user: str = Depends(require_auth)):
    """HTMX partial for live quotes table auto-refresh."""
    with get_db() as db:
        query = select(WatchlistEntry)
        if category and category != "all":
            query = query.where(WatchlistEntry.category == category)
        entries = db.execute(query.order_by(WatchlistEntry.symbol)).scalars().all()

    symbols = [e.symbol for e in entries]
    quotes = {}
    if symbols:
        try:
            quotes = fetch_prices_batch(symbols)
        except Exception as e:
            logger.warning("[watchlist_ui] Price fetch failed: %s", e)

    return templates.TemplateResponse(
        request=request,
        name="watchlist/partials/prices_table.html",
        context={
            "entries": entries,
            "quotes": quotes,
            "csrf_token": request.state.csrf_token,
        },
    )


@router.get("/api/watchlist/ticker-tape", response_class=HTMLResponse)
async def get_ticker_tape(request: Request):
    """Compact header ticker tape for base.html top bar."""
    key_symbols = ["BTC-USD", "ETH-USD", "NVDA", "^NSEI"]
    quotes = {}
    try:
        quotes = fetch_prices_batch(key_symbols)
    except Exception:
        pass

    return templates.TemplateResponse(
        request=request,
        name="watchlist/partials/ticker_tape.html",
        context={"quotes": quotes},
    )


@router.post("/api/watchlist/add", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
async def add_ticker(
    request: Request,
    ticker: str = Form(...),
    category: str = Form("us_stock"),
    user: str = Depends(require_auth),
):
    """Add a symbol to watchlist."""
    clean_ticker = ticker.strip().upper()
    with get_db() as db:
        existing = db.execute(select(WatchlistEntry).where(WatchlistEntry.symbol == clean_ticker)).scalar_one_or_none()
        if not existing:
            entry = WatchlistEntry(symbol=clean_ticker, display_name=clean_ticker, category=category, source="manual")
            db.add(entry)
            db.commit()

    return _redirect_or_refresh(request)


@router.post("/api/watchlist/delete/{symbol}", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
async def delete_ticker(symbol: str, request: Request, user: str = Depends(require_auth)):
    """Remove symbol and associated alerts."""
    clean_symbol = symbol.strip().upper()
    with get_db() as db:
        entry = db.execute(select(WatchlistEntry).where(WatchlistEntry.symbol == clean_symbol)).scalar_one_or_none()
        if entry:
            db.delete(entry)
            # Delete alerts for this symbol
            alerts = db.execute(select(PriceAlert).where(PriceAlert.symbol == clean_symbol)).scalars().all()
            for a in alerts:
                db.delete(a)
            db.commit()

    return _redirect_or_refresh(request)


@router.post("/api/watchlist/alert", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
async def create_alert(
    request: Request,
    ticker: str = Form(...),
    alert_type: str = Form("static"),
    condition: Optional[str] = Form(">"),
    target_price: Optional[float] = Form(None),
    trail_pct: Optional[float] = Form(None),
    user: str = Depends(require_auth),
):
    """Create a static or trailing price alert."""
    clean_ticker = ticker.strip().upper()
    with get_db() as db:
        if alert_type == "trailing" and trail_pct:
            alert = PriceAlert(
                symbol=clean_ticker,
                condition=f"trail {trail_pct:+g}%",
                threshold=0.0,
                alert_type="trailing",
                trail_pct=float(trail_pct),
                trail_reference_price=None,
            )
        else:
            alert = PriceAlert(
                symbol=clean_ticker,
                condition=condition or ">",
                threshold=float(target_price or 0.0),
                alert_type="static",
            )
        db.add(alert)
        db.commit()

    return _redirect_or_refresh(request)


@router.post("/api/watchlist/alert/delete/{alert_id}", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
async def delete_alert(alert_id: int, request: Request, user: str = Depends(require_auth)):
    """Delete an active price alert."""
    with get_db() as db:
        alert = db.execute(select(PriceAlert).where(PriceAlert.id == alert_id)).scalar_one_or_none()
        if alert:
            db.delete(alert)
            db.commit()

    return _redirect_or_refresh(request)
