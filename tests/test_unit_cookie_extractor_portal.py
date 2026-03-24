"""Unit tests for browser_auth cookie_extractor portal helpers (no Playwright).

IMPORTANT: browser_auth/ must NOT be added to sys.path at module level.
Doing so causes ``import server`` in later test files to resolve to
``browser_auth/server.py`` instead of the root ``server.py`` (C1).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
import pytest

_ROOT = Path(__file__).resolve().parents[1]
# Only add project root (for portal_urls etc.) — NOT browser_auth/
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Load cookie_extractor by file path to avoid adding browser_auth/ to sys.path
_spec = importlib.util.spec_from_file_location(
    "browser_auth_cookie_extractor",
    str(_ROOT / "browser_auth" / "cookie_extractor.py"),
)
_ce = importlib.util.module_from_spec(_spec)
sys.modules["browser_auth_cookie_extractor"] = _ce
_spec.loader.exec_module(_ce)

_is_logged_in = _ce._is_logged_in
portal_landing_url = _ce.portal_landing_url
portal_settings_from_env_file = _ce.portal_settings_from_env_file
target_cookies_for_profile = _ce.target_cookies_for_profile


def test_target_cookies_consumer_uses_copilot_host():
    tc = target_cookies_for_profile("consumer")
    urls = [u for u, _ in tc]
    assert "https://copilot.microsoft.com" in urls
    assert "https://www.bing.com" in urls


def test_target_cookies_m365_stays_on_m365_only():
    tc = target_cookies_for_profile("m365_hub")
    urls = [u for u, _ in tc]
    assert urls.index("https://m365.cloud.microsoft") < urls.index(
        "https://m365.cloud.microsoft.com"
    )
    # m365_hub must NOT navigate to bing or copilot (disrupts user session)
    assert "https://www.bing.com" not in urls
    assert "https://copilot.microsoft.com" not in urls


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


class _DummyContext:
    def __init__(self, cookies):
        self._cookies = cookies

    async def cookies(self, _urls):
        return self._cookies


class _DummyPage:
    def __init__(self, url: str, html: str, cookies=None):
        self.url = url
        self._html = html
        self.context = _DummyContext(cookies or [])

    async def content(self):
        return self._html


@pytest.mark.asyncio
async def test_is_logged_in_false_when_m365_auth_modal_present():
    p = _DummyPage(
        "https://m365.cloud.microsoft/chat?auth=1",
        "<div>Authentication required</div><button>Continue</button>",
    )
    ok = await _is_logged_in(p, ("m365.cloud.microsoft",))
    assert ok is False


@pytest.mark.asyncio
async def test_is_logged_in_true_on_portal_host_without_auth_gate():
    p = _DummyPage(
        "https://m365.cloud.microsoft/chat",
        "<main>Welcome to Copilot</main>",
    )
    ok = await _is_logged_in(p, ("m365.cloud.microsoft",))
    assert ok is True
