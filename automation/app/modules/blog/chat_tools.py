"""Blog domain chat tools — sub-agent used by the core router.

When core/chat.py's router identifies a blog-related free-text request, it
delegates to handle_blog_request() which runs this focused sub-agent.
Keeping tools domain-scoped avoids bloating the router with unrelated details.
"""
import logging
import uuid

from pydantic_ai import Agent
from core.llm import get_model

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """
You are a blog automation assistant for basheer.net.
Your capabilities (via tools):
  - Generate and publish a new blog post on any topic
  - Get a list of timely content ideas
  - Show recently published posts

Behaviour rules:
  - If the user's message maps to a tool, call it — do not ask for confirmation.
  - If ambiguous (e.g. "write something about Python"), make a reasonable
    assumption and proceed. Mention the assumption in your reply.
  - Keep all replies concise — this is Telegram, not a document editor.
  - Use plain text only. No markdown headers or excessive formatting.
""".strip()

blog_assistant = Agent(
    model=get_model("nano"),
    system_prompt=_SYSTEM_PROMPT,
)


# ── Tools ─────────────────────────────────────────────────────────────────────

@blog_assistant.tool_plain
async def generate_blog_post(
    topic: str,
    category: str = "Technology",
    is_draft: bool = False,
) -> str:
    """
    Generate and publish a complete blog post on the given topic.
    If is_draft is True, pushes as a draft (not published immediately).
    Returns right away — user is notified via Telegram when done.
    Use when the user asks to write, generate, or publish a post.
    """
    from core.queue import queue
    from config import config

    task_id = str(uuid.uuid4())[:8]
    logger.info(
        "[blog_chat] Queueing generate(topic=%r, draft=%r) as task %s",
        topic, is_draft, task_id,
    )

    async def _run() -> None:
        from modules.blog.pipeline import run_blog_pipeline
        from modules.blog.tools import notify_telegram
        try:
            url, draft = await run_blog_pipeline(topic, category, is_draft=is_draft)
            from core.db import get_db
            from modules.blog.models import BlogPost
            with get_db() as db:
                slug = url.rstrip("/").split("/")[-1]
                db.add(BlogPost(
                    title=draft.title,
                    slug=slug,
                    category=category,
                    url=url,
                    status="draft" if is_draft else "published",
                ))
        except Exception as e:
            logger.error("[blog_chat:%s] generate failed: %s", task_id, e, exc_info=True)
            from modules.blog.tools import notify_telegram
            await notify_telegram(f"❌ Blog generation failed for '{topic}': {e}")

    queue.submit(task_id, f"generate:{topic}", "blog", _run(), model_info=config.llm_model)
    kind = "draft" if is_draft else "post"
    return (
        f"Started generating the {kind} in the background. "
        "Takes 2–5 minutes — I'll notify you when it's done."
    )


@blog_assistant.tool_plain
async def get_content_ideas() -> str:
    """
    Generate 5 original blog post ideas tailored to the blog's niche.
    Returns right away — ideas sent to Telegram when ready.
    Use when the user asks for ideas, inspiration, or topic suggestions.
    """
    from core.queue import queue
    from config import config

    task_id = str(uuid.uuid4())[:8]
    logger.info("[blog_chat] Queueing get_content_ideas as task %s", task_id)

    async def _run() -> None:
        from modules.blog.pipeline import run_ideas_pipeline
        from modules.blog.tools import notify_telegram
        try:
            ideas = await run_ideas_pipeline()
            lines = ["📋 *Weekly Blog Ideas*\n"]
            for i, idea in enumerate(ideas, 1):
                lines.append(
                    f"{i}. *{idea.topic}*\n"
                    f"   📂 {idea.category}\n"
                    f"   💡 {idea.angle}\n"
                    f"   ⏰ {idea.why_now}"
                )
            lines.append("\n_Reply with a topic to generate a post._")
            await notify_telegram("\n\n".join(lines))
        except Exception as e:
            logger.error("[blog_chat:%s] ideas failed: %s", task_id, e, exc_info=True)
            from modules.blog.tools import notify_telegram
            await notify_telegram(f"❌ Ideas generation failed: {e}")

    queue.submit(task_id, "ideas", "blog", _run(), model_info=config.llm_model)
    return "Brainstorming ideas in the background — sending them to you shortly."


@blog_assistant.tool_plain
def get_recent_posts(limit: int = 5) -> str:
    """
    Return the most recently published blog posts from the database.
    Use when the user asks about recent posts, what was published, or post history.
    """
    logger.info("[blog_chat] Tool: get_recent_posts(limit=%d)", limit)
    try:
        from sqlalchemy import select, desc
        from core.db import get_db
        from modules.blog.models import BlogPost

        with get_db() as db:
            posts = db.execute(
                select(BlogPost)
                .where(BlogPost.status == "published")
                .order_by(desc(BlogPost.published_at))
                .limit(limit)
            ).scalars().all()

        if not posts:
            return "No published posts found yet."
        lines = [f"Last {len(posts)} published posts:"]
        for p in posts:
            lines.append(f"• [{p.published_at.strftime('%Y-%m-%d')}] {p.title}\n  {p.url}")
        return "\n".join(lines)
    except Exception as e:
        logger.error("[blog_chat] get_recent_posts failed: %s", e, exc_info=True)
        return f"Could not retrieve posts: {e}"


# ── Public interface ──────────────────────────────────────────────────────────

async def handle_blog_request(text: str) -> str:
    """Entry point called by the router in core/chat.py."""
    try:
        result = await blog_assistant.run(text)
        return str(result.output)
    except Exception as e:
        logger.error("[blog_chat] blog_assistant error: %s", e, exc_info=True)
        return f"⚠️ Blog assistant error: {e}"
