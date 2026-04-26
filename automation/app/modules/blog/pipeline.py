"""
Blog pipeline — sequential PydanticAI orchestration.

Replaces the old CrewAI Crew/Task/Process pattern with simple sequential
`await` calls. Each step passes its typed output as context to the next step.
No event loop hacks — all steps are natively async.

Steps:
  1. researcher_agent  → ResearchBrief
  2. writer_agent      → BlogPostDraft   (fed the research brief as JSON context)
  3. editor_agent      → BlogPostDraft   (fed the draft as JSON context)
  4. publish_to_github → post URL        (direct async call, no agent)
  5. notify_telegram   → void            (direct async call, no agent)
"""
import logging

from .agents import get_editor, get_ideas_agent, get_researcher, get_writer
from .schemas import BlogIdea, BlogPostDraft
from .tools import notify_telegram, publish_to_github
from core.llm import run_with_fallback

logger = logging.getLogger(__name__)


async def run_blog_pipeline(topic: str, category: str = "Technology", is_draft: bool = False) -> tuple[str, BlogPostDraft]:
    """
    Run the full 4-agent blog generation pipeline.
    Returns (post_url, final_draft).
    """
    # ── Step 1: Research ─────────────────────────────────────────────────────
    logger.info("[blog] Step 1/4 — Researching: %s", topic)
    research_result = await run_with_fallback(
        get_researcher(),
        f"Research this blog post topic thoroughly.\nTopic: {topic}\nCategory: {category}"
    )
    brief = research_result.output

    # ── Step 2: Write ─────────────────────────────────────────────────────────
    logger.info("[blog] Step 2/4 — Writing")
    write_result = await run_with_fallback(
        get_writer(),
        f"Write a complete blog post.\n"
        f"Topic: {topic}\nCategory: {category}\n\n"
        f"Use this research brief as your foundation:\n"
        f"{brief.model_dump_json(indent=2)}"
    )
    draft = write_result.output

    # ── Step 3: Edit ─────────────────────────────────────────────────────────
    logger.info("[blog] Step 3/4 — Editing")
    edit_result = await run_with_fallback(
        get_editor(),
        f"Review and improve this blog post draft:\n"
        f"{draft.model_dump_json(indent=2)}"
    )
    final_draft = edit_result.output

    # ── Step 4: Publish + Notify ─────────────────────────────────────────────
    logger.info("[blog] Step 4/4 — Publishing")
    post_url = await publish_to_github(final_draft, category, is_draft=is_draft)
    
    status_text = "New draft pushed!" if is_draft else "New post published!"
    await notify_telegram(
        f"✅ *{status_text}*\n\n"
        f"📝 {final_draft.title}\n"
        f"🔗 {post_url}\n"
        f"🏷️ {', '.join(final_draft.tags)}"
    )

    logger.info("[blog] Pipeline complete (%s) → %s", "draft" if is_draft else "live", post_url)
    return post_url, final_draft


async def run_ideas_pipeline() -> list[BlogIdea]:
    """Generate 5 content ideas and return them as a typed list."""
    logger.info("[blog] Generating content ideas")
    result = await get_ideas_agent().run(
        "Generate 5 original, timely blog post ideas for a technical blog."
    )
    return result.output.ideas
