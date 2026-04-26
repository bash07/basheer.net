"""
Conversational AI assistant for the Telegram free-text interface.

The ChatAgent has tool functions that wrap existing module pipelines.
When a user sends a plain-text message the agent decides whether to:
  - Call a tool (trigger an automation) and return its output, or
  - Answer directly from its own knowledge.

Conversation history is kept in an in-memory deque (last 30 messages).
This is intentionally not persisted to SQLite — history resets on restart,
which is fine for a single-user personal assistant.
"""
import logging
from collections import deque

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage

from core.llm import get_model

logger = logging.getLogger(__name__)

# ── Conversation history ──────────────────────────────────────────────────────
# Stores the last N ModelMessage objects so the agent maintains context.
_history: deque[ModelMessage] = deque(maxlen=30)


# ── Agent definition ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """
You are a personal automation assistant embedded in a Telegram bot.
You help manage a blog automation platform and a site uptime monitor.

Your capabilities (via tools):
  - Generate and publish a new blog post on any topic
  - Get a list of content ideas
  - Show recently published posts
  - Show the current uptime status of all monitored websites

Behaviour rules:
  - If the user's message maps clearly to one of your tools, call it directly.
    Do not ask for confirmation — just act and report the result.
  - If the user's message is ambiguous (e.g. "write something about Python"),
    make a reasonable assumption and proceed. Mention the assumption in your reply.
  - If the message is a general question (not an automation request),
    answer it helpfully and concisely — no tool call needed.
  - Keep all replies concise. This is Telegram, not a document editor.
  - Use plain text only. Do NOT use markdown headers (##), bold (**), or bullets (-)
    unless they genuinely improve readability in a short reply.
""".strip()

assistant = Agent(
    model=get_model("nano"),
    system_prompt=_SYSTEM_PROMPT,
)


# ── Tools ─────────────────────────────────────────────────────────────────────

@assistant.tool_plain
async def generate_blog_post(topic: str, category: str = "Technology", is_draft: bool = False) -> str:
    """
    Generate and publish a complete blog post on the given topic.
    If is_draft is True, it will be pushed to the repository as a draft (draft: true) instead of being published immediately.
    Returns immediately after queuing the generation. The user will be notified via Telegram.
    Use this when the user asks to write, generate, or publish a post.
    """
    import uuid
    from core.queue import queue
    from config import config

    task_id = str(uuid.uuid4())[:8]
    logger.info("[chat] Queueing generate_blog_post(topic=%r, is_draft=%r) as task %s", topic, is_draft, task_id)

    async def _run():
        from modules.blog.pipeline import run_blog_pipeline
        from modules.blog.tools import notify_telegram
        try:
            url, draft = await run_blog_pipeline(topic, category, is_draft=is_draft)
            
            # Save to DB (so /posts and recent posts tool can see it)
            from core.db import get_db
            from modules.blog.models import BlogPost
            with get_db() as db:
                slug = url.rstrip("/").split("/")[-1]
                status_db = "draft" if is_draft else "published"
                db.add(BlogPost(title=draft.title, slug=slug, category=category, url=url, status=status_db))
                
        except Exception as e:
            logger.error("[chat:%s] generate_blog_post failed: %s", task_id, e, exc_info=True)
            await notify_telegram(f"❌ Blog generation failed for topic '{topic}': {e}")

    queue.submit(task_id, f"generate:{topic}", "blog", _run(), model_info=config.llm_model)
    
    status_word = "draft" if is_draft else "post"
    return f"I have started generating the {status_word} in the background. It usually takes 2-5 minutes. You will receive a notification when it's done."


@assistant.tool_plain
async def get_content_ideas() -> str:
    """
    Generate 5 original blog post ideas tailored to the blog's niche.
    Returns immediately after queuing. The user will be notified via Telegram.
    Use this when the user asks for ideas, inspiration, or topic suggestions.
    """
    import uuid
    from core.queue import queue
    from config import config

    task_id = str(uuid.uuid4())[:8]
    logger.info("[chat] Queueing get_content_ideas as task %s", task_id)

    async def _run():
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
            logger.error("[chat:%s] get_content_ideas failed: %s", task_id, e, exc_info=True)
            await notify_telegram(f"❌ Ideas generation failed: {e}")

    queue.submit(task_id, "ideas", "blog", _run(), model_info=config.llm_model)
    return "I am brainstorming ideas in the background. I will send them to you shortly."


@assistant.tool_plain
def get_recent_posts(limit: int = 5) -> str:
    """
    Return the most recently published blog posts from the database.
    Use this when the user asks what was published, recent posts, or post history.
    """
    logger.info("[chat] Tool: get_recent_posts(limit=%d)", limit)
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
            date = p.published_at.strftime("%Y-%m-%d")
            lines.append(f"• [{date}] {p.title}\n  {p.url}")
        return "\n".join(lines)
    except Exception as e:
        logger.error("[chat] get_recent_posts failed: %s", e, exc_info=True)
        return f"Could not retrieve posts: {e}"


@assistant.tool_plain
def get_monitor_status() -> str:
    """
    Return the current uptime status of all monitored websites.
    Use this when the user asks if sites are up, down, or about monitor status.
    """
    logger.info("[chat] Tool: get_monitor_status()")
    try:
        from sqlalchemy import select
        from core.db import get_db
        from modules.monitor.models import Endpoint

        with get_db() as db:
            endpoints = db.execute(
                select(Endpoint).where(Endpoint.enabled == True)  # noqa: E712
            ).scalars().all()

        if not endpoints:
            return "No endpoints are being monitored yet."

        lines = ["Monitor status:"]
        for ep in endpoints:
            if ep.last_is_up is None:
                status = "⬜ Not checked yet"
            elif ep.last_is_up:
                status = "🟢 UP"
            else:
                status = "🔴 DOWN"

            checked = (
                ep.last_checked_at.strftime("%H:%M") if ep.last_checked_at else "never"
            )
            lines.append(f"{status}  {ep.name}  (last checked: {checked})")
        return "\n".join(lines)
    except Exception as e:
        logger.error("[chat] get_monitor_status failed: %s", e, exc_info=True)
        return f"Could not retrieve monitor status: {e}"


# ── Public interface ──────────────────────────────────────────────────────────

async def handle_message(text: str) -> str:
    """
    Process a free-text Telegram message and return the assistant's reply.
    Maintains conversation history across calls.
    """
    try:
        result = await assistant.run(text, message_history=list(_history))
        # Append the new exchange to history
        _history.extend(result.all_messages())
        return str(result.output)
    except Exception as e:
        logger.error("[chat] Assistant error: %s", e, exc_info=True)
        return f"⚠️ Something went wrong: {e}"


def clear_history() -> None:
    """Clear the conversation history (e.g. on /cancel)."""
    _history.clear()
