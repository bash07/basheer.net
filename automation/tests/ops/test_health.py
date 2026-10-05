"""Unit and integration tests for ops/health submodule."""
import os
import tempfile
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from modules.ops.health.probes import (
    get_memory_metrics,
    get_cpu_and_load_metrics,
    get_process_metrics,
    get_disk_and_storage_metrics,
    get_full_system_snapshot,
)
from modules.ops.health.formatters import (
    _format_bytes,
    _format_uptime,
    format_system_health_card,
    format_health_alert,
)
from modules.ops.health.logs import (
    read_last_n_lines,
    read_recent_errors,
    format_logs_for_telegram,
)
from modules.ops.health.jobs import (
    evaluate_health_thresholds,
    check_system_health_job,
)
from modules.ops.health import register, sys_command, logs_command


class TestHealthFormatters:
    def test_format_bytes(self):
        assert _format_bytes(0) == "0 B"
        assert _format_bytes(512) == "512 B"
        assert _format_bytes(1024) == "1.0 KB"
        assert _format_bytes(1024 * 1024 * 5) == "5.0 MB"
        assert _format_bytes(1024 * 1024 * 1024 * 2) == "2.0 GB"

    def test_format_uptime(self):
        assert _format_uptime(45) == "0m"
        assert _format_uptime(125) == "2m"
        assert _format_uptime(3665) == "1h 1m"
        assert _format_uptime(90061) == "1d 1h 1m"

    def test_format_system_health_card(self):
        snapshot = {
            "memory": {
                "total_mb": 980.0,
                "used_mb": 412.0,
                "avail_mb": 568.0,
                "used_pct": 42.0,
                "cgroup_limit_mb": 900.0,
                "cgroup_usage_mb": 350.0,
            },
            "cpu": {
                "load_1m": 0.18,
                "load_5m": 0.22,
                "load_15m": 0.15,
            },
            "process": {
                "rss_mb": 142.0,
                "thread_count": 8,
                "uptime_seconds": 390660,
            },
            "storage": {
                "mount_path": "/app/data",
                "total_gb": 20.0,
                "used_gb": 5.8,
                "free_gb": 14.2,
                "used_pct": 29.0,
                "free_pct": 71.0,
                "automation_db_bytes": 4800000,
                "automation_wal_bytes": 128000,
                "cache_db_bytes": 12100000,
            },
        }
        card = format_system_health_card(snapshot)
        assert "System Health — e2-micro (1 GB)" in card
        assert "Memory (RAM):" in card
        assert "568.0 MB 🟢" in card
        assert "Container Limit: 350.0 MB / 900.0 MB" in card
        assert "Load Avg: 0.18, 0.22, 0.15" in card
        assert "automation.db: 4.6 MB" in card or "automation.db: 4.8 MB" in card or "automation.db:" in card
        assert "Bot Uptime:" in card

    def test_format_system_health_card_critical_indicators(self):
        snapshot = {
            "memory": {
                "total_mb": 1000.0,
                "used_mb": 900.0,
                "avail_mb": 100.0,  # < 150 MB -> 🔴
                "used_pct": 90.0,
            },
            "cpu": {"load_1m": None, "load_5m": None, "load_15m": None},
            "process": {"rss_mb": 0.0, "thread_count": 1, "uptime_seconds": 10},
            "storage": {
                "mount_path": "/app/data",
                "total_gb": 20.0,
                "used_gb": 19.0,
                "free_gb": 1.0,  # < 1.5 GB and > 85% -> 🔴
                "used_pct": 95.0,
                "free_pct": 5.0,
                "automation_db_bytes": 0,
                "automation_wal_bytes": 0,
                "cache_db_bytes": 0,
            },
        }
        card = format_system_health_card(snapshot)
        assert "100.0 MB 🔴" in card
        assert "95.0% free" not in card  # free is 5.0%
        assert "🔴" in card

    def test_format_health_alert(self):
        snapshot = {
            "memory": {"avail_mb": 120.0, "total_mb": 1000.0},
            "storage": {
                "used_pct": 88.0,
                "free_gb": 1.2,
                "automation_db_bytes": 100,
                "automation_wal_bytes": 0,
                "cache_db_bytes": 100,
            },
        }
        alert = format_health_alert("CRITICAL", "RAM critically low", snapshot)
        assert "CRITICAL SYSTEM ALERT" in alert
        assert "RAM critically low" in alert
        assert "120.0 MB" in alert


class TestHealthProbes:
    def test_get_memory_metrics(self):
        metrics = get_memory_metrics()
        assert "total_mb" in metrics
        assert "avail_mb" in metrics
        assert "used_mb" in metrics
        assert "used_pct" in metrics
        assert metrics["total_mb"] > 0

    def test_get_cpu_and_load_metrics(self):
        metrics = get_cpu_and_load_metrics()
        assert "load_1m" in metrics
        assert "load_5m" in metrics
        assert "load_15m" in metrics

    def test_get_process_metrics(self):
        metrics = get_process_metrics()
        assert "rss_mb" in metrics
        assert "thread_count" in metrics
        assert metrics["thread_count"] >= 1
        assert "uptime_seconds" in metrics

    def test_get_disk_and_storage_metrics(self):
        metrics = get_disk_and_storage_metrics()
        assert "mount_path" in metrics
        assert "total_gb" in metrics
        assert "free_gb" in metrics
        assert "used_pct" in metrics
        assert "automation_db_bytes" in metrics
        assert "cache_db_bytes" in metrics

    def test_get_full_system_snapshot(self):
        snapshot = get_full_system_snapshot()
        assert "memory" in snapshot
        assert "cpu" in snapshot
        assert "process" in snapshot
        assert "storage" in snapshot


class TestHealthLogs:
    def test_read_last_n_lines_and_formatting(self):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            for i in range(50):
                f.write(f"2026-10-05 12:00:{i:02d} [INFO] test - Message {i}\n")
            temp_path = f.name

        try:
            lines = read_last_n_lines(n=10, log_path=temp_path)
            assert len(lines) == 10
            assert "Message 49" in lines[-1]
            assert "Message 40" in lines[0]

            msg = format_logs_for_telegram(lines, title="Test Logs")
            assert "*Test Logs* (10 lines):" in msg
            assert "Message 49" in msg
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_read_recent_errors(self):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            f.write("2026-10-05 12:00:00 [INFO] test - Normal start\n")
            f.write("2026-10-05 12:00:01 [WARNING] test - High memory warning\n")
            f.write("2026-10-05 12:00:02 [INFO] test - Working\n")
            f.write("2026-10-05 12:00:03 [ERROR] test - Uncaught connection failure\n")
            f.write("2026-10-05 12:00:04 [INFO] test - Recovered\n")
            temp_path = f.name

        try:
            errors = read_recent_errors(limit=10, log_path=temp_path)
            assert len(errors) == 2
            assert "High memory warning" in errors[0]
            assert "Uncaught connection failure" in errors[1]
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


class TestHealthJobsAndWatchdog:
    def test_evaluate_health_thresholds_healthy(self):
        snapshot = {
            "memory": {"avail_mb": 500.0},
            "storage": {
                "used_pct": 50.0,
                "free_gb": 10.0,
                "automation_db_bytes": 1024,
                "automation_wal_bytes": 0,
            },
        }
        alerts = evaluate_health_thresholds(snapshot)
        assert len(alerts) == 0

    def test_evaluate_health_thresholds_trigger_all(self):
        snapshot = {
            "memory": {"avail_mb": 120.0},  # < 150 MB -> Critical
            "storage": {
                "mount_path": "/app/data",
                "used_pct": 89.0,          # > 85% -> Warning
                "free_gb": 1.0,           # < 1.5 GB
                "automation_db_bytes": 450 * 1024 * 1024,
                "automation_wal_bytes": 60 * 1024 * 1024,  # total 510 MB > 500 MB -> Warning
            },
        }
        alerts = evaluate_health_thresholds(snapshot)
        assert len(alerts) == 3
        keys = [a[0] for a in alerts]
        assert "mem_critical" in keys
        assert "disk_critical" in keys
        assert "db_bloat" in keys

    @pytest.mark.asyncio
    async def test_check_system_health_job(self):
        critical_snapshot = {
            "memory": {"avail_mb": 100.0, "total_mb": 1000.0},
            "cpu": {"load_1m": None, "load_5m": None, "load_15m": None},
            "process": {"rss_mb": 100.0, "thread_count": 2, "uptime_seconds": 100},
            "storage": {
                "mount_path": "/app/data",
                "used_pct": 90.0,
                "free_gb": 0.8,
                "total_gb": 10.0,
                "free_pct": 10.0,
                "automation_db_bytes": 1000,
                "automation_wal_bytes": 0,
                "cache_db_bytes": 0,
            },
        }
        with patch("modules.ops.health.jobs.get_full_system_snapshot", return_value=critical_snapshot):
            with patch("modules.ops.health.jobs.send_health_telegram", new_callable=AsyncMock) as mock_send:
                # First run should send alerts
                reasons = await check_system_health_job()
                assert len(reasons) >= 1
                assert mock_send.called

                # Second run immediately after should be throttled by cooldown
                mock_send.reset_mock()
                reasons_cooldown = await check_system_health_job()
                assert len(reasons_cooldown) == 0
                assert not mock_send.called


class TestHealthCommands:
    @pytest.mark.asyncio
    async def test_sys_command(self, make_update):
        update, context = make_update()
        await sys_command(update, context)

        # First calls reply_text("Gathering system metrics...")
        assert update.message.reply_text.called
        # Returned message edit_text called with health card
        msg_mock = update.message.reply_text.return_value
        assert msg_mock.edit_text.called
        card = msg_mock.edit_text.call_args[0][0]
        assert "System Health" in card

    @pytest.mark.asyncio
    async def test_logs_command_default(self, make_update):
        update, context = make_update(args=[])
        with patch("modules.ops.health.read_last_n_lines", return_value=["Line 1", "Line 2"]):
            await logs_command(update, context)
            assert update.message.reply_text.called
            call_text = update.message.reply_text.call_args[0][0]
            assert "Bot Logs" in call_text
            assert "Line 1" in call_text

    @pytest.mark.asyncio
    async def test_logs_command_errors(self, make_update):
        update, context = make_update(args=["errors"])
        with patch("modules.ops.health.read_recent_errors", return_value=["[ERROR] Failed"]):
            await logs_command(update, context)
            assert update.message.reply_text.called
            call_text = update.message.reply_text.call_args[0][0]
            assert "Recent Errors & Warnings" in call_text
            assert "[ERROR] Failed" in call_text

    def test_register_module(self):
        bot_mock = MagicMock()
        scheduler_mock = MagicMock()
        auth_filter = MagicMock()

        info = register(bot_mock, scheduler_mock, auth_filter)
        assert info["name"] == "health"
        assert "sys" in info["commands"]
        assert "health" in info["commands"]
        assert "logs" in info["commands"]
        assert bot_mock.add_handler.called
        assert scheduler_mock.add_job.called
