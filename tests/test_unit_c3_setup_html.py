"""C3 /setup page HTML contract (TestClient; no Docker)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "browser_auth"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


@pytest.fixture
def c3_client(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("")
    monkeypatch.setenv("ENV_PATH", str(env_file))
    monkeypatch.setenv("API1_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("BROWSER_AUTH_SKIP_WARM_NOVNC", "1")
    import importlib
    import server as c3_server
    importlib.reload(c3_server)
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
    assert "Open selected portal in VNC browser" in text
    assert 'id="openPortalBtn"' in text


def test_setup_respects_consumer_in_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("COPILOT_PORTAL_PROFILE=consumer\n")
    monkeypatch.setenv("ENV_PATH", str(env_file))
    monkeypatch.setenv("BROWSER_AUTH_SKIP_WARM_NOVNC", "1")
    import importlib
    import server as c3_server
    importlib.reload(c3_server)
    with TestClient(c3_server.app) as client:
        r = client.get("/setup")
    assert r.status_code == 200
    assert re.search(r'value="consumer"\s+checked', r.text)
    assert not re.search(r'value="m365_hub"\s+checked', r.text)
