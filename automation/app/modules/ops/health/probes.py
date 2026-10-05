"""Pure reading of host and container resource metrics (Memory, CPU, Disk, SQLite).

Designed to run on Debian/Docker (1 GB e2-micro GCP instance) as well as fallback
gracefully on other platforms (Windows/macOS in local dev). Does not depend on heavy
third-party libraries (can use psutil if installed, otherwise reads /proc, /sys/fs/cgroup,
and standard library os / shutil / ctypes).
"""
import ctypes
import logging
import os
import shutil
import sys
import threading
import time
from typing import Any

from config import config

logger = logging.getLogger(__name__)


def _get_data_dir() -> str:
    """Return effective data directory path."""
    data_dir = getattr(config, "data_dir", "/app/data")
    if os.path.isdir(data_dir):
        return data_dir
    # Local dev fallback
    local_data = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
    if os.path.isdir(local_data):
        return local_data
    return data_dir


def _read_file_safe(path: str) -> str:
    """Safely read text file or return empty string."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def get_memory_metrics() -> dict[str, Any]:
    """Inspect Host and Container memory metrics.

    Returns dict with:
        total_mb: float
        used_mb: float
        avail_mb: float
        used_pct: float
        cgroup_limit_mb: float | None
        cgroup_usage_mb: float | None
    """
    total_mb = 0.0
    avail_mb = 0.0
    cgroup_limit_mb: float | None = None
    cgroup_usage_mb: float | None = None

    # 1. Try reading Linux /proc/meminfo
    meminfo_content = _read_file_safe("/proc/meminfo")
    if meminfo_content:
        mem_data: dict[str, int] = {}
        for line in meminfo_content.splitlines():
            parts = line.split(":")
            if len(parts) == 2:
                key = parts[0].strip()
                val_str = parts[1].strip().split()[0]
                try:
                    mem_data[key] = int(val_str)
                except ValueError:
                    pass
        if "MemTotal" in mem_data and "MemAvailable" in mem_data:
            total_mb = round(mem_data["MemTotal"] / 1024.0, 1)
            avail_mb = round(mem_data["MemAvailable"] / 1024.0, 1)

    # 2. Linux cgroup inspection (Docker container memory limits)
    # cgroup v2
    cg2_max = _read_file_safe("/sys/fs/cgroup/memory.max").strip()
    cg2_cur = _read_file_safe("/sys/fs/cgroup/memory.current").strip()
    if cg2_max and cg2_max != "max":
        try:
            cgroup_limit_mb = round(int(cg2_max) / (1024.0 * 1024.0), 1)
        except ValueError:
            pass
    if cg2_cur:
        try:
            cgroup_usage_mb = round(int(cg2_cur) / (1024.0 * 1024.0), 1)
        except ValueError:
            pass

    # cgroup v1 fallback if cgroup v2 not found
    if cgroup_limit_mb is None:
        cg1_limit = _read_file_safe("/sys/fs/cgroup/memory/memory.limit_in_bytes").strip()
        cg1_usage = _read_file_safe("/sys/fs/cgroup/memory/memory.usage_in_bytes").strip()
        if cg1_limit:
            try:
                val = int(cg1_limit)
                # arbitrary large number indicating no limit (e.g. 0x7FFFFFFFFFFFF000)
                if val < 10**15:
                    cgroup_limit_mb = round(val / (1024.0 * 1024.0), 1)
            except ValueError:
                pass
        if cg1_usage:
            try:
                cgroup_usage_mb = round(int(cg1_usage) / (1024.0 * 1024.0), 1)
            except ValueError:
                pass

    # 3. Fallback on Windows if total_mb is still 0
    if total_mb == 0.0 and sys.platform == "win32":
        try:
            from ctypes import wintypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MemoryStatusEx()
            stat.cbSize = ctypes.sizeof(MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total_mb = round(stat.ullTotalPhys / (1024.0 * 1024.0), 1)
                avail_mb = round(stat.ullAvailPhys / (1024.0 * 1024.0), 1)
        except Exception as e:
            logger.debug("Windows memory probe failed: %s", e)

    # 4. Fallback default if totally unavailable (e.g. mock or obscure env)
    if total_mb <= 0.0:
        total_mb = 1024.0
        avail_mb = 512.0

    used_mb = max(0.0, round(total_mb - avail_mb, 1))
    used_pct = round((used_mb / total_mb) * 100.0, 1) if total_mb > 0 else 0.0

    return {
        "total_mb": total_mb,
        "used_mb": used_mb,
        "avail_mb": avail_mb,
        "used_pct": used_pct,
        "cgroup_limit_mb": cgroup_limit_mb,
        "cgroup_usage_mb": cgroup_usage_mb,
    }


def get_cpu_and_load_metrics() -> dict[str, Any]:
    """Inspect CPU load averages and process CPU info.

    Returns dict with:
        load_1m: float | None
        load_5m: float | None
        load_15m: float | None
        process_cpu_pct: float | None
    """
    load_1m, load_5m, load_15m = None, None, None
    if hasattr(os, "getloadavg"):
        try:
            l1, l5, l15 = os.getloadavg()
            load_1m, load_5m, load_15m = round(l1, 2), round(l5, 2), round(l15, 2)
        except Exception:
            pass
    elif os.path.exists("/proc/loadavg"):
        content = _read_file_safe("/proc/loadavg")
        if content:
            parts = content.split()
            if len(parts) >= 3:
                try:
                    load_1m = round(float(parts[0]), 2)
                    load_5m = round(float(parts[1]), 2)
                    load_15m = round(float(parts[2]), 2)
                except ValueError:
                    pass

    return {
        "load_1m": load_1m,
        "load_5m": load_5m,
        "load_15m": load_15m,
    }


def get_process_metrics() -> dict[str, Any]:
    """Inspect current Python process vitality (RSS, Threads, Uptime).

    Returns dict with:
        rss_mb: float
        thread_count: int
        uptime_seconds: float
    """
    rss_mb = 0.0

    # 1. Linux /proc/self/statm (pages: size, resident, share, text, lib, data, dt)
    statm_content = _read_file_safe("/proc/self/statm")
    if statm_content:
        parts = statm_content.split()
        if len(parts) >= 2:
            try:
                pages = int(parts[1])
                page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
                rss_mb = round((pages * page_size) / (1024.0 * 1024.0), 1)
            except Exception:
                pass

    # 2. Windows fallback
    if rss_mb == 0.0 and sys.platform == "win32":
        try:
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = ProcessMemoryCounters()
            pmc.cb = ctypes.sizeof(ProcessMemoryCounters)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                rss_mb = round(pmc.WorkingSetSize / (1024.0 * 1024.0), 1)
        except Exception as e:
            logger.debug("Windows process memory probe failed: %s", e)

    thread_count = threading.active_count()

    # Calculate uptime relative to core.bot _startup_time or module import
    uptime_s = 0.0
    try:
        from core import bot
        if hasattr(bot, "_startup_time"):
            uptime_s = time.time() - bot._startup_time
    except Exception:
        pass

    return {
        "rss_mb": rss_mb,
        "thread_count": thread_count,
        "uptime_seconds": max(0.0, uptime_s),
    }


def _file_size_bytes(path: str) -> int:
    """Return file size in bytes, or 0 if file does not exist."""
    try:
        if os.path.isfile(path):
            return os.path.getsize(path)
    except Exception:
        pass
    return 0


def get_disk_and_storage_metrics() -> dict[str, Any]:
    """Inspect disk usage on data_dir and sizes of automation.db, WAL, cache.db.

    Returns dict with:
        mount_path: str
        total_gb: float
        used_gb: float
        free_gb: float
        used_pct: float
        free_pct: float
        automation_db_bytes: int
        automation_wal_bytes: int
        cache_db_bytes: int
    """
    data_dir = _get_data_dir()
    check_path = data_dir if os.path.exists(data_dir) else "."

    total_gb, used_gb, free_gb, used_pct, free_pct = 0.0, 0.0, 0.0, 0.0, 0.0
    try:
        usage = shutil.disk_usage(check_path)
        total_gb = round(usage.total / (1024.0**3), 1)
        used_gb = round(usage.used / (1024.0**3), 1)
        free_gb = round(usage.free / (1024.0**3), 1)
        if usage.total > 0:
            used_pct = round((usage.used / usage.total) * 100.0, 1)
            free_pct = round((usage.free / usage.total) * 100.0, 1)
    except Exception as e:
        logger.warning("Failed to inspect disk usage on %s: %s", check_path, e)

    # SQLite file inspection
    auto_db_path = os.path.join(data_dir, "automation.db")
    auto_wal_path = os.path.join(data_dir, "automation.db-wal")
    cache_db_path = os.path.join(data_dir, "cache.db")

    return {
        "mount_path": data_dir,
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "used_pct": used_pct,
        "free_pct": free_pct,
        "automation_db_bytes": _file_size_bytes(auto_db_path),
        "automation_wal_bytes": _file_size_bytes(auto_wal_path),
        "cache_db_bytes": _file_size_bytes(cache_db_path),
    }


def get_full_system_snapshot() -> dict[str, Any]:
    """Aggregate all metrics into a single snapshot dict."""
    return {
        "memory": get_memory_metrics(),
        "cpu": get_cpu_and_load_metrics(),
        "process": get_process_metrics(),
        "storage": get_disk_and_storage_metrics(),
    }
