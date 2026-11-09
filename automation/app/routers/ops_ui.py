"""System Ops & Scheduler Monitoring UI Router."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse

from core.scheduler import get_scheduler
from core.security import require_auth, require_csrf, scrub_secrets
from core.templates import templates
from modules.ops.health.logs import read_last_n_lines

logger = logging.getLogger(__name__)

router = APIRouter(tags=["System Ops"])


def _get_jobs_data() -> list[dict]:
    """Retrieve all active jobs from APScheduler via scheduler.get_jobs()."""
    jobs = []
    try:
        sched = get_scheduler()
        for j in sched.get_jobs():
            jobs.append({
                "id": j.id,
                "name": j.name,
                "trigger": str(j.trigger),
                "next_run_time": j.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z") if j.next_run_time else "Paused / None",
            })
    except Exception as e:
        logger.warning("[ops_ui] Failed to inspect scheduler jobs: %s", e)
    return jobs


def _get_sanitized_logs(n: int = 50) -> list[str]:
    """Read last N lines from bot.log and scrub all secret tokens."""
    raw_lines = read_last_n_lines(n)
    return [scrub_secrets(line) for line in raw_lines]


@router.get("/ops", response_class=HTMLResponse)
async def ops_page(request: Request, user: str = Depends(require_auth)):
    """Render System Ops, Schedulers & Live Log Tail."""
    jobs = _get_jobs_data()
    logs = _get_sanitized_logs(50)

    return templates.TemplateResponse(
        request=request,
        name="ops/index.html",
        context={
            "title": "ai.basheer.net — Logs & Schedulers",
            "active_page": "ops",
            "csrf_token": request.state.csrf_token,
            "jobs": jobs,
            "logs": logs,
        },
    )


@router.get("/api/ops/logs", response_class=HTMLResponse)
async def get_logs_partial(request: Request, user: str = Depends(require_auth)):
    """HTMX partial for live log tail polling."""
    logs = _get_sanitized_logs(50)
    return templates.TemplateResponse(
        request=request,
        name="ops/partials/logs_stream.html",
        context={"logs": logs},
    )
