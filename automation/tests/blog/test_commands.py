"""Tests for modules/blog/commands.py.

Commands under test:
  /generate <topic> [--cat Category] [--draft]
  /ideas
  /posts

generate and ideas are fire-and-forget — they queue a background task and
return immediately. Tests use the `coro_safe_submit` fixture from conftest.py
which mocks queue.submit and closes the coroutine so Python 3.13 does not
raise 'coroutine was never awaited' warnings.

posts is synchronous — it reads from the DB and replies immediately.
"""
import pytest
from datetime import datetime, timezone


# ── /generate ────────────────────────────────────────────────────────────────

class TestGenerateCommand:

    async def test_no_args_returns_usage(self, make_update):
        update, context = make_update(args=[])
        from modules.blog.commands import generate_command
        await generate_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "Usage" in update.message.reply_text.call_args[0][0]

    async def test_basic_topic_queues_task(self, make_update, coro_safe_submit):
        update, context = make_update(args=["Python", "decorators"])
        from modules.blog.commands import generate_command
        await generate_command(update, context)

        coro_safe_submit.assert_called_once()
        _, name, module, *_ = coro_safe_submit.call_args[0]
        assert "generate:" in name
        assert module == "blog"

    async def test_basic_topic_sends_status_message(self, make_update, coro_safe_submit):
        update, context = make_update(args=["async", "python"])
        from modules.blog.commands import generate_command
        await generate_command(update, context)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        # Status message should mention the topic or pipeline start
        assert any(w in text for w in ["pipeline", "async", "Topic"])

    async def test_draft_flag_sets_mode(self, make_update, coro_safe_submit):
        update, context = make_update(args=["test", "topic", "--draft"])
        from modules.blog.commands import generate_command
        await generate_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "draft" in text.lower()

    async def test_live_mode_by_default(self, make_update, coro_safe_submit):
        update, context = make_update(args=["some", "topic"])
        from modules.blog.commands import generate_command
        await generate_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "live post" in text.lower() or "draft" not in text.lower()

    async def test_cat_override_present_in_status(self, make_update, coro_safe_submit):
        update, context = make_update(args=["RTOS", "--cat", "Embedded", "Systems"])
        from modules.blog.commands import generate_command
        await generate_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "Embedded" in text

    async def test_cat_and_draft_together(self, make_update, coro_safe_submit):
        update, context = make_update(args=["topic", "--cat", "Tech", "--draft"])
        from modules.blog.commands import generate_command
        await generate_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "draft" in text.lower()

    async def test_default_category_is_technology(self, make_update, coro_safe_submit):
        """When --cat is not given, category defaults to 'Technology'."""
        update, context = make_update(args=["some", "topic"])
        from modules.blog.commands import generate_command
        await generate_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "Technology" in text


# ── /ideas ───────────────────────────────────────────────────────────────────

class TestIdeasCommand:

    async def test_sends_initial_status_message(self, make_update, coro_safe_submit):
        update, context = make_update(args=[])
        from modules.blog.commands import ideas_command
        await ideas_command(update, context)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "idea" in text.lower() or "generating" in text.lower()

    async def test_queues_task_with_correct_name_and_module(self, make_update, coro_safe_submit):
        update, context = make_update(args=[])
        from modules.blog.commands import ideas_command
        await ideas_command(update, context)

        coro_safe_submit.assert_called_once()
        _, name, module, *_ = coro_safe_submit.call_args[0]
        assert name == "ideas"
        assert module == "blog"


# ── /posts ───────────────────────────────────────────────────────────────────

class TestPostsCommand:

    async def test_empty_db_shows_no_posts(self, make_update, clean_db):
        update, context = make_update(args=[])
        from modules.blog.commands import posts_command
        await posts_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "No posts" in text

    async def test_shows_published_posts(self, make_update, clean_db):
        from modules.blog.models import BlogPost
        clean_db.add_all([
            BlogPost(
                title="Post One", slug="post-one",
                url="https://basheer.net/posts/post-one",
                category="Technology",
                published_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            ),
            BlogPost(
                title="Post Two", slug="post-two",
                url="https://basheer.net/posts/post-two",
                category="Embedded",
                published_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            ),
        ])
        clean_db.commit()

        update, context = make_update(args=[])
        from modules.blog.commands import posts_command
        await posts_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "Post One" in text
        assert "Post Two" in text

    async def test_ordered_newest_first(self, make_update, clean_db):
        from modules.blog.models import BlogPost
        clean_db.add_all([
            BlogPost(title="Old Post", slug="old", url="https://x.com/old",
                     category="Tech", published_at=datetime(2025, 1, 1, tzinfo=timezone.utc)),
            BlogPost(title="New Post", slug="new", url="https://x.com/new",
                     category="Tech", published_at=datetime(2025, 6, 1, tzinfo=timezone.utc)),
        ])
        clean_db.commit()

        update, context = make_update(args=[])
        from modules.blog.commands import posts_command
        await posts_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        # New Post should appear before Old Post in the formatted text
        assert text.index("New Post") < text.index("Old Post")

    async def test_shows_at_most_five_posts(self, make_update, clean_db):
        from modules.blog.models import BlogPost
        for i in range(8):
            clean_db.add(BlogPost(
                title=f"Post {i}", slug=f"post-{i}",
                url=f"https://basheer.net/posts/post-{i}",
                category="Tech",
                published_at=datetime(2025, 1, i + 1, tzinfo=timezone.utc),
            ))
        clean_db.commit()

        update, context = make_update(args=[])
        from modules.blog.commands import posts_command
        await posts_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "5." in text
        assert "6." not in text

    async def test_reply_contains_url(self, make_update, clean_db):
        from modules.blog.models import BlogPost
        clean_db.add(BlogPost(
            title="Linked Post", slug="linked",
            url="https://basheer.net/posts/linked",
            category="Tech",
            published_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        ))
        clean_db.commit()

        update, context = make_update(args=[])
        from modules.blog.commands import posts_command
        await posts_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "basheer.net/posts/linked" in text
