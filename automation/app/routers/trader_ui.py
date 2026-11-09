"""Trading Terminal & OMS UI Router (FastAPI + HTMX)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from core.db import get_db
from core.security import require_auth, require_csrf
from core.templates import templates
from modules.finance.trader.models import Position, Trade, TradeSignal
from modules.finance.trader.oms.engine import ExecutionEngine
from modules.finance.trader.oms.risk import RiskManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Trading OMS"])
engine = ExecutionEngine()


def _get_trader_context(db) -> dict:
    """Collect core OMS state: account metrics, setups, open positions."""
    account_summary = engine.get_account_summary(db)
    
    # 48h active signals awaiting human decision
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    signals = db.execute(
        select(TradeSignal)
        .where(
            (TradeSignal.fired_at >= cutoff) &
            ((TradeSignal.human_approved.is_(None)) | (TradeSignal.human_approved == True))
        )
        .order_by(TradeSignal.fired_at.desc())
        .limit(20)
    ).scalars().all()

    # Open positions
    open_positions = engine.get_open_positions(db)

    # Circuit breaker check
    risk = engine.risk_manager
    halted, halt_reason = risk.check_circuit_breaker(db, account_summary["equity"])

    return {
        "account": account_summary,
        "signals": signals,
        "positions": open_positions,
        "circuit_breaker": {
            "halted": halted,
            "reason": halt_reason,
        },
    }


@router.get("/trader", response_class=HTMLResponse)
async def trader_page(request: Request, user: str = Depends(require_auth)):
    """Full Trading Cockpit View."""
    with get_db() as db:
        ctx = _get_trader_context(db)

    return templates.TemplateResponse(
        request=request,
        name="trader/index.html",
        context={
            "title": "ai.basheer.net — Trading OMS",
            "active_page": "trader",
            "csrf_token": request.state.csrf_token,
            **ctx,
        },
    )


@router.get("/api/trader/positions", response_class=HTMLResponse)
async def get_positions_partial(request: Request, user: str = Depends(require_auth)):
    """HTMX partial polling open positions."""
    with get_db() as db:
        open_positions = engine.get_open_positions(db)

    return templates.TemplateResponse(
        request=request,
        name="trader/partials/positions_table.html",
        context={
            "positions": open_positions,
            "csrf_token": request.state.csrf_token,
        },
    )


@router.get("/api/trader/setups", response_class=HTMLResponse)
async def get_setups_partial(request: Request, user: str = Depends(require_auth)):
    """HTMX partial for active 48h setups."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    with get_db() as db:
        signals = db.execute(
            select(TradeSignal)
            .where(
                (TradeSignal.fired_at >= cutoff) &
                (TradeSignal.human_approved.is_(None))
            )
            .order_by(TradeSignal.fired_at.desc())
            .limit(10)
        ).scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="trader/partials/setups_cards.html",
        context={
            "signals": signals,
            "csrf_token": request.state.csrf_token,
        },
    )


@router.post("/api/trader/scan", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
async def trigger_scan(request: Request, user: str = Depends(require_auth)):
    """Trigger immediate 3-tier SMC scan and return refreshed setups fragment."""
    try:
        from modules.finance.trader.jobs import scan_all_tickers
        await scan_all_tickers(ignore_market_hours=True, force_all_strategies=True)
    except Exception as e:
        logger.error("[trader_ui] /api/trader/scan error: %s", e, exc_info=True)

    # Return refreshed setups fragment
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    with get_db() as db:
        signals = db.execute(
            select(TradeSignal)
            .where(
                (TradeSignal.fired_at >= cutoff) &
                (TradeSignal.human_approved.is_(None))
            )
            .order_by(TradeSignal.fired_at.desc())
            .limit(10)
        ).scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="trader/partials/setups_cards.html",
        context={
            "signals": signals,
            "csrf_token": request.state.csrf_token,
            "scan_completed": True,
        },
    )


@router.post("/api/trader/approve/{signal_id}", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
async def approve_signal(signal_id: int, request: Request, user: str = Depends(require_auth)):
    """Approve a TradeSignal -> execute paper position."""
    with get_db() as db:
        pos, msg = engine.open_position_from_signal(signal_id, db=db)
        db.commit()
        if not pos:
            raise HTTPException(status_code=400, detail=msg)
        open_positions = engine.get_open_positions(db)

    # Return updated positions partial
    return templates.TemplateResponse(
        request=request,
        name="trader/partials/positions_table.html",
        context={
            "positions": open_positions,
            "csrf_token": request.state.csrf_token,
            "alert_message": msg,
        },
    )


@router.post("/api/trader/dismiss/{signal_id}", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
async def dismiss_signal(signal_id: int, request: Request, user: str = Depends(require_auth)):
    """Dismiss a TradeSignal -> set human_approved = False."""
    with get_db() as db:
        sig = db.execute(select(TradeSignal).where(TradeSignal.id == signal_id)).scalar_one_or_none()
        if sig:
            sig.human_approved = False
            db.commit()

    # Returning 200 with empty body lets HTMX remove the element cleanly
    return Response(content="", status_code=status.HTTP_200_OK)


@router.post("/api/trader/close/{position_id}", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
async def close_position(position_id: int, request: Request, user: str = Depends(require_auth)):
    """Close an open position at current market price."""
    with get_db() as db:
        trade, msg = engine.close_position(position_id, reason="MANUAL_WEB_CLOSE", db=db)
        db.commit()
        if not trade:
            raise HTTPException(status_code=400, detail=msg)
        open_positions = engine.get_open_positions(db)

    return templates.TemplateResponse(
        request=request,
        name="trader/partials/positions_table.html",
        context={
            "positions": open_positions,
            "csrf_token": request.state.csrf_token,
            "alert_message": msg,
        },
    )


@router.post("/api/trader/resume", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
async def resume_trading(request: Request, user: str = Depends(require_auth)):
    """Reset the circuit breaker halt state."""
    engine.risk_manager.resume_trading()
    return HTMLResponse(
        content='<span class="text-xl font-bold text-emerald-400">NORMAL</span>'
    )
