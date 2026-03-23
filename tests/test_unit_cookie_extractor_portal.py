"""Unit tests for browser_auth cookie_extractor portal helpers (no Playwright)."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BROWSER_AUTH = _ROOT / "browser_auth"
for _p in (_ROOT, _BROWSER_AUTH):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from cookie_extractor import (  # noqa: E402
    portal_landing_url,
    portal_settings_from_env_file,
    target_cookies_for_profile,
)


def test_target_cookies_consumer_uses_copilot_host():
    tc = target_cookies_for_profile("consumer")
    assert "https://copilot.microsoft.com" in tc
    assert "https://www.bing.com" in tc


def test_target_cookies_m365_uses_hub_host():
    tc = target_cookies_for_profile("m365_hub")
    assert "https://m365.cloud.microsoft" in tc


def test_portal_landing_override_adds_scheme():
    assert portal_landing_url("consumer", "copilot.microsoft.com").startswith("https://")


def test_portal_landing_m365_default_uses_chat_path():
    u = portal_landing_url("m365_hub", "")
    assert "m365.cloud.microsoft" in u
    assert "/chat" in u


def test_portal_landing_normalizes_m365_com_typo():
    u = portal_landing_url("m365_hub", "https://m365.cloud.microsoft.com/chat")
    assert "m365.cloud.microsoft.com" not in u
    assert u.startswith("https://m365.cloud.microsoft")


def test_portal_settings_from_env_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "COPILOT_PORTAL_PROFILE=m365_hub\n"
        "COPILOT_PORTAL_BASE_URL=https://custom.example/path\n"
        "COPILOT_PORTAL_API_BASE_URL=\n"
    )
    prof, base, api = portal_settings_from_env_file(str(p))
    assert prof == "m365_hub"
    assert base == "https://custom.example/path"
    assert api == ""


def test_portal_settings_invalid_profile_defaults_consumer(tmp_path):
    p = tmp_path / ".env"
    p.write_text("COPILOT_PORTAL_PROFILE=garbage\n")
    prof, _, _ = portal_settings_from_env_file(str(p))
    assert prof == "consumer"


def test_portal_settings_empty_file_defaults_m365(tmp_path):
    p = tmp_path / ".env"
    p.write_text("")
    prof, base, api = portal_settings_from_env_file(str(p))
    assert prof == "m365_hub"
    assert base == ""
    assert api == ""
