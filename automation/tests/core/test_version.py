import os
from unittest.mock import patch

from core.version import get_version_info, get_version_string, __version__


def test_version_info_structure():
    info = get_version_info()
    assert "version" in info
    assert "commit" in info
    assert "build_date" in info
    assert "summary" in info
    assert info["version"] == __version__


def test_version_string_format():
    s = get_version_string()
    assert s.startswith(f"v{__version__}")


def test_version_info_env_override():
    with patch.dict(os.environ, {
        "APP_VERSION": "9.9.9",
        "APP_COMMIT": "abc1234",
        "APP_BUILD_DATE": "2030-01-01",
        "APP_CHANGE_SUMMARY": "Custom release test",
    }):
        info = get_version_info()
        assert info["version"] == "9.9.9"
        assert info["commit"] == "abc1234"
        assert info["build_date"] == "2030-01-01"
        assert info["summary"] == "Custom release test"

        s = get_version_string()
        assert "v9.9.9" in s
        assert "abc1234" in s
        assert "2030-01-01" in s
