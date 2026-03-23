"""
Unit tests for config.py — environment variable loading and validation.
Uses monkeypatch to set/clear env vars without touching the real environment.
"""
import pytest
import importlib


@pytest.fixture(autouse=True)
def reload_config():
    """Re-import config after each test so env changes take effect."""
    import config
    yield
    importlib.reload(config)


def test_validate_config_raises_when_no_cookie(monkeypatch):
    monkeypatch.delenv("BING_COOKIES", raising=False)
    monkeypatch.delenv("COPILOT_COOKIES", raising=False)
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    import config as cfg
    importlib.reload(cfg)
    with pytest.raises(ValueError, match="COPILOT_COOKIES"):
        cfg.validate_config()


def test_validate_config_passes_with_cookie(monkeypatch):
    monkeypatch.setenv("BING_COOKIES", "some-real-cookie")
    import config as cfg
    import importlib
    importlib.reload(cfg)
    assert cfg.validate_config() is True


def test_model_map_contains_expected_keys():
    import config
    expected = {"copilot", "gpt-4", "gpt-4o", "copilot-balanced", "copilot-creative", "copilot-precise"}
    for key in expected:
        assert key in config.MODEL_MAP, f"Missing model key: {key}"


def test_default_host_and_port():
    import config
    assert config.HOST == "0.0.0.0"
    assert config.PORT == 8000


def test_timeout_defaults():
    import config
    assert config.REQUEST_TIMEOUT == 60
    assert config.CONNECT_TIMEOUT == 15


def test_pool_warm_count_default():
    import config
    assert config.POOL_WARM_COUNT == 2


def test_agent_max_history_default():
    import config
    assert config.AGENT_MAX_HISTORY == 1000


def test_custom_timeout_from_env(monkeypatch):
    monkeypatch.setenv("REQUEST_TIMEOUT", "120")
    monkeypatch.setenv("CONNECT_TIMEOUT", "30")
    import config as cfg
    import importlib
    importlib.reload(cfg)
    assert cfg.REQUEST_TIMEOUT == 120
    assert cfg.CONNECT_TIMEOUT == 30


def test_portal_defaults_m365_when_unset(monkeypatch):
    monkeypatch.delenv("COPILOT_PORTAL_PROFILE", raising=False)
    monkeypatch.delenv("COPILOT_PORTAL_BASE_URL", raising=False)
    monkeypatch.delenv("COPILOT_PORTAL_API_BASE_URL", raising=False)
    import config as cfg
    import importlib
    importlib.reload(cfg)
    assert cfg.COPILOT_PORTAL_PROFILE == "m365_hub"
    assert cfg.portal_base_url_resolved() == "https://m365.cloud.microsoft/chat/"
    assert cfg.copilot_api_base_url() == "https://copilot.microsoft.com"
    assert cfg.copilot_browser_origin() == "https://m365.cloud.microsoft"
    assert "m365.cloud.microsoft" in cfg.copilot_browser_referer()


def test_portal_consumer_explicit_env(monkeypatch):
    monkeypatch.setenv("COPILOT_PORTAL_PROFILE", "consumer")
    monkeypatch.delenv("COPILOT_PORTAL_BASE_URL", raising=False)
    monkeypatch.delenv("COPILOT_PORTAL_API_BASE_URL", raising=False)
    import config as cfg
    importlib.reload(cfg)
    assert cfg.COPILOT_PORTAL_PROFILE == "consumer"
    assert cfg.portal_base_url_resolved() == "https://copilot.microsoft.com/"
    assert cfg.copilot_browser_origin() == "https://copilot.microsoft.com"


def test_portal_m365_hub_referer_and_default_api(monkeypatch):
    monkeypatch.setenv("COPILOT_PORTAL_PROFILE", "m365_hub")
    monkeypatch.delenv("COPILOT_PORTAL_BASE_URL", raising=False)
    monkeypatch.delenv("COPILOT_PORTAL_API_BASE_URL", raising=False)
    import config as cfg
    import importlib
    importlib.reload(cfg)
    assert cfg.COPILOT_PORTAL_PROFILE == "m365_hub"
    assert cfg.portal_base_url_resolved() == "https://m365.cloud.microsoft/chat/"
    assert cfg.copilot_api_base_url() == "https://copilot.microsoft.com"
    assert cfg.copilot_browser_origin() == "https://m365.cloud.microsoft"
    assert cfg.copilot_browser_referer() == "https://m365.cloud.microsoft/chat/"


def test_portal_invalid_profile_falls_back_consumer(monkeypatch):
    monkeypatch.setenv("COPILOT_PORTAL_PROFILE", "nope")
    import config as cfg
    import importlib
    importlib.reload(cfg)
    assert cfg.COPILOT_PORTAL_PROFILE == "consumer"


def test_portal_base_url_normalizes_m365_com_typo(monkeypatch):
    monkeypatch.setenv("COPILOT_PORTAL_PROFILE", "consumer")
    monkeypatch.setenv("COPILOT_PORTAL_BASE_URL", "https://m365.cloud.microsoft.com/chat")
    monkeypatch.delenv("COPILOT_PORTAL_API_BASE_URL", raising=False)
    import config as cfg
    import importlib
    importlib.reload(cfg)
    assert cfg.portal_base_url_resolved() == "https://m365.cloud.microsoft/chat/"


def test_portal_api_override(monkeypatch):
    monkeypatch.setenv("COPILOT_PORTAL_PROFILE", "consumer")
    monkeypatch.setenv("COPILOT_PORTAL_API_BASE_URL", "https://example.test")
    import config as cfg
    import importlib
    importlib.reload(cfg)
    assert cfg.copilot_ws_chat_url() == "wss://example.test/c/api/chat"
