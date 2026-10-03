import pytest
from unittest.mock import patch

from core.bot import status_handler


class TestStatusHandler:
    async def test_status_handler_includes_version_and_metadata(self, make_update):
        update, context = make_update()

        with patch.dict("os.environ", {
            "APP_VERSION": "1.5.0",
            "APP_COMMIT": "deadbeef",
            "APP_BUILD_DATE": "2026-10-03",
            "APP_CHANGE_SUMMARY": "Test release commit summary",
        }):
            await status_handler(update, context)

            update.message.reply_text.assert_called_once()
            reply_text = update.message.reply_text.call_args[0][0]

            assert "System Status" in reply_text
            assert "v1.5.0" in reply_text
            assert "deadbeef" in reply_text
            assert "2026-10-03" in reply_text
            assert "Test release commit summary" in reply_text
            assert "Uptime" in reply_text
            assert "LLM Config" in reply_text

    async def test_status_handler_truncates_long_summary(self, make_update):
        update, context = make_update()
        long_summary = "A" * 120

        with patch.dict("os.environ", {
            "APP_VERSION": "1.5.0",
            "APP_CHANGE_SUMMARY": long_summary,
        }):
            await status_handler(update, context)

            update.message.reply_text.assert_called_once()
            reply_text = update.message.reply_text.call_args[0][0]

            assert "..." in reply_text
            assert "Latest:" in reply_text
