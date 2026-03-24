"""
Integration-style profile flow checks for C2 -> C1 -> C3 cookie routing assumptions.

These tests avoid real browser/network calls and instead verify the contract surfaces
that tie the containers together:
- C3 profile selection and target domains
- C1 profile/provider API base selection
- C1 health of chat endpoint under profile-specific cookie shapes
"""
from __future__ import annotations

import importlib


def test_m365_profile_selects_m365_api_base(monkeypatch):
    monkeypatch.setenv("COPILOT_PORTAL_PROFILE", "m365_hub")
    monkeypatch.setenv("COPILOT_PROVIDER", "auto")
    monkeypatch.delenv("COPILOT_PORTAL_API_BASE_URL", raising=False)
    import config as cfg
    importlib.reload(cfg)
    assert cfg.resolved_provider() == "m365"
    assert cfg.copilot_api_base_url() == "https://m365.cloud.microsoft"


def test_consumer_profile_selects_copilot_api_base(monkeypatch):
    monkeypatch.setenv("COPILOT_PORTAL_PROFILE", "consumer")
    monkeypatch.setenv("COPILOT_PROVIDER", "auto")
    monkeypatch.delenv("COPILOT_PORTAL_API_BASE_URL", raising=False)
    import config as cfg
    importlib.reload(cfg)
    assert cfg.resolved_provider() == "copilot"
    assert cfg.copilot_api_base_url() == "https://copilot.microsoft.com"


def test_chat_endpoint_accepts_m365_cookie_shape(test_app, monkeypatch):
    monkeypatch.setenv(
        "COPILOT_COOKIES",
        "OH.SID=abc;MSFPC=def;OH.FLID=ghi;OH.DCAffinity=OH-cin",
    )
    monkeypatch.setenv("COPILOT_PORTAL_PROFILE", "m365_hub")
    monkeypatch.setenv("COPILOT_PROVIDER", "auto")
    test_app.post("/v1/reload-config")
    r = test_app.post(
        "/v1/chat/completions",
        json={
            "model": "copilot",
            "messages": [{"role": "user", "content": "ping"}],
        },
    )
    # Conftest stubs upstream stream; this asserts profile cookie shape doesn't break C1 path.
    assert r.status_code == 200


def test_chat_endpoint_accepts_consumer_cookie_shape(test_app, monkeypatch):
    monkeypatch.setenv(
        "COPILOT_COOKIES",
        "MUID=abc;__Host-copilot-anon=xyz;_C_ETH=1;SRCHHPGUSR=test",
    )
    monkeypatch.setenv("COPILOT_PORTAL_PROFILE", "consumer")
    monkeypatch.setenv("COPILOT_PROVIDER", "auto")
    test_app.post("/v1/reload-config")
    r = test_app.post(
        "/v1/chat/completions",
        json={
            "model": "copilot",
            "messages": [{"role": "user", "content": "ping"}],
        },
    )
    assert r.status_code == 200
