"""C3 /setup page HTML contract (TestClient; no Docker).

IMPORTANT: browser_auth/ must NOT be added to sys.path at module level.
Doing so causes ``import server`` in later test files to resolve to
``browser_auth/server.py`` instead of the root ``server.py`` (C1),
breaking 27 tests in test_unit_server.py.

Instead we add it only inside fixtures and remove it afterwards.
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
# Only add project root (for portal_urls, config etc.) — NOT browser_auth/
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _scoped_import_c3_server():
    """Temporarily add browser_auth/ to sys.path, import its server module,
    then restore sys.path and sys.modules so root server.py stays untouched."""
    ba_path = str(_ROOT / "browser_auth")
    added = ba_path not in sys.path
    if added:
        sys.path.insert(0, ba_path)
    # Remove any previously cached 'server' that points to root server.py
    prev_server = sys.modules.pop("server", None)
    try:
        import server as c3_srv  # noqa: resolves to browser_auth/server.py
        importlib.reload(c3_srv)
        return c3_srv
    finally:
        # Restore: remove browser_auth/server from cache, put back root server
        sys.modules.pop("server", None)
        if prev_server is not None:
            sys.modules["server"] = prev_server
        if added and ba_path in sys.path:
            sys.path.remove(ba_path)


@pytest.fixture
def c3_client(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("")
    monkeypatch.setenv("ENV_PATH", str(env_file))
    monkeypatch.setenv("API1_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("BROWSER_AUTH_SKIP_WARM_NOVNC", "1")
    c3_server = _scoped_import_c3_server()
    with TestClient(c3_server.app) as client:
        yield client


def test_setup_shows_two_portal_radios_m365_default(c3_client):
    r = c3_client.get("/setup")
    assert r.status_code == 200
    text = r.text
    assert "Microsoft 365 Copilot" in text
    assert "m365.cloud.microsoft/chat" in text
    assert "Consumer Copilot" in text
    assert "copilot.microsoft.com" in text
    assert 'name="profile"' in text
    assert 'value="m365_hub"' in text
    assert 'value="consumer"' in text
    assert re.search(r'value="m365_hub"\s+checked', text), "m365 should be default selected"
    assert not re.search(r'value="consumer"\s+checked', text)
    assert "POST /navigate" in text
    assert "6080" in text
    assert "Open portal in VNC" in text
    assert "Connect to selected portal" in text
    assert 'id="openPortalBtn"' in text


def test_setup_respects_consumer_in_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("COPILOT_PORTAL_PROFILE=consumer\n")
    monkeypatch.setenv("ENV_PATH", str(env_file))
    monkeypatch.setenv("BROWSER_AUTH_SKIP_WARM_NOVNC", "1")
    c3_server = _scoped_import_c3_server()
    with TestClient(c3_server.app) as client:
        r = client.get("/setup")
    assert r.status_code == 200
    assert re.search(r'value="consumer"\s+checked', r.text)
    assert not re.search(r'value="m365_hub"\s+checked', r.text)
