"""Conversational AI Assistant UI Router."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse

from core.security import require_auth, require_csrf
from core.templates import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI Assistant"])


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request, user: str = Depends(require_auth)):
    """Render Conversational AI Assistant Chat Console."""
    return templates.TemplateResponse(
        request=request,
        name="chat/index.html",
        context={
            "title": "ai.basheer.net — AI Assistant",
            "active_page": "chat",
            "csrf_token": request.state.csrf_token,
        },
    )


@router.post("/api/chat/message", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
async def chat_message(
    request: Request,
    message: str = Form(...),
    user: str = Depends(require_auth),
):
    """Process user prompt through core/chat router and return bubble fragment."""
    clean_msg = message.strip()
    ai_reply = ""
    try:
        from core.chat import router as chat_router, _history
        result = await chat_router.run(clean_msg, message_history=list(_history))
        _history.extend(result.new_messages())
        ai_reply = result.output
    except Exception as e:
        logger.error("[chat_ui] AI router execution failed: %s", e, exc_info=True)
        ai_reply = f"Sorry, I encountered an error while processing that request: {e}"

    return templates.TemplateResponse(
        request=request,
        name="chat/partials/chat_message.html",
        context={
            "user_message": clean_msg,
            "ai_reply": ai_reply,
            "csrf_token": request.state.csrf_token,
        },
    )
