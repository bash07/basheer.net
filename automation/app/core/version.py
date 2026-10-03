"""
Version and build metadata tracking for basheer.net Automation Server.
Provides structured version info for Telegram /status and API health checks.
"""
from functools import lru_cache
import os
import subprocess

__version__ = "1.2.0"
BUILD_DATE = "2026-10-03"
DEFAULT_COMMIT = "47d2ab9"
DEFAULT_SUMMARY = "feat(finance): TA divergence detection, potential setups & daily digest"


@lru_cache(maxsize=1)
def _detect_git_commit() -> str | None:
    """Attempt to detect short git commit SHA if git repository is accessible."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return None


@lru_cache(maxsize=1)
def _detect_git_summary() -> str | None:
    """Attempt to detect the latest git commit message if repository is accessible."""
    try:
        res = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return None


def get_version_info() -> dict[str, str]:
    """
    Return comprehensive version metadata.
    Priority order:
      1. Environment variables (APP_VERSION, APP_COMMIT, APP_BUILD_DATE, APP_CHANGE_SUMMARY)
      2. Live git detection (if repository is present)
      3. Hardcoded release defaults
    """
    version = os.environ.get("APP_VERSION", __version__)
    commit = (
        os.environ.get("APP_COMMIT")
        or os.environ.get("GIT_COMMIT")
        or _detect_git_commit()
        or DEFAULT_COMMIT
    )
    build_date = os.environ.get("APP_BUILD_DATE", BUILD_DATE)
    summary = (
        os.environ.get("APP_CHANGE_SUMMARY")
        or _detect_git_summary()
        or DEFAULT_SUMMARY
    )

    return {
        "version": version,
        "commit": commit,
        "build_date": build_date,
        "summary": summary,
    }


def get_version_string() -> str:
    """Return a single formatted string such as 'v1.2.0 (47d2ab9 · 2026-10-03)'."""
    info = get_version_info()
    parts = []
    if info.get("commit"):
        parts.append(info["commit"])
    if info.get("build_date"):
        parts.append(info["build_date"])
    meta = f" ({' · '.join(parts)})" if parts else ""
    return f"v{info['version']}{meta}"
