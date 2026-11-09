"""AI Blog Studio UI Router (Ideation, 4-Agent Pipeline, Publish History)."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select

from core.db import get_db
from core.security import require_auth, require_csrf
from core.templates import templates
from modules.blog.models import BlogPost

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI Blog Studio"])


@router.get("/blog", response_class=HTMLResponse)
async def blog_page(request: Request, user: str = Depends(require_auth)):
    """Render AI Blog Studio Cockpit."""
    with get_db() as db:
        posts = db.execute(
            select(BlogPost).order_by(BlogPost.published_at.desc()).limit(10)
        ).scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="blog/index.html",
        context={
            "title": "ai.basheer.net — AI Blog Studio",
            "active_page": "blog",
            "csrf_token": request.state.csrf_token,
            "posts": posts,
        },
    )


@router.get("/api/blog/ideas", response_class=JSONResponse)
async def get_ideas(user: str = Depends(require_auth)):
    """Generate 5 fresh topic ideas via the Ideas Agent."""
    try:
        from modules.blog.pipeline import run_ideas_pipeline
        ideas = await run_ideas_pipeline()
        return {"ideas": [i.title for i in ideas]}
    except Exception as e:
        logger.error("[blog_ui] Ideas generation failed: %s", e)
        return {"ideas": [
            "Building Institutional SMC Trading Bots with Python 3.13",
            "Autonomous Micro-SaaS Operations on $5/mo Cloud VMs",
            "Deterministic Order Flow and Fair Value Gap Detection",
            "Zero-Trust Architecture for Self-Hosted Telegram Bots",
            "Implementing Multi-Concept Stock Screening for Indian Equities"
        ]}


@router.post("/api/blog/generate", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
async def generate_post(
    request: Request,
    topic: str = Form(...),
    category: str = Form("Technology"),
    is_draft: bool = Form(False),
    user: str = Depends(require_auth),
):
    """
    Run 4-Agent Pipeline (Researcher -> Writer -> Editor -> Publisher).
    Fire-and-wait execution with progress spinner on the client.
    """
    from modules.blog.pipeline import run_blog_pipeline

    logger.info("[blog_ui] Triggering 4-agent pipeline for topic: %s", topic)
    try:
        post_url, final_draft = await run_blog_pipeline(topic=topic, category=category, is_draft=is_draft)
        
        # Record in database
        from modules.blog.tools import _slugify
        slug = _slugify(final_draft.title)
        with get_db() as db:
            post = BlogPost(
                title=final_draft.title,
                slug=slug,
                category=category,
                url=post_url,
                status="draft" if is_draft else "published",
            )
            db.add(post)
            db.commit()

        return templates.TemplateResponse(
            request=request,
            name="blog/partials/result_banner.html",
            context={
                "success": True,
                "title": final_draft.title,
                "url": post_url,
                "tags": final_draft.tags,
                "is_draft": is_draft,
                "csrf_token": request.state.csrf_token,
            },
        )
    except Exception as e:
        logger.error("[blog_ui] Blog pipeline execution failed: %s", e, exc_info=True)
        return templates.TemplateResponse(
            request=request,
            name="blog/partials/result_banner.html",
            context={
                "success": False,
                "error": str(e),
                "csrf_token": request.state.csrf_token,
            },
        )
