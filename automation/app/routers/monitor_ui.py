"""Site Monitor & SSL Expiry UI Router."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from core.db import get_db
from core.security import require_auth, require_csrf
from core.templates import templates
from modules.monitor.checker import check_all_and_alert
from modules.monitor.models import CheckResult, Endpoint

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Site Monitor"])


def _get_monitor_context(db) -> list[dict]:
    """Retrieve all endpoints with their latest CheckResult."""
    endpoints = db.execute(select(Endpoint)).scalars().all()
    data = []
    for ep in endpoints:
        latest = db.execute(
            select(CheckResult)
            .where(CheckResult.endpoint_id == ep.id)
            .order_by(CheckResult.checked_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        data.append({
            "endpoint": ep,
            "latest": latest,
        })
    return data


@router.get("/monitor", response_class=HTMLResponse)
async def monitor_page(request: Request, user: str = Depends(require_auth)):
    """Render Site Monitor & SSL Expiry Dashboard."""
    with get_db() as db:
        probes = _get_monitor_context(db)

    return templates.TemplateResponse(
        request=request,
        name="monitor/index.html",
        context={
            "title": "ai.basheer.net — Site Monitor",
            "active_page": "monitor",
            "csrf_token": request.state.csrf_token,
            "probes": probes,
        },
    )


@router.post("/api/monitor/check", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
async def trigger_check(request: Request, user: str = Depends(require_auth)):
    """Trigger immediate HTTP probe and SSL certificate inspection."""
    try:
        await check_all_and_alert()
    except Exception as e:
        logger.error("[monitor_ui] Immediate probe failed: %s", e)

    with get_db() as db:
        probes = _get_monitor_context(db)

    return templates.TemplateResponse(
        request=request,
        name="monitor/partials/probe_cards.html",
        context={
            "probes": probes,
            "csrf_token": request.state.csrf_token,
        },
    )
