"""Tests for modules/monitor/commands.py.

Commands under test:
  /addurl <url> [name]   — add a monitored endpoint
  /rmurl <name>          — remove an endpoint
  /urls                  — list all endpoints with status
  /check                 — manually trigger a check (mocked)
  /uptime [name]         — 24h uptime stats
"""
import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone, timedelta


# ── /addurl ──────────────────────────────────────────────────────────────────

class TestAddUrlCommand:

    async def test_no_args_returns_usage(self, make_update):
        update, context = make_update(args=[])
        from modules.monitor.commands import addurl_command
        await addurl_command(update, context)

        assert "Usage" in update.message.reply_text.call_args[0][0]

    async def test_adds_endpoint_to_db(self, make_update, clean_db):
        update, context = make_update(args=["https://basheer.net", "My", "Blog"])
        from modules.monitor.commands import addurl_command
        await addurl_command(update, context)

        from sqlalchemy import select
        from modules.monitor.models import Endpoint
        ep = clean_db.execute(
            select(Endpoint).where(Endpoint.name == "My Blog")
        ).scalar_one_or_none()
        assert ep is not None
        assert ep.url == "https://basheer.net"

    async def test_auto_adds_https_scheme(self, make_update, clean_db):
        update, context = make_update(args=["basheer.net", "Blog"])
        from modules.monitor.commands import addurl_command
        await addurl_command(update, context)

        from sqlalchemy import select
        from modules.monitor.models import Endpoint
        ep = clean_db.execute(select(Endpoint).where(Endpoint.name == "Blog")).scalar_one()
        assert ep.url.startswith("https://")

    async def test_name_defaults_to_url(self, make_update, clean_db):
        """If no name is provided, the URL itself is used as the name."""
        update, context = make_update(args=["https://example.com"])
        from modules.monitor.commands import addurl_command
        await addurl_command(update, context)

        from sqlalchemy import select
        from modules.monitor.models import Endpoint
        ep = clean_db.execute(
            select(Endpoint).where(Endpoint.url == "https://example.com")
        ).scalar_one_or_none()
        assert ep is not None

    async def test_duplicate_name_warns(self, make_update, clean_db):
        from modules.monitor.models import Endpoint
        clean_db.add(Endpoint(name="My Blog", url="https://basheer.net"))
        clean_db.commit()

        update, context = make_update(args=["https://other.com", "My", "Blog"])
        from modules.monitor.commands import addurl_command
        await addurl_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "already exists" in text

    async def test_success_reply_contains_url(self, make_update, clean_db):
        update, context = make_update(args=["https://basheer.net", "Blog"])
        from modules.monitor.commands import addurl_command
        await addurl_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "✅" in text
        assert "basheer.net" in text


# ── /rmurl ───────────────────────────────────────────────────────────────────

class TestRmUrlCommand:

    async def test_no_args_returns_usage(self, make_update):
        update, context = make_update(args=[])
        from modules.monitor.commands import rmurl_command
        await rmurl_command(update, context)

        assert "Usage" in update.message.reply_text.call_args[0][0]

    async def test_removes_existing_endpoint(self, make_update, clean_db):
        from modules.monitor.models import Endpoint
        from sqlalchemy import select
        clean_db.add(Endpoint(name="My Blog", url="https://basheer.net"))
        clean_db.commit()

        update, context = make_update(args=["My", "Blog"])
        from modules.monitor.commands import rmurl_command
        await rmurl_command(update, context)

        ep = clean_db.execute(
            select(Endpoint).where(Endpoint.name == "My Blog")
        ).scalar_one_or_none()
        assert ep is None

        text = update.message.reply_text.call_args[0][0]
        assert "🗑" in text

    async def test_nonexistent_name_shows_error(self, make_update, clean_db):
        update, context = make_update(args=["NotHere"])
        from modules.monitor.commands import rmurl_command
        await rmurl_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "❌" in text

    async def test_multiword_name_joined(self, make_update, clean_db):
        """'/rmurl My Cool Site' should look for the name 'My Cool Site'."""
        from modules.monitor.models import Endpoint
        from sqlalchemy import select
        clean_db.add(Endpoint(name="My Cool Site", url="https://cool.com"))
        clean_db.commit()

        update, context = make_update(args=["My", "Cool", "Site"])
        from modules.monitor.commands import rmurl_command
        await rmurl_command(update, context)

        ep = clean_db.execute(
            select(Endpoint).where(Endpoint.name == "My Cool Site")
        ).scalar_one_or_none()
        assert ep is None


# ── /urls ────────────────────────────────────────────────────────────────────

class TestUrlsCommand:

    async def test_empty_shows_no_endpoints(self, make_update, clean_db):
        update, context = make_update(args=[])
        from modules.monitor.commands import urls_command
        await urls_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "No endpoints" in text

    async def test_shows_all_endpoints(self, make_update, clean_db):
        from modules.monitor.models import Endpoint
        clean_db.add_all([
            Endpoint(name="Blog",  url="https://basheer.net",  last_is_up=True),
            Endpoint(name="Admin", url="https://admin.example.com", last_is_up=False),
        ])
        clean_db.commit()

        update, context = make_update(args=[])
        from modules.monitor.commands import urls_command
        await urls_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "Blog" in text
        assert "Admin" in text

    async def test_green_for_up_red_for_down(self, make_update, clean_db):
        from modules.monitor.models import Endpoint
        clean_db.add_all([
            Endpoint(name="Up Site",   url="https://a.com", last_is_up=True),
            Endpoint(name="Down Site", url="https://b.com", last_is_up=False),
        ])
        clean_db.commit()

        update, context = make_update(args=[])
        from modules.monitor.commands import urls_command
        await urls_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "🟢" in text
        assert "🔴" in text

    async def test_unchecked_shows_grey(self, make_update, clean_db):
        from modules.monitor.models import Endpoint
        clean_db.add(Endpoint(name="New", url="https://new.com", last_is_up=None))
        clean_db.commit()

        update, context = make_update(args=[])
        from modules.monitor.commands import urls_command
        await urls_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "⚪" in text


# ── /check ───────────────────────────────────────────────────────────────────

class TestCheckCommand:

    async def test_sends_initial_checking_message(self, make_update):
        update, context = make_update(args=[])

        with patch(
            "modules.monitor.commands.check_all_and_alert",
            new=AsyncMock(return_value={"checked": 0, "up": 0, "down": 0, "alerts": []}),
        ):
            from modules.monitor.commands import check_command
            await check_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "Checking" in update.message.reply_text.call_args[0][0]

    async def test_edits_message_with_results(self, make_update):
        update, context = make_update(args=[])
        summary = {"checked": 3, "up": 2, "down": 1, "alerts": ["Down Site"]}

        with patch(
            "modules.monitor.commands.check_all_and_alert",
            new=AsyncMock(return_value=summary),
        ):
            from modules.monitor.commands import check_command
            await check_command(update, context)

        msg = update.message.reply_text.return_value
        msg.edit_text.assert_called_once()
        result_text = msg.edit_text.call_args[0][0]
        assert "3" in result_text   # checked count
        assert "Down Site" in result_text

    async def test_no_alerts_in_clean_run(self, make_update):
        update, context = make_update(args=[])
        summary = {"checked": 2, "up": 2, "down": 0, "alerts": []}

        with patch(
            "modules.monitor.commands.check_all_and_alert",
            new=AsyncMock(return_value=summary),
        ):
            from modules.monitor.commands import check_command
            await check_command(update, context)

        result_text = update.message.reply_text.return_value.edit_text.call_args[0][0]
        assert "Alerts" not in result_text or "alerts" not in result_text.lower()


# ── /uptime ──────────────────────────────────────────────────────────────────

class TestUptimeCommand:

    def _add_check_results(self, clean_db, endpoint_id: int, n_up: int, n_down: int):
        """Helper: insert synthetic check results for an endpoint."""
        from modules.monitor.models import CheckResult
        now = datetime.now(timezone.utc)
        for i in range(n_up):
            clean_db.add(CheckResult(
                endpoint_id=endpoint_id, is_up=True,
                status_code=200, response_time_ms=120,
                checked_at=now - timedelta(minutes=i * 5),
            ))
        for i in range(n_down):
            clean_db.add(CheckResult(
                endpoint_id=endpoint_id, is_up=False,
                status_code=503, response_time_ms=None,
                checked_at=now - timedelta(minutes=(n_up + i) * 5),
            ))
        clean_db.commit()

    async def test_nonexistent_name_shows_error(self, make_update, clean_db):
        update, context = make_update(args=["Ghost", "Site"])
        from modules.monitor.commands import uptime_command
        await uptime_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "❌" in text

    async def test_all_endpoints_no_name(self, make_update, clean_db):
        from modules.monitor.models import Endpoint
        ep = Endpoint(name="Blog", url="https://basheer.net")
        clean_db.add(ep)
        clean_db.commit()
        self._add_check_results(clean_db, ep.id, n_up=10, n_down=2)

        update, context = make_update(args=[])
        from modules.monitor.commands import uptime_command
        await uptime_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "Blog" in text
        assert "%" in text

    async def test_specific_endpoint_by_name(self, make_update, clean_db):
        from modules.monitor.models import Endpoint
        ep1 = Endpoint(name="Blog",  url="https://basheer.net")
        ep2 = Endpoint(name="Admin", url="https://admin.example.com")
        clean_db.add_all([ep1, ep2])
        clean_db.commit()
        self._add_check_results(clean_db, ep1.id, n_up=8, n_down=0)
        self._add_check_results(clean_db, ep2.id, n_up=5, n_down=3)

        update, context = make_update(args=["Blog"])
        from modules.monitor.commands import uptime_command
        await uptime_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "Blog" in text
        assert "Admin" not in text

    async def test_100_percent_uptime_shows_green(self, make_update, clean_db):
        from modules.monitor.models import Endpoint
        ep = Endpoint(name="Perfect", url="https://perfect.com")
        clean_db.add(ep)
        clean_db.commit()
        self._add_check_results(clean_db, ep.id, n_up=12, n_down=0)

        update, context = make_update(args=[])
        from modules.monitor.commands import uptime_command
        await uptime_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "🟢" in text

    async def test_low_uptime_shows_red(self, make_update, clean_db):
        from modules.monitor.models import Endpoint
        ep = Endpoint(name="Flaky", url="https://flaky.com")
        clean_db.add(ep)
        clean_db.commit()
        self._add_check_results(clean_db, ep.id, n_up=2, n_down=10)

        update, context = make_update(args=[])
        from modules.monitor.commands import uptime_command
        await uptime_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "🔴" in text
