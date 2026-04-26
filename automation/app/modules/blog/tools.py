"""
Blog module tools — plain async Python functions.

No class boilerplate, no BaseTool inheritance.
These are called directly by the pipeline orchestrator in pipeline.py.
"""
import base64
import json
import re
from datetime import datetime, timezone

import httpx

from config import config
from .schemas import BlogPostDraft


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


async def publish_to_github(draft: BlogPostDraft, category: str, is_draft: bool = False) -> str:
    """
    Commit a Hugo markdown post to the GitHub repository.
    Returns the live post URL on success, raises RuntimeError on failure.
    """
    now = datetime.now(timezone.utc)
    slug = _slugify(draft.title)
    date_str = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    front_matter = (
        f'---\n'
        f'title: "{draft.title.replace(chr(34), chr(92) + chr(34))}"\n'
        f'date: {date_str}\n'
        f'lastmod: {date_str}\n'
        f'draft: {str(is_draft).lower()}\n'
        f'description: "{draft.description.replace(chr(34), chr(92) + chr(34))}"\n'
        f'summary: "{draft.summary.replace(chr(34), chr(92) + chr(34))}"\n'
        f'tags: {json.dumps(draft.tags)}\n'
        f'categories: ["{category}"]\n'
        f'keywords: {json.dumps(draft.keywords)}\n'
        f'author: "Basheer"\n'
        f'showToc: true\n'
        f'TocOpen: false\n'
        f'ShowReadingTime: true\n'
        f'ShowBreadCrumbs: true\n'
        f'ShowPostNavLinks: true\n'
        f'ShowWordCount: true\n'
        f'---\n\n'
        f'{draft.content}\n'
    )

    encoded = base64.b64encode(front_matter.encode()).decode()
    filepath = f"content/posts/{slug}.md"
    api_url = f"https://api.github.com/repos/{config.github_repo}/contents/{filepath}"
    headers = {
        "Authorization": f"Bearer {config.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        # Check if file already exists (get SHA for update)
        r = await client.get(api_url, headers=headers)
        payload: dict = {
            "message": f"Add post: {draft.title}",
            "content": encoded,
            "branch": "main",
        }
        if r.status_code == 200:
            payload["sha"] = r.json().get("sha")

        r = await client.put(api_url, headers=headers, json=payload)

    if r.status_code in (200, 201):
        return f"{config.blog_base_url}/posts/{slug}/"

    raise RuntimeError(f"GitHub publish failed ({r.status_code}): {r.text[:300]}")


async def notify_telegram(message: str) -> None:
    """Send a Markdown message to the configured Telegram chat."""
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
                json={
                    "chat_id": config.telegram_chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                },
            )
    except Exception:
        pass  # Notifications are best-effort; never block the pipeline
