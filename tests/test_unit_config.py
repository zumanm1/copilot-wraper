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
    import config
    importlib.reload(config)
    # After reload, BING_COOKIES is "" (the default)
    import importlib as _il
    import config as cfg
    _il.reload(cfg)
    with pytest.raises(ValueError, match="BING_COOKIES"):
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
